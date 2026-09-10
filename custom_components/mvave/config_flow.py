"""Config flow for the BLE MIDI integration."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import voluptuous as vol
from homeassistant.components.bluetooth import (
    BluetoothServiceInfoBleak,
    async_discovered_service_info,
    async_request_active_scan,
)
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_ADDRESS, CONF_NAME
from homeassistant.helpers.device_registry import format_mac

from .const import DOMAIN, MIDI_SERVICE_UUID

if TYPE_CHECKING:
    from collections.abc import Mapping


class MvaveConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle discovery and manual addition of a BLE MIDI device."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialise the flow."""
        self._discovery: BluetoothServiceInfoBleak | None = None
        self._discovered: dict[str, BluetoothServiceInfoBleak] = {}

    async def async_step_bluetooth(
        self, discovery_info: BluetoothServiceInfoBleak
    ) -> ConfigFlowResult:
        """Handle a device discovered by its BLE-MIDI service UUID."""
        await self.async_set_unique_id(format_mac(discovery_info.address))
        self._abort_if_unique_id_configured()
        self._discovery = discovery_info
        return await self.async_step_bluetooth_confirm()

    async def async_step_bluetooth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask the user to confirm a discovered device."""
        assert self._discovery is not None
        discovery = self._discovery

        if user_input is not None:
            return self._create_entry(discovery)

        self._set_confirm_only()
        placeholders: Mapping[str, str] = {"name": discovery.name, "address": discovery.address}
        self.context["title_placeholders"] = dict(placeholders)
        return self.async_show_form(
            step_id="bluetooth_confirm", description_placeholders=dict(placeholders)
        )

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Add a device by picking it from the ones currently in range."""
        if user_input is not None:
            address = user_input[CONF_ADDRESS]
            await self.async_set_unique_id(format_mac(address), raise_on_progress=False)
            self._abort_if_unique_id_configured()
            return self._create_entry(self._discovered[address])

        # AUTO-mode scanners sit passive until asked, so a manual add needs a nudge.
        await async_request_active_scan(self.hass)

        configured = self._async_current_ids(include_ignore=False)
        self._discovered = {
            info.address: info
            for info in async_discovered_service_info(self.hass, connectable=True)
            if MIDI_SERVICE_UUID in info.service_uuids
            and format_mac(info.address) not in configured
        }
        if not self._discovered:
            return self.async_abort(reason="no_devices_found")

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_ADDRESS): vol.In(
                        {
                            address: f"{info.name} ({address})"
                            for address, info in self._discovered.items()
                        }
                    )
                }
            ),
        )

    def _create_entry(self, discovery: BluetoothServiceInfoBleak) -> ConfigFlowResult:
        """Create the config entry. No GATT work happens here.

        Connecting during the flow would take the device's single central slot away
        from whatever else is using it, so the entry only records how to reach it.
        """
        return self.async_create_entry(
            title=discovery.name,
            data={CONF_ADDRESS: discovery.address, CONF_NAME: discovery.name},
        )
