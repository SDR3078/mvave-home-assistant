#!/usr/bin/env python3
"""Show candidate LED languages on the real grid, so the choice is made by looking.

The palette was walked one velocity at a time, which is the worst possible way to judge
whether two colours are distinguishable: memory for colour across six seconds is poor,
and every value looked distinct at the time. What matters for a control surface is
whether they are distinguishable **side by side, at a glance, across the room**. So this
lights all sixteen pads at once and holds the frame.

It is a design instrument rather than a measurement. Each scene prints a key naming what
is on every pad, waits for Enter, and moves on. Answer the question printed under each
scene and the LED language stops being a matter of opinion.

Run it with Home Assistant stopped: the pad accepts one central at a time.

    python3 scripts/preview_leds.py --proxy 192.168.69.35
    python3 scripts/preview_leds.py --proxy 192.168.69.35 --only bars blink
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from collections.abc import Callable
from pathlib import Path

import bleak
import bleak_retry_connector
import habluetooth
from bleak_esphome import APIConnectionManager

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "custom_components" / "mvave"))

from devices.smc_pad import (  # noqa: E402
    FACTORY_BUTTONS,
    LED_NONE,
    PAD_COUNT,
    PAD_NUMBER_BY_READING_ORDER,
    button_led_packet,
    led_packet_for_pad,
    rgb_packet_for_pad,
)
from transport import frame_many  # noqa: E402

MIDI_SERVICE = "03b80e5a-ede8-4b33-a751-6ce34ec4c700"
MIDI_CHAR = "7772e5db-3868-4112-a1a9-f2669d106bf3"
VENDOR_WRITE = "0000ae41-0000-1000-8000-00805f9b34fb"

#: Velocity 0 is off. So is 127, and 96-126 are ignored, so neither may be sent as a
#: colour (HARDWARE-BLE.md section 9).
OFF = 0

#: The palette indices worth considering, named as the owner named them while walking it.
#: Ten candidates rather than seven, because two that read alike in isolation may still
#: separate side by side, and that is exactly what this script is for.
CANDIDATES: tuple[tuple[int, str], ...] = (
    (5, "green"),
    (14, "red-pink"),
    (15, "orange"),
    (21, "bright blue"),
    (24, "light purple"),
    (40, "white"),
    (12, "pink"),
    (19, "absinth green"),
    (60, "light yellow"),
    (48, "blue-white"),
    (1, "pastel orange"),
    (4, "yellow"),
    (6, "turquoise"),
    (11, "light pink"),
    (32, "yellow-white"),
    (3, "yellow-white 3"),
)


class Grid:
    """Sixteen pads addressed in reading order, drawn as one packet per frame.

    Reading order is top-left to bottom-right, which is not the pad numbering: the
    hardware counts pad 1 at the bottom left, so the mapping goes through
    ``PAD_NUMBER_BY_READING_ORDER``. Everything above this class thinks in reading order,
    because that is what a person looking at the grid sees.
    """

    def __init__(self, client: bleak.BleakClient, note_base: int, slot: int, bank: int) -> None:
        self._client = client
        self._note_base = note_base
        self._slot = slot
        self._bank = bank
        self._armed: list[bool] = [False] * PAD_COUNT

    def note_for(self, index: int) -> int:
        """The note that lights the pad at this position in reading order."""
        return self._note_base + PAD_NUMBER_BY_READING_ORDER[index] - 1

    async def arm(self, indices: list[int] | None = None) -> None:
        """Point each pad's Led byte at its own note, so host note-ons light it.

        Paced deliberately: these are vendor writes, which the device acknowledges around
        400 ms later, and a burst of sixteen is exactly the flood the benchmark showed it
        dropping.
        """
        for index in range(PAD_COUNT) if indices is None else indices:
            pad = PAD_NUMBER_BY_READING_ORDER[index]
            await self._client.write_gatt_char(
                VENDOR_WRITE,
                led_packet_for_pad(pad, self.note_for(index), slot=self._slot, bank=self._bank),
                response=False,
            )
            self._armed[index] = True
            await asyncio.sleep(0.05)

    async def disarm(self, indices: list[int]) -> None:
        """Hand a pad back to its stored 24-bit colour, which it shows immediately."""
        for index in indices:
            pad = PAD_NUMBER_BY_READING_ORDER[index]
            await self._client.write_gatt_char(
                VENDOR_WRITE,
                led_packet_for_pad(pad, LED_NONE, slot=self._slot, bank=self._bank),
                response=False,
            )
            self._armed[index] = False
            await asyncio.sleep(0.05)

    async def rgb(self, index: int, red: int, green: int, blue: int) -> None:
        """Write a 24-bit colour to one pad. Only visible while that pad is disarmed."""
        pad = PAD_NUMBER_BY_READING_ORDER[index]
        await self._client.write_gatt_char(
            VENDOR_WRITE,
            rgb_packet_for_pad(pad, red, green, blue, slot=self._slot, bank=self._bank),
            response=False,
        )
        await asyncio.sleep(0.05)

    async def note(self, index: int, channel: int, velocity: int) -> None:
        """Light one pad with a note-on on a chosen channel, bypassing the frame.

        Every comparable controller puts brightness and blink on the channel rather than
        the velocity, and only three of this device's sixteen channels have been tried.
        """
        status = 0x90 | ((channel - 1) & 0x0F)
        message = bytes((status, self.note_for(index), velocity))
        for packet in frame_many([message], self._client.mtu_size):
            await self._client.write_gatt_char(MIDI_CHAR, packet, response=False)

    async def arm_buttons(self) -> None:
        """Point each transport button's Led byte at its own controller number.

        The buttons carry the same Led mechanism as the pads, in the last byte of their
        23-byte record. Only the play button had ever been tried.
        """
        for index, (_, _, cc) in enumerate(FACTORY_BUTTONS):
            await self._client.write_gatt_char(
                VENDOR_WRITE, button_led_packet(index, cc, slot=self._slot), response=False
            )
            await asyncio.sleep(0.05)

    async def button(self, index: int, velocity: int) -> None:
        """Light one transport button. They are single-colour, so velocity is on or off."""
        message = bytes((0x90, FACTORY_BUTTONS[index][2], velocity))
        for packet in frame_many([message], self._client.mtu_size):
            await self._client.write_gatt_char(MIDI_CHAR, packet, response=False)

    async def draw(self, frame: list[int]) -> None:
        """Light the whole grid from one list of palette indices, in reading order.

        A full frame is a single write: RP-052 packs many messages into one packet, which
        is why this path sustains sixty frames a second where the vendor path manages five.
        """
        messages = [
            bytes((0x99, self.note_for(index), value))
            for index, value in enumerate(frame)
            if self._armed[index]
        ]
        for packet in frame_many(messages, self._client.mtu_size):
            await self._client.write_gatt_char(MIDI_CHAR, packet, response=False)

    async def dark(self) -> None:
        """Put every armed pad out."""
        await self.draw([OFF] * PAD_COUNT)


def show(frame: list[int], key: dict[int, str] | None = None) -> None:
    """Print the frame as a 4x4 map, so the terminal and the grid can be compared."""
    for row in range(4):
        cells = [f"{frame[row * 4 + column]:>4}" for column in range(4)]
        print("   " + " ".join(cells))
    if key:
        for value, name in key.items():
            print(f"     {value:>3} = {name}")


#: Reading-order positions from the bottom-left rightwards, so a bar fills upwards.
#: Level rises, so a bar that grows downwards has to be learned; one that grows upwards
#: does not. The design document makes cover position fill top-down "(it's a blind)",
#: which only reads as a deliberate exception if everything else grows the other way.
BAR_ORDER = (12, 13, 14, 15, 8, 9, 10, 11, 4, 5, 6, 7, 0, 1, 2, 3)


def bar_frame(values: list[int]) -> list[int]:
    """Turn sixteen values counted from the bottom into a reading-order frame."""
    frame = [OFF] * PAD_COUNT
    for position, value in enumerate(values):
        frame[BAR_ORDER[position]] = value
    return frame


def level_frame(fraction: float, colour: int, lit: bool, track: int = OFF) -> list[int]:
    """A bar whose boundary pad blinks when the value is in the top half of its step.

    Sixteen pads give sixteen steps, which is about six percent each. Blinking the pad
    above the last solid one when the value has passed the middle of that step doubles
    the readable resolution without needing a brightness the palette does not have.
    """
    steps = fraction * PAD_COUNT
    solid = int(steps)
    values = [colour if position < solid else track for position in range(PAD_COUNT)]
    if steps - solid >= 0.5 and solid < PAD_COUNT:
        values[solid] = colour if lit else track
    return bar_frame(values)


def duty(elapsed: float, period: float, on_fraction: float) -> bool:
    """Whether a blink of this period is in its lit phase right now."""
    return (elapsed % period) < period * on_fraction


async def animate(
    grid: Grid, build: Callable[[float], list[int]], seconds: float, fps: int = 30
) -> None:
    """Redraw the grid from a function of elapsed time. Free at sixty frames a second."""
    start = time.monotonic()
    while (elapsed := time.monotonic() - start) < seconds:
        await grid.draw(build(elapsed))
        await asyncio.sleep(1 / fps)


async def pause(prompt: str, auto: float | None) -> None:
    """Hold the scene until the person looking at the pad is done with it."""
    print(f"\n  >> {prompt}")
    if auto is not None:
        await asyncio.sleep(auto)
        return
    await asyncio.to_thread(input, "     [Enter] ")


# --------------------------------------------------------------------------- scenes


async def scene_contact(grid: Grid, auto: float | None) -> None:
    """All sixteen candidate colours at once. The one scene that settles the palette."""
    print("\n=== contact sheet: sixteen candidate colours, side by side ===")
    frame = [value for value, _ in CANDIDATES]
    await grid.draw(frame)
    show(frame, {value: name for value, name in CANDIDATES})
    await pause(
        "Which pads are genuinely different from every other pad? Name the ones that\n"
        "     pair up or read the same. Those pairs cannot both mean something.",
        auto,
    )


async def scene_identity(grid: Grid, auto: float | None) -> None:
    """Eight page colours, adjacent and then scattered.

    Adjacency flatters a palette: two similar colours touching are easy to tell apart,
    and the same two on opposite corners are not. A page identity colour has to survive
    the scattered case, because that is where it will actually be used.
    """
    print("\n=== identity colours: eight pages, adjacent then scattered ===")
    eight = [5, 14, 15, 21, 24, 40, 12, 60]
    adjacent = eight + [OFF] * 8
    await grid.draw(adjacent)
    show(adjacent)
    await pause("Eight colours in the top two rows. Count how many you can name.", auto)

    scattered = [OFF] * PAD_COUNT
    for position, value in zip((0, 3, 5, 6, 9, 10, 12, 15), eight, strict=True):
        scattered[position] = value
    await grid.draw(scattered)
    show(scattered)
    await pause("The same eight, spread out. Do any two now read the same?", auto)


async def scene_state(grid: Grid, auto: float | None) -> None:
    """Three ways to say on and off without a brightness channel.

    The design document wants brightness for state, which this path does not have. Each
    row here is one substitute, with the left two pads 'on' and the right two 'off'.
    """
    print("\n=== state without brightness: three substitutes, on | on | off | off ===")
    rows = (
        ([15, 15], [OFF, OFF], "on = orange, off = dark"),
        ([15, 15], [40, 40], "on = orange, off = white"),
        ([15, 15], [1, 1], "on = orange 15, off = pastel orange 1"),
        ([5, 5], [14, 14], "on = green, off = red-pink"),
    )
    frame = [value for on, off, _ in rows for value in on + off]
    await grid.draw(frame)
    show(frame)
    for number, (_, _, description) in enumerate(rows, start=1):
        print(f"     row {number}: {description}")
    await pause("Which row says on-and-off fastest, from where you normally stand?", auto)


async def scene_bars(grid: Grid, auto: float | None) -> None:
    """Five ways to draw 60% on sixteen pads that cannot dim, filling upwards."""
    print("\n=== value bar: five designs, all showing about 60%, filling from the bottom ===")
    lit = 10  # ten of sixteen
    designs: tuple[tuple[str, list[int]], ...] = (
        ("run only, rest dark", bar_frame([15] * lit + [OFF] * (PAD_COUNT - lit))),
        ("run plus a blue track", bar_frame([15] * lit + [21] * (PAD_COUNT - lit))),
        ("run with a white head", bar_frame([15] * (lit - 1) + [40] + [OFF] * (PAD_COUNT - lit))),
        ("run with a green head", bar_frame([15] * (lit - 1) + [5] + [OFF] * (PAD_COUNT - lit))),
        (
            "warm-to-cold ramp across the run",
            bar_frame([14, 14, 15, 15, 15, 60, 60, 40, 40, 48] + [OFF] * (PAD_COUNT - lit)),
        ),
    )
    for name, frame in designs:
        await grid.draw(frame)
        print(f"\n   {name}")
        show(frame)
        await pause("Readable as a level? Could you hit 60% again without looking twice?", auto)


async def scene_blink(grid: Grid, auto: float | None) -> None:
    """Four blink rates at once, so annoyance and legibility are judged together.

    Blink is the one state channel this path has in unlimited supply, because the grid
    redraws sixty times a second for free. It is also the channel people hate most when
    it is overused, so it is worth seeing four rates competing for attention.
    """
    print("\n=== blink: 0.5, 1, 2 and 4 Hz, one per column, all orange ===")
    rates = (0.5, 1.0, 2.0, 4.0)
    print("     column 1 = 0.5 Hz, column 2 = 1 Hz, column 3 = 2 Hz, column 4 = 4 Hz")
    print("     (rows are identical; the steady bottom row is the reference)")
    stop = time.monotonic() + (auto if auto is not None else 12.0)
    start = time.monotonic()
    while time.monotonic() < stop:
        now = time.monotonic() - start
        frame = []
        for row in range(4):
            for column in range(4):
                if row == 3:
                    frame.append(15)  # steady reference row
                else:
                    on = (now * rates[column] * 2) % 2 < 1
                    frame.append(15 if on else OFF)
        await grid.draw(frame)
        await asyncio.sleep(1 / 30)
    await grid.draw([15] * PAD_COUNT)
    await pause("Which rate reads as 'this one needs you' without being irritating?", auto)


async def scene_ripple(grid: Grid, auto: float | None) -> None:
    """The design document's ripple, at a rate the palette path can actually sustain."""
    print("\n=== ripple: rings expanding from the bottom-left pad ===")
    origin = (3, 0)  # bottom-left in reading order
    # Repeated until the hold expires rather than a fixed number of times: one ripple
    # lasts a third of a second, which is far too short to form an opinion about.
    deadline = time.monotonic() + (auto if auto is not None else 15.0)
    while time.monotonic() < deadline:
        for radius in range(4):
            frame = []
            for row in range(4):
                for column in range(4):
                    distance = max(abs(row - origin[0]), abs(column - origin[1]))
                    frame.append(21 if distance == radius else OFF)
            await grid.draw(frame)
            await asyncio.sleep(0.11)
        await grid.draw([OFF] * PAD_COUNT)
        await asyncio.sleep(0.22)
    await pause("Does that read as a page change, or as noise? Worth keeping?", auto)


