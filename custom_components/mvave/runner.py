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
import time
from dataclasses import replace
from typing import TYPE_CHECKING, Any

from bleak import BleakError
from homeassistant.core import callback
from homeassistant.helpers.event import async_call_later, async_track_state_change_event

from .const import LOGGER
from .devices.smc_pad import ENCODER_CENTRE, PAD_NUMBER_BY_READING_ORDER
from .engine import BUTTONS, PAD_COUNT, STEP_SECONDS, TICK_SECONDS, Frame, changed_pads, compose
from .engine.surface import (
    ButtonPress,
    ButtonTiming,
    Idle,
    InputEvent,
    Outcome,
    Press,
    Surface,
    Turn,
)
from .registry import HomeAssistantRegistry, HomeAssistantSink, build_profile

if TYPE_CHECKING:
    from collections.abc import Callable

    from homeassistant.core import CALLBACK_TYPE, Event, EventStateChangedData, HomeAssistant

    from .coordinator import MvaveCoordinator
    from .devices.layout import DeviceLayout
    from .transport import MidiEvent

#: How long a pad must be held before it counts as a hold rather than a tap. Long enough
#: not to fire on an ordinary press, short enough that nobody thinks it failed.
HOLD_SECONDS = 0.5

#: How long the value bar stays after the last click of a knob.
HUD_SECONDS = 0.9

#: How long a knob's steps are collected before one service call is made. A knob sends
#: around thirty messages a second and every one of them would otherwise be a call.
KNOB_DEBOUNCE_SECONDS = 0.25

#: How long to wait for an entity to report the state it was asked for. Past this the pad
#: stops moving and goes back to showing whatever is actually true, because a pad that
#: swings forever is worse than one that admits the command went nowhere.
CONFIRM_SECONDS = 6.0

#: Note-on, channel 1. The device ignores the channel on the LED path entirely, measured
#: on all sixteen (``docs/HARDWARE-BLE.md`` section 9.1).
NOTE_ON = 0x90

#: Velocity that lights a transport button. They are single-colour, so anything non-zero
#: does; five is the green the palette walk used.
BUTTON_ON = 5

#: What the engine's events are called on the bus.
EVENT_TYPE = "mvave_event"


