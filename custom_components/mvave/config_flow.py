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
from homeassistant.helpers.translation import async_get_translations

from .const import (
    CONF_AREA,
    CONF_COLOUR,
    CONF_DEFAULT_PAGE,
    CONF_DOMAIN_COLOURS,
    CONF_FIXED,
    CONF_LABEL,
    CONF_PADS,
    DOMAIN,
    MIDI_SERVICE_UUID,
    SUBENTRY_PAGE,
)
from .devices.smc_pad import PAD_NUMBER_BY_READING_ORDER
from .engine.frames import PAD_COUNT
from .engine.model import STATELESS_DOMAINS
from .engine.palette import BLUE, DOMAIN_COLOURS, GREEN, IDENTITY, ORANGE, PURPLE, RED
from .engine.resolve import PINNABLE
from .registry import ROOT_ID, ROOT_TITLE, pads_now

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
#: Every kind of thing a pad can hold, in the order the boxes list them. Exactly the keys
#: of ``DOMAIN_COLOURS``, which a test holds equal to ``resolve.PINNABLE``, so a domain
#: added to one of those cannot be quietly left off this screen.
PAINTABLE: Final = tuple(DOMAIN_COLOURS)


#: The sixteen pads drawn where they sit, so a column of fields can be read as a square.
#: The numbers are the ones printed on the hardware, which run up the grid: the top-left
#: pad says 13. A square that disagreed with the pads under somebody's fingers would be
#: worse than no square at all.
_NUMBERED: Final = "\n".join(
    "".join(f"{PAD_NUMBER_BY_READING_ORDER[index]:>3} " for index in row).rstrip()
    for row in (range(start, start + 4) for start in range(0, PAD_COUNT, 4))
)


def _pad_field(pad: int) -> str:
    """What one pad's form field is called, by the number printed on it."""
    return f"pad_{pad}"


def _pad_selector() -> EntitySelector:
    """What a pad may be pointed at.

    Everything a press can reach, and everything a glance can: a pad may be a readout, and
    "is the back door open" is worth one. What is missing is anything the grid physically
    cannot show — a temperature has no on and no off, and a pad that sat white forever
    would be lying about it.
    """
    return EntitySelector(EntitySelectorConfig(domain=sorted(PINNABLE)))


#: How each kind of mistake is named when more than one is reported at once. Short, and
#: in English only: the integration ships one language, and the alternative is a separate
#: translation key per pair of problems.
_PROBLEMS: Final = {
    "colour_twice": "In two boxes",
    "colour_missing": "In no box",
    "purple_needs_stateless": "Cannot be purple",
}


