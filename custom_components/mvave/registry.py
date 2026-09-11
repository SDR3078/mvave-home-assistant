"""The house, as the engine is allowed to see it.

Two adapters and a profile builder. Between them they are the whole of what the engine
knows about Home Assistant, which is the point of the protocols in ``engine.ports``:
everything above here can be exercised against a dictionary, and everything below is this
file.

Nothing here decides what anything looks like or what a press means. It answers "what is
in the kitchen", "is that lamp on" and "call this service", and stops.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from .const import CONF_DOMAIN_COLOURS, CONF_PAGE_COLOURS, CONF_PAGES
from .engine import IDENTITY, EntityState, Page, Profile, Source, SourceKind
from .engine.palette import BLUE, DOMAIN_COLOURS

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

#: The page an empty navigation stack shows.
ROOT_ID = "home"

#: Areas whose entities are all configuration or diagnostics have nothing worth a pad, and
#: an index full of empty rooms is worse than a short one.
MIN_ENTITIES = 1


class HomeAssistantRegistry:
    """Reading the world. Satisfies ``engine.ports.RegistryView``."""

    def __init__(self, hass: HomeAssistant) -> None:
        """Hold the instance. The registries are looked up per call, not cached.

        They change under you: a device is renamed into another room, an integration
        finishes setting up. Caching them would mean a surface that quietly goes stale and
        only comes right on a restart.
        """
        self.hass = hass

    def entities_in_area(self, area_id: str) -> Sequence[str]:
        """Everything in a room that is worth a pad.

        An entity's own area beats its device's, which is how somebody puts one plug of a
        multi-socket in the hall. Hidden, disabled and category entities are left out:
        those are the update sensors and the firmware version numbers, and a room page
        made mostly of those is useless.
        """
        entities = er.async_get(self.hass)
        devices = dr.async_get(self.hass)

        found: list[str] = []
        seen: set[str] = set()
        for entry in er.async_entries_for_area(entities, area_id):
            if _usable(entry):
                found.append(entry.entity_id)
                seen.add(entry.entity_id)
        for device in dr.async_entries_for_area(devices, area_id):
            for entry in er.async_entries_for_device(entities, device.id):
                # An entity given its own area has already been placed, or deliberately
                # placed elsewhere; either way its device does not get to override it.
                if entry.area_id is None and entry.entity_id not in seen and _usable(entry):
                    found.append(entry.entity_id)
                    seen.add(entry.entity_id)
        return found

    def entities_with_label(self, label: str) -> Sequence[str]:
        """Everything carrying a label, which is how a page spans rooms."""
        entities = er.async_get(self.hass)
        return [
            entry.entity_id
            for entry in er.async_entries_for_label(entities, label)
            if _usable(entry)
        ]

    def state_of(self, entity_id: str) -> EntityState | None:
        """One entity's state, or None if nothing by that id exists."""
        state = self.hass.states.get(entity_id)
        if state is None:
            return None
        return EntityState(entity_id, state.state, state.attributes)


class HomeAssistantSink:
    """Changing the world. Satisfies ``engine.ports.ActionSink``."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, event_type: str) -> None:
        self.hass = hass
        self.entry = entry
        self.event_type = event_type

    def call(self, domain: str, service: str, data: Mapping[str, Any]) -> None:
        """Call a service without waiting for it.

        Deliberately fire and forget. The surface renders what it asked for and lets the
        state change that follows correct it; waiting for a Zigbee round trip before
        lighting a pad feels broken even when everything is working.
        """
        # Owned by the config entry, so it cannot outlive the device it belongs to and it
        # says which device it came from if it ever has to be waited for.
        self.entry.async_create_task(
            self.hass,
            self.hass.services.async_call(domain, service, dict(data), blocking=False),
            f"mvave {domain}.{service}",
            eager_start=True,
        )

    def fire(self, event_type: str, data: Mapping[str, Any]) -> None:
        """Fire an event on the bus, for a page that would rather an automation decided."""
        self.hass.bus.async_fire(self.event_type, {"type": event_type, **data})


def _usable(entry: er.RegistryEntry) -> bool:
    """Whether an entity is worth putting on a pad."""
    return entry.disabled_by is None and entry.hidden_by is None and entry.entity_category is None


def build_profile(hass: HomeAssistant, options: Mapping[str, Any] | None = None) -> Profile:
    """A surface built from the house as it stands, and from whatever was configured.

    With nothing configured this is one page per room that has anything in it, plus an
    index listing them. That is the out-of-the-box promise from the design brief:
    configuring means overriding, never building from zero, so there has to be something
    to override before anybody starts.

    Colours repeat past the fifth room. Only five are reliably distinguishable at a glance
    (``docs/HARDWARE-BLE.md`` section 9.1), so beyond that the pad's fixed position on the
    index is what identifies it, which is also the one channel that survives colour vision
    deficiency.
    """
    options = options or {}
    registry = HomeAssistantRegistry(hass)
    chosen: list[str] | None = options.get(CONF_PAGES)
    page_colours: Mapping[str, int] = options.get(CONF_PAGE_COLOURS, {})

    if chosen is None:
        # Nobody has chosen, so every room with something in it, alphabetically.
        areas = sorted(ar.async_get(hass).async_list_areas(), key=lambda area: area.name.lower())
        chosen = [area.id for area in areas if len(registry.entities_in_area(area.id))]

    registry_areas = ar.async_get(hass)
    pages: dict[str, Page] = {
        ROOT_ID: Page(
            id=ROOT_ID,
            title="Home",
            colour=BLUE,
            source=Source(SourceKind.PAGES),
            # An index is where you end up, not somewhere to time out of.
            idle_timeout=0,
        )
    }
    for area_id in chosen:
        area = registry_areas.async_get_area(area_id)
        if area is None or len(registry.entities_in_area(area_id)) < MIN_ENTITIES:
            continue
        pages[area_id] = Page(
            id=area_id,
            title=area.name,
            colour=page_colours.get(area_id, IDENTITY[len(pages) % len(IDENTITY)]),
            source=Source(SourceKind.AREA, area_id),
            parent_id=ROOT_ID,
        )
    return Profile(
        pages=pages,
        root_id=ROOT_ID,
        colours={**DOMAIN_COLOURS, **options.get(CONF_DOMAIN_COLOURS, {})},
    )
