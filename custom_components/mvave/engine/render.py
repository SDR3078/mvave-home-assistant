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
from .palette import ACTION, OFF, ON, STATE_OFF, UNASSIGNED, UNAVAILABLE
from .ports import RegistryView
from .rhythms import ALERT, BREATHE, Rhythm

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
    #: Pad index to rhythm. A rhythm only ever darkens a pad; its lit phase is whatever
    #: colour the frame already gives it, so motion never invents a colour.
    rhythms: Mapping[int, Rhythm] = field(default_factory=dict)
    buttons: Mapping[str, bool] = field(default_factory=dict)


def colour_of(slot: Slot | None, registry: RegistryView) -> int:
    """What one pad shows, before anything starts moving.

    The order matters. A slot with a colour forced on it never reflects state at all. A
    missing entity is rendered the same as an unreachable one, because from where a person
    is standing they are the same thing: pressing it will not work.
    """
    if slot is None:
        return UNASSIGNED
    if slot.colour is not None:
        return slot.colour

    entity_id = slot.entity_id
    if entity_id is None:
        # Something without an entity behind it: a bare service call, an event for an
        # automation to catch, a navigation pad with no colour of its own.
        return UNASSIGNED if isinstance(slot.tap, Nothing) else ACTION

    state = registry.state_of(entity_id)
    if state is None:
        # Nothing by that id exists at all, which is a configuration mistake rather than a
        # flat battery. It shows the same either way, because pressing it will not work.
        return UNAVAILABLE
    if slot.is_stateless:
        # Checked before the state, not after. A scene that has never been run reports
        # "unknown", and calling that unreachable would light half a fresh grid as broken.
        return ACTION
    if state.is_opaque:
        return UNAVAILABLE
    return ON if state.is_active else STATE_OFF


def render(
    page: Page,
    slots: Sequence[Slot | None],
    registry: RegistryView,
    view: ViewState | None = None,
) -> Rendering:
    """One page, as it should look right now."""
    view = view or ViewState()
    frame: Frame = tuple(colour_of(slot, registry) for slot in slots)

    rhythms: dict[int, Rhythm] = {}
    for index, slot in enumerate(slots):
        entity_id = slot.entity_id if slot is not None else None
        if entity_id is None:
            continue
        # A pending command outranks focus. Both are true at once often enough, and the
        # one that needs answering is "did that actually happen".
        if entity_id in view.pending:
            rhythms[index] = ALERT
        elif entity_id == view.focus:
            rhythms[index] = BREATHE

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
    for pad, rhythm in rendering.rhythms.items():
        if not rhythm.lit(elapsed):
            frame = overlay(frame, pad, OFF)
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
