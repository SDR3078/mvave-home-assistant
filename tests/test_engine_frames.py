"""The grid frames and the page transitions.

Every animation here was written by hand against the real device and then corrected four
or five times by looking at it: the square that did not finish, the two steps that lasted
twice as long as the others, the page that blanked before the curtain had covered it, the
buttons that changed on the wrong frame. None of those were caught by reading the code.
These tests exist so that they stay caught.
"""

from __future__ import annotations

from itertools import pairwise

import pytest
from engine import frames
from engine.frames import (
    PAD_COUNT,
    Frame,
    clockwise_order,
    collapse,
    column_order,
    expand,
    sweep,
    value_bar,
    wipe,
)
from engine.palette import BLUE, OFF, ON, STATE_OFF, WHITE, is_emittable

CURTAIN = BLUE
LEAVING: Frame = tuple(range(1, PAD_COUNT + 1))
ARRIVING: Frame = tuple([ON, STATE_OFF] * 8)

TOP_LEFT = 0
MIDDLE = 5
BOTTOM_RIGHT = 15


def lit_count(frame: Frame, colour: int) -> int:
    """How many pads are showing one colour."""
    return sum(1 for value in frame if value == colour)


def biggest_change(start: Frame, sequence: tuple[Frame, ...]) -> int:
    """The most pads any single frame of an animation changes at once.

    Never more than one, rather than exactly one: entering a page from its own pad on the
    index means that pad is already the curtain's colour, so the first frame changes
    nothing. That is what makes the curtain look like it grew out of the finger.
    """
    return max(
        sum(1 for old, new in zip(previous, current, strict=True) if old != new)
        for previous, current in pairwise((start, *sequence))
    )


# ---------------------------------------------------------------- the grid itself


def test_reading_order_runs_left_to_right_then_down() -> None:
    assert frames.position(0, 0) == 0
    assert frames.position(0, 3) == 3
    assert frames.position(3, 0) == 12
    assert (frames.row_of(6), frames.column_of(6)) == (1, 2)


# ------------------------------------------------------------------- the orders


@pytest.mark.parametrize("origin", range(PAD_COUNT))
def test_the_spiral_visits_every_pad_once_and_starts_where_it_was_pressed(origin: int) -> None:
    order = clockwise_order(origin)
    assert order[0] == origin
    assert sorted(order) == list(range(PAD_COUNT))


def test_the_spiral_winds_clockwise_from_directly_above() -> None:
    # From the middle of the grid the first ring is unambiguous: up, then round to the
    # right. A ring entered at a corner instead reads as a box being drawn.
    assert clockwise_order(frames.position(1, 1))[:9] == (5, 1, 2, 6, 10, 9, 8, 4, 0)


def test_columns_fill_from_the_top_and_can_run_either_way() -> None:
    assert column_order()[:5] == (0, 4, 8, 12, 1)
    assert column_order(rightwards=False)[:5] == (3, 7, 11, 15, 2)
    assert sorted(column_order()) == list(range(PAD_COUNT))


def test_a_sweep_changes_one_pad_per_frame() -> None:
    before, after = (OFF,) * PAD_COUNT, tuple(range(1, PAD_COUNT + 1))
    sequence = sweep(column_order(), before, after)
    assert len(sequence) == PAD_COUNT
    assert biggest_change(before, sequence) == 1
    assert sequence[-1] == after


# ------------------------------------------------------------------- entering


@pytest.mark.parametrize("origin", [TOP_LEFT, MIDDLE, BOTTOM_RIGHT])
def test_nothing_ever_lights_more_than_one_pad_at_a_time(origin: int) -> None:
    # The property the whole redesign exists for. A ring is one pad wide at a corner and
    # seven at the far edge, so however evenly it is timed the amount of light arriving
    # changes every step, and it reads as a limp.
    assert biggest_change(LEAVING, expand(origin, CURTAIN, LEAVING, ARRIVING)) == 1


def test_entering_covers_the_grid_and_then_uncovers_it() -> None:
    sequence = expand(TOP_LEFT, CURTAIN, LEAVING, ARRIVING)
    assert len(sequence) == PAD_COUNT * 2
    assert sequence[PAD_COUNT - 1] == (CURTAIN,) * PAD_COUNT
    assert sequence[-1] == ARRIVING


def test_entering_starts_on_the_pad_that_was_pressed() -> None:
    first = expand(MIDDLE, CURTAIN, LEAVING, ARRIVING)[0]
    assert first[MIDDLE] == CURTAIN
    assert lit_count(first, CURTAIN) == 1


def test_the_page_being_left_stays_lit_ahead_of_the_curtain() -> None:
    # The first thing this got wrong: everything went dark and *then* the curtain grew, so
    # for the whole first half the grid said nothing at all.
    first = expand(TOP_LEFT, CURTAIN, LEAVING, ARRIVING)[0]
    assert first[1:] == LEAVING[1:]


