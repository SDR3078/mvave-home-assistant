"""Turning what somebody configured into pages.

A page used to be a room with a page attached. It is a thing somebody makes now, which may
draw from a room, or from a label, or from nothing at all — and the last of those is the
whole reason the two are not the same idea. These cover the translation between what the
configuration says and what the engine is handed.
"""

from __future__ import annotations

from typing import Any

from custom_components.mvave.config_flow import _NUMBERED
from custom_components.mvave.engine.model import Activate, Focus, SourceKind, Toggle
from custom_components.mvave.registry import _pads_of, _source_of


def describe(**data: Any) -> dict[str, Any]:
    return data


# ---------------------------------------------------------------- where it fills from


def test_a_page_with_a_room_fills_itself_from_that_room() -> None:
    source = _source_of(describe(area="kitchen", label=None))
    assert source.kind is SourceKind.AREA
    assert source.key == "kitchen"


def test_a_page_with_a_label_spans_rooms() -> None:
    source = _source_of(describe(area=None, label="morning"))
    assert source.kind is SourceKind.LABEL
    assert source.key == "morning"


def test_a_page_with_neither_is_only_what_was_pinned_to_it() -> None:
    # Not a failure to configure. This is the page that could never have existed while a
    # page was a room, and it is the one the whole subentry change was for.
    assert _source_of(describe(area=None, label=None)).kind is SourceKind.EXPLICIT
    assert _source_of({}).kind is SourceKind.EXPLICIT


def test_a_room_wins_over_a_label_if_both_somehow_survive_the_form() -> None:
    # The form refuses both, because merging them would need an order and nothing says
    # which. If an older configuration carries both anyway, one of them has to win rather
    # than the page coming out empty.
    assert _source_of(describe(area="kitchen", label="morning")).key == "kitchen"


# --------------------------------------------------------------------- pinned pads


def test_a_pinned_pad_is_counted_the_way_a_person_counts_pads() -> None:
    # One based on the way in, because "pad 1" is the top left everywhere a person looks;
    # zero based from here on, because that is how a frame is indexed.
    pads = _pads_of(describe(pads={"1": "light.lamp", "16": "light.other"}))
    assert set(pads) == {0, 15}


def test_a_pinned_pad_does_what_that_kind_of_thing_does() -> None:
    # Pinning chooses the place and nothing else. A pinned lamp and an auto-filled lamp
    # behave identically, which is the point: there is nothing new to learn, and the two
    # cannot drift apart because they come from the same function.
    pads = _pads_of(describe(pads={"1": "light.lamp", "2": "scene.evening"}))
    assert pads[0].tap == Toggle("light.lamp")
    assert pads[0].hold == Focus("light.lamp")
    assert pads[1].tap == Activate("scene.evening")


def test_an_empty_picker_pins_nothing() -> None:
    # The form offers sixteen and most of them stay blank; a blank one must leave the pad
    # to the page's source rather than claiming it and showing nothing.
    assert _pads_of(describe(pads={"1": "", "2": None, "3": "light.lamp"})) == _pads_of(
        describe(pads={"3": "light.lamp"})
    )


def test_a_pad_number_off_the_end_of_the_grid_is_ignored() -> None:
    # There are sixteen. Anything else came from a hand-edited configuration, and dropping
    # it is better than a page that will not build at all.
    assert _pads_of(describe(pads={"0": "light.lamp", "17": "light.lamp"})) == {}


def test_a_page_with_nothing_pinned_says_so_plainly() -> None:
    assert _pads_of({}) == {}
    assert _pads_of(describe(pads=None)) == {}


# ------------------------------------------------------- what the form shows


def test_the_form_draws_the_pads_where_they_actually_sit() -> None:
    # A form is a column and a page is a square. Four of the sixteen labels used to be
    # annotated with a corner and the other twelve were not, which left somebody
    # interpolating; the square says it once instead.
    rows = _NUMBERED.splitlines()
    assert len(rows) == 4
    assert rows[0].split() == ["1", "2", "3", "4"]
    assert rows[3].split() == ["13", "14", "15", "16"]
    # Aligned either side of ten, which is the only reason to draw it rather than list it.
    assert all(len(row) == len(rows[0]) for row in rows)


# --------------------------------------------------- what "Pad N" means, everywhere


def test_pad_entities_are_named_by_where_the_pad_is_not_by_the_devices_own_number() -> None:
    # These disagreed on all sixteen pads until 2026-09-13. The device counts its preset
    # records from the bottom left; a person reads from the top left; and the config screen,
    # `mvave.press_slot` and the logbook all used reading order while the event entity used
    # the device's. "Pad 1" was the top-left pad on one screen and the bottom-left on the
    # other — opposite corners, with nothing saying so.
    from custom_components.mvave.devices.smc_pad import (
        PAD_NUMBER_BY_READING_ORDER,
        SMC_PAD_FACTORY_LAYOUT,
    )

    named = {
        PAD_NUMBER_BY_READING_ORDER.index(spec.number) + 1: spec
        for spec in SMC_PAD_FACTORY_LAYOUT.pads
    }
    assert sorted(named) == list(range(1, 17))
    # Reading order 1 is the top left, which the device calls 13 and puts on note 48.
    assert named[1].number == 13
    assert named[1].note == 48
    # And reading order 13 is the bottom left, the device's own pad 1, on note 36.
    assert named[13].number == 1
    assert named[13].note == 36
