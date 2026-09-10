"""The BLE MIDI integration.

Turns a Bluetooth LE MIDI controller into a Home Assistant control surface.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from bleak_retry_connector import close_stale_connections_by_address
from homeassistant.components import bluetooth
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_ADDRESS, CONF_NAME, EVENT_HOMEASSISTANT_STOP, Platform
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import config_validation as cv

from .const import DOMAIN, LOGGER
from .coordinator import MvaveCoordinator
from .runner import SurfaceRunner
from .services import async_setup_services

if TYPE_CHECKING:
    from homeassistant.core import Event, HomeAssistant
    from homeassistant.helpers.typing import ConfigType

type MvaveConfigEntry = ConfigEntry[MvaveCoordinator]

PLATFORMS: list[Platform] = [Platform.BINARY_SENSOR, Platform.EVENT]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register services once, whether or not any device is configured.

    Doing this here rather than per config entry means a service call against a device
    that is not set up explains itself, instead of the service simply not existing.
    """
    async_setup_services(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: MvaveConfigEntry) -> bool:
    """Set up one BLE MIDI device from a config entry."""
    address: str = entry.data[CONF_ADDRESS].upper()
    name: str = entry.data.get(CONF_NAME) or address

    # A BLE MIDI peripheral accepts one central and goes silent while held, so a
    # connection left over from a previous run would keep it unreachable.
    await close_stale_connections_by_address(address)

    if not bluetooth.async_ble_device_from_address(hass, address, connectable=True):
        raise ConfigEntryNotReady(
            translation_domain=DOMAIN,
            translation_key="device_not_found",
            translation_placeholders={
                "address": address,
                "reason": bluetooth.async_address_reachability_diagnostics(
                    hass, address, bluetooth.BluetoothReachabilityIntent.CONNECTION
                ),
            },
        )

    coordinator = MvaveCoordinator(hass, address, name)
    entry.runtime_data = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # The surface is the profile engine driving the grid. It builds itself once the device
    # has been armed, because only then is the real note map known.
    entry.async_on_unload(SurfaceRunner(hass, coordinator).async_start())

    # Start after the platforms, so entities are subscribed before the first connect.
    entry.async_on_unload(coordinator.async_start())

    async def _async_stop(event: Event) -> None:
        await coordinator.async_shutdown()

    entry.async_on_unload(hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, _async_stop))

    LOGGER.debug("%s: set up, waiting for an advertisement to connect", address)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: MvaveConfigEntry) -> bool:
    """Unload a config entry, releasing the device."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    await entry.runtime_data.async_shutdown()
    return unloaded
