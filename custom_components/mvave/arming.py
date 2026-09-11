"""Connect-time setup: read the device's own map and arm it for host control.

Everything here is a RAM edit that does not survive a power cycle, so it is repeated on
every connect. That is a feature rather than a limitation: the device is left exactly as
its owner configured it the moment it is switched off and on.

Three things are changed on the pad bank the device is currently displaying:

- every pad becomes Note-typed, so it stays velocity-sensitive;
- every pad's LED byte is set to the note it transmits, which is what makes it answer to
  a note-on from the host. Without this nothing the host sends lights anything;
- every encoder becomes relative, because absolute mode is a saturating counter that
  goes silent at either end.

All measured in docs/HARDWARE-BLE.md sections 6, 7 and 9.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from .const import LOGGER
from .devices.smc_pad import (
    BUTTON_COUNT,
    LED_NONE,
    armed_pad_bank,
    button_led_address,
    encoder_table_address,
    layout_from_preset,
    pad_bank_address,
    relative_encoder_table,
)

if TYPE_CHECKING:
    from .devices.layout import DeviceLayout
    from .devices.smc_pad import DisplayState, Preset
    from .vendor import VendorSession


@dataclass(slots=True)
class ArmResult:
    """What the device turned out to be, and what was done to it."""

    state: DisplayState
    preset: Preset
    armed_notes: tuple[int, ...] = ()
    #: The device's real control map, read out of its own memory. The factory layout
    #: is only a guess, and it stops being right the moment anybody changes preset or
    #: presses an octave key.
    layout: DeviceLayout | None = None
    #: Whether the encoders were switched to relative. It changes how a turn has to be
    #: decoded, and getting that wrong is silent: every turn is discarded rather than
    #: reported wrongly.
    encoders_relative: bool = False
    warnings: list[str] = field(default_factory=list)

    @property
    def summary(self) -> str:
        """One line for the log."""
        return (
            f"preset slot {self.state.slot + 1}, pad bank {self.state.bank}, "
            f"{len(self.armed_notes)} pads armed on notes "
            f"{min(self.armed_notes, default=0)}-{max(self.armed_notes, default=0)}"
        )


async def async_arm(session: VendorSession) -> ArmResult:
    """Read the device's configuration and rewrite it for host control."""
    state = await session.read_state()
    preset = await session.read_preset(state.slot)
    result = ArmResult(state=state, preset=preset, layout=layout_from_preset(preset, state.bank))

    bank = preset.banks[state.bank - 1]

    # A Program-typed pad corrupts later control changes on its channel until a note is
    # sent, so anything relying on those messages misreads them. Arming turns every pad
    # into a Note pad, which removes the problem, but say so rather than silently
    # changing what the user configured.
    programs = [record.note for record in bank if record.type == "Program"]
    if programs:
        result.warnings.append(
            f"pads on notes {programs} were Program-typed, which corrupts later control "
            "changes on the same channel; they are now Note pads"
        )

    image = preset.image

    # One write per table rather than one per field: 416 bytes and 96 bytes, instead of
    # 24 separate round trips.
    await session.write(pad_bank_address(state.slot, state.bank), armed_pad_bank(image, state.bank))
    await session.write(encoder_table_address(state.slot), relative_encoder_table(image))
    result.encoders_relative = True

    # Buttons are one byte each, and their record carries a SysEx payload that must not
    # be disturbed, so they are written individually.
    for index in range(BUTTON_COUNT):
        button = preset.buttons[index]
        if button.led == LED_NONE:
            await session.write(button_led_address(index, state.slot), bytes((button.number,)))

    result.armed_notes = tuple(record.note for record in bank)
    LOGGER.info("%s", result.summary)
    for warning in result.warnings:
        LOGGER.warning("%s", warning)
    return result
