"""Which page the surface is showing, as something that can be set.

The device's main feature, and the one piece of its state anybody else has a reason to
read or write. A `select` rather than a sensor plus a service because setting has to be a
first-class verb here: a presence sensor pre-selecting a room and an automation pushing to
a media page when the television comes on are both navigation that nobody pressed, and
``select.select_option`` is the way Home Assistant already spells that.

Deliberately the page and not the pads. A page is a fact about the profile and keeps its
meaning; a pad is a position whose occupant is resolved from the live area registry and
moves the moment somebody adds a lamp to a room.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.components.select import SelectEntity

from .entity import MvaveSurfaceEntity

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

    from . import MvaveConfigEntry
    from .runner import SurfaceRunner

# Nothing here talks to the device directly; the runner serialises its own writes.
PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: MvaveConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the page selector."""
    async_add_entities([MvavePageSelect(entry.runtime_data.runner)])


class MvavePageSelect(MvaveSurfaceEntity, SelectEntity):
    """The page showing now, and every page this surface has."""

    #: The device's main feature, so it takes the device's own name.
    _attr_name = None
    #: Where you are is worth keeping; the rest of these explain it and would put a
    #: database row behind every navigation for facts nobody charts.
    _unrecorded_attributes = frozenset(
        {"page_id", "parent_page_id", "source", "area_id", "depth", "root_page_id"}
    )

    def __init__(self, runner: SurfaceRunner) -> None:
        """Initialise the selector."""
        super().__init__(runner, "page")

    @property
    def options(self) -> list[str]:
        """Every page, by the name a person gave it."""
        return self.view.labels

    @property
    def current_option(self) -> str | None:
        """The page showing now, or nothing while the link is down."""
        return self.view.current_label

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """What the label does not say.

        The id is the one an automation should use with ``mvave.navigate``: labels are for
        people and change when a room is renamed, while the id is the area's own.
        """
        view = self.view
        return {
            "page_id": view.page_id,
            "parent_page_id": view.parent_page_id,
            "source": view.source,
            "area_id": view.area_id,
            "depth": view.depth,
            "root_page_id": view.root_page_id,
        }

    async def async_select_option(self, option: str) -> None:
        """Go to a page because something other than a finger said so."""
        view = self.view
        page_id = view.page_for(option)
        if page_id is None:
            return
        # The root included: the engine turns that into going home rather than a push,
        # for every caller, so this no longer needs to know.
        self.runner.drive(lambda surface: surface.navigate_to(page_id))
