"""Config flow for the BLE MIDI integration."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Final

if TYPE_CHECKING:
    from collections.abc import Mapping

import voluptuous as vol
from homeassistant.components.bluetooth import (
    BluetoothServiceInfoBleak,
    async_discovered_service_info,
    async_request_active_scan,
)
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    ConfigSubentry,
    ConfigSubentryFlow,
    OptionsFlow,
    SubentryFlowResult,
)
from homeassistant.const import CONF_ADDRESS, CONF_NAME
from homeassistant.core import callback
from homeassistant.helpers.device_registry import format_mac
from homeassistant.helpers.selector import (
    AreaSelector,
    EntitySelector,
    EntitySelectorConfig,
    LabelSelector,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)

from .const import (
    CONF_AREA,
    CONF_COLOUR,
    CONF_DOMAIN_COLOURS,
    CONF_LABEL,
    CONF_PADS,
    DOMAIN,
    MIDI_SERVICE_UUID,
    SUBENTRY_PAGE,
)
from .engine.frames import PAD_COUNT
from .engine.palette import BLUE, DOMAIN_COLOURS, GREEN, IDENTITY, ORANGE, PURPLE, RED
from .engine.resolve import PINNABLE
from .registry import pads_now

if TYPE_CHECKING:
    from collections.abc import Mapping


class MvaveConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle discovery and manual addition of a BLE MIDI device."""

    VERSION = 1
    MINOR_VERSION = 2

    @classmethod
    @callback
    def async_get_supported_subentry_types(
        cls, config_entry: ConfigEntry
    ) -> dict[str, type[ConfigSubentryFlow]]:
        """Pages are added one at a time, each with its own row in the interface."""
        return {SUBENTRY_PAGE: PageSubentryFlow}

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


