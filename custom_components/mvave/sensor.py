"""What the knobs are pointed at.

The eight encoders have no rings, no markings and no labels, so there is nothing on the
device that says what they are adjusting. This is the only place that can say it. It moves
independently of the page — a hold points the knobs at one lamp without going anywhere —
which is why it is its own entity rather than another attribute on the page.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.components.sensor import SensorEntity

from .entity import MvaveSurfaceEntity

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

    from . import MvaveConfigEntry
    from .runner import SurfaceRunner

# Nothing here talks to the device.
PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: MvaveConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the focus sensor."""
    async_add_entities([MvaveFocusSensor(entry.runtime_data.runner)])


class MvaveFocusSensor(MvaveSurfaceEntity, SensorEntity):
    """The entity the knobs are currently adjusting, and which of them can."""

    _attr_translation_key = "focus"
    #: Derived entirely from the state beside it, and it changes whenever that does, so
    #: recording it would store the same fact twice.
    _unrecorded_attributes = frozenset({"knobs"})

    def __init__(self, runner: SurfaceRunner) -> None:
        """Initialise the sensor."""
        super().__init__(runner, "focus")

    @property
    def native_value(self) -> str | None:
        """The focused entity's id, or nothing when the knobs follow the page."""
        return self.view.focus

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Which of the eight encoders are live, and what each one adjusts.

        The device says nothing about this and cannot: eight identical knobs, no rings, no
        markings. Knob one is brightness everywhere so that muscle memory can form, but
        that only helps once it has, and a lamp with no colour temperature leaves knob two
        doing nothing with no way to know.

        Keyed by knob number as a string, because that is what it becomes the moment it
        leaves Python, and a template reading `state_attr(...)['2']` should find what it
        expects rather than a number that used to be an integer.
        """
        return {"knobs": {str(knob): prop for knob, _, prop in self.view.knobs}}
