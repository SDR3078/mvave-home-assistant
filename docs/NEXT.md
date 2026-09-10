# Next steps

Updated 2026-09-10. The device is understood; `docs/PLAN.md` is the corrected
implementation plan and this file is the running to-do list.

## Done

- **Domain and naming.** The integration is `mvave`, displayed as "M-Vave", repository
  `mvave-home-assistant`. Brand level rather than `ble_midi`, because everything that
  makes it worth having is device-specific, and rather than `mvave_ble`, because USB is
  a plausible second transport and the domain cannot be renamed later.
- **Development environment.** Devcontainer, from the blueprint's own definition plus
  four editor settings. `scripts/setup` and `scripts/develop` fixed: the symlink instead
  of the template's broken import path, a restart loop, and package installation off by
  default.
- **Transport.** `coordinator.py` holds the connection, reconnects on advertisement, and
  carries the three fixes from PLAN.md section 2. Plus `config_flow.py`, `entity.py`,
  `binary_sensor.py`, the manifest, strings and `hacs.json`.
- **The extraction seam.** `transport/` and `devices/` import as top-level packages, so
  the pure suite runs with no Home Assistant present. Verified: 83 tests on Python 3.11.
- **Proven against hardware.** The pad is discovered by its service UUID through the
  ESPHome proxy, connects, and its presses arrive decoded. 30 entities: a connectivity
  sensor and one event entity per pad, button and encoder. Knob turns are coalesced, so
  126 MIDI messages become 2 events.
- **Connect-time arming.** Every connect reads the state block, decodes the preset on
  display, and rewrites that bank so all 16 pads are Note-typed with their LED byte set
  to their own note, plus the encoders switched to relative and the buttons' LEDs armed.
  Volatile, so a power cycle restores the owner's configuration exactly.
- **`mvave.send_raw`**, and with it a pad lit green from the Home Assistant interface,
  with nothing configured in MidiSuite.

## Build

1. **Repository.** `git init` and a first commit. Decide whether
   `tests/fixtures/smc_pad_presets.bin`, which holds the owner's own presets, stays in a
   public repo; three tests depend on it, and `smc_pad_factory_slot0.bin` is the neutral
   one. Then the CI workflows from PLAN.md section 4.
2. **`mvave.set_pad_color`**, using the vendor RGB write for any 24-bit colour on an
   unarmed pad. Note that a pad cannot do both: armed pads take palette colours over
   MIDI and ignore the RGB field entirely (HARDWARE-BLE.md section 6).
3. **Build the entities from the map that was read** rather than from the advertised
   name. The arming step already decodes the real note and controller numbers, which
   change with the preset and the octave keys, so the current name-based layout is wrong
   the moment the user switches preset. Needs entities added after the first connect
   rather than at platform setup.
4. **Engine** (brief, milestone 4), with the LED frame rendered through the velocity
   palette of HARDWARE-BLE.md section 9, 127 and 96–126 never sent, and the vendor RGB
   write kept as the option for unarmed static pads.
5. **Extract the transport into a PyPI package** later: Home Assistant's review checklist
   wants protocol code in a library, and no BLE-MIDI framing library exists for CPython.

## Device questions still open

All listed with their evidence in `HARDWARE-BLE.md` section 10. In rough order of
usefulness to the integration:

- whether the radio stays up while the pad is on USB power or held by a DAW (an idle
  link on battery held for nine and a half minutes without a drop);
- where the aftertouch and pad-curve settings live, so aftertouch could be switched off
  at connect time to spare the radio (needs a MidiSuite change and a diff);
- whether the PAD BANK toggle target moves with the base bank;
- CC Toggle and Momentary pads with Led feedback;
- swing and velocity from the Shift keys, and bytes 8 and 9 of the state block;
- Comb MCP with a payload; the MCP palette table; the block at 0x0360; the `AE00` service.
