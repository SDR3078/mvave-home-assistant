"""M-Vave SMC-PAD specifics that are not MIDI.

The pad's vendor GATT service is a Jieli data tunnel: service ``AE40``, characteristic
``AE41`` for writes without response, ``AE42`` for notifications. MidiSuite uses it for
configuration. Two commands on it matter here, both measured on the device on 2026-09-09:

- ``00 59 23`` reads memory; ``00 59 22`` writes it. Three length bytes follow, little
  endian, then a payload of region, 32-bit address, 24-bit count and, for a write, the
  data; the last byte is the one's complement of the payload sum. Replies arrive on
  ``AE42`` in the same framing, a write answered by ``00 59 00 01 00 00 00 FF``.
- Region 5 holds the eight preset slots back to back, each the 3539-byte image that
  MidiSuite exports as ``.spc`` (mvave-smc-pad-ableton HARDWARE.md 5.2): five 23-byte
  button records, sixteen 6-byte encoder records, then eight banks of sixteen 26-byte pad
  records. A pad record is ``type, channel, note, min velocity, max velocity, r, g, b,
  led, payload length, payload`` with the channel 0-based. The ``led`` byte is the note
  number the pad's LED answers to, 0xFF for none; the MCP type sets it equal to the note,
  which is why only MCP pads lit over USB. An encoder record is ``mode, 02, 00, cc, min,
  max``; mode 3 is relative, sending ``min`` per counter-clockwise step and ``max`` per
  clockwise. A button record is ``type, channel, number, 00, 7F, payload length, sixteen
  payload bytes, led``.

Writes to region 5 are volatile: a power cycle restores the stored preset. The colour
write itself was found by ZzpsS/m-vave-codex-bridge (MIT) from MidiSuite USB captures;
the memory layout and the read command by this project. Nothing here writes to flash.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from .layout import ButtonSpec, DeviceLayout, KnobSpec, PadSpec

VENDOR_SERVICE: Final = "0000ae40-0000-1000-8000-00805f9b34fb"
VENDOR_WRITE_CHAR: Final = "0000ae41-0000-1000-8000-00805f9b34fb"
VENDOR_NOTIFY_CHAR: Final = "0000ae42-0000-1000-8000-00805f9b34fb"

READ_COMMAND: Final = 0x23
WRITE_COMMAND: Final = 0x22
PRESET_REGION: Final = 0x05
ACK: Final = bytes.fromhex("00 59 00 01 00 00 00 FF")

PRESET_SLOTS: Final = 8
PRESET_SIZE: Final = 3539
BUTTON_COUNT: Final = 5
BUTTON_RECORD_SIZE: Final = 23
ENCODER_TABLE_OFFSET: Final = 115
ENCODER_COUNT: Final = 16
ENCODER_RECORD_SIZE: Final = 6
PAD_TABLE_OFFSET: Final = 211
PAD_BANKS: Final = 8
PAD_COUNT: Final = 16
PAD_RECORD_SIZE: Final = 26
PAD_TYPE_OFFSET: Final = 0
PAD_CHANNEL_OFFSET: Final = 1
PAD_NOTE_OFFSET: Final = 2
PAD_RGB_OFFSET: Final = 5
PAD_LED_OFFSET: Final = 8
PAD_PAYLOAD_OFFSET: Final = 9  # length byte, then up to 16 bytes of SysEx for Custom pads
PAD_PAYLOAD_MAX: Final = 16
BUTTON_LED_OFFSET: Final = 22
ENCODER_MODE_ABSOLUTE: Final = 0x00
ENCODER_MODE_RELATIVE: Final = 0x03
#: What a relative encoder sends per step once armed. The two values are configurable
#: (section 7), and these are the centre-64 convention every other controller uses, so
#: a step is the value minus this centre.
ENCODER_CENTRE: Final = 64
ENCODER_STEP_DOWN: Final = 63
ENCODER_STEP_UP: Final = 65
LED_NONE: Final = 0xFF

# Region 4 starts with a live state block. Byte 6 is the base pad bank counted from zero,
# stepped by the Shift+octave combinations, default 2 for image bank 3. Byte 10 is the
# preset slot on display, counted from zero. Byte 11 is the PAD BANK toggle: 0 shows the
# base bank, 1 shows image bank 8. All measured 2026-09-09 by reading before and after
# the owner changed them.
STATE_REGION: Final = 0x04
STATE_ADDRESS: Final = 0x0000
STATE_LENGTH: Final = 16
STATE_BASE_BANK_OFFSET: Final = 6
STATE_SLOT_OFFSET: Final = 10
STATE_BANK_OFFSET: Final = 11
TOGGLE_BANK: Final = 8

# The editor's Type dropdown, in order; the pad record's first byte is the index.
PAD_TYPES: Final = ("Note", "CC Toggle", "Momentary", "Program", "MCP", "Custom", "Comb MCP")

# The device numbers its pads PAD1 to PAD16 from the bottom-left corner, row by row upward
# (HARDWARE.md 5.3), and a bank's records follow that numbering: record 0 is PAD1. The
# factory preset puts notes 36 to 51 on records 0 to 15, so the owner's "pad 1" that sends
# note 36 is the bottom-left pad. For a display laid out top-left first, this gives the
# pad number at each reading-order position.
PAD_NUMBER_BY_READING_ORDER: Final = (13, 14, 15, 16, 9, 10, 11, 12, 5, 6, 7, 8, 1, 2, 3, 4)


# ------------------------------------------------------------------------ packets


def vendor_packet(command: int, region: int, address: int, data_or_count: bytes | int) -> bytes:
    """Frame a read (count) or write (data) for the AE41 characteristic."""
    if isinstance(data_or_count, int):
        count, data = data_or_count, b""
    else:
        count, data = len(data_or_count), data_or_count
    payload = bytes((region, *address.to_bytes(4, "little"), *count.to_bytes(3, "little"))) + data
    checksum = (~sum(payload)) & 0xFF
    return (
        bytes((0x00, 0x59, command, *len(payload).to_bytes(3, "little")))
        + payload
        + bytes((checksum,))
    )


def read_packet(address: int, count: int, region: int = PRESET_REGION) -> bytes:
    return vendor_packet(READ_COMMAND, region, address, count)


def write_packet(address: int, data: bytes, region: int = PRESET_REGION) -> bytes:
    return vendor_packet(WRITE_COMMAND, region, address, data)


@dataclass(frozen=True, slots=True)
class VendorReply:
    command: int
    region: int
    address: int
    data: bytes
    checksum_ok: bool


def parse_reply(packet: bytes) -> VendorReply | None:
    """Decode a notification from AE42; None if it is not vendor framing."""
    if len(packet) < 7 or packet[0] != 0x00 or packet[1] != 0x59:
        return None
    command = packet[2]
    length = int.from_bytes(packet[3:6], "little")
    payload = packet[6 : 6 + length]
    checksum_ok = len(packet) > 6 + length and (sum(payload) + packet[6 + length]) & 0xFF == 0xFF
    if command in (READ_COMMAND, WRITE_COMMAND) and len(payload) >= 8:
        count = int.from_bytes(payload[5:8], "little")
        return VendorReply(
            command,
            payload[0],
            int.from_bytes(payload[1:5], "little"),
            payload[8 : 8 + count],
            checksum_ok,
        )
    return VendorReply(command, 0, 0, payload, checksum_ok)


# ------------------------------------------------------------------ memory layout


def pad_record_address(record_index: int, slot: int = 0, bank: int = 3) -> int:
    """Address in region 5 of a pad record: ``slot`` 0-7, ``bank`` 1-8, record 0-15."""
    _check("record_index", record_index, PAD_COUNT - 1)
    _check("slot", slot, PRESET_SLOTS - 1)
    if not 1 <= bank <= PAD_BANKS:
        raise ValueError(f"bank must be 1 to {PAD_BANKS}, got {bank}")
    return (
        slot * PRESET_SIZE
        + PAD_TABLE_OFFSET
        + ((bank - 1) * PAD_COUNT + record_index) * PAD_RECORD_SIZE
    )


def rgb_packet(
    record_index: int, red: int, green: int, blue: int, slot: int = 0, bank: int = 3
) -> bytes:
    """The 18-byte write that sets one pad record's colour. Defaults are the bridge's target."""
    for name, value in (("red", red), ("green", green), ("blue", blue)):
        _check(name, value, 0xFF)
    address = pad_record_address(record_index, slot, bank) + PAD_RGB_OFFSET
    return write_packet(address, bytes((red, green, blue)))


def rgb_packet_for_pad(
    pad_number: int, red: int, green: int, blue: int, slot: int = 0, bank: int = 3
) -> bytes:
    """Same, addressed by the pad number printed on the device, PAD1 bottom-left to PAD16."""
    return rgb_packet(_record_for_pad(pad_number), red, green, blue, slot, bank)


def led_packet_for_pad(pad_number: int, led_note: int, slot: int = 0, bank: int = 3) -> bytes:
    """Write the note a pad's LED answers to, ``LED_NONE`` to disable host lighting."""
    _check("led_note", led_note, 0xFF)
    address = pad_record_address(_record_for_pad(pad_number), slot, bank) + PAD_LED_OFFSET
    return write_packet(address, bytes((led_note,)))


