#!/usr/bin/env python3
"""Hold the pad's link open and take one instruction at a time from a file.

`preview_leds.py` plays a fixed programme and asks its questions in a rush. This does the
opposite: it connects once, then does nothing until a line of text appears in a command
file, so a person can look at one frame, say what they see, and only then be shown the
next one. Reconnecting between questions would cost twenty seconds each time, which is
what makes the batch script feel like a batch script.

Start it in the background, then drive it by appending lines:

    python3 scripts/led_console.py --proxy 192.168.69.35 --commands /tmp/leds &
    echo "frame 5 0 0 0 0 0 0 0 0 0 0 0 0 0 0 14" >> /tmp/leds

Commands, one per line. Pads are numbered in reading order, 0 at the top left:

    frame v0 v1 ... v15   sixteen palette indices, 0 for off
    mod <pad> breathe|blink [index]   overlay a rhythm on that pad
    mod clear             drop every overlay
    bar <percent> <index> [track]     a bottom-up bar with a blinking boundary pad
    ripple <index>        rings from the bottom left, repeating
    arm <pad> ...         hand pads back to host note-on control
    disarm <pad> ...      hand pads back to their stored 24-bit colour
    rgb <pad> r g b       write a 24-bit colour, visible only while disarmed
    off                   everything dark
    quit                  disconnect and exit
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(REPO_ROOT / "custom_components" / "mvave"))

from devices.smc_pad import PAD_COUNT  # noqa: E402
from preview_leds import OFF, Grid, connect, duty, level_frame  # noqa: E402

#: Sixty frames a second, which the link sustains, so a pad can be dithered fast
#: enough for the eye to average it into a level.
FPS = 120

#: The rhythms the language uses, as period in seconds and the fraction lit. Breathing
#: is slow and lopsided, alarm is fast and even, so they are told apart by rhythm rather
#: than by rate alone.
RHYTHMS = {
    "breathe": (1.4, 0.65),
    "blink": (0.18, 0.5),
    # Faster than the eye integrates, so these read as a level rather than as motion. The
    # palette has no brightness channel, and this is the only way to imitate one: switch
    # the pad on and off within a frame or two and let the eye average it. Whether that
    # actually looks dim rather than flickering is a question only a person can answer.
    "dim67": (3 / FPS, 2 / 3),
    "dim50": (2 / FPS, 0.5),
    "dim33": (3 / FPS, 1 / 3),
}


class Console:
    """What the grid is currently showing, and the clock the rhythms run against."""

    def __init__(self) -> None:
        self.base: list[int] = [OFF] * PAD_COUNT
        #: The page that was showing before the current one. A curtain closing has to
        #: cover the old page rather than a blank grid, or everything vanishes before
        #: the animation has said anything.
        self.previous: list[int] = [OFF] * PAD_COUNT
        #: True while a batch of commands is being applied, so the renderer does not
        #: flash an intermediate state between two lines that arrived together.
        self.batching = False
        #: Deferred commands still waiting to fire, held so they are not garbage
        #: collected mid-sleep.
        self.tasks: set[asyncio.Task[None]] = set()
        self.mods: dict[int, tuple[str, int]] = {}
        self.special: Callable[[float], list[int]] | None = None
        #: When set, the animation is a one-shot: it plays for this many seconds and
        #: then the page underneath comes back, which is what entering a room does.
        self.special_until: float | None = None
        self.start = time.monotonic()
        self.running = True
        #: While paused the renderer leaves the grid alone, so a hand-built frame
        #: survives instead of being overwritten thirty times a second.
        self.paused = False
        #: Print how long each frame was actually on screen, to tell a genuinely
        #: uneven animation from one that only looks it.
        self.trace = False

    def set_frame(self, frame: list[int]) -> None:
        """Show a static frame, dropping any overlay or animation."""
        self.previous = self.base
        self.base = (frame + [OFF] * PAD_COUNT)[:PAD_COUNT]
        self.mods.clear()
        self.special = None
        self.special_until = None
        self.start = time.monotonic()

    def build(self) -> list[int]:
        """The frame to show right now."""
        elapsed = time.monotonic() - self.start
        if self.special is not None:
            if self.special_until is not None and elapsed >= self.special_until:
                self.special = None
                self.special_until = None
            else:
                return self.special(elapsed)
        frame = list(self.base)
        for pad, (kind, colour) in self.mods.items():
            period, lit = RHYTHMS[kind]
            frame[pad] = colour if duty(elapsed, period, lit) else OFF
        return frame


#: Seconds a ring is held, and how many steps the cycle runs for. Four rings is the whole
#: grid: the furthest pad from a corner is three steps away, counted the way a king moves.
#: A longer cycle is blank frames, which read as the animation stuttering rather than as a
#: pause, and fifty milliseconds a ring was too fast to follow.
RING_SECONDS = 0.15
RING_STEPS = 6


def ripple_builder(colour: int) -> Callable[[float], list[int]]:
    """Rings expanding from the bottom-left pad, then a short rest."""

    def build(elapsed: float) -> list[int]:
        radius = int(elapsed / RING_SECONDS) % RING_STEPS
        frame = []
        for row in range(4):
            for column in range(4):
                distance = max(abs(row - 3), abs(column - 0))
                frame.append(colour if distance == radius else OFF)
        return frame

    return build


#: A flood is slower than a ring because every step is brighter than the last, so the eye
#: has something to follow rather than a single moving edge. The cycle is four steps
#: filling outward from the origin, a short hold, four steps draining from the same
#: corner, then a rest, so entering and leaving a page share one direction.
FLOOD_SECONDS = 0.2
FLOOD_FILL = 4
FLOOD_HOLD = 2
FLOOD_REST = 2
FLOOD_STEPS = FLOOD_FILL * 2 + FLOOD_HOLD + FLOOD_REST


def flood_builder(colour: int) -> Callable[[float], list[int]]:
    """The origin's colour spreading outward and staying lit, rather than a moving ring.

    A ring is one pad wide at the corner and seven at the far edge, so each step puts a
    different amount of light on the grid and they cannot read as equal in length. Filling
    grows monotonically, which is what a page arriving should look like.
    """

    def lit(distance: int, step: int) -> bool:
        """Whether a pad this far from the origin is lit at this step of the cycle."""
        if step < FLOOD_FILL:
            return distance <= step
        if step < FLOOD_FILL + FLOOD_HOLD:
            return True
        if step < FLOOD_FILL * 2 + FLOOD_HOLD:
            return distance > step - FLOOD_FILL - FLOOD_HOLD
        return False

    def build(elapsed: float) -> list[int]:
        step = int(elapsed / FLOOD_SECONDS) % FLOOD_STEPS
        frame = []
        for row in range(4):
            for column in range(4):
                distance = max(abs(row - 3), abs(column - 0))
                frame.append(colour if lit(distance, step) else OFF)
        return frame

    return build


#: A wipe lights one column at a time, so every step changes exactly four pads. Filling
#: from a corner cannot do that: its first step adds three pads and its last adds seven,
#: so the area accelerates even though the timing is even, and it reads as uneven.
WIPE_SECONDS = 0.2
WIPE_COLUMNS = 4


def wipe_builder(colour: int) -> Callable[[float], list[int]]:
    """Columns filling left to right, a hold, then emptying in the same direction."""

    def lit(column: int, step: int) -> bool:
        """Whether this column is lit at this step of the cycle."""
        if step < WIPE_COLUMNS:
            return column <= step
        return column >= step - WIPE_COLUMNS + 1

    def build(elapsed: float) -> list[int]:
        # Exactly eight steps, no holds. A step that lingers at full or at dark is twice
        # as long as its neighbours, which is measurable on the wire and reads as uneven.
        step = int(elapsed / WIPE_SECONDS) % (WIPE_COLUMNS * 2)
        return [colour if lit(column, step) else OFF for _ in range(4) for column in range(4)]

    return build


def reveal_builder(console: Console, seconds: float, colour: int) -> Callable[[float], list[int]]:
    """The room's colour closes over the grid, then opens again with the page behind it.

    Two passes in the same direction. The first says which room you asked for, in one
    unmistakable colour across the whole grid. The second uncovers what is in it. Painting
    a colour and then swapping to the page in a single frame wastes the animation, because
    the page arrives all at once at the end instead of arriving as the animation.
    """

    def value(row: int, column: int, step: int) -> int:
        index = row * 4 + column
        if step < WIPE_COLUMNS:
            # Closing: the colour sweeps in from the left, and the page you are leaving
            # stays lit ahead of it until it is covered.
            return colour if column <= step else console.previous[index]
        # Opening: it retreats the same way, and the page you asked for is behind it.
        return console.base[index] if column < step - WIPE_COLUMNS + 1 else colour

    def build(elapsed: float) -> list[int]:
        step = int(elapsed / seconds)
        return [value(row, column, step) for row in range(4) for column in range(4)]

    return build


def ring_distances(origin: int) -> list[int]:
    """How many rings out each pad is from the origin, counted the way a king moves."""
    origin_row, origin_column = divmod(origin, 4)
    return [
        max(abs(row - origin_row), abs(column - origin_column))
        for row in range(4)
        for column in range(4)
    ]


def expand_rings(origin: int) -> int:
    """How many steps one pass takes from this origin. A corner is further than a middle."""
    return max(ring_distances(origin)) + 1


def expand_builder(
    console: Console, seconds: float, colour: int, origin: int
) -> Callable[[float], list[int]]:
    """Cover the grid outward from one pad in growing squares, then uncover it the same way.

    The origin is the pad that was pressed, so the animation starts under the finger and
    the page it opens onto appears from there too. The page being left stays lit ahead of
    the curtain, and the page being entered is already real behind it.

    Closing advances a whole ring at a time, so each step adds a different number of pads,
    one then three then five then seven from a corner. Covering a fixed number instead
    keeps the timing even but leaves the growing square unfinished halfway through every
    step, which is more obvious than the uneven area.
    """
    distances = ring_distances(origin)
    rings = expand_rings(origin)

    def build(elapsed: float) -> list[int]:
        step = int(elapsed / seconds)
        frame = []
        for index, distance in enumerate(distances):
            if step < rings:
                frame.append(colour if distance <= step else console.previous[index])
            else:
                # Opening is a column wipe rather than the same rings in reverse. Closing
                # answers "which pad did I press"; opening answers "what is in here", and
                # left to right is how the grid is read.
                revealed = index % 4 < step - rings + 1
                frame.append(console.base[index] if revealed else colour)
        return frame

    return build


def collapse_builder(
    console: Console, seconds: float, colour: int, target: int
) -> Callable[[float], list[int]]:
    """The mirror of expanding: close right to left, then shrink back into one pad.

    Leaving a page should undo entering it. The curtain closes against the reading
    direction, then contracts toward the pad the page occupies on the index, so the last
    thing lit before the page you are returning to is exactly the pad you first pressed.
    """
    distances = ring_distances(target)
    rings = expand_rings(target)

    def build(elapsed: float) -> list[int]:
        step = int(elapsed / seconds)
        frame = []
        for index, distance in enumerate(distances):
            if step < WIPE_COLUMNS:
                covered = index % 4 >= WIPE_COLUMNS - 1 - step
                frame.append(colour if covered else console.previous[index])
            else:
                covered = distance <= rings - 2 - (step - WIPE_COLUMNS)
                frame.append(colour if covered else console.base[index])
        return frame

    return build


def frozen_builder(
    animation: Callable[[float], list[int]], step: int
) -> Callable[[float], list[int]]:
    """Hold one step of an animation indefinitely, so it can be walked by hand.

    Half a step in, so the frame shown is unambiguously that step and not its boundary.
    """

    def build(_elapsed: float) -> list[int]:
        return animation(step + 0.5)

    return build


def bar_builder(percent: float, colour: int, track: int) -> Callable[[float], list[int]]:
    """A bottom-up bar whose boundary pad blinks for the half step."""

    def build(elapsed: float) -> list[int]:
        return level_frame(percent / 100, colour, duty(elapsed, 0.4, 0.5), track)

    return build


async def apply(console: Console, grid: Grid, line: str) -> None:
    """Act on one command line. Unknown or malformed lines are reported, never fatal."""
    parts = line.split()
    command, arguments = parts[0].lower(), parts[1:]
    numbers = [int(value) for value in arguments if value.lstrip("-").isdigit()]

    if command == "quit":
        console.running = False
    elif command == "off":
        console.set_frame([OFF] * PAD_COUNT)
    elif command == "frame":
        console.set_frame(numbers)
    elif command == "mod" and arguments[:1] == ["clear"]:
        console.mods.clear()
    elif command == "mod":
        pad, kind = int(arguments[0]), arguments[1].lower()
        colour = int(arguments[2]) if len(arguments) > 2 else (console.base[pad] or 15)
        console.mods[pad] = (kind, colour)
    elif command == "bar":
        track = numbers[2] if len(numbers) > 2 else OFF
        console.special = bar_builder(numbers[0], numbers[1], track)
        console.start = time.monotonic()
    elif command == "chan":
        # The same note and velocity on all sixteen channels, one per pad. If the channel
        # means nothing the grid comes up uniform; any variation is a second control axis.
        console.paused = True
        velocity = numbers[0] if numbers else 15
        for pad in range(PAD_COUNT):
            await grid.note(pad, pad + 1, velocity)
    elif command == "armbuttons":
        await grid.arm_buttons()
    elif command == "button":
        await grid.button(numbers[0], numbers[1] if len(numbers) > 1 else 5)
    elif command == "buttons":
        for index in range(5):
            await grid.button(index, numbers[0] if numbers else 5)
    elif command == "defer":
        # Anything that is not a frame, the transport buttons above all, has to be able to
        # land *with* a particular frame of an animation rather than before or after it.
        delay = numbers[0] / 1000
        rest = line.split(maxsplit=2)[2]

        async def later() -> None:
            await asyncio.sleep(delay)
            await apply(console, grid, rest)

        task = asyncio.create_task(later())
        console.tasks.add(task)
        task.add_done_callback(console.tasks.discard)
    elif command == "resume":
        console.paused = False
    elif command == "trace":
        console.trace = arguments[:1] != ["off"]
    elif command == "ripple":
        console.special = ripple_builder(numbers[0] if numbers else 21)
        console.start = time.monotonic()
    elif command == "flood":
        console.special = flood_builder(numbers[0] if numbers else 21)
        console.start = time.monotonic()
    elif command == "expand":
        seconds = (numbers[0] if numbers else 350) / 1000
        colour = numbers[1] if len(numbers) > 1 else 21
        origin = numbers[2] if len(numbers) > 2 else 0
        console.special = expand_builder(console, seconds, colour, origin)
        console.special_until = seconds * (expand_rings(origin) + WIPE_COLUMNS)
        console.start = time.monotonic()
    elif command == "estep":
        colour = numbers[1] if len(numbers) > 1 else 21
        origin = numbers[2] if len(numbers) > 2 else 0
        console.special = frozen_builder(expand_builder(console, 1.0, colour, origin), numbers[0])
        console.special_until = None
        console.start = time.monotonic()
    elif command == "collapse":
        seconds = (numbers[0] if numbers else 350) / 1000
        colour = numbers[1] if len(numbers) > 1 else 21
        target = numbers[2] if len(numbers) > 2 else 0
        console.special = collapse_builder(console, seconds, colour, target)
        console.special_until = seconds * (WIPE_COLUMNS + expand_rings(target))
        console.start = time.monotonic()
    elif command == "reveal":
        seconds = (numbers[0] if numbers else 350) / 1000
        console.special = reveal_builder(console, seconds, numbers[1] if len(numbers) > 1 else 21)
        console.special_until = seconds * WIPE_COLUMNS * 2
        console.start = time.monotonic()
    elif command in ("wipe", "wipe1"):
        console.special = wipe_builder(numbers[0] if numbers else 21)
        # "wipe1" plays the whole thing once and then hands the grid back to the page,
        # which is what a page change actually looks like. Looping it adds a jump from
        # the right edge back to the left that the real animation never has.
        console.special_until = WIPE_SECONDS * WIPE_COLUMNS * 2 if command == "wipe1" else None
        console.start = time.monotonic()
    elif command == "arm":
        await grid.arm(numbers or list(range(PAD_COUNT)))
    elif command == "disarm":
        await grid.disarm(numbers or list(range(PAD_COUNT)))
    elif command == "rgb":
        await grid.rgb(numbers[0], numbers[1], numbers[2], numbers[3])
    else:
        print(f"  ? {line}", flush=True)
        return
    print(f"  > {line}", flush=True)


async def watch(console: Console, grid: Grid, path: Path) -> None:
    """Follow the command file, acting on every line in it.

    Reading from the very beginning rather than from the end so a first frame can be
    queued before the link is up: connecting takes twenty seconds, and the point of this
    script is that nobody waits around.
    """
    offset = 0
    while console.running:
        if path.exists() and path.stat().st_size > offset:
            with path.open() as handle:
                handle.seek(offset)
                fresh = handle.read()
                offset = handle.tell()
            console.batching = True
            for line in fresh.splitlines():
                if line.strip():
                    try:
                        await apply(console, grid, line.strip())
                    except (IndexError, ValueError) as err:
                        print(f"  ! {line.strip()}: {err}", flush=True)
            console.batching = False
        await asyncio.sleep(0.15)


async def render(console: Console, grid: Grid) -> None:
    """Redraw whenever the frame actually changes, so a static frame costs one write."""
    last: list[int] | None = None
    changed_at = time.monotonic()
    while console.running:
        if console.paused or console.batching:
            last = None
        else:
            frame = console.build()
            if frame != last:
                await grid.draw(frame)
                last = frame
                # An animation that feels uneven is either uneven or not; this says which.
                now = time.monotonic()
                if console.trace:
                    print(f"  frame +{(now - changed_at) * 1000:.0f} ms", flush=True)
                changed_at = now
        await asyncio.sleep(1 / FPS)


async def main() -> None:
    """Connect, arm the grid, and serve commands until told to quit."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--proxy", required=True)
    parser.add_argument("--address")
    parser.add_argument("--commands", required=True, type=Path)
    parser.add_argument("--slot", type=int, default=0)
    parser.add_argument("--bank", type=int, default=3)
    parser.add_argument("--note-base", type=int, default=36)
    args = parser.parse_args()

    args.commands.touch()
    client, connection = await connect(args.proxy, args.address)
    grid = Grid(client, args.note_base, args.slot, args.bank)
    console = Console()
    try:
        await grid.arm()
        await grid.dark()
        print(f"ready, taking commands from {args.commands}", flush=True)
        await asyncio.gather(watch(console, grid, args.commands), render(console, grid))
    finally:
        await grid.dark()
        await client.disconnect()
        await connection.stop()
        print("disconnected", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
