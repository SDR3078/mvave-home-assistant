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

from typing import Final

from .palette import OFF

#: The grid, and a frame's length.
COLUMNS: Final = 4
ROWS: Final = 4
PAD_COUNT: Final = COLUMNS * ROWS

#: How long one step of any animation lasts. Arrived at by trying 50, 110, 150, 200, 250
#: and 350 milliseconds on the hardware: everything faster read either as a stutter or as
#: nothing having happened at all.
STEP_SECONDS: Final = 0.35

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


def ring_distances(origin: int) -> tuple[int, ...]:
    """How many rings out each pad is from the origin, counted the way a king moves.

    Chebyshev rather than Euclidean distance, so a ring is a square and the shape that
    grows out of a pressed pad is a square too. That is what makes the animation legible
    as "it started here" rather than as an arbitrary spreading blob.
    """
    origin_row, origin_column = row_of(origin), column_of(origin)
    return tuple(
        max(abs(row_of(index) - origin_row), abs(column_of(index) - origin_column))
        for index in range(PAD_COUNT)
    )


def rings_from(origin: int) -> int:
    """How many steps one ring pass takes from this origin.

    Four from a corner and three from anywhere nearer the middle, so entering a page from
    a middle pad is genuinely quicker than from a corner. That is a property of the shape
    rather than a bug: the alternative is padding the middle case with frames that change
    nothing, which is exactly what made an earlier version feel uneven.
    """
    return max(ring_distances(origin)) + 1


def expand(origin: int, colour: int, leaving: Frame, arriving: Frame) -> tuple[Frame, ...]:
    """Entering a page: rings out of the pressed pad, then an open from the left.

    Two questions, answered in order. Growing squares say *which pad did I press*, because
    the animation starts under the finger. The left-to-right open says *what is in here*,
    because left to right is how a grid is read.

    The page being left stays lit ahead of the curtain rather than blanking first, so
    nothing ever disappears before the animation has said anything. The page being entered
    is already in its real colours behind the curtain, so it arrives *as* the animation
    rather than all at once at the end.

    Rings advance one at a time, which puts a different number of pads on screen each step,
    one then three then five then seven from a corner. Covering a fixed number of pads
    instead keeps the area even but leaves the growing square visibly unfinished halfway
    through every step, which reads worse than the uneven area does.
    """
    distances = ring_distances(origin)
    rings = rings_from(origin)
    closing = tuple(
        tuple(
            colour if distance <= step else leaving[index]
            for index, distance in enumerate(distances)
        )
        for step in range(rings)
    )
    return closing + _open_from_left(colour, arriving)


def collapse(target: int, colour: int, leaving: Frame, arriving: Frame) -> tuple[Frame, ...]:
    """Leaving a page: a close against the reading direction, then a shrink into one pad.

    The exact mirror of :func:`expand`, so that going back undoes going in. The curtain
    closes right to left, then contracts toward the pad the page you are returning to
    occupies on the index, which means the last thing lit before that index settles is
    exactly the pad that was pressed to leave it.
    """
    distances = ring_distances(target)
    rings = rings_from(target)
    closing = tuple(
        tuple(
            colour if column_of(index) >= COLUMNS - 1 - step else leaving[index]
            for index in range(PAD_COUNT)
        )
        for step in range(COLUMNS)
    )
    shrinking = tuple(
        tuple(
            colour if distance <= rings - 2 - step else arriving[index]
            for index, distance in enumerate(distances)
        )
        for step in range(rings)
    )
    return closing + shrinking


def wipe(colour: int, leaving: Frame, arriving: Frame) -> tuple[Frame, ...]:
    """A page change with no origin: both halves are column wipes.

    For navigation that came from a service call, an automation or a presence sensor.
    Inventing an origin pad would imply a finger that was not there, and the first thing
    somebody does with a surface that lies about causality is stop trusting it.
    """
    closing = tuple(
        tuple(colour if column_of(index) <= step else leaving[index] for index in range(PAD_COUNT))
        for step in range(COLUMNS)
    )
    return closing + _open_from_left(colour, arriving)


def _open_from_left(colour: int, arriving: Frame) -> tuple[Frame, ...]:
    """Uncover a frame one column at a time, left to right."""
    return tuple(
        tuple(arriving[index] if column_of(index) <= step else colour for index in range(PAD_COUNT))
        for step in range(COLUMNS)
    )


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