def _paintable_selector() -> SelectSelector:
    """Every kind of thing a pad can hold, as chips you can move between the colours."""
    return SelectSelector(
        SelectSelectorConfig(
            options=list(PAINTABLE),
            multiple=True,
            mode=SelectSelectorMode.DROPDOWN,
            translation_key="paintable",
        )
    )


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
        """Say what each colour means, which is the same on every page.

        A box per colour rather than a dropdown per kind of thing, and the difference is
        not cosmetic. The device has five colours; that is the scarce resource the whole
        design is built around, so a form shaped like the palette cannot lie about the
        palette. It also makes moving something *visible*: to paint lights green you take
        them out of the orange box, and you watch what they were grouped with stay behind.

        The screen this replaced could not show that. It offered six kinds of thing and hid
        fourteen, so painting "Lights" green split them silently from switches, fans and
        sirens and merged them just as silently with covers and every readout — one field,
        two invisible changes, and a label reading "Scenes and scripts" that moved scenes
        and left scripts where they were.
        """
        errors: dict[str, str] = {}
        placeholders: dict[str, str] = {}
        if user_input is not None:
            painted: dict[str, int] = {}
            boxes: dict[str, list[str]] = {}
            for name, colour in CHOOSABLE.items():
                for domain in user_input.get(name, ()):
                    boxes.setdefault(domain, []).append(name)
                    painted[domain] = colour
            # Which boxes, not only which kind of thing: "Fans — in two boxes at once" sent
            # somebody through five boxes and twenty chips to find the second one.
            twice = {domain for domain, names in boxes.items() if len(names) > 1}
            missing = set(PAINTABLE) - set(painted)
            stateful = {d for d in user_input.get("purple", ()) if d not in STATELESS_DOMAINS}

            # Purple is refused on anything switchable because purple against white is the
            # one pair measured as too close to tell apart, so purple is only safe where a
            # pad never shows white.
            problems = [
                (key, kinds)
                for key, kinds in (
                    ("colour_twice", twice),
                    ("colour_missing", missing),
                    ("purple_needs_stateless", stateful),
                )
                if kinds
            ]
            if not problems:
                data: dict[str, Any] = {CONF_DOMAIN_COLOURS: painted}
                # By the page's own id, and only when it is a page: the index is what an
                # absent key already means, so choosing it stores nothing.
                resting = user_input.get(CONF_DEFAULT_PAGE)
                if resting and resting != ROOT_ID:
                    data[CONF_DEFAULT_PAGE] = resting
                return self.async_create_entry(data=data)

            # All of them, not the first. Rearranging two boxes can easily leave one kind
            # of thing in two and another in none at the same moment, and reporting only
            # the first sent somebody back round for a problem the form already knew about.
            #
            # Named, too: "something is in no box" makes a person hunt through five boxes
            # and twenty chips for a thing the form can name.
            if len(problems) == 1:
                key, kinds = problems[0]
                errors["base"] = key
                placeholders = {"kinds": await self._named(kinds, boxes)}
            else:
                errors["base"] = "colour_several"
                lines = [
                    f"- {_PROBLEMS[key]}: {await self._named(kinds, boxes)}"
                    for key, kinds in problems
                ]
                placeholders = {"kinds": "\n".join(lines)}

        # Where the pad rests: the index, or any page somebody has made, offered by name and
        # stored by id so renaming the room does not lose it. A page deleted since simply
        # means the index again, which is also what the dropdown then shows.
        made = [
            p for p in self.config_entry.subentries.values() if p.subentry_type == SUBENTRY_PAGE
        ]
        titles = [page.title for page in made]
        pages = [
            {"value": ROOT_ID, "label": ROOT_TITLE},
            *(
                {
                    "value": page.subentry_id,
                    # Two pages called Kitchen — one from the room, one from a label — are
                    # told apart by id, the way the page selector already does.
                    "label": page.title
                    if titles.count(page.title) == 1
                    else f"{page.title} ({page.subentry_id})",
                }
                for page in made
            ),
        ]
        resting = (
            user_input.get(CONF_DEFAULT_PAGE)
            if user_input is not None
            else self.config_entry.options.get(CONF_DEFAULT_PAGE)
        )
        if resting not in {page["value"] for page in pages}:
            resting = ROOT_ID

        chosen = {**DOMAIN_COLOURS, **self.config_entry.options.get(CONF_DOMAIN_COLOURS, {})}
        fields: dict[Any, Any] = {
            vol.Optional(CONF_DEFAULT_PAGE, default=resting): SelectSelector(
                SelectSelectorConfig(options=pages, mode=SelectSelectorMode.DROPDOWN)
            ),
        }
        fields |= {
            vol.Required(
                name,
                description={
                    "suggested_value": user_input.get(name)
                    if user_input is not None
                    else [d for d in PAINTABLE if chosen.get(d) == colour]
                },
            ): _paintable_selector()
            for name, colour in CHOOSABLE.items()
        }
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(fields),
            errors=errors,
            description_placeholders=placeholders,
        )

    async def _named(self, domains: set[str], boxes: Mapping[str, list[str]] | None = None) -> str:
        """The kinds of thing, by the names the boxes call them — and, for anything in
        more than one box, which boxes.

        Read back out of this integration's own translations rather than kept in a second
        list here, because a second list is one that drifts: the chips would say "Blinds,
        curtains and garage doors" while the error said "cover".
        """
        labels = await async_get_translations(
            self.hass, self.hass.config.language, "selector", {DOMAIN}
        )

        def kind(domain: str) -> str:
            return labels.get(f"component.{DOMAIN}.selector.paintable.options.{domain}", domain)

        def colour(name: str) -> str:
            return labels.get(f"component.{DOMAIN}.selector.colour.options.{name}", name)

        named: list[str] = []
        for domain in sorted(domains, key=kind):
            text = kind(domain)
            if boxes is not None and len(boxes.get(domain, ())) > 1:
                text += f" ({' and '.join(colour(name) for name in boxes[domain])})"
            named.append(text)
        return ", ".join(named)


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
        #: What the pad fields were filled from, kept from the moment the form was drawn.
        #: The submission is compared against *this*, not against the room as it stands
        #: when the form comes back: a bulb pairing while the form was open used to make an
        #: untouched save look like an edit, which silently fixed the page.
        self._showing: list[str | None] | None = None

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        """Add a page."""
        return await self._async_page_form(user_input, existing=None)

    async def async_step_pads(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        """Put things on pads, or leave the page to its room.

        The fields arrive holding what the page shows right now. Save them unchanged and
        the page carries on following its room or label, pins and all — looking at a page
        must never quietly freeze it. Change anything and the page is yours: exactly these
        sixteen fields, an empty one a dark pad, and nothing filling in behind them.

        The second rule is what makes clearing a field mean something. Until 2026-09-14 an
        emptied field stored nothing, "nothing stored" meant "let the room decide", and the
        room decided the same thing again — so taking an entity off a room page put it
        straight back, on the first real page the owner made.
        """
        if user_input is not None and self._showing is not None:
            showing = self._showing
        else:
            showing = pads_now(self.hass, self._page)
            self._showing = showing
        if user_input is not None:
            chosen = [
                user_input.get(_pad_field(PAD_NUMBER_BY_READING_ORDER[index])) or None
                for index in range(PAD_COUNT)
            ]
            if chosen == showing and not self._page.get(CONF_FIXED):
                # Untouched. Whatever pins it already had stay pins, and it keeps following.
                data = {**self._page, CONF_PADS: self._page.get(CONF_PADS) or {}}
            else:
                # Stored by position, one based: the printed number is what the field is
                # *called*, not what the configuration is keyed by, which is what let the
                # labels change without migrating anybody's pages. Empty fields are simply
                # absent — with nothing filling in, absent is dark.
                data = {
                    **self._page,
                    CONF_FIXED: True,
                    CONF_PADS: {str(index + 1): e for index, e in enumerate(chosen) if e},
                }
            if self._existing is None:
                return self.async_create_entry(title=self._title, data=data)
            return self.async_update_and_abort(
                self._get_entry(), self._existing, title=self._title, data=data
            )

        # In reading order, so the form runs down the grid the way the square above it does,
        # even though the numbers on the fields count the other way.
        fields: dict[Any, Any] = {
            vol.Optional(
                _pad_field(PAD_NUMBER_BY_READING_ORDER[index]),
                description={"suggested_value": showing[index]},
            ): _pad_selector()
            for index in range(PAD_COUNT)
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
                    # And whether those pins are the whole page. Dropped here, a fixed page
                    # re-saved unchanged would come back following its room, and everything
                    # somebody had cleared off it would return.
                    # ...unless the room or label changed: giving an edited page a
                    # different room is asking for that room. Its pins stay, and the room
                    # fills in around them again. Until 2026-09-15 the flag was carried
                    # whatever the source did, so the screen promised a room would fill
                    # the page and nothing ever did, with delete-and-re-add the only way out.
                    CONF_FIXED: (
                        bool(existing.data.get(CONF_FIXED))
                        and area == existing.data.get(CONF_AREA)
                        and label == existing.data.get(CONF_LABEL)
                        if existing
                        else False
                    ),
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
        schema = vol.Schema(fields)
        if user_input is not None:
            # A refused form comes back holding what was typed, not what was stored: the one
            # field the error asks somebody to clear used to be cleared for them, along with
            # the name and the colour they had just chosen.
            schema = self.add_suggested_values_to_schema(schema, user_input)
        return self.async_show_form(
            step_id="reconfigure" if existing else "user",
            data_schema=schema,
            errors=errors,
        )
