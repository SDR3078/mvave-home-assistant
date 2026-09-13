"""What each colour means, which is the same on every page.

All that is left in the options once pages became things somebody adds — and it is shaped
like the palette rather than like the entity registry. The device has five colours; that is
the scarce resource the whole design is built on, so the form asks what each one means
instead of asking after each kind of thing one at a time.

The screen this replaced offered six kinds of thing out of twenty and hid the rest, which
meant painting "Lights" green split them silently from switches, fans and sirens and merged
them just as silently with covers and every readout. One field, two invisible changes.
"""

from __future__ import annotations

from typing import Any

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.mvave.config_flow import CHOOSABLE, PAINTABLE
from custom_components.mvave.const import CONF_ADDRESS, CONF_DOMAIN_COLOURS, DOMAIN
from custom_components.mvave.engine.palette import DOMAIN_COLOURS, GREEN, ORANGE, WHITE

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


def boxes(result: dict[str, Any]) -> dict[str, list[str]]:
    """What is in each colour's box, as submitting the form unchanged would send it."""
    return {
        str(key): (getattr(key, "description", None) or {}).get("suggested_value") or []
        for key in result["data_schema"].schema
    }


async def open_it(hass: HomeAssistant, entry: MockConfigEntry) -> dict[str, Any]:
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "init"
    return result


async def test_there_is_one_box_per_colour_and_no_box_for_white(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    # The form is shaped like the palette because the palette is the scarce thing. White is
    # absent for the reason the whole language rests on: white is what "off" means, and a
    # lamp somebody had painted white would be unreadable exactly when it mattered.
    result = await open_it(hass, entry)
    assert list(boxes(result)) == list(CHOOSABLE)
    assert WHITE not in CHOOSABLE.values()


async def test_every_kind_of_thing_a_pad_can_hold_is_somewhere(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    # Twenty of them, against the six the old screen offered. Nothing may be missing: a kind
    # of thing with no colour would be a pad with nothing to show.
    result = await open_it(hass, entry)
    everywhere = [domain for box in boxes(result).values() for domain in box]
    assert sorted(everywhere) == sorted(PAINTABLE)
    assert len(everywhere) == len(set(everywhere))  # and in exactly one box each


async def test_the_boxes_start_holding_what_each_colour_already_means(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    starting = boxes(await open_it(hass, entry))
    assert "light" in starting["orange"]
    assert "media_player" in starting["blue"]
    assert "cover" in starting["green"]
    assert "binary_sensor" in starting["green"]  # readouts, which share green with openings
    assert "script" in starting["purple"]
    assert set(starting["orange"]) == {d for d, c in DOMAIN_COLOURS.items() if c == ORANGE}


async def test_moving_one_kind_of_thing_shows_what_it_was_sharing_a_colour_with(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    # The whole point of this shape. Painting lights green means taking them out of orange,
    # so the switches, fans and sirens they were grouped with are visibly left behind rather
    # than silently split from them.
    result = await open_it(hass, entry)
    answers = dict(boxes(result))
    answers["orange"] = [d for d in answers["orange"] if d != "light"]
    answers["green"] = [*answers["green"], "light"]

    saved = await hass.config_entries.options.async_configure(result["flow_id"], answers)
    assert saved["type"] is FlowResultType.CREATE_ENTRY
    painted = saved["data"][CONF_DOMAIN_COLOURS]
    assert painted["light"] == GREEN
    assert painted["switch"] == ORANGE  # left behind, and you watched it happen
    assert painted["fan"] == ORANGE


async def test_saving_it_untouched_changes_nothing(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    result = await open_it(hass, entry)
    saved = await hass.config_entries.options.async_configure(result["flow_id"], boxes(result))
    assert saved["data"][CONF_DOMAIN_COLOURS] == dict(DOMAIN_COLOURS)


async def test_something_in_two_boxes_is_refused(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    result = await open_it(hass, entry)
    answers = dict(boxes(result))
    answers["blue"] = [*answers["blue"], "light"]  # still in orange as well

    again = await hass.config_entries.options.async_configure(result["flow_id"], answers)
    assert again["type"] is FlowResultType.FORM
    assert again["errors"] == {"base": "colour_twice"}


async def test_something_in_no_box_is_refused(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    # A kind of thing with no colour is a pad that cannot say what it is.
    result = await open_it(hass, entry)
    answers = dict(boxes(result))
    answers["orange"] = [d for d in answers["orange"] if d != "light"]

    again = await hass.config_entries.options.async_configure(result["flow_id"], answers)
    assert again["type"] is FlowResultType.FORM
    assert again["errors"] == {"base": "colour_missing"}


async def test_purple_refuses_anything_that_can_be_switched_off(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    # Purple against white is the one pair measured as too close to tell apart on this
    # hardware, so purple is only safe where a pad never shows white. This is the cost of a
    # form that lets you paint anything any colour, and it is worth paying: the alternative
    # is a lamp that is unreadable exactly when it matters.
    result = await open_it(hass, entry)
    answers = dict(boxes(result))
    answers["orange"] = [d for d in answers["orange"] if d != "light"]
    answers["purple"] = [*answers["purple"], "light"]

    again = await hass.config_entries.options.async_configure(result["flow_id"], answers)
    assert again["type"] is FlowResultType.FORM
    assert again["errors"] == {"base": "purple_needs_stateless"}


async def test_a_refused_form_comes_back_holding_what_was_typed(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    # Otherwise being told "something is in two boxes" throws away the arrangement somebody
    # was halfway through making, and they have to build it again to find out which.
    result = await open_it(hass, entry)
    answers = dict(boxes(result))
    answers["blue"] = [*answers["blue"], "light"]

    again = await hass.config_entries.options.async_configure(result["flow_id"], answers)
    assert boxes(again)["blue"] == answers["blue"]


async def test_a_refusal_names_what_is_wrong_rather_than_saying_something_is(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    # Reported from the form: "the validation error does not specify which category does not
    # have a colour assigned yet". Five boxes and twenty chips is too many to search for a
    # thing the form already knows the name of — and it knows the *label*, not the domain,
    # because it reads them back out of the same translations the chips are drawn from.
    result = await open_it(hass, entry)
    answers = dict(boxes(result))
    answers["orange"] = [d for d in answers["orange"] if d not in ("light", "fan")]

    again = await hass.config_entries.options.async_configure(result["flow_id"], answers)
    assert again["errors"] == {"base": "colour_missing"}
    named = again["description_placeholders"]["kinds"]
    assert named == "Fans, Lights"  # the names on the chips, in a fixed order
    assert "light" not in named  # never the raw domain


async def test_a_refusal_names_the_thing_in_two_boxes_too(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    result = await open_it(hass, entry)
    answers = dict(boxes(result))
    answers["blue"] = [*answers["blue"], "cover"]

    again = await hass.config_entries.options.async_configure(result["flow_id"], answers)
    assert again["errors"] == {"base": "colour_twice"}
    assert again["description_placeholders"]["kinds"] == "Blinds, curtains and garage doors"


def test_no_two_chips_say_the_same_thing() -> None:
    # Twenty chips across five boxes, and the only way to move one is to recognise it. Home
    # Assistant's own Helpers screen calls both `button` and `input_button` "Button", so
    # taking its words unchanged would have put two identical chips in the purple box.
    import json
    from pathlib import Path

    labels = json.loads(
        (Path(__file__).parents[2] / "custom_components/mvave/strings.json").read_text()
    )["selector"]["paintable"]["options"]
    assert sorted(labels) == sorted(PAINTABLE)  # one chip per kind of thing, and no others
    assert len(set(labels.values())) == len(labels)
