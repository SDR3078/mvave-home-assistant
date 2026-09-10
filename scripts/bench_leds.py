#!/usr/bin/env python3
"""Measure how fast the pad's LEDs can actually be driven, and whether frames land.

The control surface design assumes ten to fifteen full-grid frames a second, which is
what a ripple animation needs. Nothing had measured it, and the one relevant fact we did
have was discouraging: over USB, 128 back-to-back note-ons overran the device's input
buffer and pads silently failed to light.

Speed alone is the wrong question, because that failure mode is silent, and because
writing without a response through a proxy returns as soon as the message reaches the
ESP32 rather than the pad. So this drives the grid at a series of target rates and then
reads the device's own memory back to see whether the last frame arrived intact.

Two paths, and a pad answers to one or the other depending on its LED byte:

- **palette over MIDI**: three bytes per pad, and RP-052 lets a whole grid share one
  packet, so a full frame is one GATT write. Seven usable colours, no brightness. Nothing
  is stored, so this one is timed and watched rather than verified.
- **24-bit over the vendor channel**: an 18-byte write per pad, sixteen per frame. Any
  colour, real brightness, and the result lands in memory so it can be read back.

Run it with Home Assistant stopped: the pad accepts one central at a time.

    python3 scripts/bench_leds.py --proxy 192.168.69.35
"""

from __future__ import annotations

import argparse
import asyncio
import statistics
import sys
import time
from pathlib import Path

import bleak
import bleak_retry_connector
import habluetooth
from bleak_esphome import APIConnectionManager

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "custom_components" / "mvave"))

from devices.smc_pad import (  # noqa: E402
    LED_NONE,
    PAD_COUNT,
    PAD_RECORD_SIZE,
    PAD_RGB_OFFSET,
    READ_COMMAND,
    led_packet_for_pad,
    pad_bank_address,
    parse_reply,
    read_packet,
    rgb_packet_for_pad,
)
from transport import frame_many  # noqa: E402

MIDI_SERVICE = "03b80e5a-ede8-4b33-a751-6ce34ec4c700"
MIDI_CHAR = "7772e5db-3868-4112-a1a9-f2669d106bf3"
VENDOR_WRITE = "0000ae41-0000-1000-8000-00805f9b34fb"
VENDOR_NOTIFY = "0000ae42-0000-1000-8000-00805f9b34fb"

RATES = (5, 10, 20, 30, 60)
SECONDS_PER_RATE = 2.0
SETTLE_SECONDS = 1.5
REPLY_TIMEOUT = 5.0
COLOURS = ((0, 60, 0), (60, 20, 0))
PALETTE = (5, 15)


class Vendor:
    """Reads the device's configuration memory, matching replies to requests."""

    def __init__(self, client: bleak.BleakClient) -> None:
        self._client = client
        self._replies: asyncio.Queue = asyncio.Queue()

    async def start(self) -> None:
        await self._client.start_notify(VENDOR_NOTIFY, self._on_notify)

    async def stop(self) -> None:
        await self._client.stop_notify(VENDOR_NOTIFY)

    def _on_notify(self, _char: object, data: bytearray) -> None:
        reply = parse_reply(bytes(data))
        if reply is not None:
            self._replies.put_nowait(reply)

    async def read(self, address: int, count: int, region: int = 5, attempts: int = 3) -> bytes:
        """Read memory, ignoring anything that is not the reply to this request.

        Retried, because a request sent while the device is still working through a flood
        of writes is simply dropped rather than queued.
        """
        loop = asyncio.get_running_loop()
        for _ in range(attempts):
            while not self._replies.empty():
                self._replies.get_nowait()
            await self._client.write_gatt_char(
                VENDOR_WRITE, read_packet(address, count, region), response=False
            )
            deadline = loop.time() + REPLY_TIMEOUT
            try:
                while True:
                    reply = await asyncio.wait_for(
                        self._replies.get(), max(0.05, deadline - loop.time())
                    )
                    if reply.command == READ_COMMAND and reply.address == address:
                        return reply.data
            except TimeoutError:
                continue
        raise TimeoutError(f"no reply reading 0x{address:04X}")

    async def round_trip(self) -> float:
        """Milliseconds for a read to come back; it can only answer once the queue drains."""
        start = time.perf_counter()
        await self.read(0x0000, 16, region=4)
        return (time.perf_counter() - start) * 1000


async def connect(proxy: str, address: str | None) -> tuple[bleak.BleakClient, object]:
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


