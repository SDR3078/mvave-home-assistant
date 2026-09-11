"""Grid frames, and the animations that get from one to the next.

A frame is sixteen palette values in **reading order**, top-left to bottom-right, which is
what a person looking at the grid sees. It is not the device's pad numbering, which counts
pad 1 at the bottom left; that translation belongs to the transport, not here.

Everything in this module is pure: no I/O, no Home Assistant, no clock. An animation is a
tuple of frames and the coordinator plays them on a fixed tick. That makes the awkward
parts, which are all about timing and ordering, testable without hardware.

The shapes below were built on the physical grid one step at a time and corrected by eye
several times over; ``ble-midi-surface-design.md`` section 5.3 records why each is the way
it is, and the short version is in the docstrings.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Final

from .palette import OFF

#: The grid, and a frame's length.
COLUMNS: Final = 4
ROWS: Final = 4
PAD_COUNT: Final = COLUMNS * ROWS

#: How long one pad of an animation lasts, so a whole page change is thirty-two of these.
#: Settled by eye on the hardware, which is the only way any of this was settled: a ring or
#: a column at a time needed 350 ms a step to read at all, and a single pad at a time reads
#: comfortably at a fraction of that, because there is no longer a jump to take in.
STEP_SECONDS: Final = 0.045

#: A frame is immutable so it can be compared, cached and used as a dict key. The
#: coordinator diffs consecutive frames to decide what to send.
#:
#: A plain assignment rather than a ``type`` statement: this package has to import on
#: Python 3.11, where that syntax does not exist. It is the same constraint that keeps
#: Home Assistant out of here, and for the same reason.
Frame = tuple[int, ...]

DARK: Final[Frame] = (OFF,) * PAD_COUNT


def blank() -> Frame:
    """A frame with nothing lit."""
    return DARK


def position(row: int, column: int) -> int:
    """The reading-order index of a pad."""
    return row * COLUMNS + column


def row_of(index: int) -> int:
    """Which row a reading-order index falls in, counting from the top."""
    return index // COLUMNS


def column_of(index: int) -> int:
    """Which column a reading-order index falls in, counting from the left."""
    return index % COLUMNS


def clockwise_order(origin: int) -> tuple[int, ...]:
    """Every pad, starting at one and spiralling outward clockwise.

    The order a curtain covers the grid when somebody pressed a pad: it begins under the
    finger and winds outward, each ring entered from directly above and swept clockwise.

    One pad at a time, which is the whole point. Advancing a ring at a time puts one pad
    on the grid, then three, then five, then seven, so the amount of light arriving
    changes every step however evenly the steps are timed, and it reads as a limp. A
    single pad per step cannot do that.
    """
    origin_row, origin_column = row_of(origin), column_of(origin)
    order: list[int] = [origin]
    radius = 1
    while len(order) < PAD_COUNT and radius <= max(ROWS, COLUMNS):
        for row, column in _ring(origin_row, origin_column, radius):
            if 0 <= row < ROWS and 0 <= column < COLUMNS:
                order.append(position(row, column))
        radius += 1
    return tuple(order)


def _ring(origin_row: int, origin_column: int, radius: int) -> list[tuple[int, int]]:
    """One square ring, clockwise, beginning directly above its centre."""
    top, bottom = origin_row - radius, origin_row + radius
    left, right = origin_column - radius, origin_column + radius
    cycle = [(top, column) for column in range(left, right + 1)]
    cycle += [(row, right) for row in range(top + 1, bottom + 1)]
    cycle += [(bottom, column) for column in range(right - 1, left - 1, -1)]
    cycle += [(row, left) for row in range(bottom - 1, top, -1)]
    # Rotated so it starts at twelve o'clock rather than at a corner, which is what makes
    # it read as winding outward from the pad rather than as a box being drawn.
    noon = cycle.index((top, origin_column))
    return cycle[noon:] + cycle[:noon]


def column_order(rightwards: bool = True) -> tuple[int, ...]:
    """Every pad, a column at a time, each column filled from the top.

    The order a curtain travels sideways. Left to right is how a grid is read; the other
    direction is for leaving, so that going back undoes going in.
    """
    columns = range(COLUMNS) if rightwards else range(COLUMNS - 1, -1, -1)
    return tuple(position(row, column) for column in columns for row in range(ROWS))


def sweep(order: Sequence[int], before: Frame, after: Frame) -> tuple[Frame, ...]:
    """Replace one pad per frame, in the given order, until ``before`` has become ``after``.

    Every animation in this module is one of these. What differs between them is only the
    order the pads are visited in, which is worth saying out loud: the shapes are the
    design, and the mechanism underneath has nothing in it.
    """
    current = list(before)
    frames: list[Frame] = []
    for index in order:
        current[index] = after[index]
        frames.append(tuple(current))
    return tuple(frames)


def expand(origin: int, colour: int, leaving: Frame, arriving: Frame) -> tuple[Frame, ...]:
    """Entering a page: winding out from the pressed pad, then opening from the left.

    Two questions, answered in order. The spiral says *which pad did I press*, because it
    begins under the finger. The left-to-right open says *what is in here*, because left
    to right is how a grid is read.

    The page being left stays lit ahead of the curtain rather than blanking first, so
    nothing ever disappears before the animation has said anything. The page being entered
    is already in its real colours behind the curtain, so it arrives *as* the animation
    rather than all at once at the end.
    """
    curtain: Frame = (colour,) * PAD_COUNT
    return sweep(clockwise_order(origin), leaving, curtain) + uncover(colour, arriving)


def collapse(target: int, colour: int, leaving: Frame, arriving: Frame) -> tuple[Frame, ...]:
    """Leaving a page: closing against the reading direction, then winding back into one pad.

    The exact mirror of :func:`expand`, so that going back undoes going in. The last pad
    still covered is the one the page you are returning to occupies on the index, which is
    the pad that was pressed to get here.
    """
    curtain: Frame = (colour,) * PAD_COUNT
    closing = sweep(column_order(rightwards=False), leaving, curtain)
    inward = tuple(reversed(clockwise_order(target)))
    return closing + sweep(inward, curtain, arriving)


def wipe(colour: int, leaving: Frame, arriving: Frame) -> tuple[Frame, ...]:
    """A page change with no origin: both halves travel sideways.

    For navigation that came from a service call, an automation or a presence sensor.
    Inventing an origin pad would imply a finger that was not there, and the first thing
    somebody does with a surface that lies about causality is stop trusting it.
    """
    curtain: Frame = (colour,) * PAD_COUNT
    return sweep(column_order(), leaving, curtain) + uncover(colour, arriving)


def uncover(colour: int, arriving: Frame) -> tuple[Frame, ...]:
    """Uncover a frame one pad at a time, a column at a time, from the left."""
    return sweep(column_order(), (colour,) * PAD_COUNT, arriving)


def value_bar(fraction: float, colour: int, track: int = OFF) -> Frame:
    """A level, drawn as a run of lit pads growing upward from the bottom row.

    Sixteen pads means sixteen steps, a little over six percent each, and there is no
    sub-step. The original design dimmed the last lit pad proportionally, which needs a
    brightness this device does not have; both substitutes were built and tried on the
    hardware and both were rejected. Blinking the pad above the run read as a fault,
    because blinking already means "commanded but not confirmed". Capping the run with a
    second colour read as a pad that did not belong to the bar.

    Upward because level rises. Cover position fills downward instead, which then reads as
    the deliberate exception it is rather than as an inconsistency.
    """
    lit = round(max(0.0, min(1.0, fraction)) * PAD_COUNT)
    return tuple(colour if _height(index) < lit else track for index in range(PAD_COUNT))


def _height(index: int) -> int:
    """How far up the grid a pad is, in bar steps, counting from the bottom left."""
    return (ROWS - 1 - row_of(index)) * COLUMNS + column_of(index)


def overlay(frame: Frame, pad: int, colour: int) -> Frame:
    """One pad changed. Used by the rhythms, which are the only per-pad animation."""
    return (*frame[:pad], colour, *frame[pad + 1 :])
