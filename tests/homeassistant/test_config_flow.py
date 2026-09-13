"""Adding and editing a page, driven through the real flow.

Everything else about pages is tested through the pure helpers underneath the flow, which
is how two defects reached the device on 2026-09-13 with the suite green: the pads step was
handed page data with no ``pads`` key, so it showed the contents a room supplies on its own
and hid every pin from the one screen that edits them. Nothing between the helpers and the
device was exercised at all.

These drive `hass.config_entries.subentries`, so the thing under test is the flow a person
actually walks through rather than a function it happens to call.
"""

from __future__ import annotations

from typing import Any

import pytest
import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.mvave.const import (
    CONF_ADDRESS,
    CONF_AREA,
    CONF_COLOUR,
    CONF_LABEL,
    CONF_PADS,
    DOMAIN,
    SUBENTRY_PAGE,
)

ADDRESS = "AA:BB:CC:DD:EE:FF"


@pytest.fixture
def entry(hass: HomeAssistant) -> MockConfigEntry:
    """An installed integration, without a radio or a pad anywhere near it."""
    made = MockConfigEntry(
        domain=DOMAIN,
        unique_id=ADDRESS.lower(),
        title="SMC-PAD",
        data={CONF_ADDRESS: ADDRESS},
        minor_version=2,
    )
    made.add_to_hass(hass)
    return made


@pytest.fixture
def bedroom(hass: HomeAssistant) -> str:
    """A room with four things in it, in the order a page would lay them out."""
    area = ar.async_get(hass).async_get_or_create("Bedroom")
    registry = er.async_get(hass)
    for domain, key in (
        ("light", "bed_light"),
        ("cover", "hall_window"),
        ("climate", "ecobee"),
        ("fan", "ceiling_fan"),
    ):
        made = registry.async_get_or_create(domain, "demo", key, suggested_object_id=key)
        registry.async_update_entity(made.entity_id, area_id=area.id)
        hass.states.async_set(made.entity_id, "on")
    return area.id


async def add_page(
    hass: HomeAssistant, entry: ConfigEntry, pads: dict[str, Any] | None = None, **page: Any
) -> dict[str, Any]:
    """Walk the whole flow: describe a page, then answer the pad screen."""
    started = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_PAGE), context={"source": "user"}
    )
    assert started["type"] is FlowResultType.FORM
    assert started["step_id"] == "user"

    second = await hass.config_entries.subentries.async_configure(
        started["flow_id"], {"name": "Kitchen", CONF_COLOUR: "blue", **page}
    )
    assert second["type"] is FlowResultType.FORM, second
    assert second["step_id"] == "pads"
    return (
        second
        if pads is None
        else await hass.config_entries.subentries.async_configure(second["flow_id"], pads)
    )


def suggested(result: dict[str, Any]) -> dict[str, Any]:
    """What a form arrives already holding, as submitting it unchanged would send it back.

    Both ways a field can be pre-filled, because this project uses both: ``default`` for
    the ones that must be answered, ``suggested_value`` for the ones that need not be.
    Empty fields are left out rather than sent as ``None``, which is what the interface
    does with an optional field nobody filled in, and what the selector will accept.
    """
    filled: dict[str, Any] = {}
    for key in result["data_schema"].schema:
        description = getattr(key, "description", None) or {}
        value = description.get("suggested_value")
        if value is None:
            default = getattr(key, "default", vol.UNDEFINED)
            value = default() if default is not vol.UNDEFINED else None
        if value is not None and value is not vol.UNDEFINED:
            filled[str(key)] = value
    return filled


