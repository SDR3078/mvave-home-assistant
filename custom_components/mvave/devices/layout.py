"""What controls a device has, and which MIDI messages belong to each.

Pure Python: nothing here imports Home Assistant.

A layout describes the *physical* controls, because that is what a user points an
automation at. One entity per pad, per button and per encoder, rather than one entity
carrying a pad number as an attribute: Home Assistant's event trigger filters on the
event type only, aimed at an entity, so a shared entity makes "pad 5 was pressed"
inexpressible.

The note and controller numbers a device sends are per-preset rather than fixed, so a
layout is a starting point. The device's own map can be read over the vendor channel and
used to rebuild one (docs/HARDWARE-BLE.md section 9).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class PadSpec:
    """A velocity-sensitive pad that sends note on and note off."""

    key: str  # stable, forms part of the entity's unique id
    number: int  # as printed on the device
    channel: int  # 0-based, as on the wire
    note: int


@dataclass(frozen=True, slots=True)
class ButtonSpec:
    """A transport or function button that sends a control change, 127 then 0."""

    key: str
    name: str
    channel: int
    cc: int


@dataclass(frozen=True, slots=True)
class KnobSpec:
    """One physical encoder.

    ``ccs`` maps a bank number to the controller that bank sends on, because the banks
    are a mode of the same physical control rather than separate controls.
    """

    key: str
    number: int
    channel: int
    ccs: dict[int, int]

    def bank_of(self, cc: int) -> int | None:
        """Which bank a controller number belongs to, or None if it is not ours."""
        for bank, number in self.ccs.items():
            if number == cc:
                return bank
        return None


@dataclass(frozen=True, slots=True)
class DeviceLayout:
    """Every control on one device.

    A control is looked up by its key rather than held onto, because the layout is
    replaced wholesale every time the device is armed: the numbers change with the preset
    and with the octave keys, while "pad 5" is still pad 5.
    """

    model: str
    pads: tuple[PadSpec, ...]
    buttons: tuple[ButtonSpec, ...]
    knobs: tuple[KnobSpec, ...]

    def pad(self, key: str) -> PadSpec | None:
        """One pad by its stable key, or None if this layout has no such pad."""
        return next((spec for spec in self.pads if spec.key == key), None)

    def button(self, key: str) -> ButtonSpec | None:
        """One button by its stable key."""
        return next((spec for spec in self.buttons if spec.key == key), None)

    def knob(self, key: str) -> KnobSpec | None:
        """One encoder by its stable key."""
        return next((spec for spec in self.knobs if spec.key == key), None)
