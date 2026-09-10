"""Framing for outgoing BLE-MIDI packets.

Pure Python: nothing here imports Home Assistant or a Bluetooth library.

RP-052 section 7 requires every packet to open with a header byte and every status byte
to be preceded by a timestamp byte. A sender that does not care about timing may set
both to zero, which is what the SMC-PAD itself does in every packet it sends
(docs/HARDWARE-BLE.md section 3).
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from typing import Final

HEADER_ZERO: Final = 0x80
TIMESTAMP_ZERO: Final = 0x80

#: A packet may not exceed the negotiated MTU minus three bytes (RP-052 section 7).
ATT_OVERHEAD: Final = 3


def frame_midi(midi: bytes) -> bytes:
    """Wrap one MIDI message in a single BLE-MIDI packet with a zero timestamp."""
    if not midi:
        raise ValueError("empty MIDI message")
    if not midi[0] & 0x80:
        raise ValueError(f"first byte {midi[0]:#04x} is not a status byte")
    return bytes((HEADER_ZERO, TIMESTAMP_ZERO)) + midi


def frame_many(messages: Iterable[bytes], mtu: int) -> Iterator[bytes]:
    """Pack messages into as few packets as possible without exceeding the MTU.

    Each message keeps its own timestamp byte, so no running status is used. That costs
    one byte per message and removes any chance of a receiver mis-associating a status.
    """
    limit = mtu - ATT_OVERHEAD
    if limit < len(bytes((HEADER_ZERO, TIMESTAMP_ZERO))) + 1:
        raise ValueError(f"MTU {mtu} is too small to carry a BLE-MIDI packet")
    packet = bytearray()
    for message in messages:
        if not message:
            raise ValueError("empty MIDI message")
        chunk = bytes((TIMESTAMP_ZERO,)) + message
        if not packet:
            packet = bytearray((HEADER_ZERO,))
        elif len(packet) + len(chunk) > limit:
            yield bytes(packet)
            packet = bytearray((HEADER_ZERO,))
        if 1 + len(chunk) > limit:
            raise ValueError(f"message of {len(message)} bytes does not fit an MTU of {mtu}")
        packet += chunk
    if packet:
        yield bytes(packet)
