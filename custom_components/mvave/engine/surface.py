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
from dataclasses import dataclass, field, replace
from enum import StrEnum
from typing import Any

from .frames import Frame, collapse, expand, refuse, value_bar, wipe
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
    SourceKind,
    Toggle,
)
from .ports import RegistryView
from .properties import PROPERTIES, primary_for, property_for
from .render import (
    BACK_BUTTON,
    HOME_BUTTON,
    Rendering,
    ViewState,
    assignable,
    buttons_for,
    render,
)
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
class Turn:
    """One encoder, and how many steps it moved. Clockwise is positive.

    The encoders are relative and have no rings, so this is all a knob ever says: it has no
    position of its own and cannot be read by looking at it.
    """

    knob: int
    steps: int


@dataclass(frozen=True, slots=True)
class Idle:
    """No input for long enough that the page should give up and go home."""


#: Anything that can reach the surface from outside.
InputEvent = Press | ButtonPress | Turn | Idle


@dataclass(frozen=True, slots=True)
class Hud:
    """The transient value bar: whose value, which one, and what it was set to.

    The value is what was *asked for*, not what has been confirmed. A knob that waited for
    a round trip before moving its own bar would feel broken, and unlike a pad there is
    nothing dangerous about showing an intention here: the bar disappears in a second and
    the page underneath tells the truth.
    """

    entity_id: str
    property_key: str
    value: float


# ------------------------------------------------------------------------ output


@dataclass(frozen=True, slots=True)
class Call:
    """A service to call. The coordinator performs it; the surface never waits for it."""

    domain: str
    service: str
    data: Mapping[str, Any] = field(default_factory=dict)


class EventType(StrEnum):
    """What happened, for the bus.

    One per transition and never batched, so an automation can trigger on exactly one
    thing. The surface fires these whether or not anybody is listening: a control surface
    that only works through its own pages is half a control surface, and the escape hatch
    has to be there from the start rather than bolted on once somebody asks.
    """

    PAGE_ENTERED = "page_entered"
    PAGE_EXITED = "page_exited"
    FOCUS_SET = "focus_set"
    FOCUS_CLEARED = "focus_cleared"
    PAD_PRESSED = "pad_pressed"
    PAD_HELD = "pad_held"
    KNOB_TURNED = "knob_turned"
    TAGGED = "tagged"


class Trigger(StrEnum):
    """What caused a transition, which is not always a finger."""

    PAD = "pad"
    BUTTON = "button"
    SERVICE = "service"
    IDLE = "idle"


