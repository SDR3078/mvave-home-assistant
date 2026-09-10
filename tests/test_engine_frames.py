"""The grid frames and the page transitions.

Every animation here was written by hand against the real device and then corrected four
or five times by looking at it: the square that did not finish, the two steps that lasted
twice as long as the others, the page that blanked before the curtain had covered it, the
buttons that changed on the wrong frame. None of those were caught by reading the code.
These tests exist so that they stay caught.
"""

from __future__ import annotations

import pytest
from engine import frames
from engine.frames import (
    COLUMNS,
    PAD_COUNT,
    Frame,
    collapse,
    expand,
    ring_distances,
    rings_from,
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


# ---------------------------------------------------------------- the grid itself


def test_reading_order_runs_left_to_right_then_down() -> None:
    assert frames.position(0, 0) == 0
    assert frames.position(0, 3) == 3
    assert frames.position(3, 0) == 12
    assert (frames.row_of(6), frames.column_of(6)) == (1, 2)


def test_a_corner_is_three_rings_from_the_far_side_and_the_middle_is_two() -> None:
    assert max(ring_distances(TOP_LEFT)) == 3
    assert max(ring_distances(MIDDLE)) == 2
    assert rings_from(TOP_LEFT) == 4
    assert rings_from(MIDDLE) == 3


def test_every_ring_is_a_square() -> None:
    # The shape that grows out of a pressed pad has to be a square, not a diamond, or it
    # stops reading as "it started here".
    distances = ring_distances(TOP_LEFT)
    within_two = [index for index, distance in enumerate(distances) if distance <= 2]
    assert within_two == [0, 1, 2, 4, 5, 6, 8, 9, 10]


# ------------------------------------------------------------------- entering


def test_expand_closes_in_rings_then_opens_in_columns() -> None:
    sequence = expand(TOP_LEFT, CURTAIN, LEAVING, ARRIVING)
    assert len(sequence) == rings_from(TOP_LEFT) + COLUMNS

    # Closing: one pad, then two by two, then three by three, then the whole grid.
    assert [lit_count(frame, CURTAIN) for frame in sequence[:4]] == [1, 4, 9, 16]
    # Opening: four pads of the new page at a time.
    assert [lit_count(frame, CURTAIN) for frame in sequence[4:]] == [12, 8, 4, 0]


def test_expand_leaves_the_old_page_lit_ahead_of_the_curtain() -> None:
    # The first thing this got wrong: everything went dark and *then* the curtain grew, so
    # for the whole first half the grid said nothing at all.
    first = expand(TOP_LEFT, CURTAIN, LEAVING, ARRIVING)[0]
    assert first[0] == CURTAIN
    assert first[1:] == LEAVING[1:]


def test_expand_shows_the_new_page_in_its_real_colours_as_it_arrives() -> None:
    # Not a flat block of colour that swaps to the page at the very end.
    opening = expand(TOP_LEFT, CURTAIN, LEAVING, ARRIVING)[4]
    assert [opening[index] for index in (0, 4, 8, 12)] == [ARRIVING[i] for i in (0, 4, 8, 12)]
    assert opening[1] == CURTAIN


def test_expand_ends_exactly_on_the_arriving_page() -> None:
    assert expand(TOP_LEFT, CURTAIN, LEAVING, ARRIVING)[-1] == ARRIVING
    assert expand(MIDDLE, CURTAIN, LEAVING, ARRIVING)[-1] == ARRIVING


def test_expand_from_the_middle_is_one_step_shorter_than_from_a_corner() -> None:
    # A property of the shape rather than a bug. Padding the middle case to match would
    # mean frames that change nothing, which is what made an earlier version feel uneven.
    assert len(expand(MIDDLE, CURTAIN, LEAVING, ARRIVING)) == 7
    assert len(expand(TOP_LEFT, CURTAIN, LEAVING, ARRIVING)) == 8


@pytest.mark.parametrize("origin", range(PAD_COUNT))
def test_expand_covers_the_whole_grid_before_it_opens(origin: int) -> None:
    sequence = expand(origin, CURTAIN, LEAVING, ARRIVING)
    covered = sequence[rings_from(origin) - 1]
    assert covered == (CURTAIN,) * PAD_COUNT


# -------------------------------------------------------------------- leaving


def test_collapse_mirrors_expand() -> None:
    sequence = collapse(MIDDLE, CURTAIN, ARRIVING, LEAVING)
    assert len(sequence) == COLUMNS + rings_from(MIDDLE)
    # Closing right to left: the rightmost column first.
    assert [lit_count(frame, CURTAIN) for frame in sequence[:4]] == [4, 8, 12, 16]
    assert sequence[3] == (CURTAIN,) * PAD_COUNT


def test_collapse_shrinks_into_the_pad_that_was_pressed() -> None:
    # The last thing lit before the index settles is the pad that page occupies on it.
    sequence = collapse(MIDDLE, CURTAIN, ARRIVING, LEAVING)
    penultimate = sequence[-2]
    assert lit_count(penultimate, CURTAIN) == 1
    assert penultimate[MIDDLE] == CURTAIN


def test_collapse_ends_exactly_on_the_arriving_page() -> None:
    assert collapse(MIDDLE, CURTAIN, ARRIVING, LEAVING)[-1] == LEAVING


def test_collapse_closes_against_the_reading_direction() -> None:
    first = collapse(MIDDLE, CURTAIN, ARRIVING, LEAVING)[0]
    assert [first[index] for index in (3, 7, 11, 15)] == [CURTAIN] * 4
    assert first[0] == ARRIVING[0]


# ------------------------------------------------------- navigation with no origin


def test_wipe_has_no_rings_at_all() -> None:
    # Navigation from a service call or a presence sensor. Inventing an origin pad would
    # imply a finger that was not there.
    sequence = wipe(CURTAIN, LEAVING, ARRIVING)
    assert len(sequence) == COLUMNS * 2
    assert [lit_count(frame, CURTAIN) for frame in sequence] == [4, 8, 12, 16, 12, 8, 4, 0]
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
