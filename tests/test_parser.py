"""Table-driven tests for the BLE-MIDI decoder.

Packets here are written by hand from the spec's rules and from the SMC-PAD's
measured USB behaviour (mvave-smc-pad-ableton/docs/HARDWARE.md). None of them
is a recording. Recorded packets go in fixtures/ble_midi_packets/ and every
file there is decoded by test_fixture_files_decode_cleanly.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path

import pytest
from transport import (
    TIMESTAMP_MODULUS,
    MidiEvent,
    ParserState,
    parse_ble_midi,
)

FIXTURES = Path(__file__).parent / "fixtures" / "ble_midi_packets"


def hx(text: str) -> bytes:
    return bytes.fromhex(text)


def parse(*packets: str) -> tuple[list[MidiEvent], ParserState]:
    """Decode packets in sequence through one shared state."""
    state = ParserState()
    events: list[MidiEvent] = []
    for packet in packets:
        events.extend(parse_ble_midi(hx(packet), state))
    return events, state


def ev(
    kind: str,
    timestamp: int,
    raw: str,
    channel: int | None = None,
    data1: int = 0,
    data2: int = 0,
) -> MidiEvent:
    return MidiEvent(kind, timestamp, hx(raw), channel, data1, data2)  # type: ignore[arg-type]


LESSON_SYSEX_PAYLOAD = bytes(range(0x16))  # 22 data bytes, needs two 20-byte packets

WELL_FORMED = [
    pytest.param(
        ["80 80 E0 00 40"],
        [ev("pitch_bend", 0, "E0 00 40", 0, 0x00, 0x40)],
        id="single message",
    ),
    pytest.param(
        ["80 80 90 3C 64 81 80 3C 5A"],
        [ev("note_on", 0, "90 3C 64", 0, 0x3C, 0x64), ev("note_off", 1, "80 3C 5A", 0, 0x3C, 0x5A)],
        id="two full messages",
    ),
    pytest.param(
        ["80 80 90 3C 64 3C 00"],
        [ev("note_on", 0, "90 3C 64", 0, 0x3C, 0x64), ev("note_off", 0, "90 3C 00", 0, 0x3C, 0)],
        id="running status inherits the timestamp",
    ),
    pytest.param(
        ["86 C1 90 01 7F C3 02 7F"],
        [ev("note_on", 833, "90 01 7F", 0, 1, 127), ev("note_on", 835, "90 02 7F", 0, 2, 127)],
        id="running status with its own timestamp",
    ),
    pytest.param(
        ["87 B9 90 01 00 BB 02 00"],
        [ev("note_off", 953, "90 01 00", 0, 1, 0), ev("note_off", 955, "90 02 00", 0, 2, 0)],
        id="SMC-PAD releases: BB is a timestamp by position, not a control change",
    ),
    pytest.param(
        ["86 FE 90 05 7F 81 90 06 7F"],
        [ev("note_on", 894, "90 05 7F", 0, 5, 127), ev("note_on", 897, "90 06 7F", 0, 6, 127)],
        id="FE is a timestamp by position, not active sensing; low bits wrap",
    ),
    pytest.param(
        ["80 80 E0 00 00 7F 7F 81 FE 81 00 40"],
        [
            ev("pitch_bend", 0, "E0 00 00", 0, 0, 0),
            ev("pitch_bend", 0, "E0 7F 7F", 0, 0x7F, 0x7F),
            ev("realtime", 1, "FE"),
            ev("pitch_bend", 1, "E0 00 40", 0, 0, 0x40),
        ],
        id="a system message does not cancel running status",
    ),
    pytest.param(
        ["80 80 E0 00 40 81 FE"],
        [ev("pitch_bend", 0, "E0 00 40", 0, 0, 0x40), ev("realtime", 1, "FE")],
        id="real-time arrives deinterleaved",
    ),
    pytest.param(
        ["80 80 C0 05 06"],
        [ev("program_change", 0, "C0 05", 0, 5), ev("program_change", 0, "C0 06", 0, 6)],
        id="one-data-byte message under running status",
    ),
    pytest.param(
        ["80 80 F2 10 20 81 F6"],
        [ev("system", 0, "F2 10 20", None, 0x10, 0x20), ev("system", 1, "F6")],
        id="system common messages",
    ),
    pytest.param(
        ["80 80 F0 43 12 00 81 F7"],
        [ev("sysex", 0, "F0 43 12 00 F7")],
        id="SysEx in one packet",
    ),
    pytest.param(
        ["80 80 F0 43 12 00", "80 43 12 00 43 12 00 81 F7"],
        [ev("sysex", 0, "F0 43 12 00 43 12 00 43 12 00 F7")],
        id="SysEx across two packets: continuation has no timestamp",
    ),
    pytest.param(
        ["80 80 F0 43 81 FE 12 00 82 F7"],
        [ev("realtime", 1, "FE"), ev("sysex", 0, "F0 43 12 00 F7")],
        id="real-time inside SysEx is returned first; SysEx keeps its F0 time",
    ),
    pytest.param(
        ["80 80 F0 01 02", "80 81 F7"],
        [ev("sysex", 0, "F0 01 02 F7")],
        id="SysEx closing at a packet boundary",
    ),
    pytest.param(
        [
            "86 C1 F0 " + LESSON_SYSEX_PAYLOAD[:17].hex(" "),
            "86 " + LESSON_SYSEX_PAYLOAD[17:].hex(" ") + " C2 F7",
        ],
        [ev("sysex", 833, "F0 " + LESSON_SYSEX_PAYLOAD.hex(" ") + " F7")],
        id="22-byte SysEx over two 20-byte packets",
    ),
    pytest.param(
        ["BF FF 90 3C 64 80 80 3C 5A"],
        [
            ev("note_on", 8191, "90 3C 64", 0, 0x3C, 0x64),
            ev("note_off", 0, "80 3C 5A", 0, 0x3C, 0x5A),
        ],
        id="timestamp overflow: 8191 then 0, high bits wrap with the low bits",
    ),
    pytest.param([""], [], id="empty packet, as the initial read returns"),
    pytest.param(["80"], [], id="header-only packet"),
]


@pytest.mark.parametrize(("packets", "expected"), WELL_FORMED)
def test_well_formed(packets: list[str], expected: list[MidiEvent]) -> None:
    events, state = parse(*packets)
    assert events == expected
    assert state.errors == 0, state.last_error
    assert not state.in_sysex


def test_overflow_delta_is_one_millisecond() -> None:
    events, _ = parse("BF FF 90 3C 64 80 80 3C 5A")
    assert (events[1].timestamp - events[0].timestamp) % TIMESTAMP_MODULUS == 1


def test_note_on_velocity_zero_keeps_raw_bytes() -> None:
    (event,), _ = parse("80 80 90 01 00")
    assert event.type == "note_off"
    assert event.raw == hx("90 01 00")
    assert event.status == 0x90


def test_sysex_payload_excludes_delimiters() -> None:
    (event,), _ = parse("80 80 F0 43 12 00 81 F7")
    assert event.sysex_payload == hx("43 12 00")
    (note,), _ = parse("80 80 90 01 7F")
    assert note.sysex_payload == b""


def test_channel_is_zero_based() -> None:
    (event,), _ = parse("80 80 9F 01 7F")
    assert event.channel == 15


MALFORMED = [
    pytest.param(["10 80 90 01 7F"], 0, "not a header", id="first byte is not a header"),
    pytest.param(["80 01 02"], 0, "no SysEx open", id="data after header with no SysEx open"),
    pytest.param(["80 80 01 02"], 0, "no running status", id="data with no running status"),
    pytest.param(["80 80 90 90 01 7F"], 0, "inside a", id="status byte inside a message"),
    pytest.param(
        ["80 80 90 01 7F", "80 81 02 7F"],
        1,
        "no running status",
        id="running status does not survive the packet",
    ),
    pytest.param(["80 80 90 01"], 0, "ended inside", id="message cut off by the packet end"),
    pytest.param(["80 80"], 0, "after a timestamp", id="timestamp with nothing after it"),
    pytest.param(["80 80 F7"], 0, "no SysEx open", id="end of SysEx with none open"),
    pytest.param(
        ["80 80 F0 01 81 02"], 0, "inside SysEx", id="data after a timestamp inside SysEx"
    ),
    pytest.param(["80 80 F0 01 81"], 0, "inside SysEx", id="SysEx packet ends after a timestamp"),
    pytest.param(
        ["80 80 F0 01 02", "10 80"], 0, "not a header", id="a bad packet abandons the SysEx"
    ),
]


@pytest.mark.parametrize(("packets", "events_before", "reason"), MALFORMED)
def test_malformed(packets: list[str], events_before: int, reason: str) -> None:
    events, state = parse(*packets)
    assert len(events) == events_before
    assert state.errors == 1
    assert state.last_error is not None and reason in state.last_error
    assert not state.in_sysex


def test_status_inside_sysex_abandons_it_and_decodes_the_status() -> None:
    events, state = parse("80 80 F0 01 02 81 90 05 7F")
    assert events == [ev("note_on", 1, "90 05 7F", 0, 5, 127)]
    assert state.errors == 1
    assert not state.in_sysex


def test_stray_end_of_sysex_does_not_stop_the_packet() -> None:
    events, state = parse("80 80 F7 81 90 01 7F")
    assert events == [ev("note_on", 1, "90 01 7F", 0, 1, 127)]
    assert state.errors == 1


def test_new_sysex_start_inside_sysex_restarts_it() -> None:
    events, state = parse("80 80 F0 01 02 81 F0 03 82 F7")
    assert events == [ev("sysex", 1, "F0 03 F7")]
    assert state.errors == 1


def test_recovery_after_a_dropped_packet() -> None:
    state = ParserState()
    assert parse_ble_midi(hx("80 80 90 90"), state) == []
    assert state.errors == 1
    assert parse_ble_midi(hx("80 80 90 01 7F"), state) == [ev("note_on", 0, "90 01 7F", 0, 1, 127)]
    assert state.errors == 1


def load_packets(path: Path) -> list[bytes]:
    """Read a fixture file: one hex packet per line, '#' starts a comment."""
    packets = []
    for line in path.read_text().splitlines():
        body = line.split("#", 1)[0].strip()
        if body:
            packets.append(hx(body))
    return packets


@pytest.mark.parametrize("path", sorted(FIXTURES.glob("*.txt")), ids=lambda p: p.name)
def test_fixture_files_decode_cleanly(path: Path) -> None:
    state = ParserState()
    events = [event for packet in load_packets(path) for event in parse_ble_midi(packet, state)]
    assert state.errors == 0, state.last_error
    assert not state.in_sysex
    assert events


def test_synthetic_smc_pad_fixture_shape() -> None:
    state = ParserState()
    packets = load_packets(FIXTURES / "synthetic_smc_pad.txt")
    events = [event for packet in packets for event in parse_ble_midi(packet, state)]
    assert Counter(event.type for event in events) == {"note_on": 3, "note_off": 3, "cc": 8}
    assert all(event.channel == 0 for event in events)
    assert {event.data2 for event in events if event.type == "cc"} == {63, 65}
    presses = [event.data1 for event in events if event.type == "note_on"]
    assert presses == [1, 16, 17]
