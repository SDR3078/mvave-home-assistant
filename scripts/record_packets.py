#!/usr/bin/env python3
"""Record BLE-MIDI packets from a controller, via an ESPHome Bluetooth proxy or a local adapter.

Why this exists
---------------
Over USB the SMC-PAD exposes three MIDI ports, and a control's assignment type decides
which port it uses (mvave-smc-pad-ableton HARDWARE.md 1.1 and 5.6). BLE-MIDI has no
cable concept at all (RP-052, preface), so which of those streams the pad puts on the
radio, and whether LED writes land, is unknown until measured. This tool measures it
and writes what it saw in the fixture format that tests/test_parser.py decodes, so a
recording becomes a regression test the moment it lands in the fixtures directory.

Setup
-----
    uv pip install -r scripts/requirements.txt      # or pip, in any Python >= 3.11

Flash an ESP32 with the ready-made Bluetooth proxy firmware from
https://esphome.io/projects/?type=bluetooth (WebSerial: Chrome or Edge, board on USB,
then "Configure Wi-Fi"). Its API is unencrypted and active connections are on. Note
its IP from your DHCP leases; a reservation saves you doing that twice.

Usage
-----
    # 1. What advertises BLE-MIDI within the proxy's range?
    python scripts/record_packets.py --proxy 192.168.69.50 --scan-only

    # 2. Connect, subscribe, record every notification until Ctrl-C
    python scripts/record_packets.py --proxy 192.168.69.50

    # 3. Same, plus an LED test: light pads 1-16, ask what you saw, clear them
    python scripts/record_packets.py --proxy 192.168.69.50 --led-test

    # 4. Same, plus a prompt where you type MIDI bytes in hex to send them
    python scripts/record_packets.py --proxy 192.168.69.50 --interactive

Without --proxy the machine's own Bluetooth adapter is used through bleak, which works
on Linux (BlueZ), macOS and Windows. Add --psk if the proxy's API is encrypted.

Two things to know before trusting a silent run: a BLE-MIDI peripheral accepts one
central, so a pad still connected to a phone or PC over Bluetooth never advertises;
and the spec (section 5) requires the central to read the MIDI characteristic once
after connecting, which this tool does before subscribing.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import TextIO

import bleak
import bleak_retry_connector
from bleak.backends.characteristic import BleakGATTCharacteristic
from bleak.backends.device import BLEDevice
from bleak.backends.scanner import AdvertisementData

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "custom_components" / "mvave"))

from devices.smc_pad import (  # noqa: E402
    PRESET_SIZE,
    READ_COMMAND,
    STATE_ADDRESS,
    STATE_LENGTH,
    STATE_REGION,
    VENDOR_NOTIFY_CHAR,
    VENDOR_WRITE_CHAR,
    VendorReply,
    channel_packet_for_pad,
    custom_sysex_packet_for_pad,
    decode_preset,
    describe_vendor_packet,
    led_packet_for_pad,
    parse_reply,
    parse_state,
    read_packet,
    rgb_packet_for_pad,
    type_packet_for_pad,
    write_packet,
)
from transport import (  # noqa: E402
    MidiEvent,
    ParserState,
    parse_ble_midi,
)

MIDI_SERVICE = "03b80e5a-ede8-4b33-a751-6ce34ec4c700"
MIDI_CHAR = "7772e5db-3868-4112-a1a9-f2669d106bf3"
FIXTURE_DIR = REPO_ROOT / "tests" / "fixtures" / "ble_midi_packets"

# Standard characteristics worth reading once, when the device has them.
DEVICE_INFO_CHARS = (
    ("00002a29-0000-1000-8000-00805f9b34fb", "manufacturer"),
    ("00002a24-0000-1000-8000-00805f9b34fb", "model"),
    ("00002a19-0000-1000-8000-00805f9b34fb", "battery"),
)

REALTIME_NAMES = {
    0xF8: "clock",
    0xFA: "start",
    0xFB: "continue",
    0xFC: "stop",
    0xFE: "active_sensing",
    0xFF: "reset",
}

_LOGGER = logging.getLogger("record_packets")


# --------------------------------------------------------------------------- helpers


def describe(event: MidiEvent) -> str:
    """One short human line per decoded event, channels shown 1-based."""
    ch = f"ch{event.channel + 1}" if event.channel is not None else ""
    match event.type:
        case "note_on" | "note_off":
            return f"{event.type} {ch} note {event.data1} vel {event.data2} @{event.timestamp}"
        case "cc":
            return f"cc {ch} #{event.data1}={event.data2} @{event.timestamp}"
        case "program_change":
            return f"program {ch} {event.data1} @{event.timestamp}"
        case "aftertouch":
            return f"aftertouch {ch} {event.data1} @{event.timestamp}"
        case "poly_aftertouch":
            return f"poly_aftertouch {ch} note {event.data1} {event.data2} @{event.timestamp}"
        case "pitch_bend":
            return f"pitch_bend {ch} {event.data1 | (event.data2 << 7)} @{event.timestamp}"
        case "sysex":
            payload = event.sysex_payload
            return f"sysex {len(payload)}B [{payload.hex(' ')}] @{event.timestamp}"
        case "realtime":
            return f"realtime {REALTIME_NAMES.get(event.status, f'{event.status:02X}')}"
        case _:
            return f"{event.type} {event.raw.hex(' ')} @{event.timestamp}"


def parse_note_list(text: str) -> list[int]:
    """'1-16' or '1,5,9' or '1-4,17' -> list of ints."""
    notes: list[int] = []
    for part in text.split(","):
        part = part.strip()
        if "-" in part:
            lo, hi = (int(x) for x in part.split("-", 1))
            notes.extend(range(lo, hi + 1))
        elif part:
            notes.append(int(part))
    bad = [n for n in notes if not 0 <= n <= 127]
    if bad:
        raise argparse.ArgumentTypeError(f"notes out of 0-127: {bad}")
    return notes


async def ainput(prompt: str) -> str:
    """input() without blocking the event loop, so notifications keep flowing."""
    loop = asyncio.get_running_loop()
    return (await loop.run_in_executor(None, lambda: input(prompt))).strip()


def frame(midi: bytes) -> bytes:
    """BLE-MIDI framing for an outgoing message: header and timestamp both zero."""
    return bytes((0x80, 0x80)) + midi


class Recorder:
    """Writes the fixture file incrementally, so Ctrl-C loses nothing."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.packets = 0
        self.state = ParserState()
        self.started = time.monotonic()
        self._file: TextIO | None = None

    def open(self, header: list[str]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._file = self.path.open("w", encoding="utf-8")
        for line in header:
            self._file.write(f"# {line}\n")
        self._file.write("#\n# Packets: hex bytes, then '#', arrival in ms, decoded events.\n\n")
        self._file.flush()

    def comment(self, text: str) -> None:
        print(f"    # {text}")
        if self._file:
            self._file.write(f"# {text}\n")
            self._file.flush()

    def packet(self, data: bytes) -> list[MidiEvent]:
        elapsed = int((time.monotonic() - self.started) * 1000)
        errors_before = self.state.errors
        events = parse_ble_midi(data, self.state)
        summary = "; ".join(describe(event) for event in events) or "(no events)"
        if self.state.errors > errors_before:
            summary += f"  PARSER ERROR: {self.state.last_error}"
        self.packets += 1
        print(f"+{elapsed:7d} ms  {data.hex(' ').upper():<48}  {summary}")
        if self._file:
            self._file.write(f"{data.hex(' ').upper():<48}  # +{elapsed} ms  {summary}\n")
            self._file.flush()
        return events

    def close(self) -> None:
        if self._file:
            self._file.close()
            self._file = None
        if self.packets == 0 and self.path.exists():
            self.path.unlink()
            print(f"No packets recorded; removed empty {self.path}")
        elif self.packets:
            print(f"Recorded {self.packets} packets to {self.path}")
            if self.state.errors:
                print(
                    f"  {self.state.errors} parser error(s); last: {self.state.last_error}. "
                    "The fixture test will fail on this file until the parser handles it."
                )


# ----------------------------------------------------------------------------- proxy


async def open_proxy(host: str, psk: str | None, timeout: float) -> object:
    """Bring an ESPHome proxy online and make it bleak's adapter. Returns the manager."""
    import habluetooth
    from bleak_esphome import APIConnectionManager

    manager = APIConnectionManager({"address": host, "noise_psk": psk})
    # The host-side manager must exist before any scanner is registered. Its setup
    # also swaps bleak's Scanner and Client for routing wrappers, which is why this
    # module only ever refers to bleak.BleakScanner and bleak.BleakClient by
    # attribute, never via "from bleak import ...".
    await habluetooth.BluetoothManager().async_setup()
    start = asyncio.create_task(manager.start())
    _done, pending = await asyncio.wait({start}, timeout=timeout)
    if pending:
        start.cancel()
        await asyncio.gather(start, return_exceptions=True)
        with contextlib.suppress(Exception):
            await manager.stop()
        raise SystemExit(
            f"proxy {host}: no API connection within {timeout:.0f}s. "
            "Wrong IP, proxy off, or an encrypted API that needs --psk?"
        )
    start.result()  # re-raise a failed start instead of a misleading 'not found' later
    return manager


# ------------------------------------------------------------------------------ scan


async def scan(seconds: float, via_proxy: bool) -> list[tuple[BLEDevice, AdvertisementData]]:
    if via_proxy:
        # habluetooth's BleakScanner wrapper returns what the manager has heard so far and
        # ignores the timeout, so give the proxy time to hear advertisements first.
        await asyncio.sleep(seconds)
        found = await bleak.BleakScanner.discover(return_adv=True)
    else:
        found = await bleak.BleakScanner.discover(timeout=seconds, return_adv=True)
    rows = list(found.values())
    rows.sort(key=lambda row: row[1].rssi or -999, reverse=True)
    return rows


def is_midi(adv: AdvertisementData) -> bool:
    return MIDI_SERVICE in {uuid.lower() for uuid in adv.service_uuids}


def print_scan(rows: list[tuple[BLEDevice, AdvertisementData]], show_all: bool) -> None:
    shown = 0
    for device, adv in rows:
        midi = is_midi(adv)
        if not (midi or show_all):
            continue
        shown += 1
        name = device.name or adv.local_name or "?"
        tag = "BLE-MIDI" if midi else "        "
        print(f"  {tag}  {device.address}  rssi {adv.rssi:>4}  {name}")
        if midi or show_all:
            for uuid in adv.service_uuids:
                print(f"            service {uuid}")
    if shown == 0:
        print("  nothing" + ("" if show_all else " advertising the BLE-MIDI service"))
        print(
            "  If the pad is on, check it is not already connected to a phone or PC over "
            "Bluetooth: a BLE-MIDI peripheral accepts one central and stops advertising. "
            "Use --all to list every device the adapter hears."
        )


# ----------------------------------------------------------------------------- record


async def send(client: bleak.BleakClient, midi: bytes, rec: Recorder) -> None:
    await client.write_gatt_char(MIDI_CHAR, frame(midi), response=False)
    rec.comment(f"sent {frame(midi).hex(' ').upper()}")


async def led_test(
    client: bleak.BleakClient, notes: list[int], velocity: int, channel: int, rec: Recorder
) -> None:
    print(f"\nLED test: note-on for notes {notes} at velocity {velocity}, one every 150 ms.")
    print("Over USB, velocity is a palette index on the lit port: 5 green, 15 orange, 21 blue.")
    for note in notes:
        await send(client, bytes((0x90 | (channel - 1), note, velocity)), rec)
        await asyncio.sleep(0.15)
    answer = await ainput("What did the pads do? (describe, or Enter for 'nothing'): ")
    rec.comment(f"LED test notes {notes} velocity {velocity}: {answer or 'nothing'}")
    for note in notes:
        await send(client, bytes((0x90 | (channel - 1), note, 0)), rec)
        await asyncio.sleep(0.15)
    answer = await ainput("Sent velocity 0 to the same notes. Are they off now? ")
    rec.comment(f"LED test clear with velocity 0: {answer or 'no answer'}")


async def light(
    client: bleak.BleakClient,
    notes: list[int],
    velocity: int,
    channel: int,
    hold: float,
    rec: Recorder,
    clear: bool = True,
) -> None:
    """The LED test without questions: light, hold, clear. For when someone else watches."""
    print(f"\nLighting notes {notes} at velocity {velocity}, holding {hold:.0f}s, then clearing.")
    for note in notes:
        await send(client, bytes((0x90 | (channel - 1), note, velocity)), rec)
        await asyncio.sleep(0.15)
    rec.comment(f"lit notes {notes} at velocity {velocity}; holding {hold:.0f}s")
    await asyncio.sleep(hold)
    if not clear:
        rec.comment("left lit on purpose (--no-clear)")
        return
    for note in notes:
        await send(client, bytes((0x90 | (channel - 1), note, 0)), rec)
        await asyncio.sleep(0.15)
    rec.comment("cleared with velocity 0")


async def vendor_read(
    client: bleak.BleakClient,
    replies: asyncio.Queue[VendorReply],
    address: int,
    count: int,
    region: int = 5,
) -> bytes:
    """One vendor read; waits for the reply that echoes the address, retrying twice."""
    for attempt in range(3):
        await client.write_gatt_char(
            VENDOR_WRITE_CHAR, read_packet(address, count, region), response=False
        )
        deadline = time.monotonic() + 4
        try:
            while True:
                reply = await asyncio.wait_for(replies.get(), max(0.1, deadline - time.monotonic()))
                if reply.command == READ_COMMAND and reply.address == address:
                    if not reply.checksum_ok:
                        raise RuntimeError(f"bad checksum on read of 0x{address:04X}")
                    return reply.data
        except TimeoutError:
            if attempt == 2:
                raise
            print(f"    (no reply to read of 0x{address:04X}, retrying)")


async def dump_slot(
    client: bleak.BleakClient, replies: asyncio.Queue[VendorReply], slot: int, rec: Recorder
) -> bytes:
    """Read one 3539-byte preset slot and print its decoded map."""
    base = slot * PRESET_SIZE
    image = bytearray()
    for offset in range(0, PRESET_SIZE, 128):
        image += await vendor_read(client, replies, base + offset, min(128, PRESET_SIZE - offset))
        await asyncio.sleep(0.2)
    preset = decode_preset(bytes(image))
    buttons = [b.number for b in preset.buttons]
    rec.comment(f"slot {slot}: buttons {buttons} types {[b.type for b in preset.buttons]}")
    encoders = [e.cc for e in preset.encoders]
    rec.comment(
        f"slot {slot}: encoders {encoders} relative {[e.relative for e in preset.encoders]}"
    )
    for bank, records in enumerate(preset.banks, start=1):
        notes = [r.note for r in records]
        types = sorted({r.type for r in records})
        leds = [r.led for r in records]
        colours = sorted({f"{r.rgb[0]:02x}{r.rgb[1]:02x}{r.rgb[2]:02x}" for r in records})
        rec.comment(f"slot {slot} bank {bank}: notes {notes} types {types}")
        rec.comment(f"slot {slot} bank {bank}: led {leds} rgb {colours}")
    return bytes(image)


PALETTE_VELOCITIES = (1, 3, 5, 7, 9, 11, 13, 14, 15, 17, 19, 21, 24, 32, 40, 48, 60, 64, 96, 127)


async def palette_walk(client: bleak.BleakClient, note: int, channel: int, rec: Recorder) -> None:
    """Light one armed note at each velocity in turn and ask what colour it shows.

    Stops at every step on purpose: a human asked to remember twenty colours in order
    gets some wrong, a human asked one at a time does not.
    """
    print(f"\nPalette walk on note {note}: type the colour you see, or Enter to skip.")
    seen: list[tuple[int, str]] = []
    for velocity in PALETTE_VELOCITIES:
        await send(client, bytes((0x90 | (channel - 1), note, velocity)), rec)
        answer = await ainput(f"  velocity {velocity:3d}: ")
        seen.append((velocity, answer or "(skipped)"))
        rec.comment(f"palette velocity {velocity}: {answer or '(skipped)'}")
    await send(client, bytes((0x90 | (channel - 1), note, 0)), rec)
    print("\n| Velocity | Colour |\n|---|---|")
    for velocity, answer in seen:
        print(f"| {velocity} | {answer} |")


async def interactive(client: bleak.BleakClient, rec: Recorder, stop: asyncio.Event) -> None:
    print("\nInteractive: type MIDI bytes in hex to send them (e.g. 90 01 05), 'q' to stop.")
    while not stop.is_set():
        line = await ainput("> ")
        if line.lower() in {"q", "quit", "exit"}:
            stop.set()
            return
        if not line:
            continue
        try:
            midi = bytes.fromhex(line)
        except ValueError:
            print("  not hex")
            continue
        await send(client, midi, rec)


async def record(args: argparse.Namespace) -> None:
    manager = None
    if args.proxy:
        manager = await open_proxy(args.proxy, args.psk, args.connect_timeout)
        print(f"Proxy {args.proxy} online; listening {args.scan_seconds:.0f}s for advertisements.")
    else:
        print(f"Scanning {args.scan_seconds:.0f}s on the local adapter.")

    try:
        try:
            rows = await scan(args.scan_seconds, via_proxy=bool(args.proxy))
        except Exception as err:  # any backend failure means the same thing here
            raise SystemExit(
                f"scan failed: {err!r}. No usable local Bluetooth adapter? Use --proxy."
            ) from err
        print_scan(rows, args.all)
        if args.scan_only:
            return

        target = pick_target(rows, args)
        if target is None:
            raise SystemExit("no target: nothing advertised BLE-MIDI, or use --address.")
        device, adv = target
        name = device.name or adv.local_name or device.address
        print(f"\nConnecting to {name} ({device.address})")

        stop = asyncio.Event()
        closing = False

        def on_disconnect(_client: bleak.BleakClient) -> None:
            if not closing:
                print("\nDisconnected by the peripheral or the link.")
            stop.set()

        # bleak-retry-connector is what Home Assistant integrations use. habluetooth
        # swaps its client class for a proxy-aware one at setup, so it is looked up on
        # the module at call time and never imported by name.
        try:
            client = await bleak_retry_connector.establish_connection(
                bleak_retry_connector.BleakClientWithServiceCache,
                device,
                name,
                disconnected_callback=on_disconnect,
                max_attempts=3,
                timeout=args.connect_timeout,
            )
        except Exception as err:
            raise SystemExit(f"could not connect: {err!r}") from err

        rec = Recorder(args.out or FIXTURE_DIR / f"capture-{datetime.now():%Y%m%d-%H%M%S}.txt")
        try:
            header = [
                f"capture {datetime.now():%Y-%m-%d %H:%M:%S}",
                f"device {name} {device.address}",
                f"via {'proxy ' + args.proxy if args.proxy else 'local adapter'}",
                f"mtu {getattr(client, 'mtu_size', '?')}",
            ]
            print(f"Connected. MTU {getattr(client, 'mtu_size', '?')}. Services:")
            midi_char = None
            for service in client.services:
                print(f"  {service.uuid}  {service.description}")
                header.append(f"service {service.uuid} {service.description}")
                for char in service.characteristics:
                    props = ",".join(char.properties)
                    print(f"      {char.uuid}  [{props}]")
                    header.append(f"  char {char.uuid} [{props}]")
                    if char.uuid.lower() == MIDI_CHAR:
                        midi_char = char
            if midi_char is None:
                raise SystemExit(
                    "connected, but no BLE-MIDI characteristic; not a BLE-MIDI device?"
                )

            for uuid, label in DEVICE_INFO_CHARS:
                if client.services.get_characteristic(uuid) is None:
                    continue
                try:
                    value = bytes(await client.read_gatt_char(uuid))
                except Exception as err:  # informative only
                    header.append(f"{label}: read failed {err!r}")
                    continue
                text = f"{value[0]}%" if label == "battery" else value.decode("utf-8", "replace")
                print(f"  {label}: {text}")
                header.append(f"{label}: {text}")

            if args.pair:
                print("Pairing...")
                await client.pair()
                header.append("paired: yes")

            # Spec section 5: the central reads the characteristic once; the reply is empty.
            try:
                initial = await client.read_gatt_char(MIDI_CHAR)
                header.append(f"initial read: {len(initial)} bytes")
                print(f"Initial read returned {len(initial)} bytes (spec says 0).")
            except Exception as err:  # informative, not fatal
                header.append(f"initial read failed: {err!r}")
                print(f"Initial read failed: {err!r} (continuing)")

            rec.open(header)

            def on_notify(_char: BleakGATTCharacteristic, data: bytearray) -> None:
                events = rec.packet(bytes(data))
                if args.echo is None:
                    return
                # Mirror mode: answer whatever the pad just sent on its own channel and
                # number, so the LED question needs no knowledge of the current map. A
                # note-on comes back as a note-on, a CC as the same CC; releases and
                # zero values are ignored so anything that lights stays lit.
                for event in events:
                    if event.channel is None or event.data2 == 0:
                        continue
                    if event.type == "note_on":
                        midi = bytes((0x90 | event.channel, event.data1, args.echo))
                    elif event.type == "cc":
                        midi = bytes((0xB0 | event.channel, event.data1, args.echo))
                    else:
                        continue
                    asyncio.get_running_loop().create_task(send(client, midi, rec))

            try:
                await client.start_notify(MIDI_CHAR, on_notify)
            except Exception as err:
                raise SystemExit(
                    f"start_notify failed: {err!r}. If it mentions authentication or "
                    "encryption, retry with --pair."
                ) from err
            rec.started = time.monotonic()
            print("Subscribed. Press pads and turn knobs; Ctrl-C to stop.\n")

            replies: asyncio.Queue[VendorReply] = asyncio.Queue()
            vendor_ops = bool(
                args.rgb or args.led or args.do or args.gatt_write or args.dump_slot is not None
            )
            if vendor_ops and client.services.get_characteristic(VENDOR_NOTIFY_CHAR) is not None:
                # Vendor replies are decoded and queued so reads can wait for their data.
                def on_vendor(_char: BleakGATTCharacteristic, data: bytearray) -> None:
                    packet = bytes(data)
                    reply = parse_reply(packet)
                    elapsed = int((time.monotonic() - rec.started) * 1000)
                    if reply is None:
                        rec.comment(f"+{elapsed} ms vendor notify: {packet.hex(' ').upper()}")
                        return
                    replies.put_nowait(reply)
                    if reply.command == READ_COMMAND:
                        rec.comment(
                            f"+{elapsed} ms vendor read reply 0x{reply.address:04X} "
                            f"{len(reply.data)} bytes"
                        )
                    else:
                        first = describe_vendor_packet(packet).splitlines()[0]
                        rec.comment(f"+{elapsed} ms vendor: {first}")

                await client.start_notify(VENDOR_NOTIFY_CHAR, on_vendor)

            if args.all_notify:
                # Anything the pad sends on a characteristic other than MIDI is logged as a
                # comment, so it stays out of the MIDI fixture but not out of the record.
                def make_other(uuid: str):
                    def on_other(_char: BleakGATTCharacteristic, data: bytearray) -> None:
                        elapsed = int((time.monotonic() - rec.started) * 1000)
                        rec.comment(
                            f"+{elapsed} ms notify on {uuid}: {bytes(data).hex(' ').upper()}"
                        )

                    return on_other

                for service in client.services:
                    for char in service.characteristics:
                        if char.uuid.lower() == MIDI_CHAR or "notify" not in char.properties:
                            continue
                        if vendor_ops and char.uuid.lower() == VENDOR_NOTIFY_CHAR:
                            continue
                        try:
                            await client.start_notify(char, make_other(char.uuid))
                            print(f"  also listening on {char.uuid} ({service.description})")
                        except Exception as err:  # some characteristics refuse; say so and go on
                            print(f"  could not subscribe to {char.uuid}: {err!r}")

            # Ordered steps: --do kind=value, executed in the order given, --send-gap apart.
            for step in args.do or ():
                kind, _, value = step.partition("=")
                if kind == "send":
                    await send(client, bytes.fromhex(value), rec)
                elif kind in ("rgb", "led"):
                    # rgb=PAD:RRGGBB or led=PAD:NOTE, with an optional @BANK suffix.
                    value, _, bank_text = value.partition("@")
                    bank = int(bank_text) if bank_text else args.pad_bank
                    pad, _, arg = value.partition(":")
                    if kind == "rgb":
                        packet = rgb_packet_for_pad(
                            int(pad), *bytes.fromhex(arg), slot=args.preset_slot, bank=bank
                        )
                    else:
                        packet = led_packet_for_pad(
                            int(pad), int(arg), slot=args.preset_slot, bank=bank
                        )
                    await client.write_gatt_char(VENDOR_WRITE_CHAR, packet, response=False)
                    rec.comment(
                        f"vendor {kind} pad {pad} {arg} bank {bank}: "
                        f"wrote {packet.hex(' ').upper()}"
                    )
                elif kind in ("type", "sysex", "channel"):
                    # type=PAD:NAME, sysex=PAD:HEX, channel=PAD:N, optional @BANK suffix.
                    value, _, bank_text = value.partition("@")
                    bank = int(bank_text) if bank_text else args.pad_bank
                    pad, _, arg = value.partition(":")
                    if kind == "type":
                        packet = type_packet_for_pad(int(pad), arg, args.preset_slot, bank)
                    elif kind == "sysex":
                        packet = custom_sysex_packet_for_pad(
                            int(pad), bytes.fromhex(arg), args.preset_slot, bank
                        )
                    else:
                        packet = channel_packet_for_pad(int(pad), int(arg), args.preset_slot, bank)
                    await client.write_gatt_char(VENDOR_WRITE_CHAR, packet, response=False)
                    rec.comment(f"vendor {kind} pad {pad} {arg} bank {bank}")
                elif kind == "read":
                    # read=ADDR:COUNT in region 5, or read=REGION:ADDR:COUNT.
                    parts = value.split(":")
                    region = int(parts[0], 0) if len(parts) == 3 else 5
                    addr, count = int(parts[-2], 0), int(parts[-1])
                    try:
                        data = await vendor_read(client, replies, addr, count, region)
                    except (TimeoutError, RuntimeError) as err:
                        rec.comment(f"read region {region} 0x{addr:04X}: no reply ({err!r})")
                        continue
                    rec.comment(f"read region {region} 0x{addr:04X}: {data.hex(' ').upper()}")
                elif kind == "write":
                    addr, _, hexdata = value.partition(":")
                    packet = write_packet(int(addr, 0), bytes.fromhex(hexdata))
                    await client.write_gatt_char(VENDOR_WRITE_CHAR, packet, response=False)
                    rec.comment(f"vendor write 0x{int(addr, 0):04X}: {hexdata}")
                elif kind == "dump":
                    image = await dump_slot(client, replies, int(value), rec)
                    if args.dump_file:
                        Path(args.dump_file).write_bytes(image)
                        rec.comment(f"slot {value} image written to {args.dump_file}")
                    continue
                elif kind == "state":
                    block = await vendor_read(
                        client, replies, STATE_ADDRESS, STATE_LENGTH, STATE_REGION
                    )
                    state = parse_state(block)
                    rec.comment(
                        f"display state: preset slot {state.slot}, bank state {state.bank_state}, "
                        f"image bank {state.bank}"
                    )
                    continue
                elif kind == "wait":
                    await asyncio.sleep(float(value))
                    continue
                else:
                    raise SystemExit(
                        f"--do {step!r}: kind must be send, rgb, led, read, write, dump or wait"
                    )
                await asyncio.sleep(args.send_gap)

            # Vendor writes first, so a --send can exercise what they set up.
            for text in args.led or ():
                pad, _, note = text.partition(":")
                try:
                    packet = led_packet_for_pad(
                        int(pad), int(note), slot=args.preset_slot, bank=args.pad_bank
                    )
                except ValueError as err:
                    raise SystemExit(f"--led {text!r}: expected PAD:NOTE ({err})") from None
                if client.services.get_characteristic(VENDOR_WRITE_CHAR) is None:
                    raise SystemExit("this device has no AE41 vendor characteristic")
                await client.write_gatt_char(VENDOR_WRITE_CHAR, packet, response=False)
                rec.comment(
                    f"vendor led pad {pad} answers note {note}: wrote {packet.hex(' ').upper()}"
                )
                await asyncio.sleep(args.send_gap)
            for text in args.rgb or ():
                # Vendor RAM write on the SMC-PAD's AE41 characteristic: volatile, verified
                # by its discoverer on three pads. Not MIDI; see devices/smc_pad.py.
                pad, _, colour = text.partition(":")
                try:
                    packet = rgb_packet_for_pad(
                        int(pad), *bytes.fromhex(colour), slot=args.preset_slot, bank=args.pad_bank
                    )
                except (ValueError, TypeError) as err:
                    raise SystemExit(f"--rgb {text!r}: expected PAD:RRGGBB ({err})") from None
                if client.services.get_characteristic(VENDOR_WRITE_CHAR) is None:
                    raise SystemExit("this device has no AE41 vendor characteristic")
                await client.write_gatt_char(VENDOR_WRITE_CHAR, packet, response=False)
                rec.comment(f"vendor rgb pad {pad} #{colour}: wrote {packet.hex(' ').upper()}")
                await asyncio.sleep(args.send_gap)
            if args.dump_slot is not None:
                image = await dump_slot(client, replies, args.dump_slot, rec)
                if args.dump_file:
                    Path(args.dump_file).write_bytes(image)
                    rec.comment(f"slot {args.dump_slot} image written to {args.dump_file}")
            for text in args.gatt_write or ():
                uuid, _, hexdata = text.partition(":")
                try:
                    data = bytes.fromhex(hexdata)
                except ValueError:
                    raise SystemExit(f"--gatt-write {text!r}: expected UUID:HEX") from None
                await client.write_gatt_char(uuid, data, response=False)
                rec.comment(f"gatt write {uuid}: {data.hex(' ').upper()}")
                await asyncio.sleep(args.send_gap)
            for text in args.send or ():
                try:
                    midi = bytes.fromhex(text)
                except ValueError:
                    raise SystemExit(f"--send {text!r} is not hex") from None
                await send(client, midi, rec)
                await asyncio.sleep(args.send_gap)
            if args.palette is not None:
                await palette_walk(client, args.palette, args.led_channel, rec)
            if args.led_test:
                await led_test(client, args.led_notes, args.led_velocity, args.led_channel, rec)
            if args.light is not None:
                await light(
                    client,
                    args.led_notes,
                    args.led_velocity,
                    args.led_channel,
                    args.light,
                    rec,
                    clear=not args.no_clear,
                )
            if args.interactive:
                await interactive(client, rec, stop)
            else:
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(stop.wait(), timeout=args.seconds)
        finally:
            rec.close()
            closing = True
            with contextlib.suppress(Exception):
                if client.is_connected:
                    await client.disconnect()
    finally:
        if manager is not None:
            with contextlib.suppress(Exception):
                await manager.stop()  # type: ignore[attr-defined]


def pick_target(
    rows: list[tuple[BLEDevice, AdvertisementData]], args: argparse.Namespace
) -> tuple[BLEDevice, AdvertisementData] | None:
    if args.address:
        wanted = args.address.lower()
        for row in rows:
            if row[0].address.lower() == wanted:
                return row
        print(f"{args.address} was not seen in the scan.")
        return None
    candidates = [row for row in rows if is_midi(row[1])]
    if args.name:
        candidates = [
            row
            for row in candidates
            if args.name.lower() in (row[0].name or row[1].local_name or "").lower()
        ]
    if len(candidates) > 1:
        print("More than one BLE-MIDI device; narrow it down with --address or --name:")
        for device, adv in candidates:
            print(f"  {device.address}  {device.name or adv.local_name or '?'}")
        return None
    return candidates[0] if candidates else None


# ------------------------------------------------------------------------------ main


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--proxy", metavar="HOST", help="ESPHome Bluetooth proxy IP or hostname")
    p.add_argument("--psk", help="proxy API encryption key, if it has one")
    p.add_argument("--address", help="target BLE address (UUID on macOS) instead of auto-pick")
    p.add_argument("--name", help="substring of the target's advertised name")
    p.add_argument("--scan-only", action="store_true", help="list devices and exit")
    p.add_argument(
        "--all", action="store_true", help="in the scan listing, show non-MIDI devices too"
    )
    p.add_argument("--scan-seconds", type=float, default=8.0)
    p.add_argument("--connect-timeout", type=float, default=20.0)
    p.add_argument("--seconds", type=float, default=None, help="stop recording after this long")
    p.add_argument(
        "--out", type=Path, help=f"output file (default: {FIXTURE_DIR}/capture-<time>.txt)"
    )
    p.add_argument("--pair", action="store_true", help="pair before subscribing")
    p.add_argument("--led-test", action="store_true", help="light notes, ask what happened, clear")
    p.add_argument(
        "--palette",
        type=int,
        metavar="NOTE",
        help="interactive: light this armed note at twenty velocities, asking the colour each time",
    )
    p.add_argument("--led-notes", type=parse_note_list, default=parse_note_list("1-16"))
    p.add_argument("--led-velocity", type=int, default=5, help="palette index over USB: 5 is green")
    p.add_argument(
        "--led-channel",
        type=int,
        default=1,
        metavar="1-16",
        help="MIDI channel for the LED note-ons; the pad's BLE note map measured on 10",
    )
    p.add_argument(
        "--light",
        type=float,
        metavar="SECONDS",
        help="no questions asked: light --led-notes at --led-velocity, hold this long, clear",
    )
    p.add_argument(
        "--no-clear",
        action="store_true",
        help="with --light: leave the notes lit instead of clearing them at the end",
    )
    p.add_argument(
        "--echo",
        type=int,
        metavar="VELOCITY",
        help="mirror mode: answer every note-on the pad sends with the same note at this velocity",
    )
    p.add_argument(
        "--send",
        action="append",
        metavar="HEX",
        help="MIDI message to send after connecting, e.g. '99 24 7F'; repeatable",
    )
    p.add_argument(
        "--send-gap",
        type=float,
        default=0.3,
        metavar="SECONDS",
        help="pause after each --send message (default 0.3); long gaps let someone watch",
    )
    p.add_argument(
        "--all-notify",
        action="store_true",
        help="also subscribe to every other notify characteristic and log what arrives there",
    )
    p.add_argument(
        "--rgb",
        action="append",
        metavar="PAD:RRGGBB",
        help="SMC-PAD only: set a pad's colour through the vendor RAM write, e.g. 1:FF0000; "
        "PAD is the number printed on the device, PAD1 bottom-left",
    )
    p.add_argument(
        "--led",
        action="append",
        metavar="PAD:NOTE",
        help="SMC-PAD only: set the note a pad's LED answers to, through the vendor RAM write",
    )
    p.add_argument("--preset-slot", type=int, default=0, help="preset slot 0-7 for --rgb/--led")
    p.add_argument("--pad-bank", type=int, default=3, help="pad bank 1-8 for --rgb/--led")
    p.add_argument(
        "--do",
        action="append",
        metavar="KIND=VALUE",
        help="ordered step: send=HEX, rgb=PAD:RRGGBB, led=PAD:NOTE, read=ADDR:COUNT, "
        "write=ADDR:HEX, dump=SLOT or wait=SECONDS; run in the order given, --send-gap apart",
    )
    p.add_argument("--dump-slot", type=int, metavar="SLOT", help="read and decode one preset slot")
    p.add_argument("--dump-file", type=Path, help="with a dump: also save the raw 3539-byte image")
    p.add_argument(
        "--gatt-write",
        action="append",
        metavar="UUID:HEX",
        help="raw write-without-response to any characteristic; repeatable",
    )
    p.add_argument("--interactive", action="store_true", help="prompt for hex MIDI to send")
    p.add_argument("-v", "--verbose", action="store_true", help="debug logging from the BLE stack")
    return p


def main() -> None:
    args = build_parser().parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    try:
        asyncio.run(record(args))
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
