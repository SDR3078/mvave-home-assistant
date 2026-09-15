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

from .frames import (
    COLUMNS,
    Frame,
    collapse,
    expand,
    knob_legend,
    knobs_in_reading_order,
    refuse,
    switcher_row,
    value_bar,
    wipe,
)
from .model import (
    INERT_ACTIONS,
    STATELESS_DOMAINS,
    UNKNOWN_AT_REST,
    Activate,
    Back,
    EntityState,
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
from .palette import property_colour
from .ports import RegistryView
from .properties import KNOB_COUNT, PROPERTIES, can_focus, packed, primary_for
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
class ButtonRelease:
    """A transport button let go of, after its hold had already fired.

    Only buttons get this, and only held ones. A hold on a *pad* is a thing that happened
    and is over; a hold on a button is a mode, and a mode has to be able to end.
    """

    name: str


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
InputEvent = Press | ButtonPress | ButtonRelease | Turn | Idle


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
    #: Stateless entities that just started, and whose pads are now holding a colour to
    #: say so. The engine has no clock, so it cannot decide when to let go; the caller
    #: reads this and sets the countdown.
    acknowledged: tuple[str, ...] = ()
    #: Whether the animation is a *reaction* — something brief, under the finger that asked
    #: for it — rather than a move from one page to another.
    #:
    #: The distinction exists for one reason: a reaction must never restart a reaction. Three
    #: blinks in 540 ms is already 5.6 Hz, which is only legal because three is the most a
    #: thing may flash in a second, so pressing a dead pad twice inside a second would put
    #: more than three there. Saying no a second time also tells nobody anything, since the
    #: first refusal is still on the grid saying it.
    reaction: bool = False
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
        #: Where you have been, oldest first. Never empty: the root is always underneath,
        #: with the default page already on it if the profile names one.
        self.stack: list[str] = profile.at_rest
        self.focus: str | None = None
        self.pending: set[str] = set()
        #: Stateless pads holding their acknowledgement colour. Released on a countdown
        #: the runner owns, because nothing in here knows what a second is.
        self.acknowledged: set[str] = set()
        #: The value bar currently covering the page, if one is. The coordinator takes
        #: it away again once the knob has been still long enough; the engine has no
        #: clock and so cannot decide when that is.
        self.hud: Hud | None = None
        #: The last value **Home Assistant reported** for a property, per entity.
        #:
        #: Written wherever a value is read off the house and nowhere else — never from
        #: what this surface asked for. A command is an intention: it can be clamped,
        #: ignored, or arrive at a lamp that was already moving, so remembering it would
        #: make the knob resume from a place the house was never in.
        #:
        #: Read only when an entity is on and will not name a property at all, which a
        #: light in colour mode does for colour temperature. So it never competes with a
        #: live reading; it stands in for one that does not exist, and it suits encoders
        #: with no rings, where the surface is the only thing that can hold a position.
        self.last_known: dict[tuple[str, str], float] = {}
        #: Showing which of the eight encoders do anything to what has focus, instead of a
        #: value. Put up by turning a knob that can do nothing, and by nothing else: it is
        #: an answer to a question somebody asked, in the moment they asked it, which is the
        #: same rule that keeps an unreachable pad from spending a colour of its own.
        self.legend = False
        #: The back button is being held, so the top row is offering the rooms instead of
        #: whatever the page had there. A mode rather than a state: it lasts exactly as
        #: long as the finger does.
        self.shifted = False

    def carry_into(self, fresh: Surface) -> None:
        """Hand everything that outlives a rebuild to the surface replacing this one.

        Here rather than in the caller because it is this class's own state, and a list of
        fields copied somewhere else is a list somebody will add to this class without
        adding to. Reconfiguring throws the surface away and builds another from the new
        profile; what a person should not lose in that is where they were standing, what
        the knobs were on, and where they had left a knob — none of which had anything to
        do with the colour that was changed.

        What is deliberately *not* carried is everything with a finger or a clock in it: a
        held shift, a bar that is up, a legend. Those last exactly as long as the gesture
        that opened them, and a rebuild ends the gesture.
        """
        # As far as it still exists. A page can be deleted by the same edit that caused
        # the rebuild, and standing on it afterwards is not a place.
        kept = [page for page in self.stack if fresh.profile.page(page) is not None]
        fresh.stack = kept or fresh.profile.at_rest
        fresh.focus = self.focus
        fresh.pending = set(self.pending)
        fresh.last_known = dict(self.last_known)

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
        if self.shifted:
            return self._switcher()
        if self.legend:
            return self._legend()
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
            acknowledged=frozenset(self.acknowledged),
            can_go_back=self.depth > 0,
            can_go_home=self.depth > 0,
        )

    def _page_rendering(self) -> Rendering:
        page = self.page
        return render(page, self.slots(page), self.registry, self._view())

    @property
    def showing(self) -> bool:
        """Whether anything transient is covering the page and owes it a countdown."""
        return self.legend or self.hud is not None

    def switcher(self) -> tuple[Page, ...]:
        """The pages the top row offers while the back button is held.

        Top-level pages in the profile's own order, which is the order the index shows them
        in, so a room is in the same place whichever way you reach it. Four fit across, and
        a household with more reaches the rest through the index — which is the honest
        limit of a row four pads wide rather than a decision.
        """
        rooms = tuple(
            page for page in self.profile.pages.values() if page.id != self.profile.root_id
        )
        return rooms[:COLUMNS]

    def _switcher(self) -> Rendering:
        """The rooms across the top, in their own colours, and nothing else lit."""
        return Rendering(
            frame=switcher_row([page.colour for page in self.switcher()]),
            buttons=buttons_for(self.page, self._view()),
        )

    def _legend(self) -> Rendering:
        """Which encoders are live, each in the colour of what it adjusts.

        Coloured by the property rather than by the entity, which matters more now that
        what an encoder adjusts depends on what is focused: if the meaning can move, the
        map has to say what it currently *is*, not merely that it is something. Orange is
        the level and the other three are the colour controls — see ``palette.PROPERTY_COLOURS``.
        """
        live = self.knob_map()
        colours: list[int | None] = []
        for knob in range(1, KNOB_COUNT + 1):
            adjusting = live.get(knob)
            colours.append(property_colour(adjusting[1]) if adjusting else None)
        # No rhythms, for the same reason the bar has none: how the grid arrived is a
        # channel of its own, and a legend that animated in would read as a page change.
        return Rendering(
            frame=knob_legend(colours),
            buttons=buttons_for(self.page, self._view()),
        )

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
        if isinstance(event, ButtonRelease):
            return self._released(event)
        if isinstance(event, Turn):
            return self._turn(event)
        return self._idle()

    def _press(self, event: Press, trigger: Trigger = Trigger.PAD) -> Outcome:
        if self.shifted:
            return self._switch(event.pad, trigger)
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
            return Outcome(animation=refuse(self.rendering().frame, event.pad), reaction=True)
        action = slot.hold if event.held else slot.tap
        if isinstance(action, INERT_ACTIONS):
            # A lit pad that does nothing when pressed is indistinguishable from a broken
            # one. It shudders instead, the same as one nobody can reach, so "nothing
            # happens" is never something somebody has to work out for themselves.
            #
            # A readout lands here too, and gets the same shudder on purpose. Whether the
            # pad cannot act because it is a door sensor or because the lamp behind it
            # died is a diagnostic question, not a finger question, and ``get_pages``
            # answers that one in words.
            return Outcome(animation=refuse(self.rendering().frame, event.pad), reaction=True)
        # The press has been accepted, so whatever was covering the page gives way to it.
        # A value bar covers all sixteen pads and the knob map covers eight, and neither was
        # cleared by a press: the toggle happened underneath a grid still showing the bar,
        # the pad that was pressed showed nothing, and because the bar's own expiry is
        # rearmed on every input, tapping pads kept it up indefinitely. A hold puts its own
        # bar back immediately afterwards, through `peek`.
        self.hud = None
        self.legend = False
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

    def _switch(self, pad: int, trigger: Trigger) -> Outcome:
        """A press while the back button is held: go to whichever room is on that pad."""
        rooms = self.switcher()
        if not 0 <= pad < len(rooms):
            # A dark pad. The rest of the grid is not offering anything while the shift is
            # held, and a dark pad means "nothing here" everywhere else too.
            return NOTHING_HAPPENED
        if rooms[pad].id == self.stack[-1]:
            # Lit, and pressing it would do nothing, which on this surface is never allowed
            # to be silent: you are already there.
            return Outcome(animation=refuse(self.rendering().frame, pad), reaction=True)
        # The shift is cleared inside the move, after it has read what is on the grid, so
        # the curtain grows out of the switcher rather than out of a page nobody can see.
        return self._navigate(rooms[pad].id, origin=pad, trigger=trigger)

    def _reachable(self, slot: Slot) -> bool:
        """Whether pressing this pad could do anything.

        A pad showing an unreachable entity refuses rather than pretending. Sending a
        command that cannot arrive would leave it moving forever waiting for a
        confirmation that is never coming.

        Pads resting at "unknown" are exempt — scenes, buttons and input buttons. That is
        not the same as unreachable, and they are exactly the pads people press.

        **Scripts are not among them**, even though they are drawn as stateless. A script
        rests at "off", so a script reporting "unavailable" really is unreachable and
        refuses like anything else. Those two facts came apart on 2026-09-13 and this is
        the seam: how a pad is *drawn* and whether it can be *reached* stopped being the
        same question the moment a script stopped showing its running state.
        """
        entity_id = slot.entity_id
        if entity_id is None or entity_id.split(".", 1)[0] in UNKNOWN_AT_REST:
            return True
        state = self.registry.state_of(entity_id)
        return state is not None and not state.is_opaque

    def _button(self, event: ButtonPress) -> Outcome:
        # Back and home are the two things on this surface that work the same everywhere,
        # including on the page somebody got lost on, so a page cannot rebind them.
        if event.name == BACK_BUTTON:
            if event.held:
                # Holding back used to go home, which the stop button already does. A
                # surface with five buttons cannot afford to spend two on one thing, and
                # this is the gesture the switcher needs.
                self.shifted = True
                return NOTHING_HAPPENED
            action: PadAction = Back()
        elif event.name == HOME_BUTTON:
            action = Home()
        elif assignable(event.name):
            action = self.page.buttons.get(event.name, Nothing())
        else:
            return NOTHING_HAPPENED
        return self._perform(action, origin=None, trigger=Trigger.BUTTON)

    def _released(self, event: ButtonRelease) -> Outcome:
        """A held button let go of. Ends whatever mode it was holding open."""
        if event.name == BACK_BUTTON:
            self.shifted = False
        return NOTHING_HAPPENED

    def _turn(self, event: Turn) -> Outcome:
        """One knob, one property, one value.

        What a knob adjusts is decided in one place, :meth:`knob_map`, and read from there
        rather than worked out again here — otherwise what the grid says an encoder does
        and what it actually does are two pieces of arithmetic that can disagree, and the
        one thing a map may never be is wrong.
        """
        if self.page.knobs.get(event.knob) is None and self.focus is None:
            # On the index, nothing can be focused and nothing ever will be, so a map here
            # would fire every time somebody brushed an encoder against a page that has no
            # answer to give. Everywhere else it is a fair question badly answered: this
            # returned silence on an ordinary room page too, which is the state the device
            # is in the very first time anybody touches it and again in every new room —
            # so eight unmarked encoders answered nothing at all, exactly when somebody
            # would first try them. The map says "none of these, yet", which is true.
            if self.page.source.kind is SourceKind.PAGES:
                return NOTHING_HAPPENED
            return self._refuse_turn()

        adjusting = self.knob_map().get(event.knob)
        if adjusting is None:
            return self._refuse_turn()
        target, key = adjusting
        state = self.registry.state_of(target)
        prop = PROPERTIES.get(key)
        if state is None or prop is None:  # pragma: no cover - knob_map just found both
            return self._refuse_turn()

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
        elif (current := prop.read(state)) is not None:
            self._learn(state)
            value = current + prop.step * event.steps
        elif not state.is_active:
            # Adjusting something that is off means adjusting a value nobody can see. The
            # first click turns it on at the bottom of its range instead, so the next one
            # has somewhere visible to go.
            #
            # Asked *after* the read, not before it. "Switched off" was standing in for
            # "there is no value to read", which is true of a lamp's brightness and false
            # of a thermostat's setpoint or a paused speaker's volume — both perfectly
            # readable while off. Asked first, one click at an off thermostat replaced a
            # 21 degree setpoint with 7.5 and left it there.
            value = prop.step
        else:
            # On, and yet it will not say. Home Assistant reports no colour temperature at
            # all for a light in colour mode and never derives one, because most colours
            # have no meaningful temperature — so this is not an edge, it is every coloured
            # bulb whose hue has been touched.
            #
            # This used to fall into the branch above and start at the bottom of the range,
            # which read at the grid as the knob resetting, because that is what it was:
            # one branch answering for both "off" and "on but quiet", which are not the
            # same situation. Resume where this surface last left it instead, and start in
            # the middle only if it has never been set — the one value that is not a claim
            # about anything.
            # The middle of *this entity's own* range, not a constant: a lamp reports the
            # temperatures it can reach even while it is refusing to name the one it is at,
            # so the guess is at least made out of the lamp's own numbers. It is still a
            # guess, and the only defence is that it is hard to reach — everything above
            # learns from every reading it touches, so this needs a property the house has
            # not named once since the surface started.
            value = self.last_known.get((target, prop.key), 0.5) + prop.step * event.steps
        value = max(0.0, min(1.0, value))

        domain, service, data = prop.write(state, value)
        # A live knob is past the question the legend answers: show the value instead.
        self.legend = False
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
                        # Always a finger: nothing can turn an encoder but a hand on it.
                        # Carried anyway, so every one of the eight event types has the
                        # field and an automation never has to special-case its absence.
                        "trigger": str(Trigger.PAD),
                    },
                ),
            ),
        )

    def _refuse_turn(self) -> Outcome:
        """Something is selected and this knob cannot touch it.

        Answered with the map rather than with a refusal, which is the one place this
        surface departs from its own "anything that does nothing shudders" rule, and for
        two reasons that both came out of trying it.

        A pad shudders under the finger that pressed it: fifteen other pads keep reporting,
        so the signal has a referent and costs almost nothing. A knob has no pad, so the
        only surface available is the whole grid — and a whole grid going dark and back is
        not a louder version of that signal, it is a different one. It already means
        "nothing is driving this device", it is what the photosensitivity thresholds are
        written about, and it was read on the hardware exactly as it reads everywhere else:
        as the thing failing.

        And "not that one" is the wrong answer anyway. Somebody turning a dead knob is
        searching, and the useful reply names the ones that work. On a fan, where seven of
        eight do nothing, a refusal says "no" seven times; the map says "it is number
        eight" once.
        """
        self.legend = True
        return NOTHING_HAPPENED

    def knob_map(self) -> dict[int, tuple[str, str]]:
        """What each of the eight encoders would do right now, and to what.

        Knob number to (entity, property), and the only place that decides it: the grid
        draws this, a turn obeys it, and Home Assistant reports it, so there is one answer
        rather than three that can disagree.

        A knob missing from this does nothing, and on the device there is no way whatever
        to tell that apart from one that does — no rings, no markings, no screen.

        Filled in two passes. A page may pin an individual encoder to an entity of its own
        — "volume here always means this speaker" — and those are placed first, each
        showing that entity's main control. Whatever the focused thing offers then fills the
        encoders left over, in reading order from the top left, so its main value lands on
        the first one still free.
        """
        found: dict[int, tuple[str, str]] = {}
        for knob, pinned in self.page.knobs.items():
            state = self.registry.state_of(pinned)
            prop = primary_for(state) if state is not None and not state.is_opaque else None
            if prop is not None:
                found[knob] = (pinned, prop.key)

        if self.focus is None:
            return found
        state = self.registry.state_of(self.focus)
        if state is None or state.is_opaque:
            return found
        free = (knob for knob in knobs_in_reading_order() if knob not in found)
        for knob, prop in zip(free, packed(state), strict=False):
            found[knob] = (self.focus, prop.key)
        return found

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
            # Nothing readable to show. Leave whatever is up alone rather than clearing the
            # grid to say so: a lamp that cannot dim has no value, and the legend that is
            # probably showing has already said which knobs work on it.
            return
        self._learn(state)
        self.hud = Hud(entity_id, prop.key, prop.read(state) or 0.0)
        self.legend = False

    def _learn(self, state: EntityState) -> None:
        """Record every value the house is currently naming for one entity.

        Called wherever a state is already in hand, because reading one property off a
        state and discarding the rest of the same reading is what keeps the guess below
        reachable. Hold a pad on a lamp in colour-temperature mode and the temperature is
        right there in the attributes; throwing it away means the knob has to invent one
        ten seconds later, after somebody has given the lamp a colour and the house has
        stopped naming it.
        """
        for prop in packed(state):
            value = prop.read(state)
            if value is not None:
                self.last_known[state.entity_id, prop.key] = value

    def release(self, entity_id: str) -> None:
        """Let go of an acknowledgement, once it has been held long enough.

        Called by whatever owns the clock. Held too briefly it is a flash nobody catches;
        held too long it is a pad lying about what is on.
        """
        self.acknowledged.discard(entity_id)

    def clear_hud(self) -> Outcome:
        """Take the bar away, once the coordinator says the knob has been still long enough.

        Nothing is animated. The bar snaps on, so it snaps off; and it appears every single
        time anybody touches a knob, which is often enough that a transition stops being a
        flourish and becomes something to sit through.
        """
        self.hud = None
        self.legend = False
        return NOTHING_HAPPENED

    def _idle(self) -> Outcome:
        """Give up and go back to rest: the index, or the default page sitting on it.

        The same way out a finger would have taken, which is what it looked like in
        practice: `animate=False` was asked for here and never read, because the branch
        that draws a curtain returned first. Made real on 2026-09-13 and judged at the
        grid the same afternoon — the owner wanted the curtain back, so the flag is gone
        rather than honoured, and both documents have been corrected to say so.

        What still marks it out is the event, which carries `idle` rather than a finger,
        so an automation can tell the difference even though the grid cannot.
        """
        rest = self.profile.at_rest
        if self.stack == rest:
            return NOTHING_HAPPENED
        # Shrinking into the room's own pad is only honest when the index is where you
        # arrive. Resting on a page instead, no pad there *is* the room being left, so it
        # is the plain sideways wipe in the resting page's colour.
        leaving = self.stack[-1] if len(rest) == 1 else None
        return self._go(
            lambda: self.stack.__setitem__(slice(None), rest),
            trigger=Trigger.IDLE,
            leaving=leaving,
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
            outcome = self._command(action.entity_id, self._activate_call(action.entity_id))
            if action.entity_id.split(".", 1)[0] not in STATELESS_DOMAINS:
                # A script is not stateless: it reports running and then idle, so it
                # already blinks and then settles like a lamp, and needs nothing from here.
                return outcome
            self.acknowledged.add(action.entity_id)
            return replace(outcome, acknowledged=(action.entity_id,))
        if isinstance(action, Focus):
            state = self.registry.state_of(action.entity_id)
            if not can_focus(state):
                # The breathe is this device's one way of saying "the knobs are on this
                # pad", and it was started for anything of a focusable *domain* — so a
                # blind that cannot be positioned, or a lamp nobody can reach, breathed
                # while every encoder did nothing. Refusing says the true thing instead.
                if origin is None:
                    return NOTHING_HAPPENED
                return Outcome(animation=refuse(self.rendering().frame, origin), reaction=True)
            # The same gesture releases it. Without that there is no way back to an
            # ordinary page once you have pointed the knobs at something, and a surface
            # you can get into a state you cannot get out of is a surface people stop
            # trusting.
            if self.focus == action.entity_id:
                self.focus = None
                return self._also(
                    self.clear_hud(),
                    Emit(
                        EventType.FOCUS_CLEARED,
                        {"entity_id": action.entity_id, "trigger": str(trigger)},
                    ),
                )
            self.focus = action.entity_id
            self.peek(action.entity_id)
            return Outcome(
                emits=(
                    Emit(
                        EventType.FOCUS_SET,
                        {"entity_id": action.entity_id, "trigger": str(trigger)},
                    ),
                )
            )
        if isinstance(action, Service):
            return Outcome(calls=(Call(action.domain, action.service, dict(action.data)),))
        if isinstance(action, EventOnly):
            return Outcome(
                emits=(Emit(EventType.TAGGED, {"tag": action.tag, "trigger": str(trigger)}),)
            )
        return NOTHING_HAPPENED

    def _toggle_call(self, entity_id: str) -> Call:
        domain = entity_id.split(".", 1)[0]
        if domain == "lock":
            # There is no `lock.toggle`, so the service is chosen here from the state.
            #
            # It used to be `lock.open` unconditionally, which is not a toggle and is not
            # even an unlock: it retracts the latch. So the pad opened the front door when
            # pressed, opened it again when pressed a second time, and there was no gesture
            # anywhere on this surface that locked anything. Locks reach a pad by auto-fill,
            # so nobody had to opt in to that. Found by review on 2026-09-13.
            state = self.registry.state_of(entity_id)
            service = "lock" if state is not None and state.is_active else "unlock"
            return Call("lock", service, {"entity_id": entity_id})
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
        self._learn(state)
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
    ) -> Outcome:
        """Move, and work out which curtain covers the move.

        The frame before and the frame after are both real renderings, so the page being
        left stays lit ahead of the curtain and the page being entered is already itself
        behind it. Neither is ever a blank grid.
        """
        before = self.rendering().frame
        departed = self.page
        # Read before the move, not after. `departed` was already captured here, but the
        # depth that goes out with it used to be `self.depth + 1` from *after* the stack
        # had changed — which is the right answer only for a single step back, and that is
        # the only move anybody had checked. Leaving the index announced it at depth 2.
        departed_depth = self.depth
        move()
        # Focus does not follow you between pages. A lamp singled out in the kitchen has
        # no business still holding the knobs once you are looking at the bedroom, and
        # a bar showing its brightness has no business surviving the journey either.
        self.focus = None
        self.hud = None
        self.legend = False
        self.shifted = False
        after = self.rendering().frame

        announced = (
            Emit(
                EventType.PAGE_EXITED,
                {**self._describe(departed), "depth": departed_depth, "trigger": str(trigger)},
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

        # Nowhere named on either side: a plain sideways wipe, in the colour of wherever
        # you ended up.
        return Outcome(emits=announced, animation=wipe(self.page.colour, before, after))
