"""The USB SysEx wrapping of vendor packets, and the packet describer."""

from __future__ import annotations

from devices.smc_pad import (
    describe_vendor_packet,
    rgb_packet,
    sysex_unwrap,
    sysex_wrap,
)


def test_wrap_is_seven_bit_clean_and_delimited() -> None:
    wrapped = sysex_wrap(rgb_packet(0, 255, 0, 0))
    assert wrapped[0] == 0xF0 and wrapped[-1] == 0xF7
    assert all(b < 0x80 for b in wrapped[1:-1])


def test_round_trip() -> None:
    for packet in (rgb_packet(0, 255, 0, 0), rgb_packet(15, 1, 2, 3), rgb_packet(7, 0, 0, 0)):
        assert sysex_unwrap(sysex_wrap(packet)) == packet


def test_round_trip_restores_a_trailing_zero_checksum() -> None:
    # 219 + 36 = 255, so the checksum byte is 0x00 and would vanish without the length field.
    packet = rgb_packet(0, 219, 0, 0)
    assert packet[-1] == 0x00
    assert sysex_unwrap(sysex_wrap(packet)) == packet


def test_describe_rgb_write() -> None:
    text = describe_vendor_packet(rgb_packet(3, 0, 0, 255))
    assert "command 0x22" in text and "checksum valid" in text
    assert "address 0x0466" in text and "00 00 FF" in text
    assert "slot 0 bank 3 record 3 byte 5" in text


def test_describe_the_pads_acknowledgement() -> None:
    text = describe_vendor_packet(bytes.fromhex("00 59 00 01 00 00 00 FF"))
    assert "command 0x00" in text and "checksum valid" in text and "payload 00" in text


def test_describe_rejects_other_bytes() -> None:
    assert describe_vendor_packet(bytes.fromhex("80 80 90 01 7F")).startswith("not a vendor packet")
