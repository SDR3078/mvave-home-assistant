#!/usr/bin/env python3
"""Decode MidiSuite's vendor packets from a USB capture, so this unit's real commands are known.

MidiSuite talks to the SMC-PAD over USB inside SysEx: the vendor packet is packed as one
little-endian integer, 7 bits per byte, between F0 and F7. Over Bluetooth the same packet
goes raw to the AE41 characteristic. Capturing what MidiSuite sends when a pad's colour is
changed, then unpacking it, gives the exact command for this firmware, which is how the
Codex bridge's author found the RGB write for his.

Capture on the Windows PC with Wireshark and USBPcap, MidiSuite connected over USB, then
change one pad's Color and save. Extract the outgoing MIDI event packets with tshark:

    tshark -r capture.pcapng \\
           -Y "usb.transfer_type == 0x03 && usb.endpoint_address.direction == 0" \\
           -T fields -e usb.capdata > out.hex

Then:

    python scripts/decode_midisuite_sysex.py --usb-midi out.hex

Without --usb-midi the input is plain SysEx hex, one message per line, F0 to F7.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "custom_components" / "mvave"))

from devices.smc_pad import (  # noqa: E402
    describe_vendor_packet,
    sysex_unwrap,
)

# USB-MIDI event packets are four bytes: cable/code-index, then up to three MIDI bytes.
# How many of the three are real depends on the code index.
_USB_MIDI_BYTES = {
    0x4: 3,
    0x5: 1,
    0x6: 2,
    0x7: 3,
    0x8: 3,
    0x9: 3,
    0xA: 3,
    0xB: 3,
    0xC: 2,
    0xD: 2,
    0xE: 3,
    0xF: 1,
}


def usb_midi_to_stream(hexdata: str) -> bytes:
    """Concatenate USB-MIDI event packets (hex, any spacing) into a MIDI byte stream."""
    raw = bytes.fromhex("".join(hexdata.split()).replace(":", ""))
    out = bytearray()
    for i in range(0, len(raw) - 3, 4):
        cin = raw[i] & 0x0F
        out.extend(raw[i + 1 : i + 1 + _USB_MIDI_BYTES.get(cin, 0)])
    return bytes(out)


def sysex_messages(stream: bytes) -> list[bytes]:
    messages = []
    start = None
    for i, b in enumerate(stream):
        if b == 0xF0:
            start = i
        elif b == 0xF7 and start is not None:
            messages.append(stream[start : i + 1])
            start = None
    return messages


def main() -> None:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("path", nargs="?", help="input file; stdin if omitted")
    p.add_argument("--usb-midi", action="store_true", help="input is tshark usb.capdata, not SysEx")
    args = p.parse_args()
    text = Path(args.path).read_text() if args.path else sys.stdin.read()

    if args.usb_midi:
        messages = sysex_messages(usb_midi_to_stream(text))
    else:
        messages = []
        for line in text.splitlines():
            line = line.strip()
            if line:
                messages.append(bytes.fromhex(line))

    for n, sysex in enumerate(messages, 1):
        print(f"--- message {n}: {len(sysex)} bytes")
        try:
            packet = sysex_unwrap(sysex)
        except ValueError as err:
            print(f"    cannot unwrap: {err}")
            continue
        print(f"    raw {packet.hex(' ').upper()}")
        for line in describe_vendor_packet(packet).splitlines():
            print(f"    {line}")


if __name__ == "__main__":
    main()
