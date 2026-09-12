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
    assert len(sequence) == PAD_COUNT * 2 + frames.CURTAIN_HOLD
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
    opening = expand(TOP_LEFT, CURTAIN, LEAVING, ARRIVING)[PAD_COUNT + frames.CURTAIN_HOLD]
    assert opening[0] == ARRIVING[0]
    assert lit_count(opening, CURTAIN) == PAD_COUNT - 1


def test_entering_takes_the_same_time_wherever_it_starts() -> None:
    # It used to be a step shorter from the middle than from a corner, because a corner is
    # further from the far edge. One pad per step removes that entirely.
    lengths = {len(expand(origin, CURTAIN, LEAVING, ARRIVING)) for origin in range(PAD_COUNT)}
    assert lengths == {PAD_COUNT * 2 + frames.CURTAIN_HOLD}


# -------------------------------------------------------------------- leaving


def test_leaving_mirrors_entering() -> None:
    sequence = collapse(MIDDLE, CURTAIN, ARRIVING, LEAVING)
    # The same two halves, plus a beat at full closure: see CURTAIN_HOLD.
    assert len(sequence) == PAD_COUNT * 2 + frames.CURTAIN_HOLD
    assert sequence[PAD_COUNT - 1] == (CURTAIN,) * PAD_COUNT
    assert sequence[-1] == LEAVING


def test_the_curtain_rests_a_moment_before_it_opens_again() -> None:
    sequence = collapse(MIDDLE, CURTAIN, ARRIVING, LEAVING)
    closed = (CURTAIN,) * PAD_COUNT
    assert sum(1 for frame in sequence if frame == closed) == frames.CURTAIN_HOLD + 1


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
    assert len(sequence) == PAD_COUNT * 2 + frames.CURTAIN_HOLD
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


# ------------------------------------------------------------------- refusing


def test_one_pad_refusing_blinks_three_times() -> None:
    frame = tuple([ON] * PAD_COUNT)
    blinks = frames.refuse(frame, 5)
    dark_runs = _runs(blinks, lambda f: f[5] == OFF)
    assert dark_runs == frames.REFUSAL_BLINKS
    # Only that pad moves; the page behind it stays exactly where it was.
    assert all(f[0] == ON for f in blinks)
    assert blinks[-1] == frame


def test_a_pad_blinks_three_times_because_it_is_one_pad() -> None:
    # Fifteen pads keep reporting throughout, so this costs nothing but the pad under the
    # finger. Nothing else on this surface may borrow the count: a whole-grid version would
    # be three flashes in one second across the entire device, which is the WCAG 2.3.1
    # limit and the band that provokes photosensitive seizures, and it is why a knob that
    # can do nothing answers with a map instead of a blink.
    frame = tuple([ON] * PAD_COUNT)
    assert _runs(frames.refuse(frame, 0), lambda f: f[0] == OFF) == 3


def test_nothing_blinks_the_whole_grid() -> None:
    # The only full-surface animations left are the page transitions, which sweep one pad
    # at a time and never take the grid out all at once.
    assert not hasattr(frames, "refuse_grid")


# ------------------------------------------------------------------- the legend


#: Where each knob lands, in knob order. The encoders are two across and four up, numbered
#: from the bottom left, so knob one is the bottom left pad and knob eight the top right of
#: the pair of columns.
KNOB_PADS = (12, 13, 8, 9, 4, 5, 0, 1)


def test_the_legend_stands_the_knobs_up_the_way_the_encoders_do() -> None:
    legend = frames.knob_legend([BLUE] * 8)
    assert [legend[pad] for pad in KNOB_PADS] == [BLUE] * 8
    # Nothing anywhere else.
    assert {legend[pad] for pad in range(PAD_COUNT) if pad not in KNOB_PADS} == {OFF}


