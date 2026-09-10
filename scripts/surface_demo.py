#!/usr/bin/env python3
"""Drive the real pad with the real engine, against a pretend house.

Everything below the engine is real: the pad, the link through the proxy, the arming, the
palette, the frames, the animations, the transport button LEDs, and the presses coming
back. Only the house is invented, because the engine's side of Home Assistant is not
written yet. Four rooms of made-up lamps, and toggling one really does flip it, so a press
changes a colour.

What this is for is the half that no unit test can check: whether the surface is pleasant
to use. Whether a room arrives fast enough. Whether the blink while a lamp is being
switched is reassuring or irritating. Whether an unreachable pad is obvious. Whether
holding a pad to point the knobs at it is discoverable or a secret.

Run it with Home Assistant stopped: the pad accepts one central at a time.

    python3 scripts/surface_demo.py --proxy 192.168.69.35
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import sys
import time
from collections.abc import Coroutine
from dataclasses import dataclass, field
from pathlib import Path

import bleak

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(REPO_ROOT / "custom_components" / "mvave"))

from devices.smc_pad import (  # noqa: E402
    FACTORY_BUTTONS,
    FACTORY_PAD_FIRST_NOTE,
    PAD_NUMBER_BY_READING_ORDER,
)
from engine import (  # noqa: E402
    BLUE,
    GREEN,
    ORANGE,
    PURPLE,
    RED,
    STEP_SECONDS,
    TICK_SECONDS,
    EntityState,
    Frame,
    PadConfig,
    Page,
    Profile,
    Source,
    SourceKind,
    compose,
)
from engine.surface import ButtonPress, ButtonTiming, Idle, Outcome, Press, Surface  # noqa: E402
from preview_leds import MIDI_CHAR, Grid, connect  # noqa: E402
from transport import ParserState, parse_ble_midi  # noqa: E402

#: How long a pad has to be held before it counts as a hold rather than a tap.
HOLD_SECONDS = 0.5

#: How long the pretend house takes to answer, so the blink that means "asked but not
#: confirmed yet" is actually visible. A real Zigbee lamp is in this range.
LATENCY_SECONDS = 0.45

#: Reading-order position of each pad, by the note it sends. Pad 1 is the bottom left on
#: this device, so this is not the identity mapping.
READING_BY_NOTE = {
    FACTORY_PAD_FIRST_NOTE + pad - 1: index for index, pad in enumerate(PAD_NUMBER_BY_READING_ORDER)
}

#: Transport buttons by the controller number they send, and by their position in the
#: device's own record order, which is what lights them.
BUTTON_BY_CC = {cc: key for key, _, cc in FACTORY_BUTTONS}
BUTTON_INDEX = {key: index for index, (key, _, _) in enumerate(FACTORY_BUTTONS)}


# ----------------------------------------------------------------- a pretend house

HOUSE = {
    "living": ("light.lamp", "light.floor", "media_player.tv", "switch.fan"),
    "kitchen": ("light.counter", "light.table", "switch.kettle", "scene.dinner"),
    "bedroom": ("light.bed", "cover.blind", "switch.heater", "light.broken"),
    "office": ("light.desk", "light.monitor", "script.focus"),
}

STATES = {
    "light.lamp": "on",
    "light.floor": "off",
    "media_player.tv": "off",
    "switch.fan": "off",
    "light.counter": "on",
    "light.table": "on",
    "switch.kettle": "off",
    "scene.dinner": "unknown",
    "light.bed": "off",
    "cover.blind": "closed",
    "switch.heater": "on",
    # Deliberately unreachable, to see what that looks like next to the others.
    "light.broken": "unavailable",
    "light.desk": "on",
    "light.monitor": "off",
    "script.focus": "off",
}

PROFILE = Profile(
    pages={
        "home": Page("home", "Home", BLUE, source=Source(SourceKind.PAGES), idle_timeout=0),
        "living": Page("living", "Living room", ORANGE, source=Source(SourceKind.AREA, "living")),
        "kitchen": Page("kitchen", "Kitchen", GREEN, source=Source(SourceKind.AREA, "kitchen")),
        "bedroom": Page("bedroom", "Bedroom", RED, source=Source(SourceKind.AREA, "bedroom")),
        "office": Page(
            "office",
            "Office",
            PURPLE,
            source=Source(SourceKind.AREA, "office"),
            pads={15: PadConfig()},
        ),
    },
    root_id="home",
)


@dataclass
class World:
    """A house made of a dictionary, standing in for Home Assistant."""

    states: dict[str, str] = field(default_factory=lambda: dict(STATES))

    def entities_in_area(self, area_id: str) -> tuple[str, ...]:
        return HOUSE.get(area_id, ())

    def entities_with_label(self, label: str) -> tuple[str, ...]:
        return ()

    def state_of(self, entity_id: str) -> EntityState | None:
        state = self.states.get(entity_id)
        return None if state is None else EntityState(entity_id, state)

    def apply(self, entity_id: str) -> None:
        """What a service call would eventually do."""
        current = self.states.get(entity_id)
        if current in ("unavailable", "unknown", None):
            return
        if entity_id.startswith("cover."):
            self.states[entity_id] = "closed" if current == "open" else "open"
        elif entity_id.startswith("media_player."):
            self.states[entity_id] = "off" if current != "off" else "playing"
        else:
            self.states[entity_id] = "off" if current == "on" else "on"


# --------------------------------------------------------------------- the runner


class Runner:
    """Everything asynchronous, so the engine can stay synchronous and pure."""

    def __init__(self, grid: Grid, surface: Surface, world: World) -> None:
        self.grid = grid
        self.surface = surface
        self.world = world
        self.queue: asyncio.Queue[Outcome] = asyncio.Queue()
        self._sent: Frame | None = None
        self._buttons: dict[str, bool] = {}
        self.parser = ParserState()
        self._holds: dict[str, asyncio.Task[None]] = {}
        self._fired: set[str] = set()
        #: Background work still running, held so it is not garbage collected.
        self._tasks: set[asyncio.Task[None]] = set()
        self._idle: asyncio.Task[None] | None = None

    # ------------------------------------------------------------- output

    async def show(self, frame: Frame) -> None:
        """Send a frame, but only if it differs from the one already on the grid."""
        if frame != self._sent:
            await self.grid.draw(list(frame))
            self._sent = frame

    async def show_buttons(self, wanted: dict[str, bool]) -> None:
        for name, lit in wanted.items():
            if self._buttons.get(name) != lit:
                await self.grid.button(BUTTON_INDEX[name], 5 if lit else 0)
                self._buttons[name] = lit

    async def play(self, outcome: Outcome) -> None:
        """One transition, at the pace the hardware and the eye agreed on."""
        rendering = self.surface.rendering()
        if outcome.buttons is ButtonTiming.START:
            await self.show_buttons(dict(rendering.buttons))
        for step, frame in enumerate(outcome.animation):
            await self.show(frame)
            if outcome.buttons is ButtonTiming.END and step == len(outcome.animation) - 1:
                await self.show_buttons(dict(rendering.buttons))
            await asyncio.sleep(STEP_SECONDS)
        await self.show_buttons(dict(rendering.buttons))

    async def render_loop(self) -> None:
        """Hold the page, animate anything on it, and play transitions as they arrive."""
        started = time.monotonic()
        while True:
            try:
                outcome = self.queue.get_nowait()
            except asyncio.QueueEmpty:
                rendering = self.surface.rendering()
                await self.show(compose(rendering, time.monotonic() - started))
                await self.show_buttons(dict(rendering.buttons))
                await asyncio.sleep(TICK_SECONDS)
                continue
            await self.play(outcome)
            started = time.monotonic()

    # -------------------------------------------------------------- input

    def on_midi(self, _characteristic: object, data: bytearray) -> None:
        """Decode one notification and turn it into presses."""
        for event in parse_ble_midi(bytes(data), self.parser):
            if event.type == "note_on" and event.data1 in READING_BY_NOTE:
                key = f"pad:{READING_BY_NOTE[event.data1]}"
                if event.data2:
                    self._begin(key)
                else:
                    self._end(key)
            elif event.type == "note_off" and event.data1 in READING_BY_NOTE:
                self._end(f"pad:{READING_BY_NOTE[event.data1]}")
            elif event.type == "cc" and event.data1 in BUTTON_BY_CC:
                key = f"button:{BUTTON_BY_CC[event.data1]}"
                self._begin(key) if event.data2 else self._end(key)

    def _begin(self, key: str) -> None:
        self._holds[key] = asyncio.create_task(self._hold(key))

    async def _hold(self, key: str) -> None:
        """Fire a hold while the finger is still down, rather than on release.

        Waiting for the release would mean a hold only happens once you let go, which is
        exactly backwards: the point of a hold is that something happens while you keep
        pressing.
        """
        await asyncio.sleep(HOLD_SECONDS)
        self._fired.add(key)
        self.dispatch(key, held=True)

    def _end(self, key: str) -> None:
        task = self._holds.pop(key, None)
        if task is not None:
            task.cancel()
        if key in self._fired:
            self._fired.discard(key)
            return
        self.dispatch(key, held=False)

    def dispatch(self, key: str, held: bool) -> None:
        """Hand one input to the engine and queue whatever it asks for."""
        kind, _, rest = key.partition(":")
        event = Press(int(rest), held) if kind == "pad" else ButtonPress(rest, held)
        outcome = self.surface.handle(event)
        print(f"  {event} -> {self.surface.page.title}", flush=True)

        for call in outcome.calls:
            entity_id = call.data.get("entity_id")
            print(f"    call {call.domain}.{call.service} {entity_id}", flush=True)
            if isinstance(entity_id, str):
                self._spawn(self._answer(entity_id))
        for emit in outcome.emits:
            print(f"    event {emit.tag}", flush=True)
        if outcome.animation:
            self.queue.put_nowait(outcome)
        self._restart_idle()

    def _spawn(self, work: Coroutine[None, None, None]) -> None:
        """Run something in the background and keep hold of it until it finishes."""
        task = asyncio.create_task(work)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def _restart_idle(self) -> None:
        """Begin the page's idle countdown again, because something just happened."""
        if self._idle is not None:
            self._idle.cancel()
        timeout = self.surface.page.idle_timeout
        self._idle = asyncio.create_task(self._wait_idle(timeout)) if timeout else None

    async def _wait_idle(self, timeout: float) -> None:
        """Go home after long enough, quietly."""
        await asyncio.sleep(timeout)
        outcome = self.surface.handle(Idle())
        print(f"  idle -> {self.surface.page.title}", flush=True)
        if outcome.animation:
            self.queue.put_nowait(outcome)

    async def _answer(self, entity_id: str) -> None:
        """The house getting round to it, a beat later."""
        await asyncio.sleep(LATENCY_SECONDS)
        self.world.apply(entity_id)
        self.surface.settled(entity_id)

    # -------------------------------------------------------------- setup

    async def start(self, client: bleak.BleakClient) -> None:
        """Begin listening. Presses arrive from here on."""
        await client.start_notify(MIDI_CHAR, self.on_midi)
        self._restart_idle()


# ----------------------------------------------------------------------- plumbing


async def main() -> None:
    """Arm the pad, put the index on it, and hand it over."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--proxy", required=True)
    parser.add_argument("--address")
    parser.add_argument("--slot", type=int, default=0)
    parser.add_argument("--bank", type=int, default=3)
    args = parser.parse_args()

    client, connection = await connect(args.proxy, args.address)
    grid = Grid(client, FACTORY_PAD_FIRST_NOTE, args.slot, args.bank)
    world = World()
    runner = Runner(grid, Surface(PROFILE, world), world)
    try:
        print("arming the grid and the transport buttons")
        await grid.arm()
        await grid.arm_buttons()
        await grid.dark()
        await runner.start(client)
        print(
            "\nready. Press a room on the top row to go in, left to come back, "
            "stop for home.\nHold a lamp for half a second to point the knobs at it. "
            "Ctrl-C to stop.\n"
        )
        await runner.render_loop()
    finally:
        with contextlib.suppress(Exception):
            await client.stop_notify(MIDI_CHAR)
            await grid.dark()
        await client.disconnect()
        await connection.stop()


if __name__ == "__main__":
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(main())