def type_packet_for_pad(pad_number: int, pad_type: str, slot: int = 0, bank: int = 3) -> bytes:
    """Write a pad's type, one of ``PAD_TYPES``; takes effect on the next press."""
    address = pad_record_address(_record_for_pad(pad_number), slot, bank) + PAD_TYPE_OFFSET
    return write_packet(address, bytes((PAD_TYPES.index(pad_type),)))


def channel_packet_for_pad(pad_number: int, channel: int, slot: int = 0, bank: int = 3) -> bytes:
    """Write a pad's MIDI channel, 0-based as on the wire."""
    _check("channel", channel, 15)
    address = pad_record_address(_record_for_pad(pad_number), slot, bank) + PAD_CHANNEL_OFFSET
    return write_packet(address, bytes((channel,)))


def custom_sysex_packet_for_pad(
    pad_number: int, sysex: bytes, slot: int = 0, bank: int = 3
) -> bytes:
    """Write the SysEx a Custom-typed pad sends verbatim on press, F0 to F7 included."""
    if not 1 <= len(sysex) <= PAD_PAYLOAD_MAX:
        raise ValueError(f"payload must be 1 to {PAD_PAYLOAD_MAX} bytes, got {len(sysex)}")
    address = pad_record_address(_record_for_pad(pad_number), slot, bank) + PAD_PAYLOAD_OFFSET
    return write_packet(address, bytes((len(sysex),)) + sysex)


