"""Shared entity base for the BLE MIDI integration."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import DeviceInfo, format_mac
from homeassistant.helpers.entity import Entity

from .const import DOMAIN

if TYPE_CHECKING:
    from .coordinator import MvaveCoordinator


class MvaveEntity(Entity):
    """An entity belonging to one BLE MIDI device."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, coordinator: MvaveCoordinator, key: str) -> None:
        """Initialise the entity for a coordinator and a stable key."""
        self.coordinator = coordinator
        address = coordinator.address
        self._attr_unique_id = f"{format_mac(address)}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, format_mac(address))},
            # Lets Home Assistant merge this with the same physical device as seen by
            # another integration, such as the ESPHome proxy that relays it.
            connections={(dr.CONNECTION_BLUETOOTH, address)},
            name=coordinator.device_name,
        )

    @property
    def available(self) -> bool:
        """Entities are usable only while the link is up."""
        return self.coordinator.connected
