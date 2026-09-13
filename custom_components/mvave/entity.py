"""Shared entity base for the BLE MIDI integration."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import DeviceInfo, format_mac
from homeassistant.helpers.entity import Entity

from .const import DOMAIN

if TYPE_CHECKING:
    from .coordinator import MvaveCoordinator
    from .runner import SurfaceRunner, SurfaceView


def _known(**fields: str | None) -> dict[str, str]:
    """Only the fields that have an answer, so the rest are absent rather than null."""
    return {key: value for key, value in fields.items() if value}


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
            # All three are read off the device on the first connect and are None before
            # that. Entities exist before anything has connected, so this is the optimistic
            # half; `async_describe_device` is what corrects the registry once the device
            # has actually said who it is.
            # Only what is actually known. `DeviceInfo` is a TypedDict, so passing
            # `manufacturer=None` is a present key with a null value, and the device
            # registry writes anything that is not UNDEFINED — so every restart, when the
            # platforms are set up before anything has connected, overwrote the stored
            # manufacturer, model and name with nothing. Proved against the real registry.
            # The comment in `_describe_device` saying the registry "keeps what it was told
            # first" is the other way round: it keeps what it was told last.
            **_known(
                name=coordinator.device_name,
                manufacturer=coordinator.manufacturer,
                model=coordinator.model,
            ),
        )

    @property
    def available(self) -> bool:
        """Entities are usable only while the link is up."""
        return self.coordinator.connected


class MvaveSurfaceEntity(MvaveEntity):
    """An entity that reports where the surface is, rather than what the hardware did.

    The line between this and :class:`MvaveEntity` is the one that matters in this
    integration. A pad is a position on a device and means whatever is on it at the time;
    a page, a focus and a home button are facts about the *profile*, which is the thing a
    person actually reasons about. Everything hanging off this base is addressable without
    knowing which pad anything happens to be sitting on today.
    """

    def __init__(self, runner: SurfaceRunner, key: str) -> None:
        """Initialise for one surface."""
        super().__init__(runner.coordinator, key)
        self.runner = runner

    @property
    def view(self) -> SurfaceView:
        """Where the surface is now."""
        return self.runner.view

    async def async_added_to_hass(self) -> None:
        """Follow the surface, and the link it depends on."""
        self.async_on_remove(self.runner.async_add_listener(self.async_write_ha_state))
        # Availability comes from the link, and the surface says nothing when it drops.
        self.async_on_remove(self.coordinator.async_add_listener(self.async_write_ha_state))
