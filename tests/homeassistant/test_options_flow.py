"""What each kind of thing looks like, which is the same on every page.

All that is left in the options once pages became things somebody adds. It is one screen,
and the rule it has to keep is the one the whole language rests on: white is what "off"
means, so white is never a colour anybody may choose for something that is on.
"""

from __future__ import annotations

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.mvave.config_flow import CHOOSABLE, COLOURABLE
from custom_components.mvave.const import CONF_ADDRESS, CONF_DOMAIN_COLOURS, DOMAIN
from custom_components.mvave.engine.palette import (
    BLUE,
    DOMAIN_COLOURS,
    GREEN,
    ORANGE,
    PURPLE,
    RED,
    WHITE,
)

ADDRESS = "AA:BB:CC:DD:EE:FF"


@pytest.fixture
def entry(hass: HomeAssistant) -> MockConfigEntry:
    made = MockConfigEntry(
        domain=DOMAIN,
        unique_id=ADDRESS.lower(),
        title="SMC-PAD",
        data={CONF_ADDRESS: ADDRESS},
        minor_version=2,
    )
    made.add_to_hass(hass)
    return made


def filled(result: dict[str, object]) -> dict[str, object]:
    """What each field arrives already holding."""
    schema = result["data_schema"].schema  # type: ignore[attr-defined]
    return {
        str(key): (getattr(key, "description", None) or {}).get("suggested_value") for key in schema
    }


async def test_every_kind_of_thing_that_can_be_recoloured_gets_a_field(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "init"
    assert list(filled(result)) == list(COLOURABLE)


async def test_a_field_starts_at_the_colour_that_kind_of_thing_already_is(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    # By name, because that is what somebody picks from; the palette value is what gets
    # stored. Anything not chosen starts at the default for its domain.
    result = await hass.config_entries.options.async_init(entry.entry_id)
    starting = filled(result)
    assert starting["light"] == "orange"
    assert starting["media_player"] == "blue"
    assert starting["cover"] == "green"
    assert starting["climate"] == "red"
    assert starting["scene"] == "purple"
    assert {CHOOSABLE[str(value)] for value in starting.values()} == {
        DOMAIN_COLOURS[domain] for domain in COLOURABLE
    }


async def test_white_is_never_a_colour_anybody_can_choose(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    # The one rule the readability of every page rests on. White means the thing behind a
    # pad is off; a lamp somebody had coloured white would be unreadable exactly when it
    # mattered, and "is anything still on in here" would stop being one glance.
    assert WHITE not in CHOOSABLE.values()
    assert set(CHOOSABLE.values()) == {BLUE, GREEN, ORANGE, RED, PURPLE}

    result = await hass.config_entries.options.async_init(entry.entry_id)
    schema = result["data_schema"].schema  # type: ignore[attr-defined]
    for selector in schema.values():
        assert "white" not in selector.config["options"]


async def test_choosing_a_colour_stores_what_the_grid_speaks(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    # A person picks a name and the device takes a palette value. Storing the name would
    # put the translation somewhere that has to do it again on every render.
    result = await hass.config_entries.options.async_init(entry.entry_id)
    answers = {domain: "purple" for domain in COLOURABLE}
    saved = await hass.config_entries.options.async_configure(result["flow_id"], answers)

    assert saved["type"] is FlowResultType.CREATE_ENTRY
    assert saved["data"][CONF_DOMAIN_COLOURS] == dict.fromkeys(COLOURABLE, PURPLE)


async def test_a_colour_somebody_chose_comes_back_when_they_look_again(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    first = await hass.config_entries.options.async_init(entry.entry_id)
    await hass.config_entries.options.async_configure(
        first["flow_id"], {**dict.fromkeys(COLOURABLE, "orange"), "light": "green"}
    )
    await hass.async_block_till_done()

    again = await hass.config_entries.options.async_init(entry.entry_id)
    starting = filled(again)
    assert starting["light"] == "green"
    assert starting["cover"] == "orange"
    assert entry.options[CONF_DOMAIN_COLOURS]["light"] == GREEN