async def scene_brightness(grid: Grid, auto: float | None) -> None:
    """The same colour on both paths, so the cost of losing brightness is visible.

    The left half stays armed and shows palette orange at full brightness. The right half
    is disarmed and given the same hue at a quarter level over the vendor channel. This
    is the whole trade in one frame.
    """
    print("\n=== brightness: palette orange (left) against dimmed 24-bit orange (right) ===")
    right = [index for index in range(PAD_COUNT) if index % 4 >= 2]
    await grid.disarm(right)
    for index in right:
        await grid.rgb(index, 60, 20, 0)
    await grid.draw([15 if index % 4 < 2 else OFF for index in range(PAD_COUNT)])
    print("     columns 1-2: palette 15, full brightness, no dimming possible")
    print("     columns 3-4: 24-bit (60, 20, 0), genuinely dim, but ~5 frames/s and it")
    print("     flashes white when you press it")
    await pause("Press a pad in each half. Is the dimming worth losing animation for?", auto)
    await grid.arm(right)


async def scene_page(grid: Grid, auto: float | None) -> None:
    """A whole room page rendered in the proposed language, which is the real deliverable.

    Every earlier scene tests one channel in isolation. This one puts them together, which
    is the only way to find out whether the rules collide: on entities and off entities and
    navigation pads and the back pad all competing for the same glance.

    The rules on display are: the room's colour means an entity that is on, white means one
    that is off, the pads along the bottom are other pages in their own colours, the corner
    is back, and the focused pad breathes.
    """
    print("\n=== a living-room page in the proposed language ===")
    room, off_state, back = 15, 40, 40
    static = [
        room, off_state, room, off_state,
        room, off_state, off_state, OFF,
        OFF, OFF, OFF, OFF,
        5, 21, 24, back,
    ]  # fmt: skip
    focused = 4

    def build(elapsed: float) -> list[int]:
        frame = list(static)
        # A slow, uneven rhythm, which is as close to a pulse as a grid without a
        # brightness channel can get.
        frame[focused] = room if duty(elapsed, 1.4, 0.65) else OFF
        return frame

    show(static)
    print("     orange = on, white = off, dark = nothing assigned")
    print("     row 1 to 2 are the room's entities; row 4 is green/blue/purple pages")
    print("     bottom right is back; row 2 column 1 is focused and breathing")
    await animate(grid, build, auto if auto is not None else 25.0)
    await pause(
        "Three questions. Does the breathing pad read as different from the steady ones?\n"
        "     Can you tell an off entity from the back pad, which are both white?\n"
        "     Does the room's colour still register when half the grid is white?",
        auto,
    )