def encoder_record_packet(
    index: int, cc: int, relative: bool, minimum: int, maximum: int, slot: int = 0
) -> bytes:
    """Write a whole encoder record: mode flag, CC number and the two range bytes.

    In absolute mode the encoder counts within ``minimum`` to ``maximum``. In relative mode
    it sends ``minimum`` for a counter-clockwise step and ``maximum`` for a clockwise one,
    so 63 and 65 give the centre-64 convention and 127 and 1 the two's-complement one.
    """
    _check("index", index, ENCODER_COUNT - 1)
    _check("cc", cc, 127)
    _check("minimum", minimum, 127)
    _check("maximum", maximum, 127)
    address = slot * PRESET_SIZE + ENCODER_TABLE_OFFSET + index * ENCODER_RECORD_SIZE
    mode = ENCODER_MODE_RELATIVE if relative else ENCODER_MODE_ABSOLUTE
    return write_packet(address, bytes((mode, 0x02, 0x00, cc, minimum, maximum)))


def button_led_address(index: int, slot: int = 0) -> int:
    """Address in region 5 of one button's LED byte."""
    _check("index", index, BUTTON_COUNT - 1)
    _check("slot", slot, PRESET_SLOTS - 1)
    return slot * PRESET_SIZE + index * BUTTON_RECORD_SIZE + BUTTON_LED_OFFSET


def button_led_packet(index: int, led_note: int, slot: int = 0) -> bytes:
    """Write the note a button's LED answers to.

    Buttons are indexed 0 to 4: left, right, play, stop, record.
    """
    _check("index", index, BUTTON_COUNT - 1)
    _check("led_note", led_note, 0xFF)
    address = slot * PRESET_SIZE + index * BUTTON_RECORD_SIZE + BUTTON_LED_OFFSET
    return write_packet(address, bytes((led_note,)))