def test_the_legend_counts_up_from_the_bottom_like_the_device_does() -> None:
    # The encoders are numbered from the bottom left, the same convention the pads use
    # where PAD1 is bottom left, and every other index in the engine counts down from the
    # top. Drawing this the engine's way instead of the device's put knob one at the top,
    # which made the map a puzzle rather than an answer.
    only_the_first = frames.knob_legend([BLUE] + [None] * 7)
    assert frames.row_of(only_the_first.index(BLUE)) == frames.ROWS - 1
    assert frames.column_of(only_the_first.index(BLUE)) == 0

    only_the_last = frames.knob_legend([None] * 7 + [BLUE])
    assert frames.row_of(only_the_last.index(BLUE)) == 0
    assert frames.column_of(only_the_last.index(BLUE)) == 1


def test_the_legend_says_white_for_a_knob_that_does_nothing() -> None:
    # The grid's own rule, applied to a knob instead of an entity: colour means it is
    # there, white means it is not.
    legend = frames.knob_legend([BLUE, None, BLUE, None, None, None, None, BLUE])
    assert [legend[pad] for pad in KNOB_PADS] == [
        BLUE,
        STATE_OFF,
        BLUE,
        STATE_OFF,
        STATE_OFF,
        STATE_OFF,
        STATE_OFF,
        BLUE,
    ]


def test_the_legend_is_a_shape_no_page_can_make() -> None:
    # A page fills left to right and top to bottom, so it can never produce two standing
    # columns. That is what stops the legend being mistaken for one.
    legend = frames.knob_legend([BLUE] * 8)
    lit = [pad for pad, colour in enumerate(legend) if colour != OFF]
    assert lit != list(range(len(lit)))
    assert {frames.column_of(pad) for pad in lit} == {0, 1}
    assert {frames.row_of(pad) for pad in lit} == {0, 1, 2, 3}


def test_a_pad_refusing_leaves_every_other_pad_reporting() -> None:
    # Which is what makes it affordable, and what a whole-surface version could never be.
    frame = tuple([ON] * PAD_COUNT)
    assert frames.refuse(frame, 0)[0][1:] == frame[1:]


def _runs(sequence: tuple[Frame, ...], predicate: object) -> int:
    """How many separate stretches of the sequence satisfy the predicate."""
    count, inside = 0, False
    for frame in sequence:
        matches = predicate(frame)  # type: ignore[operator]
        if matches and not inside:
            count += 1
        inside = matches
    return count


def test_no_pad_shows_the_curtain_for_only_a_blink() -> None:
    # The two halves of the way out collide on one pad: the last the curtain covers is the
    # first it uncovers. That pad held the curtain for a single frame — 45 ms, against 23
    # for its neighbours — and it read on the hardware as a pad that sometimes just fails
    # to light. Both directions now floor at the same four frames.
    leaving = tuple([ON] * PAD_COUNT)
    arriving = (BLUE,) + (OFF,) * (PAD_COUNT - 1)

    def shortest(frames: tuple[Frame, ...]) -> int:
        return min(sum(1 for f in frames if f[pad] == CURTAIN) for pad in range(PAD_COUNT))

    going_out = frames.collapse(0, CURTAIN, leaving, arriving)
    going_in = frames.expand(0, CURTAIN, arriving, leaving)
    assert shortest(going_out) > frames.CURTAIN_HOLD
    assert shortest(going_in) > frames.CURTAIN_HOLD
    # And every transition that closes a curtain rests the same length of time in it, so
    # one never reads as hurried against another.
    assert len(going_out) == len(going_in) == len(frames.wipe(CURTAIN, leaving, arriving))


def test_the_way_out_still_ends_on_the_page_it_was_going_to() -> None:
    # The hold sits between the two halves, so it must not disturb either end.
    leaving = tuple([ON] * PAD_COUNT)
    arriving = (BLUE,) + (OFF,) * (PAD_COUNT - 1)
    going_out = frames.collapse(0, CURTAIN, leaving, arriving)
    assert going_out[-1] == arriving
    assert going_out[0] != leaving  # the first pad is already covered
