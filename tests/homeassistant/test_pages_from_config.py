"""Turning what somebody configured into pages.

A page used to be a room with a page attached. It is a thing somebody makes now, which may
draw from a room, or from a label, or from nothing at all — and the last of those is the
whole reason the two are not the same idea. These cover the translation between what the
configuration says and what the engine is handed.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from custom_components.mvave.config_flow import _NUMBERED
from custom_components.mvave.engine.model import Activate, Focus, SourceKind, Toggle
from custom_components.mvave.event import MvavePadEvent
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
    # Positions in reading order, not the numbers printed on the pads: "1" here is the top
    # left, while the pad with 1 written on it is the bottom left. The stored key is
    # deliberately the one a person never sees, so the labels can be corrected without
    # migrating anything — which is exactly what happened on 2026-09-13.
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
    #
    # And it says it in the numbers printed on the hardware, which run *up* the grid: the
    # top row is 13 to 16 and the bottom row is 1 to 4. Drawn the other way up on
    # 2026-09-13 for half a day, which put a tidy 1-2-3-4 along the top of a screen whose
    # top-left pad has 13 written on it.
    rows = _NUMBERED.splitlines()
    assert len(rows) == 4
    assert rows[0].split() == ["13", "14", "15", "16"]
    assert rows[3].split() == ["1", "2", "3", "4"]
    # Aligned either side of ten, which is the only reason to draw it rather than list it.
    assert all(len(row) == len(rows[0]) for row in rows)


# --------------------------------------------------- what "Pad N" means, everywhere


def test_every_number_shown_to_a_person_is_the_one_printed_on_the_pad() -> None:
    # Three screens show a pad number — the event entity, the pad fields on the page
    # screen, and `mvave.press_slot` — and the only numbering all three can be checked
    # against is the one written on the hardware. PAD1 is the bottom-left pad.
    #
    # On 2026-09-13 all three were briefly moved onto reading order instead, on the belief
    # that the device's numbering was a protocol detail nobody could see. It is printed on
    # the pads. That made "Pad 1" the top-left pad in Home Assistant and the bottom-left
    # pad under your hand, which is the same opposite-corner confusion the move was meant
    # to end, relocated. This test is what fails if anybody tries it again.
    from custom_components.mvave.config_flow import _pad_field
    from custom_components.mvave.devices.smc_pad import (
        PAD_NUMBER_BY_READING_ORDER,
        SMC_PAD_FACTORY_LAYOUT,
    )

    # Ground truth, from docs/HARDWARE-BLE.md section 4: note 36 is PAD1 and note 51 is
    # PAD16, so the notes ascend with the printed numbers and both run up the grid.
    by_number = {spec.number: spec for spec in SMC_PAD_FACTORY_LAYOUT.pads}
    assert sorted(by_number) == list(range(1, 17))
    assert by_number[1].note == 36
    assert by_number[16].note == 51

    top_left, bottom_left = PAD_NUMBER_BY_READING_ORDER[0], PAD_NUMBER_BY_READING_ORDER[12]
    assert (top_left, bottom_left) == (13, 1)

    # The entity name, built for real rather than re-derived: the top-left pad is "Pad 13"
    # and the bottom-left one is "Pad 1".
    stub = SimpleNamespace(
        address="AA:BB:CC:DD:EE:FF", device_name="SMC-PAD", manufacturer=None, model=None
    )
    named = {
        spec.number: MvavePadEvent(stub, spec, SMC_PAD_FACTORY_LAYOUT).translation_placeholders[  # type: ignore[arg-type]
            "number"
        ]
        for spec in SMC_PAD_FACTORY_LAYOUT.pads
    }
    assert named == {number: str(number) for number in range(1, 17)}

    # The page screen's field for those same two pads.
    assert _pad_field(top_left) == "pad_13"
    assert _pad_field(bottom_left) == "pad_1"

    # `mvave.press_slot` is covered by test_services.py, which drives the real service and
    # checks slot 13 reaches the first room while slot 1 reaches nothing.
