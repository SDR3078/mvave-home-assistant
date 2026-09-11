"""The SMC-PAD vendor packets and memory layout.

Checked against hand-computed bytes and against one real preset image: the device's own
factory default, read from slot 0 after a full reset and a power cycle on 2026-09-09.

There was a second fixture, a dump of all eight of the owner's slots, and the tests that
used it were the strongest here: they decoded configurations nobody wrote by hand. It was
removed because it is a dump of somebody's own device, and a repository is a poor place
for that. What replaced it is weaker in one specific way, recorded on each test below, and
the measurements it established are in ``docs/HARDWARE-BLE.md`` rather than in a file
anybody has to keep.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from devices.smc_pad import (
    ACK,
    LED_NONE,
    PAD_NUMBER_BY_READING_ORDER,
    PRESET_SIZE,
    decode_preset,
    led_packet_for_pad,
    pad_record_address,
    parse_reply,
    read_packet,
    rgb_packet,
    rgb_packet_for_pad,
)

FACTORY = Path(__file__).parent / "fixtures" / "smc_pad_factory_slot0.bin"


def test_record_zero_red_matches_hand_computed_bytes() -> None:
    # payload 05 | 18 04 00 00 | 03 00 00 | FF 00 00 sums to 0x123; ~0x23 & 0xFF = 0xDC.
    assert rgb_packet(0, 255, 0, 0) == bytes.fromhex(
        "00 59 22 0B 00 00 05 18 04 00 00 03 00 00 FF 00 00 DC"
    )


def test_bridge_default_target_is_slot_zero_bank_three() -> None:
    assert pad_record_address(0) + 5 == 0x0418
    assert rgb_packet(0, 1, 2, 3)[:6] == bytes.fromhex("00 59 22 0B 00 00")


def test_checksum_is_ones_complement_of_payload_sum() -> None:
    packet = rgb_packet(15, 0x12, 0x34, 0x56)
    payload, checksum = packet[6:-1], packet[-1]
    assert (sum(payload) + checksum) & 0xFF == 0xFF
    assert len(packet) == 18


def test_record_address_arithmetic() -> None:
    assert pad_record_address(0, slot=0, bank=1) == 211
    assert pad_record_address(1, slot=0, bank=1) == 211 + 26
    assert pad_record_address(0, slot=0, bank=3) == 0x0413
    assert pad_record_address(0, slot=6, bank=3) == 0x5705
    assert pad_record_address(0, slot=1, bank=1) == PRESET_SIZE + 211


def test_pad_numbers_are_the_devices_own() -> None:
    assert rgb_packet_for_pad(1, 0, 0, 0) == rgb_packet(0, 0, 0, 0)  # PAD1, bottom-left
    assert rgb_packet_for_pad(16, 0, 0, 0) == rgb_packet(15, 0, 0, 0)  # PAD16, top-right
    assert sorted(PAD_NUMBER_BY_READING_ORDER) == list(range(1, 17))
    assert PAD_NUMBER_BY_READING_ORDER[0] == 13  # top-left is PAD13
    assert PAD_NUMBER_BY_READING_ORDER[-1] == 4  # bottom-right is PAD4


def test_led_packet_targets_the_ninth_byte() -> None:
    packet = led_packet_for_pad(1, 36, slot=6, bank=3)
    reply = parse_reply(packet)
    assert reply is not None
    assert reply.address == pad_record_address(0, slot=6, bank=3) + 8
    assert reply.data == bytes((36,))
    assert led_packet_for_pad(1, LED_NONE)[-2] == 0xFF


def test_read_packet_matches_the_one_the_pad_answered() -> None:
    assert read_packet(0x0418, 64) == bytes.fromhex("00 59 23 08 00 00 05 18 04 00 00 40 00 00 9E")


def test_parse_reply_of_a_read() -> None:
    reply = parse_reply(bytes.fromhex("00 59 23 0B 00 00 05 18 04 00 00 03 00 00 FF 00 00 DC"))
    assert reply is not None
    assert (reply.region, reply.address, reply.data, reply.checksum_ok) == (
        5,
        0x0418,
        b"\xff\x00\x00",
        True,
    )


def test_parse_reply_of_the_ack() -> None:
    reply = parse_reply(ACK)
    assert reply is not None
    assert reply.command == 0 and reply.data == b"\x00" and reply.checksum_ok
    assert parse_reply(bytes.fromhex("80 80 90 01 7F")) is None


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"record_index": 16, "red": 0, "green": 0, "blue": 0}, "record_index"),
        ({"record_index": 0, "red": 256, "green": 0, "blue": 0}, "red"),
        ({"record_index": 0, "red": 0, "green": 0, "blue": 0, "slot": 8}, "slot"),
        ({"record_index": 0, "red": 0, "green": 0, "blue": 0, "bank": 0}, "bank"),
    ],
)
def test_out_of_range_is_refused(kwargs: dict[str, int], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        rgb_packet(**kwargs)


def test_pad_number_out_of_range_is_refused() -> None:
    with pytest.raises(ValueError, match="pad_number"):
        rgb_packet_for_pad(0, 0, 0, 0)


def test_state_block_as_read_on_preset_3_after_one_bank_press() -> None:
    from devices.smc_pad import parse_state, state_read_packet

    state = parse_state(bytes.fromhex("78 00 32 04 00 00 02 00 01 01 02 01 00 00 00 00"))
    assert (state.slot, state.base_bank, state.bank_state, state.bank) == (2, 2, 1, 8)
    state = parse_state(bytes.fromhex("78 00 32 04 00 00 02 00 01 01 06 00 00 00 00 00"))
    assert (state.slot, state.base_bank, state.bank_state, state.bank) == (6, 2, 0, 3)
    # After one Shift+octave up on the factory preset, PAD1 sent note 52, bank 4's first.
    state = parse_state(bytes.fromhex("78 00 32 04 00 00 03 00 01 01 00 00 00 00 00 00"))
    assert (state.slot, state.base_bank, state.bank_state, state.bank) == (0, 3, 0, 4)
    assert state_read_packet() == bytes.fromhex("00 59 23 08 00 00 04 00 00 00 00 10 00 00 EB")


def test_state_block_refuses_unseen_values() -> None:
    from devices.smc_pad import parse_state

    with pytest.raises(ValueError, match="bank_state"):
        parse_state(bytes.fromhex("78 00 32 04 00 00 02 00 01 01 02 05 00 00 00 00"))


# ------------------------------------------------- what a rewrite reads back as


def test_field_packets_target_the_right_bytes() -> None:
    from devices.smc_pad import (
        button_led_packet,
        channel_packet_for_pad,
        custom_sysex_packet_for_pad,
        encoder_record_packet,
        type_packet_for_pad,
    )

    base = pad_record_address(0, slot=0, bank=3)  # PAD1 of slot 0, bank 3, 0x0413
    assert parse_reply(channel_packet_for_pad(1, 0)).address == base + 1  # type: ignore[union-attr]
    assert parse_reply(type_packet_for_pad(3, "Custom")).address == base + 2 * 26  # type: ignore[union-attr]
    assert type_packet_for_pad(3, "Custom")[-2] == 5
    packet = custom_sysex_packet_for_pad(3, bytes.fromhex("F0 7F 7F 06 01 F7"))
    reply = parse_reply(packet)
    assert reply is not None and reply.address == base + 2 * 26 + 9
    assert reply.data == bytes.fromhex("06 F0 7F 7F 06 01 F7")
    reply = parse_reply(encoder_record_packet(0, 30, True, 63, 65))
    assert (
        reply is not None
        and reply.address == 115
        and reply.data == bytes.fromhex("03 02 00 1E 3F 41")
    )
    reply = parse_reply(button_led_packet(2, 27))
    assert reply is not None and reply.address == 2 * 23 + 22 and reply.data == b"\x1b"


def test_custom_payload_and_channel_decode() -> None:
    image = bytearray(
        (Path(__file__).parent / "fixtures" / "smc_pad_factory_slot0.bin").read_bytes()
    )
    start = pad_record_address(2, slot=0, bank=3)
    image[start] = 5
    image[start + 9 : start + 16] = bytes.fromhex("06 F0 7F 7F 06 01 F7")
    image[start + 1] = 0
    pad = decode_preset(bytes(image)).pad(3, bank=3)
    assert pad.type == "Custom" and pad.payload == bytes.fromhex("F0 7F 7F 06 01 F7")
    assert pad.channel == 0
    assert decode_preset(bytes(image)).pad(1, bank=3).channel == 9


def test_factory_reset_image_is_the_pristine_default() -> None:
    # Read from slot 0 right after a full factory reset and a power cycle (2026-09-09).
    image = (Path(__file__).parent / "fixtures" / "smc_pad_factory_slot0.bin").read_bytes()
    preset = decode_preset(image)
    assert [b.number for b in preset.buttons] == [25, 26, 27, 28, 29]
    assert [b.type for b in preset.buttons] == [2] * 5
    assert {b.led for b in preset.buttons} == {LED_NONE} and {
        b.channel for b in preset.buttons
    } == {0}
    assert [e.cc for e in preset.encoders] == list(range(30, 46))
    assert not any(e.relative for e in preset.encoders)
    assert {(e.minimum, e.maximum) for e in preset.encoders} == {(0, 127)}
    for bank, first_note in zip(preset.banks, (4, 20, 36, 52, 68, 84, 100, 52), strict=True):
        assert [r.note for r in bank] == list(range(first_note, first_note + 16))
        assert {r.type for r in bank} == {"Note"} and {r.led for r in bank} == {LED_NONE}
        assert {r.channel for r in bank} == {9}, "pads transmit on channel 10"
        assert {(r.min_velocity, r.max_velocity) for r in bank} == {(0, 127)}


def test_a_type_byte_decodes_to_the_type_it_was_measured_to_mean() -> None:
    """Pins what each type byte means, which is not the order the editor lists them in.

    Weaker than it was. A slot configured by hand with one of each type proved the *device*
    numbers them this way; writing the numbers and reading the names back only proves this
    package has not renumbered them since. The measurement is in HARDWARE-BLE.md section 5.
    """
    from devices.smc_pad import PAD_BANK_SIZE, PAD_RECORD_SIZE, PAD_TABLE_OFFSET, PAD_TYPE_OFFSET

    image = bytearray(FACTORY.read_bytes())
    bank = PAD_TABLE_OFFSET + 2 * PAD_BANK_SIZE  # bank 3
    for index in range(7):
        image[bank + index * PAD_RECORD_SIZE + PAD_TYPE_OFFSET] = index

    preset = decode_preset(bytes(image))
    assert [preset.pad(n, bank=3).type for n in range(1, 8)] == [
        "Note",
        "CC Toggle",
        "Momentary",
        "Program",
        "MCP",
        "Custom",
        "Comb MCP",
    ]


def test_arming_a_bank_reads_back_as_armed() -> None:
    """The round trip the integration depends on at every connect.

    Replaces a test that decoded the owner's own Ableton preset, which carried MCP pads
    and relative encoders that nobody wrote by hand. This proves less about what a device
    can hold and more about the pair of functions that actually matter: what the
    integration writes is what the decoder reads back.
    """
    from devices.smc_pad import PAD_BANK_SIZE, PAD_TABLE_OFFSET, armed_pad_bank

    image = bytearray(FACTORY.read_bytes())
    start = PAD_TABLE_OFFSET + 2 * PAD_BANK_SIZE
    image[start : start + PAD_BANK_SIZE] = armed_pad_bank(bytes(image), bank=3)

    bank = decode_preset(bytes(image)).banks[2]
    assert {record.type for record in bank} == {"Note"}
    assert all(record.led == record.note for record in bank), "every pad answers to its own note"
    # Only bank 3 was touched; the rest of the owner's configuration is left alone.
    assert {record.led for record in decode_preset(bytes(image)).banks[1]} == {LED_NONE}


def test_switching_the_encoders_to_relative_reads_back_as_relative() -> None:
    """The other half of the same round trip.

    Absolute encoders saturate and then send nothing, so this write is the difference
    between a knob that works and one that stops. The controller numbers must survive it:
    the entities match on those.
    """
    from devices.smc_pad import (
        ENCODER_TABLE_OFFSET,
        ENCODER_TABLE_SIZE,
        relative_encoder_table,
    )

    image = bytearray(FACTORY.read_bytes())
    before = [record.cc for record in decode_preset(bytes(image)).encoders]
    image[ENCODER_TABLE_OFFSET : ENCODER_TABLE_OFFSET + ENCODER_TABLE_SIZE] = (
        relative_encoder_table(bytes(image))
    )

    encoders = decode_preset(bytes(image)).encoders
    assert all(record.relative for record in encoders)
    assert {(record.minimum, record.maximum) for record in encoders} == {(63, 65)}
    assert [record.cc for record in encoders] == before, "a rewrite must not move a knob"
