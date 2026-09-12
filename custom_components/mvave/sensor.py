"""What the knobs are pointed at.

The eight encoders have no rings, no markings and no labels, so there is nothing on the
device that says what they are adjusting. This is the only place that can say it. It moves
independently of the page — a hold points the knobs at one lamp without going anywhere —
which is why it is its own entity rather than another attribute on the page.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import PERCENTAGE

from .entity import MvaveEntity, MvaveSurfaceEntity

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

    from . import MvaveConfigEntry
    from .coordinator import MvaveCoordinator
    from .runner import SurfaceRunner

# Nothing here talks to the device.
PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: MvaveConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the sensors."""
    async_add_entities(
        [
            MvaveFocusSensor(entry.runtime_data.runner),
            MvaveBatterySensor(entry.runtime_data.coordinator),
        ]
    )


class MvaveBatterySensor(MvaveEntity, SensorEntity):
    """How much charge the pad has left.

    It has said so all along — a standard Battery service, read and notify — and nothing
    had ever asked. A wall-mounted surface that goes flat without warning is a surface
    somebody stops trusting, and this is the only warning available.
    """

    _attr_device_class = SensorDeviceClass.BATTERY
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT
    #: Deliberately *not* EntityCategory.DIAGNOSTIC, which is where roughly half of Home
    #: Assistant's own integrations put a battery. The category decides which section of
    #: the device page an entity is listed under, and nothing else — the percentage badge
    #: on the device card is found by `device_class` alone (`findBatteryEntity` in the
    #: frontend never looks at the category). For a surface somebody keeps on a wall,
    #: "will this still be working tomorrow" is not an aside about the device, so it sits
    #: with the rest rather than under Diagnostic.

    def __init__(self, coordinator: MvaveCoordinator) -> None:
        """Initialise the battery sensor."""
        super().__init__(coordinator, "battery")

    @property
    def native_value(self) -> int | None:
        """The last percentage the device reported, or nothing before it has."""
        return self.coordinator.battery

    async def async_added_to_hass(self) -> None:
        """Follow the link, which is what brings a new reading."""
        self.async_on_remove(self.coordinator.async_add_listener(self.async_write_ha_state))


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