def _record_for_pad(pad_number: int) -> int:
    if not 1 <= pad_number <= PAD_COUNT:
        raise ValueError(f"pad_number must be 1 to {PAD_COUNT}, got {pad_number}")
    return pad_number - 1


# ----------------------------------------------------------------- display state


@dataclass(frozen=True, slots=True)
class DisplayState:
    """Which preset slot and pad bank the pad is showing, from the region 4 state block."""

    slot: int
    base_bank: int  # 0-based image bank index chosen by the octave keys
    bank_state: int  # PAD BANK toggle

    @property
    def bank(self) -> int:
        """The image bank (1-8) on display: bank 8 while toggled, else the base bank."""
        return TOGGLE_BANK if self.bank_state else self.base_bank + 1


def state_read_packet() -> bytes:
    return read_packet(STATE_ADDRESS, STATE_LENGTH, STATE_REGION)


def parse_state(block: bytes) -> DisplayState:
    """Decode the state block; raises on values outside what has been observed."""
    if len(block) < STATE_LENGTH:
        raise ValueError(f"state block is {len(block)} bytes, need {STATE_LENGTH}")
    slot = block[STATE_SLOT_OFFSET]
    base_bank = block[STATE_BASE_BANK_OFFSET]
    bank_state = block[STATE_BANK_OFFSET]
    _check("slot", slot, PRESET_SLOTS - 1)
    _check("base_bank", base_bank, PAD_BANKS - 1)
    _check("bank_state", bank_state, 1)
    return DisplayState(slot, base_bank, bank_state)


# -------------------------------------------------------------- preset decoding


@dataclass(frozen=True, slots=True)
class PadRecord:
    type: str
    channel: int  # 0-based, as on the wire
    note: int
    min_velocity: int
    max_velocity: int
    rgb: tuple[int, int, int]
    led: int
    payload: bytes  # the SysEx a Custom pad sends, empty otherwise


@dataclass(frozen=True, slots=True)
class EncoderRecord:
    mode: int
    cc: int
    minimum: int
    maximum: int

    @property
    def relative(self) -> bool:
        """Mode 3 sends ``minimum`` per counter-clockwise step and ``maximum`` per clockwise."""
        return self.mode == ENCODER_MODE_RELATIVE


@dataclass(frozen=True, slots=True)
class ButtonRecord:
    type: int
    channel: int
    number: int
    led: int


@dataclass(frozen=True, slots=True)
class Preset:
    buttons: tuple[ButtonRecord, ...]
    encoders: tuple[EncoderRecord, ...]
    banks: tuple[tuple[PadRecord, ...], ...]  # banks[bank-1][record_index]
    #: The bytes this was decoded from. Kept so a rewrite can change a few fields and
    #: leave everything else exactly as the owner configured it.
    image: bytes

    def pad(self, pad_number: int, bank: int) -> PadRecord:
        """A pad by the number printed on the device, PAD1 bottom-left to PAD16 top-right."""
        return self.banks[bank - 1][_record_for_pad(pad_number)]


def decode_preset(image: bytes) -> Preset:
    """Decode one 3539-byte preset image, as read from region 5 or exported as ``.spc``."""
    if len(image) < PRESET_SIZE:
        raise ValueError(f"preset image is {len(image)} bytes, need {PRESET_SIZE}")
    buttons = []
    for i in range(BUTTON_COUNT):
        record = image[i * BUTTON_RECORD_SIZE : (i + 1) * BUTTON_RECORD_SIZE]
        buttons.append(ButtonRecord(record[0], record[1], record[2], record[BUTTON_LED_OFFSET]))
    encoders = []
    for i in range(ENCODER_COUNT):
        start = ENCODER_TABLE_OFFSET + i * ENCODER_RECORD_SIZE
        record = image[start : start + ENCODER_RECORD_SIZE]
        encoders.append(EncoderRecord(record[0], record[3], record[4], record[5]))
    banks = []
    for bank in range(PAD_BANKS):
        records = []
        for index in range(PAD_COUNT):
            start = PAD_TABLE_OFFSET + (bank * PAD_COUNT + index) * PAD_RECORD_SIZE
            record = image[start : start + PAD_RECORD_SIZE]
            kind = PAD_TYPES[record[0]] if record[0] < len(PAD_TYPES) else f"type {record[0]}"
            length = min(record[PAD_PAYLOAD_OFFSET], PAD_PAYLOAD_MAX)
            payload = bytes(record[PAD_PAYLOAD_OFFSET + 1 : PAD_PAYLOAD_OFFSET + 1 + length])
            records.append(
                PadRecord(
                    kind,
                    record[PAD_CHANNEL_OFFSET],
                    record[PAD_NOTE_OFFSET],
                    record[3],
                    record[4],
                    (record[5], record[6], record[7]),
                    record[PAD_LED_OFFSET],
                    payload if kind == "Custom" else b"",
                )
            )
        banks.append(tuple(records))
    return Preset(tuple(buttons), tuple(encoders), tuple(banks), bytes(image[:PRESET_SIZE]))