async def scene_rhythms(grid: Grid, auto: float | None) -> None:
    """Steady, breathing and alarm, side by side, then one of each inside a page.

    Motion is the channel that replaces brightness, so the whole language rests on three
    rhythms being told apart in the half second someone glances at the grid. Isolated they
    obviously differ. What matters is whether they still differ in context.
    """
    print("\n=== three rhythms: steady, breathing, alarm ===")
    print("     column 1 steady, column 2 breathing (1.4 s), column 3 alarm (0.18 s)")

    def isolated(elapsed: float) -> list[int]:
        frame = []
        for _ in range(4):
            frame.append(15)
            frame.append(15 if duty(elapsed, 1.4, 0.65) else OFF)
            frame.append(14 if duty(elapsed, 0.18, 0.5) else OFF)
            frame.append(OFF)
        return frame

    await animate(grid, isolated, auto if auto is not None else 20.0)
    await pause("Three obviously different things, or two that blur together?", auto)

    static = [
        15, 40, 15, 40,
        15, 40, 40, OFF,
        OFF, OFF, OFF, OFF,
        5, 21, 24, 40,
    ]  # fmt: skip

    def in_context(elapsed: float) -> list[int]:
        frame = list(static)
        frame[4] = 15 if duty(elapsed, 1.4, 0.65) else OFF
        frame[2] = 14 if duty(elapsed, 0.18, 0.5) else OFF
        return frame

    print("\n   the same two rhythms inside a page: one breathing, one alarming")
    await animate(grid, in_context, auto if auto is not None else 20.0)
    await pause(
        "Can you still find both, and does the alarm pull your eye first as it should?",
        auto,
    )


