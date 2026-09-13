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

from homeassistant.const import ATTR_DEVICE_ID
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device_registry import format_mac

from .const import (
    CONF_AREA,
    CONF_COLOUR,
    CONF_DOMAIN_COLOURS,
    CONF_LABEL,
    CONF_PADS,
    DOMAIN,
    SUBENTRY_PAGE,
)
from .engine import IDENTITY, EntityState, PadConfig, Page, Profile, Source, SourceKind
from .engine.describe import describe_slot
from .engine.frames import PAD_COUNT
from .engine.palette import BLUE, DOMAIN_COLOURS
from .engine.resolve import default_actions, resolve

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

#: Everything whose movement can change which rooms become pages.
#:
#: All three, and it takes all three. An area appearing or being renamed is the obvious
#: one. An entity given an area is not an area event at all, and it is what turns an empty
#: room into one worth a page. And a whole device moved into a room carries its entities
#: with it without any of them being touched.
REGISTRY_EVENTS = (
    ar.EVENT_AREA_REGISTRY_UPDATED,
    er.EVENT_ENTITY_REGISTRY_UPDATED,
    dr.EVENT_DEVICE_REGISTRY_UPDATED,
)

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

    def __init__(
        self, hass: HomeAssistant, entry: ConfigEntry, event_type: str, address: str
    ) -> None:
        self.hass = hass
        self.entry = entry
        self.event_type = event_type
        self.address = address
        self._device_id: str | None = None

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
        device_id = self.device_id
        payload: dict[str, Any] = {"type": event_type, **data}
        if device_id is not None:
            payload[ATTR_DEVICE_ID] = device_id
        self.hass.bus.async_fire(self.event_type, payload)

    @property
    def device_id(self) -> str | None:
        """Which device this came from, which the event has to say.

        Home Assistant's own guidance on integration events is explicit that an event
        about a device carries a ``device_id``, and without one the automation editor
        cannot offer the event against the device somebody is looking at. The address is
        already in the payload, but nothing in the interface is indexed by a MAC.

        Looked up once and kept. The device is registered when the first entity is added,
        which is after the surface starts, so this cannot be resolved in the constructor;
        its id never changes afterwards.
        """
        if self._device_id is None:
            # Scoped to this entry, like the coordinator's own lookup. An identifier is
            # unique only *within* a config entry — this pad is very likely also known to
            # the ESPHome proxy relaying it — so the unscoped call has to guess between
            # them, which is why it is deprecated.
            device = dr.async_get(self.hass).async_get_device_by_identifier(
                (DOMAIN, format_mac(self.address)), self.entry.entry_id
            )
            if device is not None:
                self._device_id = device.id
        return self._device_id


def _usable(entry: er.RegistryEntry) -> bool:
    """Whether an entity is worth putting on a pad."""
    return entry.disabled_by is None and entry.hidden_by is None and entry.entity_category is None


def _source_of(data: Mapping[str, Any]) -> Source:
    """Where a page fills its unclaimed pads from, if anywhere.

    A room, or a label, or nothing. Nothing is not a failure to configure: it is a page
    made of exactly the pads its owner pinned, which is what a page is for once it has
    stopped being a room.
    """
    if area := data.get(CONF_AREA):
        return Source(SourceKind.AREA, area)
    if label := data.get(CONF_LABEL):
        return Source(SourceKind.LABEL, label)
    return Source(SourceKind.EXPLICIT)


def _pads_of(data: Mapping[str, Any]) -> dict[int, PadConfig]:
    """Entities somebody pinned to particular pads.

    What a pad does is still derived from what is behind it — a lamp toggles, a scene runs,
    a hold points the knobs at anything a knob can adjust — so pinning chooses the *place*
    and nothing else. Any pad left unpinned fills itself from the page's source as before,
    and a page with no source at all is exactly these and nothing more.

    One based on the way in, because that is how a person counts pads and how every other
    interface here numbers them; zero based from here on, because that is how a frame is
    indexed.
    """
    pinned: dict[int, PadConfig] = {}
    for pad, entity_id in (data.get(CONF_PADS) or {}).items():
        if not entity_id:
            continue
        index = int(pad) - 1
        if 0 <= index < PAD_COUNT:
            pinned[index] = PadConfig(*default_actions(entity_id))
    return pinned