#: The sixteen pads drawn where they sit, so a column of fields can be read as a square.
_NUMBERED: Final = "\n".join(
    "".join(f"{pad:>3} " for pad in range(row * 4 + 1, row * 4 + 5)).rstrip()
    for row in range(PAD_COUNT // 4)
)


def _pad_field(pad: int) -> str:
    """What one pad's form field is called. One based, as a person counts them."""
    return f"pad_{pad}"


def _pad_selector() -> EntitySelector:
    """What a pad may be pointed at.

    Everything a press can reach, and everything a glance can: a pad may be a readout, and
    "is the back door open" is worth one. What is missing is anything the grid physically
    cannot show — a temperature has no on and no off, and a pad that sat white forever
    would be lying about it.
    """
    return EntitySelector(EntitySelectorConfig(domain=sorted(PINNABLE)))


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
    """What each kind of thing looks like, which is the same on every page.

    Pages used to be here too — a screen for which rooms appeared and a screen for their
    colours. They are subentries now, because a page is a thing somebody makes rather than
    a setting somebody changes, and an options flow can only ever describe one of each.
    What is left is the one thing that genuinely is a single setting.
    """

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Give each kind of thing its colour, which is the same on every page."""
        if user_input is not None:
            return self.async_create_entry(
                data={
                    CONF_DOMAIN_COLOURS: {
                        domain: CHOOSABLE[user_input[domain]] for domain in COLOURABLE
                    }
                }
            )

        chosen = self.config_entry.options.get(CONF_DOMAIN_COLOURS, {})
        fields: dict[Any, Any] = {}
        for domain in COLOURABLE:
            colour = chosen.get(domain, DOMAIN_COLOURS.get(domain, ORANGE))
            fields[vol.Required(domain, description={"suggested_value": BY_VALUE[colour]})] = (
                _colour_selector()
            )
        return self.async_show_form(step_id="init", data_schema=vol.Schema(fields))


class PageSubentryFlow(ConfigSubentryFlow):
    """Adding and editing one page.

    A subentry rather than a screen in the options, because a page is a thing somebody
    *makes* — added, renamed, deleted, several of them — and options are for settings that
    exist once. It also gives every page an id of its own, so a page survives the room it
    draws from being renamed or deleted, or never having had one.
    """

    def __init__(self) -> None:
        """Start with a page nobody has described yet."""
        self._page: dict[str, Any] = {}
        self._title = ""
        self._existing: ConfigSubentry | None = None

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        """Add a page."""
        return await self._async_page_form(user_input, existing=None)

    async def async_step_pads(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        """Pin whatever should sit in a particular place.

        Every pad is optional. A pad left empty fills itself from the page's room or label
        as before, so pinning one thing does not mean pinning sixteen — and on a page with
        no room at all, these are the whole page.
        """
        showing = pads_now(self.hass, self._page)
        was: Mapping[str, str] = (
            (self._existing.data.get(CONF_PADS) or {}) if self._existing else {}
        )
        if user_input is not None:
            # Only what somebody actually changed. The fields arrive filled with whatever
            # the page already shows, most of which a room supplied, so storing all of it
            # would pin all of it — and a page that had merely been looked at would quietly
            # stop following its room, which nobody would notice until a lamp added to that
            # room failed to appear on it.
            pads = {
                str(pad): chosen
                for pad in range(1, PAD_COUNT + 1)
                if (chosen := user_input.get(_pad_field(pad)))
                and (chosen != showing[pad - 1] or str(pad) in was)
            }
            data = {**self._page, CONF_PADS: pads}
            if self._existing is None:
                return self.async_create_entry(title=self._title, data=data)
            return self.async_update_and_abort(
                self._get_entry(), self._existing, title=self._title, data=data
            )

        fields: dict[Any, Any] = {
            vol.Optional(
                _pad_field(pad), description={"suggested_value": showing[pad - 1]}
            ): _pad_selector()
            for pad in range(1, PAD_COUNT + 1)
        }
        return self.async_show_form(
            step_id="pads",
            data_schema=vol.Schema(fields),
            description_placeholders={"grid": _NUMBERED},
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Change a page that already exists."""
        self._existing = self._get_reconfigure_subentry()
        return await self._async_page_form(user_input, existing=self._existing)

    async def _async_page_form(
        self, user_input: dict[str, Any] | None, existing: ConfigSubentry | None
    ) -> SubentryFlowResult:
        """One form for both, because adding and editing a page ask the same questions."""
        errors: dict[str, str] = {}
        if user_input is not None:
            area = user_input.get(CONF_AREA)
            label = user_input.get(CONF_LABEL)
            if area and label:
                # A page fills itself from one place. Two would need an order to merge
                # them in, and nothing about either says which should win.
                errors["base"] = "one_source"
            else:
                self._page = {
                    CONF_COLOUR: CHOOSABLE[user_input[CONF_COLOUR]],
                    CONF_AREA: area,
                    CONF_LABEL: label,
                    # Carried through, so the next step resolves the page somebody actually
                    # has rather than the one its room would supply on its own. Without it
                    # the pad fields showed the room's own contents and every existing pin
                    # was missing from the screen that exists to edit them — and the "keep
                    # only what changed" comparison was against a page nobody was looking at.
                    CONF_PADS: (existing.data.get(CONF_PADS) or {}) if existing else {},
                }
                self._title = user_input[CONF_NAME]
                return await self.async_step_pads()

        was = dict(existing.data) if existing else {}
        # A colour nobody has reached yet, so adding several rooms in a row does not need
        # anybody to remember which ones are taken.
        taken = {
            page.data.get(CONF_COLOUR)
            for page in self._get_entry().subentries.values()
            if page is not existing
        }
        spare = next((colour for colour in IDENTITY if colour not in taken), IDENTITY[0])
        suggested = BY_VALUE.get(was.get(CONF_COLOUR, spare), BY_VALUE[spare])

        fields: dict[Any, Any] = {
            vol.Required(CONF_NAME, default=existing.title if existing else vol.UNDEFINED): str,
            vol.Required(CONF_COLOUR, default=suggested): _colour_selector(),
            vol.Optional(
                CONF_AREA, description={"suggested_value": was.get(CONF_AREA)}
            ): AreaSelector(),
            vol.Optional(
                CONF_LABEL, description={"suggested_value": was.get(CONF_LABEL)}
            ): LabelSelector(),
        }
        return self.async_show_form(
            step_id="reconfigure" if existing else "user",
            data_schema=vol.Schema(fields),
            errors=errors,
        )
