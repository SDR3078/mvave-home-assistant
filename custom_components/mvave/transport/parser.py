"""Receive-side framing for MIDI over Bluetooth Low Energy.

Implements the decoder half of MMA/AMEI RP-052, "Specification for MIDI over
Bluetooth Low Energy (BLE-MIDI) 1.0" (November 2015). Pure Python: no I/O and
no Home Assistant imports, so it is tested from recorded packets alone.

A BLE-MIDI packet is one GATT notification of at most MTU-3 bytes::

    [header][timestamp][MIDI message]([timestamp][MIDI message])*

Header, timestamp and status bytes all have bit 7 set, so they cannot be told
apart by value. They are told apart by position, which is why this is a state
machine rather than a byte scanner. The rules encoded here, by spec section:

- 7: the first byte of a packet is a header carrying timestamp bits 12-7, and
  every status byte is preceded by a timestamp byte carrying bits 6-0.
- 7: running status (status byte omitted) needs a full channel message earlier
  in the same packet, may carry its own timestamp or inherit the previous one,
  survives interleaved system messages, and is cancelled by the end of packet.
- 7: real-time messages are deinterleaved by the sender, except inside SysEx.
- 8: only SysEx spans packets. A continuation packet is a header followed by
  data bytes with no timestamp. Inside SysEx a high-bit byte is a timestamp,
  and the byte after it is F7 (end) or a real-time message.
- 9: timestamps are 13-bit milliseconds. When a timestamp byte's value drops
  below the previous one in the same packet, the receiver adds one to the high
  bits. Consumers take differences modulo ``TIMESTAMP_MODULUS``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Final, Literal

TIMESTAMP_MODULUS: Final = 1 << 13
"""Timestamps are 13-bit milliseconds; take differences modulo this."""

MAX_SYSEX_LENGTH: Final = 64 * 1024
"""A SysEx still open past this many payload bytes is abandoned as malformed."""

EventType = Literal[
    "note_on",
    "note_off",
    "poly_aftertouch",
    "cc",
    "program_change",
    "aftertouch",
    "pitch_bend",
    "sysex",
    "system",
    "realtime",
]

_CHANNEL_TYPES: Final[dict[int, EventType]] = {
    0x80: "note_off",
    0x90: "note_on",
    0xA0: "poly_aftertouch",
    0xB0: "cc",
    0xC0: "program_change",
    0xD0: "aftertouch",
    0xE0: "pitch_bend",
}

# Data bytes following a System Common status. F4 and F5 are undefined and
# carry none. SysEx (F0/F7) and real-time (F8-FF) are handled separately.
_SYSTEM_COMMON_LENGTH: Final[dict[int, int]] = {
    0xF1: 1,
    0xF2: 2,
    0xF3: 1,
    0xF4: 0,
    0xF5: 0,
    0xF6: 0,
}


def _data_length(status: int) -> int:
    """Number of data bytes that follow ``status``."""
    if status < 0xF0:
        return 1 if 0xC0 <= status <= 0xDF else 2
    return _SYSTEM_COMMON_LENGTH.get(status, 0)


@dataclass(frozen=True, slots=True)
class MidiEvent:
    """One decoded MIDI message.

    ``timestamp`` is the sender's 13-bit millisecond clock, 0 to 8191; subtract
    two of them modulo ``TIMESTAMP_MODULUS``. ``channel`` is 0-based as on the
    wire and in mido, and None for system messages. A note-on with velocity 0
    is reported as ``note_off`` while ``raw`` keeps the bytes as received.
    ``raw`` is the complete MIDI message, F0 to F7 inclusive for SysEx.
    """

    type: EventType
    timestamp: int
    raw: bytes
    channel: int | None = None
    data1: int = 0
    data2: int = 0

    @property
    def status(self) -> int:
        """The status byte, for telling F8 clock from FE active sensing and so on."""
        return self.raw[0]

    @property
    def sysex_payload(self) -> bytes:
        """The bytes between F0 and F7. Empty for anything but SysEx."""
        return self.raw[1:-1] if self.type == "sysex" else b""


@dataclass(slots=True)
class ParserState:
    """What survives from one packet to the next.

    Only an unterminated SysEx does, plus error accounting for the caller to
    log. Running status never crosses a packet boundary.
    """

    in_sysex: bool = False
    sysex: bytearray = field(default_factory=bytearray)
    sysex_timestamp: int = 0
    errors: int = 0
    last_error: str | None = None


# Decoder modes. The mode decides what a byte with bit 7 set means.
_AFTER_HEADER: Final = 0  # next: a timestamp
_AFTER_TIMESTAMP: Final = 1  # next: a status byte, or data under running status
_AFTER_MESSAGE: Final = 2  # next: a timestamp, or data under running status
_IN_MESSAGE: Final = 3  # next: a data byte
_IN_SYSEX: Final = 4  # next: SysEx data, or a timestamp
_SYSEX_AFTER_TIMESTAMP: Final = 5  # next: F7, or a real-time byte


def parse_ble_midi(packet: bytes, state: ParserState) -> list[MidiEvent]:
    """Decode one BLE-MIDI packet into MIDI events, in decoding order.

    ``state`` carries an unterminated SysEx across packets and is updated in
    place. On a malformed byte the parser records the error in ``state``,
    abandons any SysEx in progress and discards the rest of the packet; the
    events decoded before that point are still returned. Nothing raises.

    A real-time message received inside a SysEx is returned before it, since
    the SysEx completes only at its F7. The SysEx carries its F0's timestamp.
    """
    events: list[MidiEvent] = []
    if not packet:
        return events  # the initial characteristic read returns no payload (spec 5)
    head = packet[0]
    if not head & 0x80:
        _fail(state, f"first byte {head:02X} is not a header")
        return events

    # Bit 6 of the header is reserved; it is ignored rather than enforced.
    ts_high = head & 0x3F
    ts_low = -1  # low bits of the last timestamp byte seen in this packet
    timestamp = 0  # timestamp of the message being decoded
    running = -1  # running status, cancelled by the end of the packet
    buf = bytearray()  # the channel or system message under construction
    need = 0  # data bytes still needed to complete it
    mode = _IN_SYSEX if state.in_sysex else _AFTER_HEADER

    for b in packet[1:]:
        if b & 0x80:
            if mode in (_AFTER_HEADER, _AFTER_MESSAGE, _IN_SYSEX):
                # Every status byte is preceded by a timestamp, so a high-bit
                # byte here can only be a timestamp, whatever its value.
                low = b & 0x7F
                if low < ts_low:
                    ts_high = (ts_high + 1) & 0x3F
                ts_low = low
                timestamp = (ts_high << 7) | low
                mode = _SYSEX_AFTER_TIMESTAMP if mode == _IN_SYSEX else _AFTER_TIMESTAMP
                continue
            if mode == _IN_MESSAGE:
                _fail(state, f"status {b:02X} inside a {len(buf) + need}-byte message")
                return events
            if mode == _SYSEX_AFTER_TIMESTAMP:
                if b == 0xF7:
                    events.append(_end_sysex(state))
                    mode = _AFTER_MESSAGE
                    continue
                if b >= 0xF8:
                    events.append(MidiEvent("realtime", timestamp, bytes((b,))))
                    mode = _IN_SYSEX
                    continue
                # Only real-time may interrupt a SysEx (spec 8). The status byte
                # itself is unambiguous here, so abandon the SysEx and decode the
                # new message rather than dropping the whole packet.
                _fail(state, f"status {b:02X} inside SysEx")
                mode = _AFTER_TIMESTAMP
            # mode is _AFTER_TIMESTAMP: this is a status byte.
            if b >= 0xF8:
                events.append(MidiEvent("realtime", timestamp, bytes((b,))))
                mode = _AFTER_MESSAGE
            elif b == 0xF0:
                state.in_sysex = True
                state.sysex = bytearray()
                state.sysex_timestamp = timestamp
                mode = _IN_SYSEX
            elif b == 0xF7:
                _fail(state, "end of SysEx with no SysEx open")
                mode = _AFTER_MESSAGE
            else:
                need = _data_length(b)
                if b < 0xF0:
                    running = b
                if need == 0:
                    events.append(_message_event(bytes((b,)), timestamp))
                    mode = _AFTER_MESSAGE
                else:
                    buf = bytearray((b,))
                    mode = _IN_MESSAGE
        elif mode == _IN_MESSAGE:
            buf.append(b)
            need -= 1
            if need == 0:
                events.append(_message_event(bytes(buf), timestamp))
                mode = _AFTER_MESSAGE
        elif mode == _IN_SYSEX:
            if len(state.sysex) >= MAX_SYSEX_LENGTH:
                _fail(state, f"SysEx longer than {MAX_SYSEX_LENGTH} bytes")
                return events
            state.sysex.append(b)
        elif mode in (_AFTER_TIMESTAMP, _AFTER_MESSAGE):
            if running < 0:
                _fail(state, f"data byte {b:02X} with no running status")
                return events
            buf = bytearray((running, b))
            need = _data_length(running) - 1
            if need == 0:
                events.append(_message_event(bytes(buf), timestamp))
                mode = _AFTER_MESSAGE
            else:
                mode = _IN_MESSAGE
        elif mode == _AFTER_HEADER:
            _fail(state, f"data byte {b:02X} after the header with no SysEx open")
            return events
        else:  # _SYSEX_AFTER_TIMESTAMP
            _fail(state, f"data byte {b:02X} after a timestamp inside SysEx")
            return events

    if mode == _IN_MESSAGE:
        _fail(state, "packet ended inside a message")
    elif mode == _AFTER_TIMESTAMP:
        _fail(state, "packet ended after a timestamp")
    elif mode == _SYSEX_AFTER_TIMESTAMP:
        _fail(state, "packet ended after a timestamp inside SysEx")
    return events


def _message_event(raw: bytes, timestamp: int) -> MidiEvent:
    status = raw[0]
    data1 = raw[1] if len(raw) > 1 else 0
    data2 = raw[2] if len(raw) > 2 else 0
    if status >= 0xF0:
        return MidiEvent("system", timestamp, raw, None, data1, data2)
    kind = _CHANNEL_TYPES[status & 0xF0]
    if kind == "note_on" and data2 == 0:
        kind = "note_off"
    return MidiEvent(kind, timestamp, raw, status & 0x0F, data1, data2)


def _end_sysex(state: ParserState) -> MidiEvent:
    raw = bytes((0xF0, *state.sysex, 0xF7))
    state.in_sysex = False
    state.sysex = bytearray()
    return MidiEvent("sysex", state.sysex_timestamp, raw)


def _fail(state: ParserState, reason: str) -> None:
    """Record a malformed byte and abandon any SysEx, which can no longer be trusted."""
    state.errors += 1
    state.last_error = reason
    state.in_sysex = False
    state.sysex = bytearray()