def pads_now(hass: HomeAssistant, data: Mapping[str, Any]) -> list[str | None]:
    """What a page made of this configuration would put on each of the sixteen pads.

    For the configuration screen, which otherwise shows sixteen empty fields and no hint
    of what is already there — a page that fills itself from a room looks identical to an
    empty one. Names, in reading order, ``None`` where a pad is dark.

    Deliberately *shown* rather than pre-filled into the fields. Every non-empty field on
    that screen is saved as a **pin**, so pre-filling what the room supplies would mean
    that merely opening the screen and pressing submit froze the page: it would stop
    following the room, and the next lamp added there would never appear on it.
    """
    page = Page(
        id="preview",
        title="",
        colour=BLUE,
        source=_source_of(data),
        pads=_pads_of(data),
    )
    registry = HomeAssistantRegistry(hass)
    slots = resolve(page, registry, Profile(pages={}, root_id="preview"))
    named: list[str | None] = []
    for index, slot in enumerate(slots):
        described = describe_slot(index, slot, registry)
        named.append(described["name"] or described["entity_id"])
    return named


def build_profile(hass: HomeAssistant, entry: ConfigEntry | None = None) -> Profile:
    """A surface built from the house as it stands, and from whatever was configured.

    With nothing configured this is one page per room that has anything in it, plus an
    index listing them. That is the out-of-the-box promise from the design brief:
    configuring means overriding, never building from zero, so there has to be something
    to override before anybody starts.

    Once somebody has made a page, the pages are theirs and nothing is invented. A page is
    a subentry: it has a name, a colour, and optionally a room or a label to fill itself
    from — and a page with neither is sixteen pads its owner pins by hand, which is the
    whole reason a page is not the same thing as a room.

    Colours repeat past the fifth page. Only five are reliably distinguishable at a glance
    (``docs/HARDWARE-BLE.md`` section 9.1), so beyond that the pad's fixed position on the
    index is what identifies it, which is also the one channel that survives colour vision
    deficiency.
    """
    registry = HomeAssistantRegistry(hass)
    options: Mapping[str, Any] = entry.options if entry else {}
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

    made = list(entry.subentries.values()) if entry else []
    if made:
        for index, page in enumerate(made):
            if page.subentry_type != SUBENTRY_PAGE:
                continue
            pages[page.subentry_id] = Page(
                id=page.subentry_id,
                title=page.title,
                colour=page.data.get(CONF_COLOUR) or IDENTITY[index % len(IDENTITY)],
                source=_source_of(page.data),
                pads=_pads_of(page.data),
                parent_id=ROOT_ID,
            )
    else:
        # Nobody has made a page yet, so every room with something in it, alphabetically.
        # These are named after their area, which is the one case where a page id and an
        # area id are the same string — and it stops mattering the moment anybody edits
        # one, because editing makes a subentry with an id of its own.
        areas = sorted(ar.async_get(hass).async_list_areas(), key=lambda area: area.name.lower())
        for area in areas:
            if len(registry.entities_in_area(area.id)) < MIN_ENTITIES:
                continue
            pages[area.id] = Page(
                id=area.id,
                title=area.name,
                colour=IDENTITY[len(pages) % len(IDENTITY)],
                source=Source(SourceKind.AREA, area.id),
                parent_id=ROOT_ID,
            )

    return Profile(
        pages=pages,
        root_id=ROOT_ID,
        colours={**DOMAIN_COLOURS, **options.get(CONF_DOMAIN_COLOURS, {})},
    )
