"""Connectivity sensor for the BLE MIDI integration."""

from __future__ import annotations

from typing import TYPE_CHECKING, override

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.const import EntityCategory

from .entity import MvaveEntity

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

    from . import MvaveConfigEntry

# Nothing here talks to the device, so updates need no serialising.
PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: MvaveConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the connectivity sensor."""
    async_add_entities([MvaveConnectedSensor(entry.runtime_data)])


class MvaveConnectedSensor(MvaveEntity, BinarySensorEntity):
    """Whether the integration currently holds the GATT link."""

    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_translation_key = "connected"

    def __init__(self, coordinator) -> None:
        """Initialise the sensor."""
        super().__init__(coordinator, "connected")

    @property
    @override
    def available(self) -> bool:
        """Always available: this is the entity that reports the link being down."""
        return True

    @property
    def is_on(self) -> bool:
        """Return True while connected.

        Read from the client rather than from the platform's availability tracking.
        A BLE MIDI peripheral stops advertising while a central holds it, so the
        platform expires the address a few minutes into every working session.
        """
        return self.coordinator.connected

    async def async_added_to_hass(self) -> None:
        """Follow the coordinator's connection state."""
        self.async_on_remove(self.coordinator.async_add_listener(self.async_write_ha_state))
