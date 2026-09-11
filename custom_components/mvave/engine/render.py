"""Turning slots and states into something the grid can show.

Rendering is split in two on purpose. :func:`render` produces a still: the colours, plus
which pads are supposed to be moving and how. :func:`compose` turns that still plus a
clock reading into an actual frame. Time never enters the first half, which is what makes
"does an unavailable lamp look different from a lamp that is off" a test you can write in
one line, and "does the focused pad breathe" a test that does not need to sleep.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Final

from .frames import Frame, overlay
from .model import Nothing, Page, Slot
from .palette import ACTION, ON, STATE_OFF, UNASSIGNED
from .ports import RegistryView
from .rhythms import ALERT, BREATHE, Motion

#: The transport buttons, in the order they sit under the grid. Back and home are fixed to
#: the first two; the rest belong to whatever page is showing.
BUTTONS: Final = ("left", "stop", "right", "play", "record")
BACK_BUTTON: Final = "left"
HOME_BUTTON: Final = "stop"

#: The two a page may never bind. Everything else on this surface can be reconfigured,
#: which is exactly why these cannot: a person who has got lost needs one gesture that
#: works the same on every page, including the page they got lost on.
RESERVED_BUTTONS: Final = frozenset({BACK_BUTTON, HOME_BUTTON})


def assignable(name: str) -> bool:
    """Whether a page is allowed to bind this button."""
    return name in BUTTONS and name not in RESERVED_BUTTONS


@dataclass(frozen=True, slots=True)
class ViewState:
    """Everything about the current moment that is not the page itself."""

    #: The entity the knobs are pointed at, which is the pad that breathes.
    focus: str | None = None
    #: Entities commanded but not yet confirmed. The one meaning blinking is allowed.
    pending: frozenset[str] = frozenset()
    can_go_back: bool = False
    can_go_home: bool = False


@dataclass(frozen=True, slots=True)
class Rendering:
    """A still of the grid, plus what is moving on it."""

    frame: Frame
    #: Pad index to movement. The lit half is whatever colour the frame already gives the
    #: pad, so motion never invents the pad's identity; only its second colour is chosen.
    rhythms: Mapping[int, Motion] = field(default_factory=dict)
    buttons: Mapping[str, bool] = field(default_factory=dict)


def colour_of(slot: Slot | None, registry: RegistryView) -> int:
    """What one pad shows, before anything starts moving.

    Colour says what it is and white says it is off, so a pad's own colour appears only
    when the thing behind it is on. A pad with nothing behind it that can be on or off, a
    navigation pad or a bare action, simply shows its colour all the time.
    """
    if slot is None:
        return UNASSIGNED

    entity_id = slot.entity_id
    if entity_id is None:
        # Something without an entity behind it: a bare service call, an event for an
        # automation to catch, a navigation pad. Nothing to be on or off about.
        if slot.colour is not None:
            return slot.colour
        return UNASSIGNED if isinstance(slot.tap, Nothing) else ACTION

    state = registry.state_of(entity_id)
    if slot.is_stateless:
        # Checked before the state, not after. A scene that has never been run reports
        # "unknown", and it has no on and off to report anyway.
        return own_colour(slot)
    if state is None or state.is_opaque:
        # An entity nobody can reach shows the same as one that is off, and says so only
        # when it is pressed, by refusing under the finger. A colour reserved for this
        # would cost a fifth of the vocabulary for something rare and usually temporary.
        return STATE_OFF
    return own_colour(slot) if state.is_active else STATE_OFF


def counterpart(colour: int, slot: Slot | None = None) -> int:
    """The colour a moving pad alternates with.

    The other of its two states, so a pad being switched swings between on and off rather
    than blinking to darkness. Off is white for everything, and on is whatever that pad's
    own colour is, which is why this needs the slot rather than just the colour it happens
    to be showing at this instant.
    """
    if colour == STATE_OFF:
        return own_colour(slot)
    return STATE_OFF


def own_colour(slot: Slot | None) -> int:
    """What a pad shows when what is behind it is on.

    Decided when the page was laid out, not here. A colour can come from the pad's own
    configuration or from the profile's default for its domain, and resolving that once
    per page rather than once per render keeps the choice in one place.
    """
    return slot.colour if slot is not None and slot.colour is not None else ON


def render(
    page: Page,
    slots: Sequence[Slot | None],
    registry: RegistryView,
    view: ViewState | None = None,
) -> Rendering:
    """One page, as it should look right now."""
    view = view or ViewState()
    frame: Frame = tuple(colour_of(slot, registry) for slot in slots)

    rhythms: dict[int, Motion] = {}
    for index, slot in enumerate(slots):
        entity_id = slot.entity_id if slot is not None else None
        # Only a pad that is in one of its two states can be shown moving between them.
        # A stateless pad has nothing to be between, and swinging to darkness is what
        # makes a pad look like it is failing rather than working.
        if entity_id is None or (slot is not None and slot.is_stateless):
            continue
        other = counterpart(frame[index], slot)
        # A pending command outranks focus. Both are true at once often enough, and the
        # one that needs answering is "did that actually happen".
        if entity_id in view.pending:
            rhythms[index] = Motion(ALERT, other)
        elif entity_id == view.focus:
            rhythms[index] = Motion(BREATHE, other)

    return Rendering(frame=frame, rhythms=rhythms, buttons=_buttons(page, view))


def _buttons(page: Page, view: ViewState) -> dict[str, bool]:
    """Which transport lights are on. Lit means pressing it will do something."""
    lit = dict.fromkeys(BUTTONS, False)
    lit[BACK_BUTTON] = view.can_go_back
    lit[HOME_BUTTON] = view.can_go_home
    for name, action in page.buttons.items():
        if assignable(name) and not isinstance(action, Nothing):
            lit[name] = True
    return lit


def compose(rendering: Rendering, elapsed: float) -> Frame:
    """The still plus a clock reading, which is what actually goes to the device."""
    frame = rendering.frame
    for pad, motion in rendering.rhythms.items():
        if not motion.lit(elapsed):
            frame = overlay(frame, pad, motion.other)
    return frame


def changed_pads(before: Frame, after: Frame) -> dict[int, int]:
    """Only the pads that differ, so a grid of sixteen is never rewritten to move one.

    Cheap, but it is the difference between a knob turn sending one message and sending
    sixteen, and the device drops writes long before it complains about them.
    """
    return {
        index: value
        for index, (old, value) in enumerate(zip(before, after, strict=True))
        if old != value
    }