class SurfaceRunner:
    """Drives one profile against one device: input in, light out, timing in between."""

    def __init__(self, hass: HomeAssistant, coordinator: MvaveCoordinator) -> None:
        self.hass = hass
        self.coordinator = coordinator
        self.surface: Surface | None = None
        self.sink = HomeAssistantSink(hass, EVENT_TYPE)

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
        self._timers: dict[str, CALLBACK_TYPE] = {}
        self._ticker: asyncio.Task[None] | None = None
        self._playing: asyncio.Task[None] | None = None
        self._watching: CALLBACK_TYPE | None = None
        self._started = time.monotonic()
        self._unsubscribe: list[CALLBACK_TYPE] = []

    # ------------------------------------------------------------------ life

    @callback
    def async_start(self) -> CALLBACK_TYPE:
        """Begin. Returns the callback that stops everything again."""
        self._unsubscribe.append(self.coordinator.async_add_midi_listener(self._on_midi))
        self._unsubscribe.append(self.coordinator.async_add_listener(self._on_connection))
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
        for task in (*self._holds.values(), self._ticker, self._playing):
            if task is not None:
                task.cancel()
        self._holds.clear()
        self._ticker = self._playing = None
        if self._watching is not None:
            self._watching()
            self._watching = None

    @callback
    def _on_connection(self) -> None:
        """Build or discard the surface as the link comes and goes."""
        arming = self.coordinator.arming
        if not self.coordinator.connected or arming is None:
            self.surface = None
            self._shown = None
            self._lit.clear()
            return
        if self.surface is None:
            if arming.layout is None:
                return
            self._learn(arming.layout)
            self.surface = Surface(build_profile(self.hass), HomeAssistantRegistry(self.hass))
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
        self._cancel_animation()
        outcome = ask(self.surface)
        self._restart("idle", self.surface.page.idle_timeout, self._timed_out)
        self._redraw()
        self._perform(outcome)
        self._play(outcome)

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
        self._holds[key] = self.hass.async_create_task(self._hold(key), eager_start=False)

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
            return
        self._dispatch(_event_for(key, held=False))

    @callback
    def _turned(self, knob: int, steps: int) -> None:
        """Collect a knob's steps, then make one call for the lot.

        Every step still reaches the engine at once, so the bar follows the finger. Only
        the service call waits, because a knob sends about thirty messages a second and
        nobody wants thirty light commands out of one gesture.
        """
        if steps == 0:
            return
        outcome = self._handle(Turn(knob, steps))
        # Logged when the knob or its target changes, not per step: one turn is around
        # thirty messages, and thirty identical lines hide the one that matters.
        showing = self.surface.hud if self.surface is not None else None
        signature = (
            knob,
            showing.entity_id if showing else None,
            showing.property_key if showing else None,
        )
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
        # The announcement goes out per step and only the call waits. An automation
        # watching a knob wants to see it move, and thirty light commands out of one
        # gesture is the thing the debounce exists to prevent.
        self._perform(replace(outcome, calls=()))
        self._restart("knob", KNOB_DEBOUNCE_SECONDS, self._flush_knobs)
        self._restart("hud", HUD_SECONDS, self._drop_hud)
        self._pending_call = outcome

    @callback
    def _flush_knobs(self, _now: Any = None) -> None:
        """Make the one call the whole turn asked for."""
        self._timers.pop("knob", None)
        self._steps.clear()
        if self._pending_call is not None:
            self._perform(replace(self._pending_call, emits=()))
            self._pending_call = None

    # ---------------------------------------------------------------- output

    def _dispatch(self, event: InputEvent | None) -> None:
        if event is None:
            return
        # Whatever was playing is over. Input is never queued behind eye candy: a press
        # during a transition has to land now, not once the pretty part has finished.
        self._cancel_animation()
        outcome = self._handle(event)
        self._perform(outcome)
        self._play(outcome)

    @callback
    def _cancel_animation(self) -> None:
        """Stop a transition mid-flight, leaving the grid to be redrawn as it now is."""
        if self._playing is not None:
            self._playing.cancel()
            self._playing = None

    def _handle(self, event: InputEvent) -> Outcome:
        """Ask the engine, and set the timers its answer implies."""
        if self.surface is None:
            return Outcome()
        outcome = self.surface.handle(event)
        for call in outcome.calls:
            entity_id = call.data.get("entity_id")
            if isinstance(entity_id, str) and entity_id in self.surface.pending:
                self._restart(f"confirm:{entity_id}", CONFIRM_SECONDS, self._give_up(entity_id))
        self._restart("idle", self.surface.page.idle_timeout, self._timed_out)
        if self.surface.hud is not None:
            self._restart("hud", HUD_SECONDS, self._drop_hud)
        self._redraw()
        return outcome

    def _perform(self, outcome: Outcome) -> None:
        """Do the parts of an outcome that touch the world."""
        for call in outcome.calls:
            self.sink.call(call.domain, call.service, call.data)
        for emit in outcome.emits:
            self.sink.fire(str(emit.type), {**emit.data, "address": self.coordinator.address})

    @callback
    def _play(self, outcome: Outcome) -> None:
        """Start a transition, replacing whatever was already running."""
        if not outcome.animation:
            return
        self._cancel_animation()
        self._playing = self.hass.async_create_task(self._animate(outcome), eager_start=False)

    async def _animate(self, outcome: Outcome) -> None:
        """Play a transition, and change the transport lights on the frame that owns them."""
        mine = asyncio.current_task()
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
                self._redraw()

    @callback
    def _redraw(self) -> None:
        """Put the settled state on the grid, and watch whatever it shows."""
        if self.surface is None or not self.coordinator.connected:
            return
        rendering = self.surface.rendering()
        self._watch(self.surface)
        if self._playing is None:
            frame = compose(rendering, time.monotonic() - self._started)
            # Rendered once and handed to both. A knob turn redraws around thirty times a
            # second, and each render re-resolves every slot of the page.
            self.hass.async_create_task(self._send(frame), eager_start=False)
            self.hass.async_create_task(
                self._show_buttons(dict(rendering.buttons)), eager_start=False
            )
        # A ticker is only worth its wake-ups while something is actually moving.
        if rendering.rhythms and self._ticker is None:
            self._ticker = self.hass.async_create_task(self._tick(), eager_start=False)
        elif not rendering.rhythms and self._ticker is not None:
            self._ticker.cancel()
            self._ticker = None

    async def _tick(self) -> None:
        """Redraw while anything on the grid is breathing or blinking."""
        try:
            while self.surface is not None:
                rendering = self.surface.rendering()
                if not rendering.rhythms:
                    return
                if self._playing is None:
                    await self._send(compose(rendering, time.monotonic() - self._started))
                await asyncio.sleep(TICK_SECONDS)
        finally:
            self._ticker = None

    async def _send(self, frame: Frame) -> None:
        """Send only the pads that changed. A whole frame fits one packet either way."""
        if self._shown is None:
            changes = dict(enumerate(frame))
        else:
            changes = changed_pads(self._shown, frame)
        if not changes:
            return
        messages = [
            bytes((NOTE_ON, self._notes[pad], value))
            for pad, value in changes.items()
            if pad in self._notes
        ]
        try:
            await self.coordinator.async_send_many(messages)
        except (BleakError, EOFError, TimeoutError) as err:
            # The link dropping mid-frame is ordinary and the reconnect handles it. What
            # must not happen is swallowing everything: a mistake in the note map would
            # then look exactly like a dark grid with nothing wrong.
            LOGGER.debug("%s: frame not sent: %r", self.coordinator.address, err)
            self._shown = None
            return
        self._shown = frame

    async def _show_buttons(self, wanted: dict[str, bool] | None = None) -> None:
        """Light the transport buttons that would do something if pressed."""
        if self.surface is None:
            return
        if wanted is None:
            wanted = dict(self.surface.rendering().buttons)
        messages = [
            bytes((NOTE_ON, self._lights[name], BUTTON_ON if lit else 0))
            for name, lit in wanted.items()
            if name in self._lights and self._lit.get(name) != lit
        ]
        if not messages:
            return
        try:
            await self.coordinator.async_send_many(messages)
        except (BleakError, EOFError, TimeoutError) as err:
            LOGGER.debug("%s: buttons not sent: %r", self.coordinator.address, err)
            self._lit.clear()
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
        self.surface.settled(entity_id)
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
