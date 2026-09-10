"""Transport layer: BLE-MIDI framing, parsing and encoding.

Nothing in this package may import ``homeassistant`` or a Bluetooth library. It is pure
protocol code, exercised by recorded packets and plain pytest, and it is what will be
extracted to PyPI (docs/PLAN.md section 4).

**Import from this package, never from its modules.** Everything a consumer needs is
re-exported here, so the extraction is a change to this one file plus the manifest:

    from .parser import ...      ->      from ble_midi_proto import ...
"""

from __future__ import annotations

from .encoder import ATT_OVERHEAD, frame_many, frame_midi
from .parser import TIMESTAMP_MODULUS, MidiEvent, ParserState, parse_ble_midi

__all__ = [
    "ATT_OVERHEAD",
    "TIMESTAMP_MODULUS",
    "MidiEvent",
    "ParserState",
    "frame_many",
    "frame_midi",
    "parse_ble_midi",
]
