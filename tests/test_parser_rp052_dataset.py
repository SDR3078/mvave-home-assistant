"""Cross-check the decoder against trueroad's BLE-MIDI packet data set.

The data set (github.com/trueroad/BLE_MIDI_packet_data_set) transcribes every
packet figure in RP-052 into JSONL, one packet per line as ``{"ns": ..,
"data": [..]}``. It carries no licence, so it is fetched on demand by
``scripts/fetch_rp052_dataset`` into tests/fixtures/external/ (git-ignored)
instead of being vendored. The expected results below are transcribed from
its Readme: raw MIDI bytes and the delta time to the previous message, in
the Readme's "integrated" order, where a SysEx is listed once at its start
time.

Because the parser returns a real-time message received inside a SysEx before
the SysEx itself, results are compared as sorted (timestamp, bytes) pairs;
ordering is covered by test_parser.py.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from transport import (
    TIMESTAMP_MODULUS,
    ParserState,
    parse_ble_midi,
)

DATASET = Path(__file__).parent / "fixtures" / "external" / "BLE_MIDI_packet_data_set"

pytestmark = pytest.mark.skipif(
    not DATASET.is_dir(),
    reason="run scripts/fetch_rp052_dataset to download the (unlicensed, unvendored) data set",
)

SYSEX = "f0 43 12 00 43 12 00 43 12 00 f7"
Expected = list[tuple[str, int | None]]

CASES: dict[str, Expected] = {
    "p3_1_BLE_Packet_with_One_MIDI_Message": [("e0 00 40", None)],
    "p3_2_BLE_Packet_with_Two_MIDI_Messages": [("90 3c 64", None), ("80 3c 5a", 1)],
    "p3_3_2_MIDI_Messages_with_Running_Status": [("90 3c 64", None), ("90 3c 00", 0)],
    "p4_1_Multiple_MIDI_Message_mixed_type": [
        ("90 3c 64", None),
        ("90 3c 00", 1),
        ("90 3d 5a", 0),
        ("90 3d 00", 1),
        ("e0 00 40", 1),
    ],
    "p4_2_System_Messges_Do_Not_Cancel_Running_Status": [
        ("e0 00 00", None),
        ("e0 7f 7f", 0),
        ("fe", 1),
        ("e0 00 40", 0),
    ],
    "p5_1_MIDI_Stream_with_System_Real-Time_Message_in_the_middle_of_another_MIDI_Message": [
        ("e0 00 40", None),
        ("fe", 1),
    ],
    "p6_1_System_Exclusive_Start_and_End_in_1_Packet": [(SYSEX, None)],
    "p6_2_System_Exclusive_Split_Across_2_Packets": [(SYSEX, None)],
    "p6_3_System_Exclusive_Split_Across_3_Packets": [(SYSEX, None)],
    "p6_e1_System_Exclusive_Start_and_End_in_1_Packet_with_System_Real-time": [
        (SYSEX, None),
        ("fe", 1),
    ],
    "p6_e1p_System_Exclusive_Start_and_End_in_1_Packet_with_System_Real-time_plus_Another_Packet": [
        (SYSEX, None),
        ("fe", 1),
        ("e0 00 40", 2),
    ],
    "p6_e2_System_Exclusive_Split_Across_1_Packets_with_System_Real-time": [
        (SYSEX, None),
        ("fe", 1),
        ("fe", 1),
    ],
    "p6_e2p_System_Exclusive_Split_Across_1_Packets_with_System_Real-time_plus_Another_Packet": [
        (SYSEX, None),
        ("fe", 1),
        ("fe", 1),
        ("e0 00 40", 2),
    ],
    "p6_e3_System_Exclusive_Split_Across_3_Packets_with_System_Real-time": [
        (SYSEX, None),
        ("fe", 1),
        ("fe", 1),
        ("fe", 1),
    ],
    "p6_e3p_System_Exclusive_Split_Across_3_Packets_with_System_Real-time_plus_Another_Packet": [
        (SYSEX, None),
        ("fe", 1),
        ("fe", 1),
        ("fe", 1),
        ("e0 00 40", 2),
    ],
    "p7_e1_Overflow_high": [("90 3c 64", None), ("80 3c 5a", 1)],
    "p7_e2_Overflow_low": [("90 3c 64", None), ("80 3c 5a", 1)],
    "p7_e3_Overflow_both": [
        ("90 3c 64", None),
        ("80 3c 5a", 1),
        ("90 3c 50", 1),
        ("80 3c 46", 1),
    ],
}


def load(name: str) -> list[bytes]:
    lines = (DATASET / f"{name}.ns.jsonl").read_text().splitlines()
    return [bytes(json.loads(line)["data"]) for line in lines if line.strip()]


@pytest.mark.parametrize("name", CASES, ids=lambda name: name.split("_", 2)[:2].__str__())
def test_dataset_case(name: str) -> None:
    packets = load(name)
    # The first message's absolute time comes straight from the spec's formula
    # on the first packet's header and timestamp bytes, independent of the parser.
    start = ((packets[0][0] & 0x3F) << 7) | (packets[0][1] & 0x7F)

    state = ParserState()
    events = [event for packet in packets for event in parse_ble_midi(packet, state)]
    assert state.errors == 0, state.last_error
    assert not state.in_sysex

    expected: list[tuple[int, str]] = []
    time = start
    for raw, delta in CASES[name]:
        if delta is not None:
            time = (time + delta) % TIMESTAMP_MODULUS
        expected.append((time, raw))
    actual = [(event.timestamp, event.raw.hex(" ")) for event in events]
    assert sorted(actual) == sorted(expected)


def test_every_dataset_file_has_a_case() -> None:
    files = {path.name.removesuffix(".ns.jsonl") for path in DATASET.glob("*.ns.jsonl")}
    assert files == set(CASES)
