"""The BLE MIDI integration.

Turns a Bluetooth LE MIDI controller into a Home Assistant control surface.
"""

from __future__ import annotations

from dataclasses import dataclass
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


@dataclass(slots=True)
class MvaveData:
    """What one configured device carries: its link, and the surface driving it.

    Two things rather than one because a service has to be able to reach either. Telling
    the surface to go to a page and sending a device a raw MIDI message are both
    legitimate, and neither belongs to the other.
    """

    coordinator: MvaveCoordinator
    runner: SurfaceRunner


type MvaveConfigEntry = ConfigEntry[MvaveData]

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.EVENT,
    Platform.SELECT,
    Platform.SENSOR,
]

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

    coordinator = MvaveCoordinator(hass, entry, address, name)
    # The surface is the profile engine driving the grid. It builds itself once the device
    # has been armed, because only then is the real note map known.
    runner = SurfaceRunner(hass, entry, coordinator)
    entry.runtime_data = MvaveData(coordinator=coordinator, runner=runner)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    entry.async_on_unload(runner.async_start())

    async def _async_options_changed(hass: HomeAssistant, entry: MvaveConfigEntry) -> None:
        """Rebuild the surface in place when the options change."""
        entry.runtime_data.runner.reconfigure()

    entry.async_on_unload(entry.add_update_listener(_async_options_changed))

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
    if unloaded:
        # Only on success. Home Assistant runs the `async_on_unload` callbacks only when
        # this returns True, so dropping the link on a failed unload would leave the
        # timers, the subscriptions and the listeners alive against a coordinator that has
        # set its shutdown flag and can therefore never reconnect.
        await entry.runtime_data.coordinator.async_shutdown()
    return unloaded
