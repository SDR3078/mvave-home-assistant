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
from custom_components.mvave.const import (
    CONF_ADDRESS,
    CONF_DEFAULT_PAGE,
    CONF_DOMAIN_COLOURS,
    DOMAIN,
)
from custom_components.mvave.engine.palette import DOMAIN_COLOURS, GREEN, ORANGE, WHITE
from custom_components.mvave.registry import ROOT_ID, build_profile
from tests.homeassistant.test_config_flow import add_page

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
        if str(key) in CHOOSABLE  # the boxes; the default-page dropdown sits above them
    }


def resting_field(result: dict[str, Any]) -> Any:
    """The default-page dropdown's schema key, so its default and options can be read."""
    return next(key for key in result["data_schema"].schema if str(key) == CONF_DEFAULT_PAGE)


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
    # And which boxes: the message says "take it out of the one you did not mean", which
    # presumes you can see both. Five boxes and twenty chips is too many to search.
    assert again["description_placeholders"]["kinds"] == (
        "Blinds, curtains and garage doors (Blue and Green)"
    )


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


async def test_two_mistakes_at_once_are_both_reported(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    # Rearranging two boxes can easily leave one kind of thing in two and another in none at
    # the same moment. Reporting only the first sent somebody back round for a problem the
    # form already knew about.
    result = await open_it(hass, entry)
    answers = dict(boxes(result))
    answers["blue"] = [*answers["blue"], "cover"]  # cover is now in green and blue
    answers["orange"] = [d for d in answers["orange"] if d != "light"]  # and light is nowhere

    again = await hass.config_entries.options.async_configure(result["flow_id"], answers)
    assert again["errors"] == {"base": "colour_several"}
    listed = again["description_placeholders"]["kinds"]
    assert "Blinds, curtains and garage doors" in listed
    assert "Lights" in listed
    assert listed.count("\n") == 1  # one line per problem, both of them


# ------------------------------------------------------------ where the pad rests


async def test_the_pad_can_be_told_where_to_rest(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    # A page somebody made, offered by name and stored by its id, so renaming the room
    # later does not lose it. Only connect and the timeout follow it: the stop button is
    # the index regardless, which is the owner's split from the grid on 2026-09-15.
    await add_page(hass, entry, pads={})
    kitchen = next(iter(entry.subentries))

    result = await open_it(hass, entry)
    field = resting_field(result)
    offered = result["data_schema"].schema[field].config["options"]
    assert [option["label"] for option in offered] == ["Home", "Kitchen"]
    assert field.default() == ROOT_ID  # nothing chosen yet

    saved = await hass.config_entries.options.async_configure(
        result["flow_id"], {**boxes(result), CONF_DEFAULT_PAGE: kitchen}
    )
    assert saved["type"] is FlowResultType.CREATE_ENTRY
    assert saved["data"][CONF_DEFAULT_PAGE] == kitchen

    profile = build_profile(hass, entry)
    assert profile.default_page_id == kitchen
    assert profile.at_rest == [ROOT_ID, kitchen]
    # The index is somewhere you visit now rather than where you end up, so it goes back
    # to rest like any other page instead of never timing out.
    assert profile.root is not None and profile.root.idle_timeout > 0

    # And the screen shows the choice next time.
    assert resting_field(await open_it(hass, entry)).default() == kitchen


async def test_two_pages_with_the_same_name_are_told_apart(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    # One Kitchen page from the room and another from a label: a dropdown reading
    # Home / Kitchen / Kitchen stores the right id but cannot show you which you chose.
    await add_page(hass, entry, pads={})
    await add_page(hass, entry, pads={})
    first, second = entry.subentries
    result = await open_it(hass, entry)
    offered = result["data_schema"].schema[resting_field(result)].config["options"]
    assert [option["label"] for option in offered] == [
        "Home",
        f"Kitchen ({first})",
        f"Kitchen ({second})",
    ]


async def test_choosing_home_stores_nothing_which_is_what_it_always_meant(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    result = await open_it(hass, entry)
    saved = await hass.config_entries.options.async_configure(
        result["flow_id"], {**boxes(result), CONF_DEFAULT_PAGE: ROOT_ID}
    )
    assert CONF_DEFAULT_PAGE not in saved["data"]
    profile = build_profile(hass, entry)
    assert profile.at_rest == [ROOT_ID]
    assert profile.root is not None and profile.root.idle_timeout == 0


async def test_a_deleted_default_page_means_the_index_again(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    # Stored by id, and the page is gone. Not an error: the pad rests on the index, and the
    # dropdown says Home rather than offering a page that is not there.
    hass.config_entries.async_update_entry(entry, options={CONF_DEFAULT_PAGE: "01GONE"})
    profile = build_profile(hass, entry)
    assert profile.default_page_id is None
    assert profile.at_rest == [ROOT_ID]
    assert resting_field(await open_it(hass, entry)).default() == ROOT_ID