async def set_led_bytes(
    client: bleak.BleakClient, note_base: int | None, slot: int, bank: int
) -> None:
    """Arm every pad to its own note, or disarm every pad when note_base is None."""
    for pad in range(1, PAD_COUNT + 1):
        led = LED_NONE if note_base is None else note_base + pad - 1
        await client.write_gatt_char(
            VENDOR_WRITE, led_packet_for_pad(pad, led, slot=slot, bank=bank), response=False
        )
        await asyncio.sleep(0.05)


async def bench_rgb(client: bleak.BleakClient, vendor: Vendor, slot: int, bank: int) -> None:
    """Drive the grid over the vendor channel, verifying each rate by reading it back."""
    print("\n24-bit over the vendor channel: 16 writes per frame, verified by read-back")
    for rate in RATES:
        frames = max(2, int(SECONDS_PER_RATE * rate))
        start = time.perf_counter()
        for index in range(frames):
            red, green, blue = COLOURS[index % 2]
            for pad in range(1, PAD_COUNT + 1):
                await client.write_gatt_char(
                    VENDOR_WRITE,
                    rgb_packet_for_pad(pad, red, green, blue, slot=slot, bank=bank),
                    response=False,
                )
            target = start + (index + 1) / rate
            await asyncio.sleep(max(0.0, target - time.perf_counter()))
        elapsed = time.perf_counter() - start

        # Let anything still in flight arrive before asking a question.
        await asyncio.sleep(SETTLE_SECONDS)
        try:
            drain = await vendor.round_trip()
            records = await vendor.read(pad_bank_address(slot, bank), PAD_COUNT * PAD_RECORD_SIZE)
        except TimeoutError:
            print(
                f"  {rate:>2} fps: {frames} frames in {elapsed:.1f}s, "
                "DEVICE STOPPED ANSWERING - this rate is past its limit"
            )
            return

        expected = COLOURS[(frames - 1) % 2]
        got = [
            tuple(
                records[
                    i * PAD_RECORD_SIZE + PAD_RGB_OFFSET : i * PAD_RECORD_SIZE + PAD_RGB_OFFSET + 3
                ]
            )
            for i in range(PAD_COUNT)
        ]
        wrong = [i + 1 for i, colour in enumerate(got) if colour != expected]
        verdict = "all 16 pads correct" if not wrong else f"WRONG on pads {wrong}"
        print(
            f"  {rate:>2} fps: {frames} frames in {elapsed:.1f}s, drain {drain:.0f} ms, {verdict}"
        )


async def bench_midi(client: bleak.BleakClient, vendor: Vendor, notes: list[int]) -> None:
    """Drive the grid over MIDI. Nothing is stored, so this is timed rather than verified."""
    print("\npalette over MIDI: 1 write per frame, watch the grid for stutter")
    for rate in RATES:
        frames = max(2, int(SECONDS_PER_RATE * rate))
        start = time.perf_counter()
        for index in range(frames):
            colour = PALETTE[index % 2]
            messages = [bytes((0x99, note, colour)) for note in notes]
            for packet in frame_many(messages, client.mtu_size):
                await client.write_gatt_char(MIDI_CHAR, packet, response=False)
            target = start + (index + 1) / rate
            await asyncio.sleep(max(0.0, target - time.perf_counter()))
        elapsed = time.perf_counter() - start
        drain = await vendor.round_trip()
        print(f"  {rate:>2} fps: {frames} frames in {elapsed:.1f}s, drain {drain:.0f} ms")


async def main() -> None:
    """Measure both LED paths."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--proxy", required=True)
    parser.add_argument("--address")
    parser.add_argument("--slot", type=int, default=0)
    parser.add_argument("--bank", type=int, default=3)
    args = parser.parse_args()

    client, connection = await connect(args.proxy, args.address)
    vendor = Vendor(client)
    await vendor.start()
    try:
        notes = list(range(36, 36 + PAD_COUNT))
        idle = statistics.median([await vendor.round_trip() for _ in range(5)])
        print(f"\nidle round trip: {idle:.0f} ms, the floor for every drain figure below")

        await set_led_bytes(client, None, args.slot, args.bank)
        await bench_rgb(client, vendor, args.slot, args.bank)

        await set_led_bytes(client, notes[0], args.slot, args.bank)
        await bench_midi(client, vendor, notes)

        for packet in frame_many([bytes((0x99, note, 0)) for note in notes], client.mtu_size):
            await client.write_gatt_char(MIDI_CHAR, packet, response=False)
        print("\ngrid left dark, pads left armed as the integration leaves them")
    finally:
        await vendor.stop()
        await client.disconnect()
        await connection.stop()


if __name__ == "__main__":
    asyncio.run(main())
