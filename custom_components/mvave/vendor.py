"""A request and response session on the device's vendor GATT channel.

The packet building and decoding live in ``devices/smc_pad.py`` and are pure. This module
only does the input and output: subscribe to the notify characteristic, send a request,
wait for the matching reply.

Everything written here is a RAM edit of the device's stored configuration. It shows on
the preset the device is displaying and it does not survive a power cycle, which is why
the whole session is repeated on every connect. Nothing writes to flash.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Self

from .const import LOGGER
from .devices.smc_pad import (
    PRESET_SIZE,
    READ_COMMAND,
    VENDOR_NOTIFY_CHAR,
    VENDOR_WRITE_CHAR,
    DisplayState,
    Preset,
    VendorReply,
    decode_preset,
    parse_reply,
    parse_state,
    read_packet,
    write_packet,
)

if TYPE_CHECKING:
    from bleak import BleakClient
    from bleak.backends.characteristic import BleakGATTCharacteristic

#: How long to wait for a reply before retrying. Replies were measured at about 410 ms.
REPLY_TIMEOUT = 5.0
REPLY_ATTEMPTS = 3

#: Reads are answered in a single notification, and 256 bytes was measured working. A
#: smaller chunk is used because a long run of back-to-back reads proved more reliable.
READ_CHUNK = 128


class VendorError(RuntimeError):
    """The device did not answer, or answered with a bad checksum."""


class VendorSession:
    """Talks to the device's configuration memory for as long as it is open."""

    def __init__(self, client: BleakClient, address: str) -> None:
        """Wrap an already connected client."""
        self._client = client
        self._address = address
        self._replies: asyncio.Queue[VendorReply] = asyncio.Queue()
        self._open = False

    @property
    def available(self) -> bool:
        """Whether this device has the vendor channel at all."""
        return self._client.services.get_characteristic(VENDOR_WRITE_CHAR) is not None

    async def __aenter__(self) -> Self:
        """Subscribe to replies."""
        await self._client.start_notify(VENDOR_NOTIFY_CHAR, self._on_notify)
        self._open = True
        return self

    async def __aexit__(self, *_exc: object) -> None:
        """Stop listening. Failure here is not worth propagating over a real error."""
        self._open = False
        try:
            await self._client.stop_notify(VENDOR_NOTIFY_CHAR)
        except Exception as err:
            LOGGER.debug("%s: stop_notify on the vendor channel: %r", self._address, err)

    def _on_notify(
        self,
        _characteristic: BleakGATTCharacteristic,
        data: bytearray,
    ) -> None:
        reply = parse_reply(bytes(data))
        if reply is None:
            LOGGER.debug("%s: unrecognised vendor reply %s", self._address, bytes(data).hex(" "))
            return
        self._replies.put_nowait(reply)

    async def read(self, address: int, count: int, region: int = 5) -> bytes:
        """Read memory, retrying if the device does not answer."""
        for attempt in range(1, REPLY_ATTEMPTS + 1):
            await self._client.write_gatt_char(
                VENDOR_WRITE_CHAR, read_packet(address, count, region), response=False
            )
            try:
                reply = await self._await_reply(address)
            except TimeoutError:
                LOGGER.debug(
                    "%s: no vendor reply for 0x%04X (attempt %d)", self._address, address, attempt
                )
                continue
            if not reply.checksum_ok:
                raise VendorError(f"bad checksum reading 0x{address:04X}")
            return reply.data
        raise VendorError(f"no reply reading 0x{address:04X} after {REPLY_ATTEMPTS} attempts")

    async def _await_reply(self, address: int) -> VendorReply:
        """Wait for the read reply that echoes this address, discarding anything else."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + REPLY_TIMEOUT
        while True:
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise TimeoutError
            reply = await asyncio.wait_for(self._replies.get(), remaining)
            if reply.command == READ_COMMAND and reply.address == address:
                return reply

    async def write(self, address: int, data: bytes, region: int = 5) -> None:
        """Write memory. The device acknowledges, but the acknowledgement is not awaited.

        Writes are applied in order on one link, and a read afterwards is the only proof
        that matters, so waiting on each acknowledgement only makes a bulk write slow.
        """
        await self._client.write_gatt_char(
            VENDOR_WRITE_CHAR, write_packet(address, data, region), response=False
        )

    async def read_state(self) -> DisplayState:
        """Which preset slot and pad bank the device is showing."""
        block = await self.read(0x0000, 16, region=4)
        return parse_state(block)

    async def read_preset(self, slot: int) -> Preset:
        """Read and decode one preset image."""
        base = slot * PRESET_SIZE
        image = bytearray()
        while len(image) < PRESET_SIZE:
            chunk = min(READ_CHUNK, PRESET_SIZE - len(image))
            image += await self.read(base + len(image), chunk)
        return decode_preset(bytes(image))