async def test_a_page_can_be_added_with_nothing_on_it(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    result = await add_page(hass, entry, pads={})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Kitchen"
    assert result["data"][CONF_PADS] == {}


async def test_a_room_fills_the_pad_fields_so_they_can_be_seen_and_changed(
    hass: HomeAssistant, entry: MockConfigEntry, bedroom: str
) -> None:
    # The fields arrive holding what the page will actually show. Sixteen empty boxes look
    # the same whether a room supplies four things or nothing does.
    #
    # A room fills from the top left, and the top-left pad has 13 printed on it, so the
    # first four things in the room land on 13 to 16 and pad 1 — bottom left — stays empty.
    form = await add_page(hass, entry, area=bedroom)
    filled = suggested(form)
    assert filled["pad_13"] == "light.bed_light"
    assert filled["pad_14"] == "cover.hall_window"
    assert filled["pad_15"] == "climate.ecobee"
    assert filled["pad_16"] == "fan.ceiling_fan"
    assert filled.get("pad_9") is None  # the next pad down, and the fifth to fill
    assert filled.get("pad_1") is None  # three rows below that, and the last


async def test_saving_a_room_page_untouched_pins_nothing(
    hass: HomeAssistant, entry: MockConfigEntry, bedroom: str
) -> None:
    # The one that matters. Every non-empty field used to be stored as a pin, so filling
    # them from the room would mean that merely opening this screen and pressing submit
    # froze the page — and nobody would find out until a lamp added to that room failed to
    # appear on it.
    form = await add_page(hass, entry, area=bedroom)
    result = await hass.config_entries.subentries.async_configure(form["flow_id"], suggested(form))
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_PADS] == {}


async def test_changing_one_pad_pins_that_one_and_leaves_the_rest_following(
    hass: HomeAssistant, entry: MockConfigEntry, bedroom: str
) -> None:
    form = await add_page(hass, entry, area=bedroom)
    answers = {**suggested(form), "pad_14": "light.bed_light"}
    result = await hass.config_entries.subentries.async_configure(form["flow_id"], answers)
    # Stored by position rather than by the printed number — PAD14 is the second pad in
    # reading order — so the label on the field could change without migrating anything.
    assert result["data"][CONF_PADS] == {"2": "light.bed_light"}


async def test_a_page_cannot_fill_itself_from_two_places(
    hass: HomeAssistant, entry: MockConfigEntry, bedroom: str
) -> None:
    started = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_PAGE), context={"source": "user"}
    )
    result = await hass.config_entries.subentries.async_configure(
        started["flow_id"],
        {"name": "Kitchen", CONF_COLOUR: "blue", CONF_AREA: bedroom, CONF_LABEL: "lamps"},
    )
    # Two would need an order to merge them in, and nothing about either says which wins.
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "one_source"}


# ------------------------------------------------------------- editing one


async def reconfigure(hass: HomeAssistant, entry: ConfigEntry) -> dict[str, Any]:
    """Open the edit flow for the page this entry has, and get as far as the pads."""
    subentry_id = next(iter(entry.subentries))
    started = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_PAGE),
        context={"source": "reconfigure", "subentry_id": subentry_id},
    )
    assert started["step_id"] == "reconfigure"
    # Submitted exactly as it arrived, which is what somebody who only wants the pad screen
    # does. Anything this step drops here would look like a deliberate change.
    second = await hass.config_entries.subentries.async_configure(
        started["flow_id"], suggested(started)
    )
    assert second["step_id"] == "pads", second
    return second


async def test_editing_a_page_shows_the_pins_it_already_has(
    hass: HomeAssistant, entry: MockConfigEntry, bedroom: str
) -> None:
    # The defect of 2026-09-13, and the reason this file exists. The page carried between
    # the two steps had no pads key, so the pad screen resolved the page the room supplies
    # on its own: every pin was missing from the one screen that edits them, and the
    # comparison deciding what to keep was made against a page nobody had.
    await add_page(hass, entry, area=bedroom, pads={"pad_14": "light.bed_light"})
    assert dict(next(iter(entry.subentries.values())).data[CONF_PADS]) == {"2": "light.bed_light"}

    filled = suggested(await reconfigure(hass, entry))
    assert filled["pad_14"] == "light.bed_light"  # the pin, not what the room would put here
    # And the room's own contents fill in around it, never placed twice.
    assert filled["pad_13"] == "cover.hall_window"
    assert "light.bed_light" not in [filled[key] for key in filled if key != "pad_14"]


async def test_editing_a_page_and_changing_nothing_keeps_its_pins(
    hass: HomeAssistant, entry: MockConfigEntry, bedroom: str
) -> None:
    # A pin is a statement, so it survives being looked at even where it matches what the
    # room would have supplied anyway. Only an emptied field gives one up.
    await add_page(hass, entry, area=bedroom, pads={"pad_14": "light.bed_light"})
    form = await reconfigure(hass, entry)
    await hass.config_entries.subentries.async_configure(form["flow_id"], suggested(form))
    assert dict(next(iter(entry.subentries.values())).data[CONF_PADS]) == {"2": "light.bed_light"}