def test_the_page_being_entered_is_already_itself_behind_the_curtain() -> None:
    # Not a flat block of colour that swaps to the page at the very end.
    opening = expand(TOP_LEFT, CURTAIN, LEAVING, ARRIVING)[PAD_COUNT]
    assert opening[0] == ARRIVING[0]
    assert lit_count(opening, CURTAIN) == PAD_COUNT - 1


def test_entering_takes_the_same_time_wherever_it_starts() -> None:
    # It used to be a step shorter from the middle than from a corner, because a corner is
    # further from the far edge. One pad per step removes that entirely.
    lengths = {len(expand(origin, CURTAIN, LEAVING, ARRIVING)) for origin in range(PAD_COUNT)}
    assert lengths == {PAD_COUNT * 2}


# -------------------------------------------------------------------- leaving


def test_leaving_mirrors_entering() -> None:
    sequence = collapse(MIDDLE, CURTAIN, ARRIVING, LEAVING)
    assert len(sequence) == PAD_COUNT * 2
    assert sequence[PAD_COUNT - 1] == (CURTAIN,) * PAD_COUNT
    assert sequence[-1] == LEAVING


def test_leaving_closes_against_the_reading_direction() -> None:
    first = collapse(MIDDLE, CURTAIN, ARRIVING, LEAVING)[0]
    assert first[frames.position(0, 3)] == CURTAIN
    assert lit_count(first, CURTAIN) == 1


def test_leaving_winds_back_into_the_pad_that_was_pressed() -> None:
    # The last thing still covered is the pad the page you are returning to occupies.
    penultimate = collapse(MIDDLE, CURTAIN, ARRIVING, LEAVING)[-2]
    assert lit_count(penultimate, CURTAIN) == 1
    assert penultimate[MIDDLE] == CURTAIN


@pytest.mark.parametrize("target", [TOP_LEFT, MIDDLE, BOTTOM_RIGHT])
def test_leaving_also_moves_one_pad_at_a_time(target: int) -> None:
    assert biggest_change(ARRIVING, collapse(target, CURTAIN, ARRIVING, LEAVING)) == 1


# ------------------------------------------------------- navigation with no origin


def test_a_page_change_nobody_asked_for_has_no_spiral_at_all() -> None:
    # Navigation from a service call or a presence sensor. Inventing an origin pad would
    # imply a finger that was not there.
    sequence = wipe(CURTAIN, LEAVING, ARRIVING)
    assert len(sequence) == PAD_COUNT * 2
    assert sequence[0][0] == CURTAIN
    assert lit_count(sequence[0], CURTAIN) == 1
    assert sequence[PAD_COUNT - 1] == (CURTAIN,) * PAD_COUNT
    assert sequence[-1] == ARRIVING


# ------------------------------------------------------------------ the value bar


def test_the_bar_fills_upward_from_the_bottom_left() -> None:
    # Level rises, so the bar rises. Cover position is the one exception and inverts this.
    bar = value_bar(0.25, ON)
    assert [bar[index] for index in (12, 13, 14, 15)] == [ON] * 4
    assert lit_count(bar, ON) == 4


def test_the_bar_has_sixteen_steps_and_no_more() -> None:
    # Both attempts at sub-step resolution were built and rejected on the hardware: a
    # blinking pad above the run read as a fault, and a second colour capping the run read
    # as a pad that did not belong to the bar.
    counts = {value_bar(step / 32, ON).count(ON) for step in range(33)}
    assert counts == set(range(17))


def test_the_bar_is_empty_at_zero_and_full_at_one() -> None:
    assert value_bar(0.0, ON) == (OFF,) * PAD_COUNT
    assert value_bar(1.0, ON) == (ON,) * PAD_COUNT


def test_the_bar_clamps_rather_than_running_off_the_grid() -> None:
    assert value_bar(-1.0, ON) == (OFF,) * PAD_COUNT
    assert value_bar(9.0, ON) == (ON,) * PAD_COUNT


def test_a_bar_with_a_track_shows_the_scale() -> None:
    bar = value_bar(0.25, ON, track=WHITE)
    assert lit_count(bar, ON) == 4
    assert lit_count(bar, WHITE) == 12


# ------------------------------------------------------- what may reach the device


@pytest.mark.parametrize(
    "sequence",
    [
        expand(TOP_LEFT, CURTAIN, LEAVING, ARRIVING),
        collapse(MIDDLE, CURTAIN, ARRIVING, LEAVING),
        wipe(CURTAIN, LEAVING, ARRIVING),
    ],
)
def test_no_animation_ever_emits_an_unusable_velocity(sequence: tuple[Frame, ...]) -> None:
    # 96 to 126 leave the pad showing whatever it showed before, which would silently
    # desynchronise the grid from the frame the engine thinks it drew, and 127 is a second
    # off. Neither may ever be sent as a colour.
    assert all(is_emittable(value) for frame in sequence for value in frame)


def test_emittable_rejects_the_range_the_device_ignores() -> None:
    assert is_emittable(0)
    assert is_emittable(95)
    assert not is_emittable(96)
    assert not is_emittable(126)
    assert not is_emittable(127)
