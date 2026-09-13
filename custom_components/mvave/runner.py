"""Everything with a clock in it, so the engine can have none.

The engine decides what a press means and what the grid should look like. It cannot decide
*when*, because it has no clock by design, and the awkward parts of a control surface are
almost all timing: how long a press has to last to be a hold, how long a value bar stays
after the knob stops, how long to wait before admitting a lamp is never going to answer.
All of that is here, along with the translation between reading-order pads and the notes
this particular device happens to send.

Nothing here decides what anything means. If a rule about behaviour is in this file rather
than in ``engine/``, it is in the wrong place.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any

from bleak import BleakError
from homeassistant.core import callback
from homeassistant.helpers.event import async_call_later, async_track_state_change_event

from .const import LOGGER
from .devices.smc_pad import ENCODER_CENTRE, PAD_NUMBER_BY_READING_ORDER
from .engine import BUTTONS, PAD_COUNT, STEP_SECONDS, TICK_SECONDS, Frame, changed_pads, compose
from .engine.model import Profile, SourceKind
from .engine.surface import (
    ButtonPress,
    ButtonRelease,
    ButtonTiming,
    EventType,
    Idle,
    InputEvent,
    Outcome,
    Press,
    Surface,
    Turn,
)
from .registry import (
    REGISTRY_EVENTS,
    HomeAssistantRegistry,
    HomeAssistantSink,
    build_profile,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Coroutine

    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import CALLBACK_TYPE, Event, EventStateChangedData, HomeAssistant

    from .coordinator import MvaveCoordinator
    from .devices.layout import DeviceLayout
    from .transport import MidiEvent

#: How long a pad must be held before it counts as a hold rather than a tap. Long enough
#: not to fire on an ordinary press, short enough that nobody thinks it failed.
HOLD_SECONDS = 0.5

#: How long the value bar stays after the last click of a knob.
HUD_SECONDS = 0.9

#: How often a turning knob is allowed to reach the house, and how long after it stops
#: before the last word goes out.
#:
#: A throttle rather than a plain wait. Waiting until the knob was still meant a lamp did
#: not move at all while somebody was turning it and then jumped once they stopped, which
#: is not what a dimmer is for: you turn it until the room looks right, and that needs the
#: room to change while you turn. Seven or so commands a second is comfortable for Zigbee
#: and nowhere near the thirty a knob actually sends.
KNOB_THROTTLE_SECONDS = 0.15
KNOB_SETTLE_SECONDS = 0.25

#: How long to wait for an entity to report the state it was asked for. Past this the pad
#: stops moving and goes back to showing whatever is actually true, because a pad that
#: swings forever is worse than one that admits the command went nowhere.
CONFIRM_SECONDS = 6.0

#: How long a stateless pad holds its acknowledgement colour after being pressed.
#:
#: Long enough to be seen as a state rather than caught as a flash, short enough that the
#: pad is not still claiming to be acting when it has finished. Two transitions this far
#: apart are not a flash at all — a flash is a *pair* of opposing changes, and these are a
#: second apart — which keeps an acknowledgement out of the photosensitivity arithmetic
#: entirely rather than merely inside it.
#:
#: Settled at the grid, which is the only place it could be. 1.5 s read as correct but
#: overstayed — "it works, but maybe just a little less long"; 1.0 s was still a touch
#: long. Each step was judged by pressing it, not reasoned about.
#:
#: Note what pressing again does. The countdown is *restarted*, not re-fired, so hammering
#: a scene pad holds one unbroken colour rather than strobing it. That is the property that
#: makes this safe to press as fast as somebody likes, and it is why the acknowledgement is
#: a latch rather than a one-shot animation.
ACKNOWLEDGE_SECONDS = 0.8

#: Note-on, channel 1. The device ignores the channel on the LED path entirely, measured
#: on all sixteen (``docs/HARDWARE-BLE.md`` section 9.1).
NOTE_ON = 0x90

#: Velocity that lights a transport button. They are single-colour, so anything non-zero
#: does; five is the green the palette walk used.
BUTTON_ON = 5

#: What the engine's events are called on the bus.
EVENT_TYPE = "mvave_event"

#: How long to let the registries settle before asking whether the house is different.
#:
#: They move in bursts: one integration setting up rewrites dozens of entries, and a
#: restart rewrites all of them. Rebuilding on each would throw away whatever animation was
#: in flight, dozens of times, for an answer that is the same every time until the burst
#: ends.
REGISTRY_SETTLE_SECONDS = 2.0


@dataclass(frozen=True, slots=True)
class SurfaceView:
    """What the surface looks like from outside, for the entities that report it.

    A value rather than a set of accessors, because every entity that reads it wants a
    consistent answer and because comparing two of them is how the runner decides whether
    anything worth writing to the state machine has actually changed. A knob turn redraws
    thirty times a second and none of those are a state change.
    """

    page_id: str | None = None
    page_title: str | None = None
    parent_page_id: str | None = None
    source: str | None = None
    area_id: str | None = None
    depth: int = 0
    root_page_id: str | None = None
    focus: str | None = None
    #: Every page as (id, label), in the profile's own order. Labels are what a person
    #: picks from, so they are made unique here rather than in each thing that shows them.
    pages: tuple[tuple[str, str], ...] = ()
    #: Which encoders would do something right now, as (knob, entity, property). The one
    #: thing the hardware cannot say about itself, so it has to be said here.
    knobs: tuple[tuple[int, str, str], ...] = ()

    def page_for(self, label: str) -> str | None:
        """The page id behind a label somebody chose."""
        return next((page_id for page_id, shown in self.pages if shown == label), None)

    @property
    def labels(self) -> list[str]:
        """Every page, as a person sees it."""
        return [label for _, label in self.pages]

    @property
    def current_label(self) -> str | None:
        """The label of the page showing now."""
        return next(
            (label for page_id, label in self.pages if page_id == self.page_id),
            None,
        )


def page_labels(profile: Profile) -> tuple[tuple[str, str], ...]:
    """Page ids paired with names nobody can confuse for each other.

    Areas cannot share a name, so this only ever fires when a page's title collides with
    the index's, and the alternative is a picker with two identical entries where choosing
    either one gets you whichever came first.
    """
    labels: list[tuple[str, str]] = []
    taken: set[str] = set()
    for page in profile.pages.values():
        label = page.title
        if label in taken:
            # The id is unique, so one qualifier always settles it. This used to fall into
            # a `while` whose body recomputed the identical string every pass — a no-op at
            # best and a hang at worst, never the counter it was written as.
            label = f"{page.title} ({page.id})"
        taken.add(label)
        labels.append((page.id, label))
    return tuple(labels)


class SurfaceRunner:
    """Drives one profile against one device: input in, light out, timing in between."""

    def __init__(
        self, hass: HomeAssistant, entry: ConfigEntry, coordinator: MvaveCoordinator
    ) -> None:
        self.hass = hass
        #: Tasks are created through the entry rather than through hass, so Home Assistant
        #: cancels them on unload and on shutdown and does not wait for them in
        #: ``async_block_till_done``. The redraw ticker is the one that matters: it has no
        #: natural end, and on hass it would hold a shutdown open until a page timed out.
        self.entry = entry
        self.coordinator = coordinator
        self.surface: Surface | None = None
        self.sink = HomeAssistantSink(hass, entry, EVENT_TYPE, coordinator.address)

        self._notes: dict[int, int] = {}  # reading-order pad -> note it answers to
        self._pads: dict[int, int] = {}  # note -> reading-order pad
        self._buttons: dict[int, str] = {}  # controller -> button name
        self._lights: dict[str, int] = {}  # button name -> controller that lights it
        self._knobs: dict[int, int] = {}  # controller -> knob number

        self._shown: Frame | None = None
        self._lit: dict[str, bool] = {}
        self._holds: dict[str, asyncio.Task[None]] = {}
        self._fired: set[str] = set()
        self._steps: dict[int, int] = {}
        #: The last thing a turn asked for. Only the last one is worth making: a knob
        #: sends thirty messages a second and each would otherwise be a service call.
        self._pending_call: Outcome | None = None
        #: The entities currently on the grid, so the state subscription only changes
        #: when the set does.
        self._watched: set[str] = set()
        self._logged: tuple[int, str | None, str | None] | None = None
        self._logged_page: str | None = None
        #: When a knob last reached the house, so a turn can be rationed rather than
        #: waited out.
        self._last_call = 0.0
        self._timers: dict[str, CALLBACK_TYPE] = {}
        self._ticker: asyncio.Task[None] | None = None
        self._writer: asyncio.Task[None] | None = None
        self._wanted: Frame | None = None
        self._wanted_buttons: dict[str, bool] | None = None
        self._playing: asyncio.Task[None] | None = None
        #: Whether what is playing is a reaction rather than a page change. Only reactions
        #: are protected from being restarted by another reaction; a page change is always
        #: interruptible, because somebody pressing a second room means it.
        self._reacting = False
        self._watching: CALLBACK_TYPE | None = None
        self._started = time.monotonic()
        self._unsubscribe: list[CALLBACK_TYPE] = []
        #: Entities that show where the surface is. Told only when the answer changes.
        self._watchers: list[CALLBACK_TYPE] = []
        self._announced = SurfaceView()
        #: Held across every write. Both caches below are read, awaited over and then
        #: written, so two writers in flight at once would each diff against what the
        #: other has not recorded yet and the grid would end up showing neither.
        self._writing = asyncio.Lock()

    # ------------------------------------------------------------------ view

    @property
    def view(self) -> SurfaceView:
        """Where the surface is, for anything outside that reports it.

        Empty while the link is down. The entities showing it are unavailable then anyway,
        and inventing a last known page would be a claim about a device nobody is talking
        to.
        """
        surface = self.surface
        if surface is None:
            return SurfaceView()
        page = surface.page
        return SurfaceView(
            page_id=page.id,
            page_title=page.title,
            parent_page_id=page.parent_id,
            source=str(page.source.kind),
            area_id=page.source.key if page.source.kind is SourceKind.AREA else None,
            depth=surface.depth,
            root_page_id=surface.profile.root_id,
            focus=surface.focus,
            pages=page_labels(surface.profile),
            knobs=tuple(
                (knob, entity_id, prop)
                for knob, (entity_id, prop) in sorted(surface.knob_map().items())
            ),
        )

    @callback
    def async_add_listener(self, listener: CALLBACK_TYPE) -> CALLBACK_TYPE:
        """Follow where the surface is. Returns the callback that stops following."""
        self._watchers.append(listener)

        @callback
        def _remove() -> None:
            if listener in self._watchers:
                self._watchers.remove(listener)

        return _remove

    @callback
    def _announce(self) -> None:
        """Tell the watchers, but only when what they show has actually changed.

        The gate is the whole point. This is reached from every redraw, which during a
        knob turn is thirty a second, and an entity that wrote its state that often would
        put thirty rows a second into the database for a page that did not move.
        """
        view = self.view
        if view == self._announced:
            return
        if view.knobs != self._announced.knobs:
            # The one question the device cannot answer about itself, in the one place
            # somebody can go and look. "Knob two does nothing" has three possible causes
            # — nothing selected, the wrong domain, or a lamp with no white LEDs — and
            # from in front of the grid all three look identical.
            LOGGER.debug(
                "%s: live knobs: %s",
                self.coordinator.address,
                ", ".join(f"{knob}={prop} on {entity}" for knob, entity, prop in view.knobs)
                or "none",
            )
        self._announced = view
        for listener in list(self._watchers):
            listener()

    # ------------------------------------------------------------------ life
    def _task(self, work: Coroutine[Any, Any, None], what: str) -> asyncio.Task[None]:
        """Start something the config entry owns, and that says what it is in a log."""
        return self.entry.async_create_background_task(
            self.hass, work, f"mvave {self.coordinator.address} {what}", eager_start=False
        )

    @callback
    def async_start(self) -> CALLBACK_TYPE:
        """Begin. Returns the callback that stops everything again."""
        self._unsubscribe.append(self.coordinator.async_add_midi_listener(self._on_midi))
        self._unsubscribe.append(self.coordinator.async_add_listener(self._on_connection))
        for event in REGISTRY_EVENTS:
            self._unsubscribe.append(self.hass.bus.async_listen(event, self._house_changed))
        self._on_connection()
        return self.async_stop

    @callback
    def async_stop(self) -> None:
        """Cancel every timer and task. Safe to call more than once."""
        for unsubscribe in self._unsubscribe:
            unsubscribe()
        self._unsubscribe.clear()
        for cancel in self._timers.values():
            cancel()
        self._timers.clear()
        for task in (*self._holds.values(), self._ticker, self._playing, self._writer):
            if task is not None:
                task.cancel()
        self._ticker = self._playing = self._writer = None
        self._wanted = self._wanted_buttons = None
        self._forget_presses()
        # What every loop in here waits on. Anything that slipped through a cancellation
        # stops on its next turn rather than running on past the config entry.
        self.surface = None
        if self._watching is not None:
            self._watching()
            self._watching = None
        self._announce()

    @callback
    def _forget_presses(self) -> None:
        """Drop every half-finished press, because none of them can be completed now."""
        for task in self._holds.values():
            task.cancel()
        self._holds.clear()
        self._fired.clear()

    @callback
    def _on_connection(self) -> None:
        """Build or discard the surface as the link comes and goes."""
        arming = self.coordinator.arming
        if not self.coordinator.connected or arming is None:
            self.surface = None
            self._shown = None
            self._lit.clear()
            # A finger down when the link dropped never sends its release, so without this
            # its hold fires into nothing, the key stays marked as fired, and the next tap
            # of that pad is swallowed as though it were that release.
            self._forget_presses()
            self._announce()
            return
        if self.surface is None:
            if arming.layout is None:
                return
            self._learn(arming.layout)
            self.surface = Surface(
                build_profile(self.hass, self.entry), HomeAssistantRegistry(self.hass)
            )
            LOGGER.info(
                "%s: surface ready with %d pages",
                self.coordinator.address,
                len(self.surface.profile.pages),
            )
        # The grid was dark or showing something else while disconnected, so nothing about
        # what is on it can be assumed.
        self._shown = None
        self._lit.clear()
        self._redraw()

    def _learn(self, layout: DeviceLayout) -> None:
        """Take the note and controller numbers from the device rather than guessing.

        They move: the preset changes them, and so do the octave keys. The arming step has
        already read the real map out of the device's memory, and the same map is what the
        event entities match against, so there is one answer to "which pad is that" rather
        than two that can disagree.

        Matching is on the number alone, without the channel. The device ignores the
        channel on the LED path entirely, and a preset that puts pads on an unusual channel
        should still work rather than going silently dead.
        """
        by_number = {spec.number: spec for spec in layout.pads}
        self._notes = {
            index: by_number[pad].note
            for index, pad in enumerate(PAD_NUMBER_BY_READING_ORDER)
            if pad in by_number
        }
        self._pads = {note: index for index, note in self._notes.items()}

        self._buttons = {spec.cc: spec.key for spec in layout.buttons}
        self._lights = {spec.key: spec.cc for spec in layout.buttons}
        # A bank is a mode of the same physical control, so every controller a knob can
        # send on means that knob.
        self._knobs = {cc: spec.number for spec in layout.knobs for cc in spec.ccs.values()}
        LOGGER.debug(
            "%s: learned the device's own map: pads %s, buttons %s, knobs %s",
            self.coordinator.address,
            self._notes,
            self._lights,
            self._knobs,
        )

    @callback
    def _house_changed(self, _event: Any) -> None:
        """Something moved in the area, entity or device registry.

        Nothing is done yet. They move in bursts — one integration setting up rewrites
        dozens of entries — and the only question worth asking is whether the *answer*
        changed, which is worth asking once the burst is over rather than forty times
        during it.
        """
        self._restart("registry", REGISTRY_SETTLE_SECONDS, self._rebuild_if_the_house_moved)

    @callback
    def _rebuild_if_the_house_moved(self, _now: Any = None) -> None:
        """Rebuild, but only if the house is actually laid out differently now.

        Which rooms become pages is worked out once, at connect. So a room added, renamed
        or deleted after that did not reach the grid until somebody restarted Home
        Assistant — a page pointing at an area that no longer exists being the worst of
        those. What is *in* a room has always been live, because a page resolves its slots
        against the registry on every single render; it is only the set of pages that was
        frozen.

        Compared rather than assumed, because these events are frequent and rebuilding
        throws away the animation in flight.
        """
        self._timers.pop("registry", None)
        if self.surface is None:
            return
        rebuilt = build_profile(self.hass, self.entry)
        if rebuilt == self.surface.profile:
            return
        LOGGER.info("%s: the house changed, rebuilding the surface", self.coordinator.address)
        self.reconfigure(rebuilt)

    @callback
    def reconfigure(self, profile: Profile | None = None) -> None:
        """Rebuild the surface from the options, without dropping the link.

        Reloading the config entry would be the ordinary answer and would also work, but
        it disconnects the device and reconnecting costs twenty seconds. Nothing about the
        options touches the link, so nothing about the link needs to be disturbed.
        """
        if self.surface is None:
            return
        was = self.surface
        surface = Surface(
            profile or build_profile(self.hass, self.entry),
            HomeAssistantRegistry(self.hass),
        )
        # Changing a colour and being thrown back to the index is the surface losing your
        # place over something that had nothing to do with where you were. What survives is
        # the surface's own business, so it decides.
        was.carry_into(surface)
        self.surface = surface
        self._logged_page = None
        self._shown = None
        self._lit.clear()
        LOGGER.info(
            "%s: reconfigured, %d pages", self.coordinator.address, len(self.surface.profile.pages)
        )
        self._redraw()

    # -------------------------------------------------------------- outside

    def knows(self, page_id: str) -> bool:
        """Whether this surface has a page by that name."""
        return self.surface is not None and self.surface.profile.page(page_id) is not None

    def page_names(self) -> str:
        """Every page this surface has, for an error message worth reading."""
        return ", ".join(sorted(self.surface.profile.pages)) if self.surface else ""

    def drive(self, ask: Callable[[Surface], Outcome]) -> None:
        """Tell the surface to do something that nobody pressed.

        The same path a press takes, so a service call animates, announces and lights the
        transport buttons exactly as a finger would. What differs is only what the engine
        records as the cause, which is what stops an automation mistaking its own effect
        for a person.
        """
        if self.surface is None:
            return
        outcome = ask(self.surface)
        self._arm(outcome)
        self._make_way_for(outcome)
        self._settle(outcome)

    # ----------------------------------------------------------------- input

    @callback
    def _on_midi(self, event: MidiEvent) -> None:
        """One decoded message from the pad."""
        if self.surface is None:
            return
        if event.type == "note_on" and event.data1 in self._pads:
            key = f"pad:{self._pads[event.data1]}"
            self._down(key) if event.data2 else self._up(key)
        elif event.type == "note_off" and event.data1 in self._pads:
            self._up(f"pad:{self._pads[event.data1]}")
        elif event.type == "cc" and event.data1 in self._buttons:
            key = f"button:{self._buttons[event.data1]}"
            self._down(key) if event.data2 else self._up(key)
        elif event.type == "cc" and event.data1 in self._knobs:
            self._turned(self._knobs[event.data1], event.data2 - ENCODER_CENTRE)

    @callback
    def _down(self, key: str) -> None:
        self._holds[key] = self._task(self._hold(key), f"hold {key}")

    async def _hold(self, key: str) -> None:
        """Fire a hold while the finger is still down rather than when it lets go.

        Waiting for the release would mean the thing a hold does only happens once you stop
        holding, which is exactly backwards.
        """
        await asyncio.sleep(HOLD_SECONDS)
        self._fired.add(key)
        self._dispatch(_event_for(key, held=True))

    @callback
    def _up(self, key: str) -> None:
        task = self._holds.pop(key, None)
        if task is not None:
            task.cancel()
        if key in self._fired:
            self._fired.discard(key)
            kind, _, name = key.partition(":")
            if kind == "button" and name in BUTTONS:
                # A held pad is a thing that happened and is over. A held *button* is a
                # mode — the switcher hangs off one — and a mode has to be able to end.
                self._dispatch(ButtonRelease(name))
            # The finger has lifted, so whatever the hold put up can start counting down.
            # It does not count down while the finger is still there: having the grid clear
            # under your own hand is the surface deciding you have finished looking.
            if self.surface is not None and self.surface.showing and not self._fired:
                self._restart("hud", HUD_SECONDS, self._drop_hud)
            return
        self._dispatch(_event_for(key, held=False))

    @callback
    def _turned(self, knob: int, steps: int) -> None:
        """Collect a knob's steps, then make one call and one announcement for the lot.

        Every step still reaches the engine at once, so the bar follows the finger. What
        waits is everything that leaves this machine: a knob sends around thirty messages
        a second, and neither a light nor an automation wants thirty of anything out of
        one gesture.
        """
        if steps == 0:
            return
        outcome = self._handle(Turn(knob, steps))
        # Logged when the knob or its target changes, not per step: one turn is around
        # thirty messages, and thirty identical lines hide the one that matters.
        # What this knob resolved to, not what the bar happens to be showing. Those are
        # the same thing right up until a knob does nothing, when the bar is still holding
        # the last live knob's value and the log says the dead one adjusts it. Which is
        # exactly the line that made a refusal look like four refusals while reading back.
        adjusting = self.surface.knob_map().get(knob) if self.surface is not None else None
        signature = (knob, *(adjusting or (None, None)))
        if signature != self._logged:
            self._logged = signature
            LOGGER.debug(
                "%s: knob %d now adjusts %s of %s",
                self.coordinator.address,
                knob,
                signature[2] or "nothing",
                signature[1] or "nothing",
            )
        self._steps[knob] = self._steps.get(knob, 0) + steps
        # Only if it asked for something. A knob that refused carries no call, and letting
        # it become the pending one would throw away the last command of a live knob
        # turned a moment earlier.
        if outcome.calls:
            self._pending_call = outcome

        # Every step reaches the engine, so the bar follows the finger exactly. What is
        # rationed is what leaves this machine: a knob sends around thirty messages a
        # second and neither a lamp nor an automation wants thirty of anything out of one
        # gesture. The first step of a turn goes out at once, then at most one every
        # throttle interval while it keeps moving, then a last one once it stops.
        now = time.monotonic()
        if now - self._last_call >= KNOB_THROTTLE_SECONDS:
            self._flush_knobs()
        self._restart("knob", KNOB_SETTLE_SECONDS, self._flush_knobs)
        self._redraw()

    @callback
    def _flush_knobs(self, _now: Any = None) -> None:
        """Make the one call the whole turn asked for.

        This is the one timer callback that is also called *directly*, at the throttle
        boundary, which is why it takes no argument. Popping without cancelling therefore
        left a live timer behind every 0.15 s of a sustained turn; an orphan firing later
        would clear the key that `_on_state` reads as "a turn is in flight", and could
        outlive an unload and call a service for a device that is gone.
        """
        cancel = self._timers.pop("knob", None)
        if cancel is not None:
            cancel()
        moved, self._steps = self._steps, {}
        outcome, self._pending_call = self._pending_call, None
        if outcome is None:
            return
        self._last_call = time.monotonic()
        # One announcement for the whole gesture, carrying how far it actually went. The
        # engine's own event describes a single step, which is the right thing for it to
        # know and the wrong thing to put on the bus.
        self._perform(
            replace(
                outcome,
                emits=tuple(
                    replace(emit, data={**emit.data, "steps": moved[emit.data["knob"]]})
                    if emit.type is EventType.KNOB_TURNED and emit.data.get("knob") in moved
                    else emit
                    for emit in outcome.emits
                ),
            )
        )

    # ---------------------------------------------------------------- output

    def _dispatch(self, event: InputEvent | None) -> None:
        if event is None:
            return
        outcome = self._handle(event)
        self._make_way_for(outcome)
        self._settle(outcome)

    def _make_way_for(self, outcome: Outcome) -> None:
        """Stop whatever is playing, unless what is arriving must not interrupt it.

        Input is never queued behind eye candy: a press during a transition has to land
        now, not once the pretty part has finished. The exception is a reaction arriving
        while a reaction is playing — pressing a dead pad twice — which must leave the
        first one alone, because three blinks in 540 ms is already 5.6 Hz and restarting
        it puts more than three flashes into one second.

        This cancelled unconditionally until 2026-09-13, *before* the outcome was even
        computed, which made the guard in `_play` unreachable: `_playing` was always None
        by the time it was asked. The whole of that protection was dead code.
        """
        if outcome == Outcome():
            # It asked for nothing, so there is nothing for it to show and no reason to
            # stop what is. Letting go of the back button is the case: it ends the switcher
            # mode, which is already over, and its only observable effect was killing the
            # curtain that same gesture had just started — a few frames into 1.575 s, every
            # time, because a finger comes off *back* not long after tapping the room.
            return
        if outcome.reaction and self._playing is not None and self._reacting:
            return
        self._cancel_animation()

    def _settle(self, outcome: Outcome) -> None:
        """Carry out an outcome, in the order the grid needs it.

        The redraw comes last, and that matters. It skips the grid while a transition is
        playing, so if it runs before the animation has been started there is nothing yet
        for it to skip: it paints the destination, and the curtain then sweeps over a page
        that has already arrived. Which is precisely what it did until this was noticed.
        """
        self._perform(outcome)
        self._play(outcome)
        self._redraw()

    @callback
    def _cancel_animation(self) -> None:
        """Stop a transition mid-flight, leaving the grid to be redrawn as it now is."""
        if self._playing is not None:
            self._playing.cancel()
            self._playing = None
            self._reacting = False

    def _handle(self, event: InputEvent) -> Outcome:
        """Ask the engine, and set the countdowns its answer implies."""
        if self.surface is None:
            return Outcome()
        outcome = self.surface.handle(event)
        self._arm(outcome)
        return outcome

    def _arm(self, outcome: Outcome) -> None:
        """Every countdown an outcome implies, wherever the outcome came from.

        Shared with :meth:`drive` rather than duplicated there. A service call that sets a
        value bar and does not arm its expiry leaves the grid covered until a human touches
        the surface, which is what happened when this was written out twice.
        """
        if self.surface is None:
            return
        for call in outcome.calls:
            entity_id = call.data.get("entity_id")
            if isinstance(entity_id, str) and entity_id in self.surface.pending:
                self._restart(f"confirm:{entity_id}", CONFIRM_SECONDS, self._give_up(entity_id))
        for entity_id in outcome.acknowledged:
            # Restarted rather than skipped if one is already running: pressing a scene
            # again is another acknowledgement, and it should read as one.
            self._restart(f"ack:{entity_id}", ACKNOWLEDGE_SECONDS, self._release(entity_id))
        self._restart("idle", self.surface.page.idle_timeout, self._timed_out)
        # Not while a finger is still down on a pad: see `_up`.
        if self.surface.showing and not self._fired:
            self._restart("hud", HUD_SECONDS, self._drop_hud)

    def _perform(self, outcome: Outcome) -> None:
        """Do the parts of an outcome that touch the world."""
        for call in outcome.calls:
            self.sink.call(call.domain, call.service, call.data)
        for emit in outcome.emits:
            self.sink.fire(str(emit.type), {**emit.data, "address": self.coordinator.address})

    @callback
    def _play(self, outcome: Outcome) -> None:
        """Start a transition, replacing whatever was already running.

        Except that a reaction never replaces a reaction. A refusal is three blinks in
        540 ms, which is 5.6 Hz and legal only because three is the most a thing may flash
        in one second; restarting one partway through puts more than three there, and
        pressing a dead pad twice is exactly what somebody does when the first press
        appeared to do nothing. Hammering it now holds the one refusal it already has —
        the same property the acknowledgement latch has, for the same reason.
        """
        if not outcome.animation:
            return
        if outcome.reaction and self._playing is not None and self._reacting:
            return
        # The caller has already made way, via _make_way_for.
        LOGGER.debug("%s: animating %d frames", self.coordinator.address, len(outcome.animation))
        self._reacting = outcome.reaction
        self._playing = self._task(self._animate(outcome), "transition")

    async def _animate(self, outcome: Outcome) -> None:
        """Play a transition, and change the transport lights on the frame that owns them."""
        mine = asyncio.current_task()
        began = time.monotonic()
        try:
            if outcome.buttons is ButtonTiming.START:
                await self._show_buttons()
            for step, frame in enumerate(outcome.animation):
                await self._send(frame)
                if outcome.buttons is ButtonTiming.END and step == len(outcome.animation) - 1:
                    await self._show_buttons()
                await asyncio.sleep(STEP_SECONDS)
            await self._show_buttons()
        finally:
            # Only if this is still the animation in charge. A cancelled one finishes
            # after the press that replaced it, and clearing the new task here would leave
            # the next input with nothing to cancel.
            if self._playing is mine:
                self._started = time.monotonic()
                self._playing = None
                self._reacting = False
                self._redraw()
                # How long the grid was actually owned by the animation, which is the only
                # way to tell "it blinked twice" from "one blink took twice as long".
                LOGGER.debug(
                    "%s: animation done in %.0f ms",
                    self.coordinator.address,
                    (time.monotonic() - began) * 1000,
                )

    @callback
    def _redraw(self) -> None:
        """Put the settled state on the grid, and watch whatever it shows."""
        # Before the early return, not after: the link going down is exactly the moment the
        # entities reporting where the surface is need to hear that it is nowhere.
        self._announce()
        if self.surface is None or not self.coordinator.connected:
            return
        rendering = self.surface.rendering()
        self._watch(self.surface)
        self._log_page(rendering.frame)
        if self._playing is None:
            self._paint(
                compose(rendering, time.monotonic() - self._started), dict(rendering.buttons)
            )
        # A ticker is only worth its wake-ups while something is actually moving.
        if rendering.rhythms and self._ticker is None:
            self._ticker = self._task(self._tick(), "redraw ticker")
        elif not rendering.rhythms and self._ticker is not None:
            self._ticker.cancel()
            self._ticker = None

    @callback
    def _paint(self, frame: Frame, buttons: dict[str, bool]) -> None:
        """Ask for a frame, and let the writer get to it when the link is free.

        Collapsing rather than queueing. A knob turn redraws around thirty times a second,
        and each redraw used to start its own write; if the link were ever slower than the
        redraw rate that queue would grow for the length of the gesture and the grid would
        lag the finger by the whole backlog. Only the newest frame is worth sending, so
        anything still waiting when a newer one arrives is simply replaced.
        """
        self._wanted = frame
        self._wanted_buttons = buttons
        if self._writer is None or self._writer.done():
            self._writer = self._task(self._drain(), "writer")

    async def _drain(self) -> None:
        """Send whatever is wanted, newest first, until nothing is.

        Dropped rather than sent once a transition has started. A redraw asked for a moment
        earlier is a settled page, and painting one over a curtain that is halfway across
        is the same mistake as redrawing during an animation, arriving by a different
        route: the animation owns the grid while it runs.
        """
        while self._wanted is not None or self._wanted_buttons is not None:
            frame, self._wanted = self._wanted, None
            buttons, self._wanted_buttons = self._wanted_buttons, None
            if self._playing is not None:
                return
            if frame is not None:
                await self._send(frame)
            if buttons is not None:
                await self._show_buttons(buttons)

    @callback
    def _log_page(self, frame: Frame) -> None:
        """One line per page arrived at, saying what ended up on which pad.

        The question this answers is "why is that pad not what I expected", which is
        otherwise three guesses: the entity is not in the area, it was filtered out as a
        diagnostic, or it is there and its colour is not what was assumed.
        """
        page = self.surface.page.id if self.surface else None
        if page == self._logged_page or self.surface is None:
            return
        self._logged_page = page
        laid_out = [
            f"{index}:{slot.entity_id or type(slot.tap).__name__.lower()}={frame[index]}"
            for index, slot in enumerate(self.surface.slots())
            if slot is not None
        ]
        LOGGER.debug(
            "%s: page %s has %s", self.coordinator.address, page, " ".join(laid_out) or "nothing"
        )

    async def _tick(self) -> None:
        """Redraw while anything on the grid is breathing or blinking."""
        mine = asyncio.current_task()
        try:
            while self.surface is not None:
                rendering = self.surface.rendering()
                if not rendering.rhythms:
                    return
                if self._playing is None:
                    await self._send(compose(rendering, time.monotonic() - self._started))
                await asyncio.sleep(TICK_SECONDS)
        finally:
            # Only if this is still the ticker in charge, for the same reason the animation
            # checks. One notification can carry several messages, so a ticker can be
            # cancelled and another started before the first one's cancellation is
            # delivered, and clearing the slot then orphans a live thirty-a-second loop
            # that nothing afterwards can reach.
            if self._ticker is mine:
                self._ticker = None

    async def _send(self, frame: Frame) -> None:
        """Send only the pads that changed. A whole frame fits one packet either way."""
        async with self._writing:
            changes = (
                dict(enumerate(frame)) if self._shown is None else changed_pads(self._shown, frame)
            )
            if not changes:
                return
            messages = [
                bytes((NOTE_ON, self._notes[pad], value))
                for pad, value in changes.items()
                if pad in self._notes
            ]
            # Cleared *before* the write, not after it. A cancellation is delivered at the
            # resume point, so a write that has already reached the device can still skip
            # the line that records it — and the next diff against a cache claiming the old
            # frame then finds no changes and sends nothing, leaving a pad dark until
            # something else happens to move it. `_cancel_animation` runs on every input.
            self._shown = None
            try:
                await self.coordinator.async_send_many(messages)
            except (BleakError, EOFError, TimeoutError) as err:
                # The link dropping mid-frame is ordinary and the reconnect handles it.
                # What must not happen is swallowing everything: a mistake in the note map
                # would then look exactly like a dark grid with nothing wrong.
                LOGGER.debug("%s: frame not sent: %r", self.coordinator.address, err)
                return
            self._shown = frame

    async def _show_buttons(self, wanted: dict[str, bool] | None = None) -> None:
        """Light the transport buttons that would do something if pressed."""
        if self.surface is None:
            return
        if wanted is None:
            wanted = dict(self.surface.rendering().buttons)
        async with self._writing:
            messages = [
                bytes((NOTE_ON, self._lights[name], BUTTON_ON if lit else 0))
                for name, lit in wanted.items()
                if name in self._lights and self._lit.get(name) != lit
            ]
            if not messages:
                return
            # Same reasoning as the frame cache above: cleared before the write, so a
            # cancellation cannot leave this claiming a transport light it did not set.
            self._lit.clear()
            try:
                await self.coordinator.async_send_many(messages)
            except (BleakError, EOFError, TimeoutError) as err:
                LOGGER.debug("%s: buttons not sent: %r", self.coordinator.address, err)
                return
            self._lit.update(wanted)

    # ----------------------------------------------------------------- timers

    @callback
    def _restart(self, name: str, delay: float, action: Any) -> None:
        """Begin a countdown again, cancelling whatever was already counting."""
        cancel = self._timers.pop(name, None)
        if cancel is not None:
            cancel()
        if delay > 0:
            self._timers[name] = async_call_later(self.hass, delay, action)

    @callback
    def _timed_out(self, _now: Any) -> None:
        self._timers.pop("idle", None)
        self._dispatch(Idle())

    @callback
    def _drop_hud(self, _now: Any) -> None:
        self._timers.pop("hud", None)
        if self.surface is not None:
            self.surface.clear_hud()
            self._redraw()

    def _release(self, entity_id: str) -> Any:
        """Let a stateless pad stop saying it just ran."""

        @callback
        def _held_long_enough(_now: Any) -> None:
            self._timers.pop(f"ack:{entity_id}", None)
            if self.surface is not None:
                self.surface.release(entity_id)
                self._redraw()

        return _held_long_enough

    def _give_up(self, entity_id: str) -> Any:
        """Stop waiting for an entity that is never going to answer."""

        @callback
        def _expired(_now: Any) -> None:
            self._timers.pop(f"confirm:{entity_id}", None)
            if self.surface is not None:
                LOGGER.debug("%s: %s never confirmed", self.coordinator.address, entity_id)
                self.surface.settled(entity_id)
                self._redraw()

        return _expired

    # ------------------------------------------------------------ the world

    @callback
    def _watch(self, surface: Surface) -> None:
        """Follow only the entities currently on the grid, and no others.

        A global state listener on a busy instance is thousands of callbacks a second for
        a surface showing four lamps.
        """
        entities = {slot.entity_id for slot in surface.slots() if slot and slot.entity_id}
        if entities == self._watched:
            return
        if self._watching is not None:
            self._watching()
            self._watching = None
        self._watched = entities
        if entities:
            self._watching = async_track_state_change_event(
                self.hass, sorted(entities), self._on_state
            )

    @callback
    def _on_state(self, event: Event[EventStateChangedData]) -> None:
        """An entity on the grid changed, so the pad waiting on it can stop waiting."""
        if self.surface is None:
            return
        entity_id = event.data["entity_id"]
        # A bar follows its entity, but not while a knob is still turning: see `settled`.
        self.surface.settled(entity_id, follow="knob" not in self._timers)
        if LOGGER.isEnabledFor(logging.DEBUG):
            # Every state change that reaches the surface, with the moment it arrived.
            # Answers the question a slow-looking readout always raises: is the value
            # reported rarely, or reported often and shown once? Measured against Home
            # Assistant's own brightness slider, one continuous drag produces exactly one
            # state change, on release. There is nothing finer to couple to: the state
            # machine is the finest granularity Home Assistant has, and listening for
            # service calls instead catches no more, because one change is one call.
            new_state = event.data.get("new_state")
            LOGGER.debug(
                "%s: %s is %s",
                self.coordinator.address,
                entity_id,
                new_state.state if new_state else None,
            )
        cancel = self._timers.pop(f"confirm:{entity_id}", None)
        if cancel is not None:
            cancel()
        self._redraw()


def _event_for(key: str, held: bool) -> InputEvent | None:
    """Turn a pad or button key back into something the engine understands."""
    kind, _, rest = key.partition(":")
    if kind == "pad":
        pad = int(rest)
        return Press(pad, held) if 0 <= pad < PAD_COUNT else None
    return ButtonPress(rest, held) if rest in BUTTONS else None