async def scene_substep(grid: Grid, auto: float | None) -> None:
    """Half-steps by blinking the boundary pad, which is the replacement for dimming.

    Three levels a half step apart. If they read as three different values then sixteen
    pads carry thirty-two steps, which is about three percent, and the design document's
    proportionally dimmed final pad is not missed.
    """
    print("\n=== half steps: three levels, each half a pad apart ===")
    levels = ((0.50, "eight solid"), (0.53, "eight solid, ninth blinking"), (0.5625, "nine solid"))
    hold = (auto if auto is not None else 18.0) / 3
    for fraction, description in levels:
        print(f"\n   {description}")

        def build(elapsed: float, fraction: float = fraction) -> list[int]:
            return level_frame(fraction, 5, duty(elapsed, 0.4, 0.5))

        await animate(grid, build, hold)
    await pause("Did those read as three different levels, or as two?", auto)


SCENES = {
    "contact": scene_contact,
    "identity": scene_identity,
    "state": scene_state,
    "bars": scene_bars,
    "blink": scene_blink,
    "ripple": scene_ripple,
    "brightness": scene_brightness,
    "page": scene_page,
    "rhythms": scene_rhythms,
    "substep": scene_substep,
}


# ------------------------------------------------------------------------ plumbing


async def connect(
    proxy: str, address: str | None
) -> tuple[bleak.BleakClient, APIConnectionManager]:
    """Open a link to the pad through an ESPHome Bluetooth proxy."""
    manager = habluetooth.BluetoothManager()
    await manager.async_setup()
    connection = APIConnectionManager({"address": proxy, "noise_psk": None})
    await connection.start()
    print(f"proxy {proxy}: waiting for advertisements")
    await asyncio.sleep(8)

    found = await bleak.BleakScanner.discover(return_adv=True)
    for found_address, (device, adv) in found.items():
        if address and found_address.upper() != address.upper():
            continue
        if MIDI_SERVICE in [uuid.lower() for uuid in adv.service_uuids]:
            print(f"found {found_address} rssi {adv.rssi}")
            # The pad accepts one central and goes silent while held, so a link left over
            # from Home Assistant would make this look like an absent device.
            await bleak_retry_connector.close_stale_connections_by_address(found_address)
            client = await bleak_retry_connector.establish_connection(
                bleak.BleakClient, device, found_address
            )
            print(f"connected, MTU {client.mtu_size}")
            return client, connection
    raise SystemExit("no BLE-MIDI device found")


async def main() -> None:
    """Play the scenes and leave the grid dark."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--proxy", required=True)
    parser.add_argument("--address")
    parser.add_argument("--slot", type=int, default=0)
    parser.add_argument("--bank", type=int, default=3)
    parser.add_argument("--note-base", type=int, default=36)
    parser.add_argument(
        "--only", nargs="+", choices=sorted(SCENES), help="run just these scenes, in order"
    )
    parser.add_argument(
        "--auto",
        type=float,
        metavar="SECONDS",
        help="hold each scene for this long instead of waiting for Enter",
    )
    args = parser.parse_args()

    client, connection = await connect(args.proxy, args.address)
    grid = Grid(client, args.note_base, args.slot, args.bank)
    try:
        print("arming all sixteen pads to their own notes")
        await grid.arm()
        await grid.dark()
        for name in args.only or list(SCENES):
            await SCENES[name](grid, args.auto)
        await grid.dark()
        print("\ngrid left dark, pads left armed as the integration leaves them")
    finally:
        await client.disconnect()
        await connection.stop()


if __name__ == "__main__":
    asyncio.run(main())
