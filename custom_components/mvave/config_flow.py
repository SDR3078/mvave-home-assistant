"""Config flow for the BLE MIDI integration."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Final

import voluptuous as vol
from homeassistant.components.bluetooth import (
    BluetoothServiceInfoBleak,
    async_discovered_service_info,
    async_request_active_scan,
)
from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.const import CONF_ADDRESS, CONF_NAME
from homeassistant.core import callback
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers.device_registry import format_mac
from homeassistant.helpers.selector import (
    AreaSelector,
    AreaSelectorConfig,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)

from .const import (
    CONF_DOMAIN_COLOURS,
    CONF_PAGE_COLOURS,
    CONF_PAGES,
    DOMAIN,
    MIDI_SERVICE_UUID,
)
from .engine.palette import BLUE, DOMAIN_COLOURS, GREEN, IDENTITY, ORANGE, PURPLE, RED
from .registry import build_profile

if TYPE_CHECKING:
    from collections.abc import Mapping


class MvaveConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle discovery and manual addition of a BLE MIDI device."""

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> MvaveOptionsFlow:
        """Let a configured device be reconfigured."""
        return MvaveOptionsFlow()

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


#: The colours a person may choose. Five is not a design preference: it is how many this
#: device can show that anybody can tell apart from across a room, measured on the grid
#: (``docs/HARDWARE-BLE.md`` section 9.1). White is missing on purpose, because white is
#: what "off" means and the readability of every page rests on that.
CHOOSABLE: Final = {
    "blue": BLUE,
    "green": GREEN,
    "orange": ORANGE,
    "red": RED,
    "purple": PURPLE,
}
BY_VALUE: Final = {value: name for name, value in CHOOSABLE.items()}

#: The kinds of thing worth colouring separately. Not every domain: a list of forty would
#: be worse than useless, and anything not here follows the built-in default.
COLOURABLE: Final = ("light", "switch", "media_player", "cover", "climate", "scene")


def _colour_selector() -> SelectSelector:
    """A choice of the five colours, by name."""
    return SelectSelector(
        SelectSelectorConfig(
            options=list(CHOOSABLE),
            mode=SelectSelectorMode.DROPDOWN,
            translation_key="colour",
        )
    )


class MvaveOptionsFlow(OptionsFlow):
    """Choose which rooms appear, what colour they are, and what things look like."""

    def __init__(self) -> None:
        """Start with nothing chosen."""
        self._pages: list[str] = []

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Pick the rooms, in the order they should appear on the index."""
        if user_input is not None:
            self._pages = list(user_input[CONF_PAGES])
            return await self.async_step_colours()

        current = self.config_entry.options.get(CONF_PAGES)
        if current is None:
            # Everything with something in it, which is what the surface does on its own
            # when nobody has configured anything.
            current = [page.id for page in build_profile(self.hass).pages.values()]
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_PAGES, default=current): AreaSelector(
                        AreaSelectorConfig(multiple=True)
                    )
                }
            ),
        )

    async def async_step_colours(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Give each room a colour, and each kind of thing a colour of its own."""
        if user_input is not None:
            return self.async_create_entry(
                data={
                    CONF_PAGES: self._pages,
                    CONF_PAGE_COLOURS: {
                        area: CHOOSABLE[user_input[f"page_{area}"]] for area in self._pages
                    },
                    CONF_DOMAIN_COLOURS: {
                        domain: CHOOSABLE[user_input[f"domain_{domain}"]] for domain in COLOURABLE
                    },
                }
            )

        areas = ar.async_get(self.hass)
        pages = self.config_entry.options.get(CONF_PAGE_COLOURS, {})
        domains = self.config_entry.options.get(CONF_DOMAIN_COLOURS, {})
        fields: dict[Any, Any] = {}
        for index, area_id in enumerate(self._pages):
            # Rooms past the fifth share a colour with an earlier one, because there are
            # only five. Which is fine: position on the index identifies them, and that is
            # the one channel that survives colour vision deficiency.
            fallback = IDENTITY[(index + 1) % len(IDENTITY)]
            fields[
                vol.Required(
                    f"page_{area_id}",
                    description={"suggested_value": BY_VALUE.get(pages.get(area_id, fallback))},
                )
            ] = _colour_selector()
        for domain in COLOURABLE:
            chosen = domains.get(domain, DOMAIN_COLOURS.get(domain, ORANGE))
            fields[
                vol.Required(f"domain_{domain}", description={"suggested_value": BY_VALUE[chosen]})
            ] = _colour_selector()

        return self.async_show_form(
            step_id="colours",
            data_schema=vol.Schema(fields),
            description_placeholders={
                "rooms": ", ".join(
                    (
                        areas.async_get_area(area_id).name
                        if areas.async_get_area(area_id)
                        else area_id
                    )
                    for area_id in self._pages
                )
            },
        )