@dataclass(frozen=True, slots=True)
class Emit:
    """One thing that happened, ready for the bus."""

    type: EventType
    data: Mapping[str, Any] = field(default_factory=dict)


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
        #: The value bar currently covering the page, if one is. The coordinator takes
        #: it away again once the knob has been still long enough; the engine has no
        #: clock and so cannot decide when that is.
        self.hud: Hud | None = None

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
        """What the grid should be showing, once anything moving has settled.

        A value bar covers the whole page while one is up. That is deliberate: sixteen pads
        is not enough to show a level *and* a room at once, and a bar squeezed into a row
        would be both unreadable and permanently in the way.
        """
        if self.hud is not None:
            bar = self._bar(self.hud)
            if bar is not None:
                return bar
        return self._page_rendering()

    def _view(self) -> ViewState:
        """Everything about right now that is not the page itself."""
        return ViewState(
            focus=self.focus,
            pending=frozenset(self.pending),
            can_go_back=self.depth > 0,
            can_go_home=self.depth > 0,
        )

    def _page_rendering(self) -> Rendering:
        page = self.page
        return render(page, self.slots(page), self.registry, self._view())

    def _bar(self, hud: Hud) -> Rendering | None:
        """The value bar, or None if the property has stopped making sense."""
        prop = PROPERTIES.get(hud.property_key)
        if prop is None:
            return None
        # No rhythms. A bar snaps on and holds still: how the grid arrived is a channel of
        # its own, and a bar that animated in would look like a page change.
        return Rendering(
            frame=value_bar(hud.value, prop.colour),
            buttons=buttons_for(self.page, self._view()),
        )

    # ------------------------------------------------------------------ input

    def handle(self, event: InputEvent) -> Outcome:
        """Act on one input. Returns what it wants done; does none of it."""
        if isinstance(event, Press):
            return self._press(event)
        if isinstance(event, ButtonPress):
            return self._button(event)
        if isinstance(event, Turn):
            return self._turn(event)
        return self._idle()

    def _press(self, event: Press, trigger: Trigger = Trigger.PAD) -> Outcome:
        slots = self.slots()
        if not 0 <= event.pad < len(slots):
            return NOTHING_HAPPENED
        slot = slots[event.pad]
        if slot is None:
            return NOTHING_HAPPENED
        if not self._reachable(slot):
            # It looks the same as one that is off, because it has to look like something
            # and a colour reserved for "unreachable" would cost a fifth of the whole
            # vocabulary. It says so under the finger instead, which is the only moment
            # anybody can act on it.
            return Outcome(animation=refuse(self.rendering().frame, event.pad))
        action = slot.hold if event.held else slot.tap
        if isinstance(action, Nothing):
            # A lit pad that does nothing when pressed is indistinguishable from a broken
            # one. It shudders instead, the same as one nobody can reach, so "nothing
            # happens" is never something somebody has to work out for themselves.
            return Outcome(animation=refuse(self.rendering().frame, event.pad))
        outcome = self._perform(action, origin=event.pad, trigger=trigger)
        # Fired even when the pad does nothing the engine understands, because "pad 5 was
        # held" is exactly the thing somebody wants to hang an automation on.
        #
        # Carrying the trigger matters as much here as it does on a page change: a press
        # can come from a service as well as from a finger, and an automation that cannot
        # tell the two apart will eventually trigger itself.
        return self._also(
            outcome,
            Emit(
                EventType.PAD_HELD if event.held else EventType.PAD_PRESSED,
                {"pad": event.pad, "entity_id": slot.entity_id, "trigger": str(trigger)},
            ),
        )

    def _reachable(self, slot: Slot) -> bool:
        """Whether pressing this pad could do anything.

        A pad showing an unreachable entity refuses rather than pretending. Sending a
        command that cannot arrive would leave it moving forever waiting for a
        confirmation that is never coming.

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
            action: PadAction = Home() if event.held else Back()
        elif event.name == HOME_BUTTON:
            action = Home()
        elif assignable(event.name):
            action = self.page.buttons.get(event.name, Nothing())
        else:
            return NOTHING_HAPPENED
        return self._perform(action, origin=None, trigger=Trigger.BUTTON)

    def _turn(self, event: Turn) -> Outcome:
        """One knob, one property, one value.

        A knob pointed at something without that property is inert rather than falling back
        to something else. The alternative is the same knob doing different things
        depending on what happened to be focused, which is the end of muscle memory.
        """
        target = self.page.knobs.get(event.knob) or self.focus
        if target is None:
            return NOTHING_HAPPENED
        state = self.registry.state_of(target)
        if state is None or state.is_opaque:
            return NOTHING_HAPPENED
        prop = property_for(event.knob, state)
        if prop is None:
            return NOTHING_HAPPENED

        showing = self.hud
        if showing is not None and (showing.entity_id, showing.property_key) == (
            target,
            prop.key,
        ):
            # Continue from what the bar is already showing rather than from the entity.
            # The entity lags by a round trip, and a knob sends thirty messages a second,
            # so re-reading it every step means a fast turn barely moves and then jumps
            # backwards when the answer finally arrives.
            value = showing.value + prop.step * event.steps
        elif state.is_active and (current := prop.read(state)) is not None:
            value = current + prop.step * event.steps
        else:
            # Adjusting something that is off means adjusting a value nobody can see. The
            # first click turns it on at the bottom of its range instead, so the next one
            # has somewhere visible to go.
            value = prop.step
        value = max(0.0, min(1.0, value))

        domain, service, data = prop.write(state, value)
        self.hud = Hud(target, prop.key, value)
        return Outcome(
            calls=(Call(domain, service, data),),
            emits=(
                Emit(
                    EventType.KNOB_TURNED,
                    {
                        "knob": event.knob,
                        "steps": event.steps,
                        "entity_id": target,
                        "property": prop.key,
                        "value": round(value, 4),
                    },
                ),
            ),
        )

    def peek(self, entity_id: str) -> None:
        """Put an entity's main value on the grid without changing it.

        What holding a pad does, and the only thing that stands in for the rings these
        encoders do not have: you cannot see what a knob is set to until you ask.
        """
        state = self.registry.state_of(entity_id)
        if state is None or state.is_opaque:
            return
        prop = primary_for(state)
        if prop is None:
            return
        self.hud = Hud(entity_id, prop.key, prop.read(state) or 0.0)

    def clear_hud(self) -> Outcome:
        """Take the bar away, once the coordinator says the knob has been still long enough.

        Nothing is animated. The bar snaps on, so it snaps off; and it appears every single
        time anybody touches a knob, which is often enough that a transition stops being a
        flourish and becomes something to sit through.
        """
        self.hud = None
        return NOTHING_HAPPENED

    def _idle(self) -> Outcome:
        """Give up and go home, quietly.

        No rings and no button flash. Nothing happened, so it must not look like it did;
        an animation here would pull somebody's eye across the room for no reason.
        """
        if self.depth == 0:
            return NOTHING_HAPPENED
        leaving = self.stack[-1]
        return self._go(
            lambda: self.stack.__setitem__(slice(None), [self.profile.root_id]),
            trigger=Trigger.IDLE,
            leaving=leaving,
            animate=False,
        )

    # ---------------------------------------------------------------- actions

    def _perform(
        self, action: PadAction, origin: int | None, trigger: Trigger = Trigger.PAD
    ) -> Outcome:
        """Everything one action asks for, whether it moves the surface or the house."""
        if isinstance(action, Navigate):
            return self._navigate(action.page_id, origin, trigger)
        if isinstance(action, Back):
            return self._back(trigger)
        if isinstance(action, Home):
            return self._home(trigger)
        if isinstance(action, Toggle):
            return self._command(action.entity_id, self._toggle_call(action.entity_id))
        if isinstance(action, Activate):
            return self._command(action.entity_id, self._activate_call(action.entity_id))
        if isinstance(action, Focus):
            # The same gesture releases it. Without that there is no way back to an
            # ordinary page once you have pointed the knobs at something, and a surface
            # you can get into a state you cannot get out of is a surface people stop
            # trusting.
            if self.focus == action.entity_id:
                self.focus = None
                return self._also(
                    self.clear_hud(),
                    Emit(EventType.FOCUS_CLEARED, {"entity_id": action.entity_id}),
                )
            self.focus = action.entity_id
            self.peek(action.entity_id)
            return Outcome(emits=(Emit(EventType.FOCUS_SET, {"entity_id": action.entity_id}),))
        if isinstance(action, Service):
            return Outcome(calls=(Call(action.domain, action.service, dict(action.data)),))
        if isinstance(action, EventOnly):
            return Outcome(emits=(Emit(EventType.TAGGED, {"tag": action.tag}),))
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

    def settled(self, entity_id: str, follow: bool = True) -> None:
        """Told by the coordinator that an entity's real state has arrived.

        A bar showing that entity follows it, so holding a pad to look at a value never
        shows a stale one, and somebody moving the same lamp from a phone moves the bar.

        Not while a knob is turning, though, which is what ``follow`` is for. Commands are
        rationed on the way out, so what arrives mid-turn is where the knob was a moment
        ago rather than where it is, and following it would drag the bar backwards under
        the finger. The caller knows whether a turn is in flight; this has no clock.
        """
        self.pending.discard(entity_id)
        showing = self.hud if follow else None
        if showing is None or showing.entity_id != entity_id:
            return
        state = self.registry.state_of(entity_id)
        prop = PROPERTIES.get(showing.property_key)
        if state is None or prop is None:
            return
        value = prop.read(state)
        if value is not None:
            self.hud = replace(showing, value=value)

    # --------------------------------------------------------------- outside

    def navigate_to(self, page_id: str) -> Outcome:
        """Go to a page because something other than a finger said so.

        Symmetric in and out is a requirement rather than a nicety: a presence sensor
        pre-selecting a room, a wall tablet steering the pad and an automation pushing to a
        media page when the television comes on all need this, and none of them is a press.
        """
        return self._perform(Navigate(page_id), origin=None, trigger=Trigger.SERVICE)

    def focus_on(self, entity_id: str) -> Outcome:
        """Point the knobs at an entity from outside."""
        return self._perform(Focus(entity_id), origin=None, trigger=Trigger.SERVICE)

    def go_home(self) -> Outcome:
        """Clear the navigation history from outside."""
        return self._perform(Home(), origin=None, trigger=Trigger.SERVICE)

    def go_back(self) -> Outcome:
        """Pop one level of navigation history from outside."""
        return self._perform(Back(), origin=None, trigger=Trigger.SERVICE)

    def press(self, pad: int, held: bool = False) -> Outcome:
        """Press a pad that nobody touched.

        A **position**, deliberately, not an entity: "whatever is on pad five right now",
        which is the only thing about a pad that is stable. What the pad means is resolved
        from the live registry and moves when a room gains a lamp, so an outside caller
        naming a position is honest in a way one naming a target through a position would
        not be. Anything that wants a specific lamp should call that lamp's own service.

        The refusal, the hold, the animation and the event are all exactly what a finger
        would get. Only the trigger differs, so an automation cannot mistake its own
        press for a person's.
        """
        return self._press(Press(pad, held), trigger=Trigger.SERVICE)

    # ------------------------------------------------------------- navigating

    def _navigate(self, page_id: str, origin: int | None, trigger: Trigger) -> Outcome:
        if self.profile.page(page_id) is None or page_id == self.stack[-1]:
            return NOTHING_HAPPENED
        if origin is None:
            # Nobody pressed anything, but the page still lives somewhere on the screen
            # being left, and growing out of where it lives is not an invented origin. It
            # is the same pad a finger would have used, so the surface looks the same
            # whoever asked. What caused it is carried by the event's trigger instead,
            # which is where an automation needs it and where the grid cannot say it.
            origin = pad_showing(self.slots(), page_id)
        return self._go(
            lambda: self.stack.append(page_id),
            trigger=trigger,
            origin=origin,
            entering=page_id,
        )

    def _back(self, trigger: Trigger) -> Outcome:
        if self.depth == 0:
            return NOTHING_HAPPENED
        leaving = self.stack[-1]
        return self._go(lambda: self.stack.pop(), trigger=trigger, leaving=leaving)

    def _home(self, trigger: Trigger) -> Outcome:
        if self.depth == 0:
            return NOTHING_HAPPENED
        leaving = self.stack[-1]
        return self._go(
            lambda: self.stack.__setitem__(slice(None), [self.profile.root_id]),
            trigger=trigger,
            leaving=leaving,
        )

    def _describe(self, page: Page) -> dict[str, Any]:
        """The fields every page event carries, so an automation can read one and act."""
        return {
            "page_id": page.id,
            "page_title": page.title,
            "page_source": str(page.source.kind),
            "area_id": page.source.key if page.source.kind is SourceKind.AREA else None,
            "parent_page_id": page.parent_id,
        }

    def _also(self, outcome: Outcome, *emits: Emit) -> Outcome:
        """The same outcome with more to announce."""
        return replace(outcome, emits=outcome.emits + emits)

    def _go(
        self,
        move: Any,
        *,
        trigger: Trigger,
        origin: int | None = None,
        entering: str | None = None,
        leaving: str | None = None,
        animate: bool = True,
    ) -> Outcome:
        """Move, and work out which curtain covers the move.

        The frame before and the frame after are both real renderings, so the page being
        left stays lit ahead of the curtain and the page being entered is already itself
        behind it. Neither is ever a blank grid.
        """
        before = self.rendering().frame
        departed = self.page
        move()
        # Focus does not follow you between pages. A lamp singled out in the kitchen has
        # no business still holding the knobs once you are looking at the bedroom, and
        # a bar showing its brightness has no business surviving the journey either.
        self.focus = None
        self.hud = None
        after = self.rendering().frame

        announced = (
            Emit(
                EventType.PAGE_EXITED,
                {**self._describe(departed), "depth": self.depth + 1, "trigger": str(trigger)},
            ),
            Emit(
                EventType.PAGE_ENTERED,
                {
                    **self._describe(self.page),
                    "depth": self.depth,
                    "trigger": str(trigger),
                    "previous": departed.id,
                },
            ),
        )

        if entering is not None:
            destination = self.profile.page(entering)
            colour = destination.colour if destination else self.page.colour
            # A wipe only when the page has nowhere to grow from: it is not on the screen
            # being left at all, so there is no pad that could honestly be the origin.
            frames = (
                wipe(colour, before, after)
                if origin is None
                else expand(origin, colour, before, after)
            )
            return Outcome(emits=announced, animation=frames, buttons=ButtonTiming.END)

        if leaving is not None:
            page = self.profile.page(leaving)
            colour = page.colour if page else self.page.colour
            target = pad_showing(self.slots(), leaving)
            frames = (
                wipe(colour, before, after)
                if target is None
                else collapse(target, colour, before, after)
            )
            return Outcome(emits=announced, animation=frames)

        # An idle timeout. A plain sideways wipe, in the colour of wherever you ended up.
        animation = wipe(self.page.colour, before, after) if animate else ()
        return Outcome(emits=announced, animation=animation)