# ------------------------------------------------------------------ USB wrapping


def sysex_wrap(packet: bytes) -> bytes:
    """The USB form of a vendor packet: the bytes as one little-endian integer, 7 bits per byte.

    Trailing zero bytes vanish in this encoding; ``sysex_unwrap`` restores them from the
    packet's own length field.
    """
    value = int.from_bytes(packet, "little")
    body = bytearray()
    while value:
        body.append(value & 0x7F)
        value >>= 7
    return bytes((0xF0, *body, 0xF7))


def sysex_unwrap(sysex: bytes) -> bytes:
    """Inverse of ``sysex_wrap``: a ``F0 … F7`` SysEx back to the raw vendor packet."""
    if len(sysex) < 2 or sysex[0] != 0xF0 or sysex[-1] != 0xF7:
        raise ValueError("not a SysEx message")
    body = sysex[1:-1]
    if any(b & 0x80 for b in body):
        raise ValueError("SysEx body has a byte with bit 7 set")
    value = 0
    for index, b in enumerate(body):
        value |= b << (7 * index)
    raw = bytearray(value.to_bytes(max(1, (value.bit_length() + 7) // 8), "little"))
    if len(raw) >= 6:
        expected = 6 + int.from_bytes(raw[3:6], "little") + 1
        raw.extend(b"\x00" * (expected - len(raw)))
    return bytes(raw)


def describe_vendor_packet(packet: bytes) -> str:
    """One line per field of a vendor packet, for reading captures and replies."""
    reply = parse_reply(packet)
    if reply is None:
        return f"not a vendor packet: {packet.hex(' ').upper()}"
    lines = [
        f"command 0x{reply.command:02X}  checksum "
        + ("valid" if reply.checksum_ok else "INVALID or missing")
    ]
    if reply.command in (READ_COMMAND, WRITE_COMMAND):
        verb = "read" if reply.command == READ_COMMAND else "write"
        lines.append(
            f"{verb} region 0x{reply.region:02X} address 0x{reply.address:04X} "
            f"{len(reply.data)} bytes: {reply.data.hex(' ').upper()}"
        )
        if reply.region == PRESET_REGION:
            slot, offset = divmod(reply.address, PRESET_SIZE)
            if offset >= PAD_TABLE_OFFSET:
                record, field = divmod(offset - PAD_TABLE_OFFSET, PAD_RECORD_SIZE)
                bank, index = divmod(record, PAD_COUNT)
                lines.append(f"  = slot {slot} bank {bank + 1} record {index} byte {field}")
    else:
        lines.append(f"payload {reply.data.hex(' ').upper()}")
    return "\n".join(lines)


def _check(name: str, value: int, maximum: int) -> None:
    if not 0 <= value <= maximum:
        raise ValueError(f"{name} must be 0 to {maximum}, got {value}")


# ------------------------------------------------------------------- rewriting

PAD_BANK_SIZE: Final = PAD_COUNT * PAD_RECORD_SIZE  # 416 bytes
ENCODER_TABLE_SIZE: Final = ENCODER_COUNT * ENCODER_RECORD_SIZE  # 96 bytes


def pad_bank_address(slot: int, bank: int) -> int:
    """Address in region 5 of the first pad record of one bank."""
    return pad_record_address(0, slot, bank)


def encoder_table_address(slot: int = 0) -> int:
    """Address in region 5 of the sixteen encoder records."""
    _check("slot", slot, PRESET_SLOTS - 1)
    return slot * PRESET_SIZE + ENCODER_TABLE_OFFSET


def armed_pad_bank(image: bytes, bank: int) -> bytes:
    """One pad bank rewritten so the host owns its LEDs.

    Every pad becomes Note-typed and gets its ``led`` byte set to the note it transmits,
    which is what makes it answer to a host note-on. Its channel, note, velocity range and
    stored colour are left alone, so the user's own configuration survives.

    Returns the 416 bytes of that bank, ready to write in one go.
    """
    if len(image) < PRESET_SIZE:
        raise ValueError(f"preset image is {len(image)} bytes, need {PRESET_SIZE}")
    if not 1 <= bank <= PAD_BANKS:
        raise ValueError(f"bank must be 1 to {PAD_BANKS}, got {bank}")
    start = PAD_TABLE_OFFSET + (bank - 1) * PAD_BANK_SIZE
    records = bytearray(image[start : start + PAD_BANK_SIZE])
    for index in range(PAD_COUNT):
        record = index * PAD_RECORD_SIZE
        records[record + PAD_TYPE_OFFSET] = PAD_TYPES.index("Note")
        records[record + PAD_LED_OFFSET] = records[record + PAD_NOTE_OFFSET]
    return bytes(records)


def relative_encoder_table(
    image: bytes, minimum: int = ENCODER_STEP_DOWN, maximum: int = ENCODER_STEP_UP
) -> bytes:
    """The sixteen encoder records rewritten as relative, keeping each one's CC.

    Relative is the only usable mode for an endless control: absolute mode is a counter
    that saturates, so once at either end the encoder sends nothing at all. ``minimum``
    and ``maximum`` are the values sent per counter-clockwise and clockwise step, so the
    defaults give the centre-64 convention.
    """
    if len(image) < PRESET_SIZE:
        raise ValueError(f"preset image is {len(image)} bytes, need {PRESET_SIZE}")
    _check("minimum", minimum, 127)
    _check("maximum", maximum, 127)
    records = bytearray(image[ENCODER_TABLE_OFFSET : ENCODER_TABLE_OFFSET + ENCODER_TABLE_SIZE])
    for index in range(ENCODER_COUNT):
        record = index * ENCODER_RECORD_SIZE
        records[record] = ENCODER_MODE_RELATIVE
        records[record + 4] = minimum
        records[record + 5] = maximum
    return bytes(records)


# --------------------------------------------------------------- factory layout

# What the pad sends on its factory configuration, measured over the radio on
# 2026-09-09 and re-measured after a full factory reset (docs/HARDWARE-BLE.md section 4).
# Pads are numbered as the device numbers them: PAD1 is bottom-left.
FACTORY_PAD_CHANNEL: Final = 9  # channel 10, 0-based on the wire
FACTORY_PAD_FIRST_NOTE: Final = 36
FACTORY_CONTROL_CHANNEL: Final = 0  # channel 1: buttons and encoders
FACTORY_BUTTONS: Final = (
    ("left", "Left", 25),
    ("right", "Right", 26),
    ("play", "Play", 27),
    ("stop", "Stop", 28),
    ("record", "Record", 29),
)
FACTORY_KNOB_FIRST_CC: Final = 30  # bank 1 is 30-37, bank 2 is 38-45

SMC_PAD_FACTORY_LAYOUT: Final = DeviceLayout(
    model="SMC-PAD",
    pads=tuple(
        PadSpec(
            key=f"pad_{number}",
            number=number,
            channel=FACTORY_PAD_CHANNEL,
            note=FACTORY_PAD_FIRST_NOTE + number - 1,
        )
        for number in range(1, PAD_COUNT + 1)
    ),
    buttons=tuple(
        ButtonSpec(key=key, name=name, channel=FACTORY_CONTROL_CHANNEL, cc=cc)
        for key, name, cc in FACTORY_BUTTONS
    ),
    knobs=tuple(
        KnobSpec(
            key=f"knob_{number}",
            number=number,
            channel=FACTORY_CONTROL_CHANNEL,
            ccs={
                1: FACTORY_KNOB_FIRST_CC + number - 1,
                2: FACTORY_KNOB_FIRST_CC + 8 + number - 1,
            },
        )
        for number in range(1, 9)
    ),
)
