# Next steps

Updated 2026-09-11. The device is understood; `docs/PLAN.md` is the corrected
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
- **The LED path is decided: the palette over MIDI, for the whole grid.** Measured on the
  hardware and judged by eye on 2026-09-10, recorded in HARDWARE-BLE.md sections 8.1 and
  9.1. The colour path can do saturation and the palette cannot, but it is dimmer at every
  hue, too slow to animate, and it flashes white on its own press, which breaks the rule
  the whole design rests on. Shown the same room page rendered both ways, the owner chose
  the palette. `scripts/preview_leds.py` plays that comparison as a fixed programme.
- **There is no brightness channel on this device by any route**, so "colour is identity,
  brightness is state" cannot be built as written. Not in the palette, which has no shade
  families; not on the MIDI channel, which is ignored on all sixteen; not by dithering,
  which reads as flicker; and the one path that does dim is dim everywhere.
- **The LED language is designed and `ble-midi-surface-design.md` is rewritten to match.**
  Sections 5, 6 and 7 of that document were built frame by frame on the physical grid, the
  owner judging each one. What came out of it: five identity colours on an index page, a
  fixed orange-and-white pair for on and off inside a page, the two never mixing so colour
  can be reused; breathing for the knob's target and one fast blink reserved for "not
  confirmed"; a sixteen-step value bar with no sub-step, both substitutes for it having
  been built and rejected on the hardware; and a page transition that grows in rings out
  of the pad you pressed, then opens left to right, mirrored exactly on the way back out.
- **Navigation moved off the grid onto the transport buttons**, the owner's idea. All five
  buttons take LED feedback, confirmed on the hardware, where only play had been tried.
  That returns the sixteenth pad to content and removes a colour collision, since any
  colour a back pad could take was either an identity colour or white.
- **The knob entities had been silently discarding every turn**, found by measuring what
  the encoders actually send rather than trusting the code. They computed a turn as the
  difference between consecutive controller values, which is right for the factory
  absolute encoders and always zero for relative ones, where the value *is* the step and
  never changes. Connect-time arming switches them to relative, so every turn had been
  dropped since that was added. `ArmResult` now records the mode and the entity reads it.
- **It runs inside Home Assistant.** `registry.py` is the whole of what the engine needs
  from the platform: what is in a room, what an entity is doing, call a service, fire an
  event, plus a profile built from the area registry so a fresh install has a surface
  before anybody configures anything. `runner.py` is everything with a clock in it, which
  is why the engine has none: hold against tap, how long a value bar stays, collecting a
  knob's thirty messages a second into one service call, and giving up on an entity that
  never reports back. The note and controller numbers come from the device's own memory
  by way of the arming step, so they stay right when the preset changes.
- **Every control is matched against the device's own map.** The arming step builds a
  `DeviceLayout` out of the preset it just read, and both the event entities and the
  surface use that one map instead of the factory guess. Keys stay put, so "pad 5 was
  pressed" keeps meaning pad 5 across a preset change while the note underneath moves.
  Checked by building a layout from a real dump and asserting it equals the constant that
  was written from measurements, which tests both at once.
- **Transitions light one pad at a time**, the owner's idea and a better one than what it
  replaced. Rings and columns were built first and felt uneven however evenly they were
  timed, because they cannot help it: a ring around a corner pad is one pad wide, the next
  is three, then five, then seven, so the amount of light arriving changes at every step.
  Measuring the frames on the wire proved the timing was even to within a few milliseconds
  while it still read as a limp. One pad per step cannot have that problem, and it removed
  a second one for free, since entering from a middle pad used to take fewer steps than
  from a corner and so had a different duration. 45 ms a pad, settled by eye.
