"""What the knobs are pointed at.

The eight encoders have no rings, no markings and no labels, so there is nothing on the
device that says what they are adjusting. This is the only place that can say it. It moves
independently of the page — a hold points the knobs at one lamp without going anywhere —
which is why it is its own entity rather than another attribute on the page.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

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
    """The entity the knobs are currently adjusting, if any."""

    _attr_translation_key = "focus"

    def __init__(self, runner: SurfaceRunner) -> None:
        """Initialise the sensor."""
        super().__init__(runner, "focus")

    @property
    def native_value(self) -> str | None:
        """The focused entity's id, or nothing when the knobs follow the page."""
        return self.view.focus
