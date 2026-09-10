"""The surface itself: where you are, how you got there, and what a press does.

This is the only stateful thing in the engine, and its state is three values: which pages
you walked through to get here, what the knobs are pointed at, and what has been commanded
but not yet confirmed.

:meth:`Surface.handle` never calls anything. It returns what it wants done, and something
else does it. That is not purity for its own sake: a surface that performs its own side
effects cannot be driven through a hundred presses in a millisecond, and the bugs that
actually happen here are sequences, not single presses.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from .frames import Frame, collapse, expand, wipe
from .model import (
    Activate,
    Back,
    EventOnly,
    Focus,
    Home,
    Navigate,
    Nothing,
    PadAction,
    Page,
    Profile,
    Service,
    Slot,
    Toggle,
)
from .ports import RegistryView
from .render import BACK_BUTTON, HOME_BUTTON, Rendering, ViewState, assignable, render
from .resolve import resolve

# ------------------------------------------------------------------------- input


@dataclass(frozen=True, slots=True)
class Press:
    """A pad, in reading order. ``held`` distinguishes a hold from a tap."""

    pad: int
    held: bool = False


@dataclass(frozen=True, slots=True)
class ButtonPress:
    """One of the five transport buttons, by name."""

    name: str
    held: bool = False


@dataclass(frozen=True, slots=True)
class Idle:
    """No input for long enough that the page should give up and go home."""


#: Anything that can reach the surface from outside.
InputEvent = Press | ButtonPress | Idle


# ------------------------------------------------------------------------ output


@dataclass(frozen=True, slots=True)
class Call:
    """A service to call. The coordinator performs it; the surface never waits for it."""

    domain: str
    service: str
    data: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Emit:
    """An event to fire, for a page that would rather an automation decided."""

    tag: str


class ButtonTiming(StrEnum):
    """When the transport lights should change during a transition.

    Going into a page they light as the last of the curtain clears, because that is when
    the affordance appears. Coming out they go dark on the very first frame, because by
    then it has already been used.
    """

    START = "start"
    END = "end"


@dataclass(frozen=True, slots=True)
class Outcome:
    """Everything one input asks for."""

    calls: tuple[Call, ...] = ()
    emits: tuple[Emit, ...] = ()
    #: Frames to play in order, one per ``frames.STEP_SECONDS``. Empty means the grid just
    #: redraws, which is what an ordinary toggle does.
    animation: tuple[Frame, ...] = ()
    buttons: ButtonTiming = ButtonTiming.START


NOTHING_HAPPENED = Outcome()

#: How a toggle is performed, by domain. ``homeassistant.toggle`` would cover all of them,
#: but the specific service is what shows up in a person's logbook.
TOGGLE_SERVICES: Mapping[str, str] = {
    "light": "light",
    "switch": "switch",
    "fan": "fan",
    "input_boolean": "input_boolean",
    "siren": "siren",
}

#: How something without a lasting state is started.
ACTIVATE_SERVICES: Mapping[str, tuple[str, str]] = {
    "scene": ("scene", "turn_on"),
    "script": ("script", "turn_on"),
    "button": ("button", "press"),
    "input_button": ("input_button", "press"),
}


def pad_showing(slots: Sequence[Slot | None], page_id: str) -> int | None:
    """Which pad of a page navigates to another page, if any does.

    This is what lets leaving a room shrink back into the pad that was pressed to enter
    it, rather than into an arbitrary corner.
    """
    for index, slot in enumerate(slots):
        if slot is not None and isinstance(slot.tap, Navigate) and slot.tap.page_id == page_id:
            return index
    return None


class Surface:
    """One control surface: a profile, a place in it, and what the knobs are on."""

    def __init__(self, profile: Profile, registry: RegistryView) -> None:
        self.profile = profile
        self.registry = registry
        #: Where you have been, oldest first. Never empty: the root is always underneath.
        self.stack: list[str] = [profile.root_id]
        self.focus: str | None = None
        self.pending: set[str] = set()

    # ------------------------------------------------------------------ where

    @property
    def page(self) -> Page:
        """The page showing now."""
        page = self.profile.page(self.stack[-1])
        if page is None:  # pragma: no cover - a profile without its own root is a bug
            raise KeyError(f"no page {self.stack[-1]!r} in this profile")
        return page

    @property
    def depth(self) -> int:
        """How far from the root you have wandered."""
        return len(self.stack) - 1

    def slots(self, page: Page | None = None) -> tuple[Slot | None, ...]:
        """A page's sixteen slots, resolved against the world as it is now."""
        return resolve(page or self.page, self.registry, self.profile)

    def rendering(self) -> Rendering:
        """What the grid should be showing, once anything moving has settled."""
        page = self.page
        return render(
            page,
            self.slots(page),
            self.registry,
            ViewState(
                focus=self.focus,
                pending=frozenset(self.pending),
                can_go_back=self.depth > 0,
                can_go_home=self.depth > 0,
            ),
        )

    # ------------------------------------------------------------------ input

    def handle(self, event: InputEvent) -> Outcome:
        """Act on one input. Returns what it wants done; does none of it."""
        if isinstance(event, Press):
            return self._press(event)
        if isinstance(event, ButtonPress):
            return self._button(event)
        return self._idle()

    def _press(self, event: Press) -> Outcome:
        slots = self.slots()
        if not 0 <= event.pad < len(slots):
            return NOTHING_HAPPENED
        slot = slots[event.pad]
        if slot is None or not self._reachable(slot):
            return NOTHING_HAPPENED
        action = slot.hold if event.held else slot.tap
        return self._perform(action, origin=event.pad)

    def _reachable(self, slot: Slot) -> bool:
        """Whether pressing this pad could do anything.

        A pad showing an unreachable entity is inert rather than merely unhelpful. Sending
        a command that cannot arrive would leave it moving forever waiting for a
        confirmation that is never coming, and a pad that looks broken and then behaves
        broken is at least honest.

        Scenes and scripts are exempt. Their resting state in Home Assistant is "unknown",
        which is not the same as unreachable, and they are exactly the pads people press.
        """
        entity_id = slot.entity_id
        if entity_id is None or slot.is_stateless:
            return True
        state = self.registry.state_of(entity_id)
        return state is not None and not state.is_opaque

    def _button(self, event: ButtonPress) -> Outcome:
        # Back and home are the two things on this surface that work the same everywhere,
        # including on the page somebody got lost on, so a page cannot rebind them.
        if event.name == BACK_BUTTON:
            return self._perform(Home() if event.held else Back(), origin=None)
        if event.name == HOME_BUTTON:
            return self._perform(Home(), origin=None)
        if not assignable(event.name):
            return NOTHING_HAPPENED
        return self._perform(self.page.buttons.get(event.name, Nothing()), origin=None)

    def _idle(self) -> Outcome:
        """Give up and go home, quietly.

        No rings and no button flash. Nothing happened, so it must not look like it did;
        an animation here would pull somebody's eye across the room for no reason.
        """
        if self.depth == 0:
            return NOTHING_HAPPENED
        return self._go(lambda: self.stack.__setitem__(slice(None), [self.profile.root_id]))

    # ---------------------------------------------------------------- actions

    def _perform(self, action: PadAction, origin: int | None) -> Outcome:
        """Everything one action asks for, whether it moves the surface or the house."""
        if isinstance(action, Navigate):
            return self._navigate(action.page_id, origin)
        if isinstance(action, Back):
            return self._back()
        if isinstance(action, Home):
            return self._home()
        if isinstance(action, Toggle):
            return self._command(action.entity_id, self._toggle_call(action.entity_id))
        if isinstance(action, Activate):
            return self._command(action.entity_id, self._activate_call(action.entity_id))
        if isinstance(action, Focus):
            # The same gesture releases it. Without that there is no way back to an
            # ordinary page once you have pointed the knobs at something, and a surface
            # you can get into a state you cannot get out of is a surface people stop
            # trusting.
            self.focus = None if self.focus == action.entity_id else action.entity_id
            return Outcome()
        if isinstance(action, Service):
            return Outcome(calls=(Call(action.domain, action.service, dict(action.data)),))
        if isinstance(action, EventOnly):
            return Outcome(emits=(Emit(action.tag),))
        return NOTHING_HAPPENED

    def _toggle_call(self, entity_id: str) -> Call:
        domain = entity_id.split(".", 1)[0]
        return Call(
            TOGGLE_SERVICES.get(domain, "homeassistant"), "toggle", {"entity_id": entity_id}
        )

    def _activate_call(self, entity_id: str) -> Call:
        domain = entity_id.split(".", 1)[0]
        service = ACTIVATE_SERVICES.get(domain, ("homeassistant", "turn_on"))
        return Call(service[0], service[1], {"entity_id": entity_id})

    def _command(self, entity_id: str, call: Call) -> Outcome:
        """Ask for something and mark the pad as not yet confirmed.

        The grid never waits for a Zigbee round trip before responding. It blinks the pad
        instead, and the state change that follows corrects it. A surface that waits feels
        broken even when it is working perfectly.
        """
        self.pending.add(entity_id)
        return Outcome(calls=(call,))

    def settled(self, entity_id: str) -> None:
        """Told by the coordinator that an entity's real state has arrived."""
        self.pending.discard(entity_id)

    # ------------------------------------------------------------- navigating

    def _navigate(self, page_id: str, origin: int | None) -> Outcome:
        if self.profile.page(page_id) is None or page_id == self.stack[-1]:
            return NOTHING_HAPPENED
        return self._go(lambda: self.stack.append(page_id), origin=origin, entering=page_id)

    def _back(self) -> Outcome:
        if self.depth == 0:
            return NOTHING_HAPPENED
        leaving = self.stack[-1]
        return self._go(lambda: self.stack.pop(), leaving=leaving)

    def _home(self) -> Outcome:
        if self.depth == 0:
            return NOTHING_HAPPENED
        leaving = self.stack[-1]
        return self._go(
            lambda: self.stack.__setitem__(slice(None), [self.profile.root_id]), leaving=leaving
        )

    def _go(
        self,
        move: Any,
        *,
        origin: int | None = None,
        entering: str | None = None,
        leaving: str | None = None,
    ) -> Outcome:
        """Move, and work out which curtain covers the move.

        The frame before and the frame after are both real renderings, so the page being
        left stays lit ahead of the curtain and the page being entered is already itself
        behind it. Neither is ever a blank grid.
        """
        before = self.rendering().frame
        move()
        # Focus does not follow you between pages. A lamp singled out in the kitchen has
        # no business still holding the knobs once you are looking at the bedroom.
        self.focus = None
        after = self.rendering().frame

        if entering is not None:
            destination = self.profile.page(entering)
            colour = destination.colour if destination else self.page.colour
            if origin is None:
                # Nobody pressed anything: a service call, an automation, a presence
                # sensor. Inventing an origin would imply a finger that was not there.
                return Outcome(animation=wipe(colour, before, after), buttons=ButtonTiming.END)
            return Outcome(
                animation=expand(origin, colour, before, after), buttons=ButtonTiming.END
            )

        if leaving is not None:
            departed = self.profile.page(leaving)
            colour = departed.colour if departed else self.page.colour
            target = pad_showing(self.slots(), leaving)
            if target is None:
                return Outcome(animation=wipe(colour, before, after))
            return Outcome(animation=collapse(target, colour, before, after))

        # An idle timeout. A plain wipe, in the colour of wherever you have ended up.
        return Outcome(animation=wipe(self.page.colour, before, after))