- **Two agents reviewed the concurrency**, one against Home Assistant's own conventions
  and one for races. The verdict on the architecture was that running the engine in the
  event loop is what Home Assistant expects and there is nothing to move: pure in-memory
  computation is explicitly loop-safe in its docs, its blocking-call detector does not
  look at CPU at all, and every input and output the surface needs is loop-affine, so a
  thread would marshal each one back and need locks around state the single loop already
  serialises for free. They then found eleven real defects in the scheduling *around* it,
  all now fixed: a redraw running one line before the animation it was meant to defer to,
  so every page change painted its destination and then swept a curtain over it; a
  cancelled ticker orphaning a live one that nothing could reach; two device writes
  diffing against each other's stale cache; a press interrupted by a disconnect swallowing
  the next tap of that pad; a service call arming one countdown and not the other; tasks
  created on `hass` rather than on the config entry, so a breathing pad could hold a
  shutdown open; a shutdown that never told its listeners the link had gone; an unload
  that dropped the link before knowing it had succeeded; an unload queueing behind a whole
  preset read, eleven seconds typically and minutes at worst; and a redraw starting its
  own write rather than replacing the one already waiting. Every one of them needed
  several things to happen inside a single notification, which the parser makes possible
  because one packet can carry several messages, and none would have shown up in a test.
- **A config flow**, in three screens with one job each: which rooms get a page and in
  what order, a colour for each room, and a colour for each kind of thing. White is never
  on offer, because white is what "off" means and the readability of a page rests on it.
  Saving rebuilds the surface in place and keeps you where you were standing, rather than
  reloading the entry and spending twenty seconds reconnecting over a colour.
- **CI, a licence and a notice.** hassfest and HACS validation on every push and weekly,
  because both check against a moving target. Lint, format and types. The protocol tests
  across Python 3.11 to 3.14 with nothing but pytest installed, and a step that *fails* if
  Home Assistant is importable, because the whole point of that job is the absence and a
  dependency creeping in would leave it passing and meaning nothing. Verified by running
  it: 274 tests pass on 3.11 with no Home Assistant present. Plus a release check that the
  manifest version equals the tag, since HACS reads one and Home Assistant reads the other.
- **`scripts/led_console.py`** holds the link open and takes one instruction at a time from
  a file, which is what made designing by eye possible: reconnecting between questions cost
  twenty seconds each. It renders frames, rhythms, bars and the page animations, can freeze
  an animation on one step to walk through it, can schedule a command to land on a chosen
  frame, and can log how long each frame was actually on screen. That last one settled an
  argument: an animation that felt uneven measured 189, 411, 190, 206, 206, 395 ms.

## Build

1. **Repository.** A README, and then this can be pushed somewhere.
2. **`mvave.set_pad_color`**, using the vendor RGB write for any 24-bit colour on an
   unarmed pad. Note that a pad cannot do both: armed pads take palette colours over
   MIDI and ignore the RGB field entirely (HARDWARE-BLE.md section 6).
3. **Engine** (brief, milestone 4), building the language now specified in
   `ble-midi-surface-design.md` sections 5 to 7. `engine/` has the palette, the frames and
   transitions, the two rhythms, the page model, the two protocols the platform reaches in
   through, slot resolution, rendering, and `Surface`: the navigation stack, presses,
   holds, the transport buttons, the idle timeout and `handle(event) -> Outcome`.
   The knobs are built too: the fixed global assignment, per-page overrides, holding a pad
   to peek at its value, the transient bar, and the clamps. **Proven on the hardware** with
   `scripts/surface_demo.py`, which drives the real pad from the real engine against a
   pretend house, and which found four defects that the tests had not. What is left:
   - **The shift gesture**: holding the left button turning row one into a page switcher.
   - **The min and max flash.** The design asks for one quick full-bar flash on reaching
     either end. The bar being full or empty is most of that signal already, and adding it
     needs an outcome to be able to set its own pace.
   - **Telling somebody which knobs are live.** With no rings and no labels there is
     nothing that says knob one adjusts the lamp you are holding and knob six does not.
     The design's answer is that the assignment never changes so it is learned once, which
     is a lot to ask on the first day. Open.
   - **Rebuilding the profile when areas change.** It is built once, on the first connect
     after a restart, so a room added later needs a reload.
   - **Two provisional colours to judge on the grid**: what an unreachable entity looks
     like, currently blue, and what a scene or script pad looks like, currently green.
     Every other colour in the language was chosen by looking at it; these two were not.
4. **Extract the transport into a PyPI package** later: Home Assistant's review checklist
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
