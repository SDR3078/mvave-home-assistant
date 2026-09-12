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

from .palette import OFF, STATE_OFF

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
    covering = sweep(clockwise_order(origin), leaving, curtain)
    return covering + (curtain,) * CURTAIN_HOLD + uncover(colour, arriving)


def collapse(target: int, colour: int, leaving: Frame, arriving: Frame) -> tuple[Frame, ...]:
    """Leaving a page: closing against the reading direction, then winding back into one pad.

    The exact mirror of :func:`expand`, so that going back undoes going in. The last pad
    still covered is the one the page you are returning to occupies on the index, which is
    the pad that was pressed to get here.
    """
    curtain: Frame = (colour,) * PAD_COUNT
    closing = sweep(column_order(rightwards=False), leaving, curtain)
    inward = tuple(reversed(clockwise_order(target)))
    return closing + (curtain,) * CURTAIN_HOLD + sweep(inward, curtain, arriving)


def wipe(colour: int, leaving: Frame, arriving: Frame) -> tuple[Frame, ...]:
    """A page change with no origin: both halves travel sideways.

    For navigation that came from a service call, an automation or a presence sensor.
    Inventing an origin pad would imply a finger that was not there, and the first thing
    somebody does with a surface that lies about causality is stop trusting it.
    """
    curtain: Frame = (colour,) * PAD_COUNT
    covering = sweep(column_order(), leaving, curtain)
    return covering + (curtain,) * CURTAIN_HOLD + uncover(colour, arriving)


def uncover(colour: int, arriving: Frame) -> tuple[Frame, ...]:
    """Uncover a frame one pad at a time, a column at a time, from the left."""
    return sweep(column_order(), (colour,) * PAD_COUNT, arriving)


#: How long the curtain stays fully closed before it opens again. Every transition that
#: closes one, in both directions.
#:
#: It began as a fix rather than a flourish. The two halves of the way *out* collide on one
#: pad — the last one the curtain covers is the first one it uncovers — so that pad held
#: the curtain for a single frame, 45 ms, while its neighbours held it for up to 23. It
#: read on the hardware as a pad that sometimes simply failed to light, which is how it was
#: reported. Three frames put it at four, matching what the same pad already got on the way
#: in.
#:
#: Then the same beat went on the way in and on a change nobody asked for, because a
#: curtain that pauses when it is shut in one direction and not the other is a curtain with
#: a stutter, and because the two halves of these transitions answer different questions —
#: *which pad did I press* and *what is in here* — so a moment between them is what makes
#: them read as two halves rather than one long slide.
CURTAIN_HOLD: Final = 3

#: How a pad says no. Three blinks, each half held for two frames, so it lasts a little
#: over a quarter of a second and is plainly a reaction rather than a state.
REFUSAL_BLINKS: Final = 3
REFUSAL_HOLD: Final = 2

#: How the eight encoders are laid out on the device: two across and four up, numbered
#: from the bottom left, so knobs one and two are the nearest pair and seven and eight the
#: furthest.
#:
#:      7 8
#:      5 6
#:      3 4
#:      1 2
#:
#: The same convention the pads use, where PAD1 is the bottom left. Worth writing down
#: because the engine counts everything else in reading order from the top, and the legend
#: is the one place those two orders meet.
KNOB_COLUMNS: Final = 2
KNOBS_PER_COLUMN: Final = ROWS


def refuse(frame: Frame, pad: int) -> tuple[Frame, ...]:
    """One pad shuddering to say nothing will happen, then going back to what it was.

    This is the whole treatment for an entity nobody can reach. A colour reserved for it
    would cost one of the five the grid has, permanently, for a condition that is rare and
    usually temporary, and it would still only tell somebody something they can act on at
    the moment they try. A refusal under the finger says it when it is useful and says
    nothing the rest of the time.

    It cannot add to the density problem either: only the pad being pressed can refuse, and
    it is finished before anybody looks away.
    """
    return _shudder(frame, overlay(frame, pad, OFF), REFUSAL_BLINKS, REFUSAL_HOLD)


def knob_legend(colours: Sequence[int | None]) -> Frame:
    """Which of the eight encoders do anything right now, drawn where they actually are.

    The answer to the only question this hardware cannot answer about itself. Eight
    identical knobs, no rings, no markings, and an assignment that is fixed precisely so it
    can be learned once, which is no help at all on the first day.

    A knob that does something shows the colour of what it is adjusting; a knob that does
    nothing shows white; the rest of the grid is dark. That is the grid's own rule — colour
    means it is there, white means it is not — applied to a knob instead of an entity, so
    it costs no new vocabulary.

    Laid out two across and four up, **numbered from the bottom**, exactly as the encoders
    are (see ``KNOB_COLUMNS``). Getting this wrong is not a cosmetic matter: a map whose
    shape does not match the thing it describes is a puzzle, and a puzzle is worse than
    nothing when somebody is standing there with a hand on the wrong knob. It has a second
    virtue: a page fills left to right and top to bottom, so eight lit pads standing in two
    columns is a shape a page can never produce, and this cannot be mistaken for one.
    """
    frame = list(blank())
    for knob, colour in enumerate(colours[: KNOB_COLUMNS * KNOBS_PER_COLUMN], start=1):
        frame[knob_pad(knob)] = STATE_OFF if colour is None else colour
    return tuple(frame)


def switcher_row(colours: Sequence[int]) -> Frame:
    """The top row offering pages while a modifier is held, and nothing else lit.

    The rest of the grid goes dark on purpose. A switcher laid over a page that was still
    showing its own entities would be half one thing and half another, with no way to tell
    which pad belonged to which; dark says plainly that the surface is in a mode, and a
    dark pad already means "nothing here" everywhere else.
    """
    frame = list(blank())
    for column, colour in enumerate(colours[:COLUMNS]):
        frame[position(0, column)] = colour
    return tuple(frame)


def knob_pad(knob: int) -> int:
    """Where an encoder sits on the grid, by its printed number, one based.

    Up from the bottom and across in pairs: knob one is the near left, knob eight the far
    right. Every other index in this package counts down from the top, so this is the one
    place the device's own numbering is honoured rather than translated — and the one place
    anything else should ask, rather than working it out again.
    """
    index = knob - 1
    return position(ROWS - 1 - index // KNOB_COLUMNS, index % KNOB_COLUMNS)


def knobs_in_reading_order() -> tuple[int, ...]:
    """The encoders as somebody looking at them reads them: top left, across, then down.

    Which is **not** the order the device numbers them in. Numbered from the bottom left,
    read from the top left, the block comes out 7, 8, 5, 6, 3, 4, 1, 2 — and that is the
    order anything ranked should be handed out in, because the front of a ranking belongs
    where the eye lands first.
    """
    return tuple(sorted(range(1, KNOB_COLUMNS * KNOBS_PER_COLUMN + 1), key=knob_pad))


def _shudder(frame: Frame, dark: Frame, blinks: int, hold: int) -> tuple[Frame, ...]:
    """Blink from something to darkness and back, and end on what was there."""
    return tuple(state for _ in range(blinks) for state in (*(dark,) * hold, *(frame,) * hold))


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
