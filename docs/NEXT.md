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
- **The integration produces entities about the surface, not about the hardware.** It
  used to be thirty event entities and a connectivity sensor, which is a description of a
  MIDI controller rather than of a control surface. The rule that settled it, after two
  agents surveyed what core integrations actually do: **a pad is a position, a page is a
  thing**. What sits on pad five is resolved from the live area registry, so it moves the
  day somebody adds a bulb to that room, with no navigation and no reconfiguration; a
  page, a focus and a home button are facts about the profile and keep their meaning. So
  there is a settable `select` for the page, a `sensor` for what the knobs are on, and
  `button` entities for the two gestures a page may never rebind — and deliberately no
  per-slot buttons, because a dashboard button labelled "Kitchen lamp" that quietly starts
  closing a blind is the worst kind of bug and no naming scheme can prevent it. The pad
  events stay enabled and the knob events are now off by default, which is the line core
  itself splits along: Hue, Shelly and Z-Wave JS enable theirs and have no logic of their
  own, while Bang & Olufsen disables around ninety per remote and ESPHome's Voice PE
  withholds the press "used to control the device itself". Pads and buttons also report
  `long_press_start` and `long_press_end` now, on Home Assistant's own standard strings and
  at the same threshold the engine uses, so the entity and the surface cannot disagree
  about what a gesture was.
- **Two defects that had been shipping, and one absence.** Bus events carried the MAC
  address but not a `device_id`, which Home Assistant's guidance on integration events
  requires and which is what lets the automation editor offer an event against the device
  somebody is looking at. The five transport buttons had hard-coded English names while
  the pads and knobs were already translated. And there was no `logbook.py`, so every
  transition appeared in a timeline as a row saying `mvave_event`.
- **`mvave.get_pages`** answers the question this device creates by design. Nothing is
  written on it and a room page fills itself from the live registry, so the running
  integration is the only thing that can say what a pad would do — not the configuration,
  and not anybody who was not there when it was set up. It returns the resolved answer in
  the same vocabulary the LEDs use, including the one thing the grid physically cannot
  say: an unreachable pad and a pad that is off are the same white. Pull-only, because
  sixteen live slots in an entity's attributes would be a database row for every light
  toggled anywhere on the visible page — which is the same conclusion Home Assistant
  reached for weather forecasts, calendar events and to-do items.
- **`mvave.press_slot`** presses a position from outside, for when the pad is out of reach
  or out of battery. Named in hardware language on purpose so nobody mistakes it for a way
  to reach a particular lamp, and recorded with `trigger: service` so an automation can
  never mistake its own effect for a person.
- **A `select.py` in a custom integration shadows the standard library.** The test
  configuration put the integration's own directory at the *front* of `sys.path`, so
  `import select` — which `subprocess` and `asyncio` both do — found ours. It had been
  harmless only because pytest imports `subprocess` before it reads its own configuration.
  The path is appended in `tests/conftest.py` now. It surfaced by hiding a second trap:
  the same file imported as `engine.model` and as `custom_components.mvave.engine.model`
  is two classes, so every `is` comparison between them is False and a page built through
  one and resolved through the other comes back empty.
- **The surface says which encoders are live**, which was the last open question from
  the first day and the one the hardware cannot answer for itself: eight identical knobs,
  no rings, no markings. Turning one that can do nothing draws a map of all eight, in the
  arrangement they physically have, each live one in the colour of what it adjusts. It
  took three tries and two UX agents to land on a map rather than a refusal, and the
  reasons are in the design brief §6.0 — the short version is that a whole grid blinking
  dark is not a louder version of a pad shuddering but a different signal, one that
  already means "nothing is driving this device" and that the photosensitivity thresholds
  are written about. Three defects surfaced underneath it: capabilities were never
  checked, so a bulb that only switches reported a live brightness knob; the properties
  were handed out up the device's wiring rather than down the reading order, putting the
  most wanted one on the least obvious encoder; and the eighth encoder was a spare that
  meant a different thing on every kind of device, which is exactly what the design
  forbids. Properties now pack from the top left, so the first encoder always does the
  main thing.
- **A knob no longer resets when the entity will not name its value.** Felt at the grid
  first — "i feel that the color_temp is resetting" — and it was: a lamp that was on and
  showing a colour sent 2281 K on the next click instead of continuing, while the same
  lamp in colour-temperature mode correctly continued from 4000 K to 4281 K. Reproduced
  against the engine before anything was changed.

  The cause was an asymmetry in Home Assistant, confirmed in `components/light/__init__.py`:
  it derives `hs_color` from a colour temperature, but sets `color_temp_kelvin` to None in
  any other mode and never derives it, because most colours have no meaningful
  temperature. So touching hue or saturation made colour temperature unreadable, and
  `Surface._turn` fell into the branch written for a light that is **off**, where starting
  at the bottom of the range is right. One branch was answering for two situations that
  only look alike: off has no visible value, while this had a visible light that simply
  would not name one.

  Split, so a knob resumes from **the last value Home Assistant reported** for that
  property, and starts in the middle only if the house has never named one. Written
  wherever a value is read off the house — a turn that could read it, holding a pad to
  peek, an entity reporting back — and never from what this surface asked for, which was
  the first attempt and was wrong: a command is an intention, and it can be clamped,
  ignored, or land on a lamp somebody else is already moving, so remembering it would
  resume the knob from a place the house was never in. It survives the bar expiring, a
  page change and a rebuild, because a reading is a fact about the entity rather than
  about where somebody is standing. It suits the hardware too: these
  encoders have no rings and no position, so the surface is the only thing that can hold
  one. Deriving a temperature from the colour was ruled out — Home Assistant declines to
  on purpose, and inventing a number the platform refuses to state is how a knob lies.
- **Brand assets, from the manufacturer's own logo.** The owner's call, and the right one:
  Home Assistant's brands repository is explicitly for "the brand or product", so a
  manufacturer wordmark is the convention rather than something to be squeamish about. Six
  PNGs in `custom_components/mvave/brand/`, cut from the supplied wordmark — the `M`
  measured off the alpha channel at 214x221, which is near enough square to make an icon
  without distorting it.

  Two things worth knowing before anybody touches them. The supplied logo is **white on
  transparent**, which is the `dark_` variant: brands prefers the default optimised for a
  *white* background, so `logo.png` and `icon.png` carry near-black ink and the whites are
  `dark_logo.png` and `dark_icon.png`. And the `custom_integrations` folder in the brands
  repository is now **legacy** — since 2026.3.0 a custom component carries its own assets,
  which `homeassistant/components/brands/const.py` serves under exactly these eight
  filenames with a fallback chain. So there is no pull request to make. `logo@2x.png` is
  deliberately absent: the source is 221 pixels tall and the hDPI rule wants a shortest
  side of at least 256, so upscaling would only blur it, and the chain falls back.
- **The pad's Bluetooth address is out of the repository and out of its history.** It stood
  in 18 files — a line of `HARDWARE-BLE.md`, a parametrize in `test_layout.py` proving both
  cases parse, and a `# device SMC-PAD ...` comment atop 16 capture fixtures. None of it
  load-bearing, which is what made the scrub mechanical: `AA:BB:CC:DD:EE:FF` was already the
  placeholder three Home Assistant test modules used.

  Rewritten across all 44 commits rather than only in the working tree, because the half
  that gets deferred is the half that never happens, and this one gets harder the moment the
  repository is public. `filter-branch --tree-filter` over `--all`, both cases, then the
  `refs/original` backups dropped, the reflog expired and `gc --prune=now --aggressive`.
  Verified four ways: no blob under any ref contains it, `log -S` finds nothing for either
  case, `fsck` is clean with no unreachable objects, and the suite still passes. A sweep for
  anything else address-shaped found only the placeholder.

  **GitHub still serves the pre-rewrite objects by direct SHA, and that is fine.** Checked,
  not assumed: fetching the old commit hash still succeeds, because unreferenced objects
  survive until GitHub's own gc runs. Deliberately not chased — deleting and recreating the
  repository to finish it would be out of proportion. The pad advertises as `SMC-PAD` with
  its service UUID and **connects without pairing or bonding**, so anybody in radio range
  already has more than the address gives them, and a BLE address is not routable, not in
  any registry, and means nothing to anyone further away than the next room. The scrub was
  cheap hygiene, not a fix for a danger. Do not reopen this.
- **Engine** (brief, milestone 4), building the language now specified in
  `ble-midi-surface-design.md` sections 5 to 7. `engine/` has the palette, the frames and
  transitions, the two rhythms, the page model, the two protocols the platform reaches in
  through, slot resolution, rendering, and `Surface`: the navigation stack, presses,
  holds, the transport buttons, the idle timeout and `handle(event) -> Outcome`.
  The knobs are built too: the fixed global assignment, per-page overrides, holding a pad
  to peek at its value, the transient bar, and the clamps. **Proven on the hardware** with
  `scripts/surface_demo.py`, which drives the real pad from the real engine against a
  pretend house, and which found four defects that the tests had not.

  The last open question in it is now answered. What a scene or script pad looks like was
  provisionally purple, and purple was the one
  colour in the language nobody had judged by eye. **Judged on 2026-09-13 and kept.** The
  worry was specific rather than aesthetic: `HARDWARE-BLE.md` section 9.1 records purple as
  the weak pair against white, and a stateless pad shows its colour permanently and never
  goes white — so if the two read alike, a scene is indistinguishable from a lamp somebody
  switched off. Set up as a side-by-side, `button.push` pinned beside a lamp on the same
  page and the lamp switched off, so the two were adjacent and touching. Verdict: "yes i can
  tell them apart". Unreachable has no colour at all — it shows white like anything that is
  off and shudders when pressed, which was the owner's idea and buys back a fifth of the
  vocabulary.
- **A scene says it ran.** The one pad on the surface that could be pressed with no result
  of any kind: a scene has no on and no off, so nothing changed anywhere. Raised at the
  grid — "i actually want a feedback mechanism after i touched the button".

  Three UX agents were asked, and the two obvious answers are both wrong. **Reusing the
  existing blink fails on termination, not rate**: ALERT means the surface and the world
  disagree and stops when the world reports, but a scene that fired correctly never
  disagrees, so the condition that ends it does not exist — the `is_stateless` guard
  everyone wanted deleted is the thing that bounds it, and ITU-R BT.1702-3 warns that a
  sequence over five seconds may be a risk even when compliant, against a `CONFIRM_SECONDS`
  of six. **Flashing white fails on contrast**: §5.2 gives purple to stateless domains
  precisely because those pads never show white, purple against white being the one pair
  recorded as too close — so it would be the lowest-contrast event the palette can make.

  So a **latch, not a flash**: hold the action colour, let go. One transition in and one
  out, which is not a flash at all — a flash is a *pair* of opposing changes — so it never
  enters that arithmetic rather than merely passing it. No new rhythm, nothing new to tell
  apart from breathing and blinking, and it never shows white. Drawn over the settled frame
  rather than animated, so the other fifteen pads keep reporting while it is held.

  **0.8 s, judged at the grid over three rounds**: 1.5 s read as correct but overstayed,
  1.0 s was still a touch long. Pressing again restarts the countdown rather than re-firing
  it, so hammering a scene pad holds one unbroken colour instead of strobing — a property
  of it being a latch, and the reason it is safe to press as fast as anybody likes.

  Scripts are deliberately untouched: a script is not stateless, it reports running and
  then idle, so it already blinks and settles like a lamp.
- **The config flow is tested through the flow, not around it.** There were no flow tests
  at all, and two defects reached the device on 2026-09-13 with the suite green because of
  it: a deprecated device lookup that had a second call site nobody grepped for, and a pads
  step handed page data with no `pads` key — so it showed the contents a room supplies on
  its own, hid every pin from the one screen that edits them, and compared against a page
  nobody had. The count went 407 to 407 across that fix, which was the argument.

  `pytest-homeassistant-custom-component` registers itself through an entry point, so it
  loads where it is installed and nowhere else — the pure jobs install no Home Assistant and
  run with `--ignore=tests/homeassistant`, so none of this reaches them. Seven tests drive
  `hass.config_entries.subentries` for real: adding a page, a room filling the pad fields,
  saving one untouched pinning nothing, changing one pad pinning only that one, the
  two-sources error, and the two that cover the defect — that editing a page shows the pins
  it has, and that looking at one and saving keeps them.

  **The two regression tests were checked by putting the bug back**, which is the only way
  to know a regression test does anything. Both failed; both pass with it fixed.

  **Migration is covered too**, and is the piece most worth it: it runs exactly once on a
  real installation and cannot be run again to see what it did, so if it drops something,
  the thing it dropped is already gone. Nine tests — order preserved, because the order
  rooms were picked is where they sit on the index; a chosen colour kept and the rest handed
  out; a deleted room still becoming a page, since outliving its room is the whole point;
  genuine settings staying in the options; both shapes of the old list, in the data and in
  the options; and the version check on its own.

  **All of it was checked by breaking the code**, which is how two of these earned their
  keep. Reordering and leaving a stale option were caught immediately. Deleting the version
  guard was *not*: the list is read from the data or the options, the migration only cleaned
  the options, and the test happened to use that shape — so the second run found nothing to
  duplicate and the suite stayed green. That exposed a real gap on both sides. The migration
  now clears the list out of the data as well, rather than half surviving its own migration,
  and there is a test of the guard alone that fails when it is removed.

  Still uncovered: the discovery and bluetooth config flow, and the options flow.
- **The shift gesture**, which was the last engine item. Hold the back button and the top
  row becomes the rooms, each in its own colour, with the rest of the grid dark so that it
  plainly is not a page; press one to go straight there. Holding back used to go home,
  which the stop button already does, so a five-button surface was spending one of its
  holds on a duplicate. The pad for the room you are already on stays lit and shudders if
  pressed, because lit-and-does-nothing is never allowed to be silent.
- **The profile follows the house.** Which rooms become pages was worked out once, at
  connect, so a room added, renamed or deleted afterwards needed a restart. All three
  registries are watched now — an entity *given* an area is not an area event, and a
  device moved into a room carries its entities without any of them being touched —
  debounced two seconds and rebuilt only when the answer actually differs.
- **`scripts/led_console.py`** holds the link open and takes one instruction at a time from
  a file, which is what made designing by eye possible: reconnecting between questions cost
  twenty seconds each. It renders frames, rhythms, bars and the page animations, can freeze
  an animation on one step to walk through it, can schedule a command to land on a chosen
  frame, and can log how long each frame was actually on screen. That last one settled an
  argument: an animation that felt uneven measured 189, 411, 190, 206, 206, 395 ms.

## Build

1. **Extract the transport into a PyPI package** later: Home Assistant's review checklist
   wants protocol code in a library, and no BLE-MIDI framing library exists for CPython.

## Decided against

- **The min and max flash.** The design asked for one quick full-bar flash on reaching
  either end of a knob's travel. Not building it, on evidence gathered while solving the
  dead-knob problem on 2026-09-12. A full-bar flash is a whole-surface luminance change,
  and it would fire *repeatedly* for as long as somebody kept turning at the limit — the
  two properties that together made the knob refusal read as the device failing, and the
  ones the photosensitivity thresholds are written about (WCAG 2.3.1, Section 508 §408.3;
  see `frames.CURTAIN_HOLD` and the design brief §6.0). The signal is also already there
  and free: a bar at either end is sixteen pads lit or sixteen dark, which is as
  unambiguous as this grid gets. Nothing needs adding.

- **Splitting the knob map by colour mode.** A colour bulb is offered four live encoders —
  brightness, colour temperature, hue, saturation — but it can only be in one colour mode
  at a time, so turning hue throws away the colour temperature and turning colour
  temperature throws away the hue. Home Assistant's own light dialog treats these as modes
  rather than peers: `more-info-light` renders `light-color-rgb-picker` **or**
  `light-color-temp-picker`, never both, with an icon button group to switch. Read out of
  the shipped frontend bundle on 2026-09-13, after asserting the opposite from memory.

  Taken to the grid anyway, which is where it lost. Shown the map on a lamp that does both
  and asked whether anything said those four could not all be true at once, the owner said
  it did not bother him. The practical harm is small now that a knob resumes from the last
  value the house reported, so the round trip through a colour and back to white is
  smooth.

  Worth knowing what it would have cost: mirroring Home Assistant means a lamp showing a
  colour has no colour-temperature encoder at all, so there is no way back to white with
  your hand — a poor trade for a distinction nobody standing at the device wanted. If it
  is ever revisited, the map uses two columns and the right half of the grid is dark, so
  there is room to say more without needing a colour the palette does not have.

- **`mvave.set_pad_color`.** Was the first item on the build list; deleted 2026-09-13 after
  reading back what section 6 already recorded. Three independent reasons, none of them
  fixable:

  1. **An armed pad never shows its RGB field** — the write is stored, not shown. Every pad
     the surface runs is armed at connect: `16 pads armed on notes 36-51`, straight out of
     the log.
  2. **Disarming one to make it visible breaks the language.** An unarmed pad *"flashes
     white, then returns"* on its own press, firmware-owned feedback nothing can suppress,
     and white is the one colour this design reserves for "off". The host also loses the
     pad entirely — a note-on to an unarmed pad is ignored.
  3. **The RGB path is worse even where it works.** Dimmer than the palette at every hue —
     `(240, 0, 0)` against palette 14 read as "deeper red, but dimmer" — and far too slow
     to animate. That is already the recorded reason the whole design chose the palette.

  No niche survives. All sixteen pads are a page, `mvave.send_raw` covers experimenting,
  and nothing written to the pad outlives a power cycle either way.

## Also open

- **A websocket subscription for the live grid.** `mvave.get_pages` answers "what does
  this mean" and is deliberately pull-only. A wall tablet mirroring the grid as it
  changes wants a subscription instead, which is what `weather` does alongside its own
  action. Nothing needs it yet.
- **One setting that turns all motion off.** Raised by the accessibility pass and not built.
  `BREATHE` starts on its own and runs for as long as a pad is selected, which is the shape
  WCAG 2.2.2 is written about; 2.3.3 covers animation triggered by interaction, which the
  page transitions are. Neither literally binds an LED grid, but one switch would answer
  both at once and is the cheapest accessibility work available here. Nothing needs it yet,
  and it wants deciding at the grid: with motion off, a focused pad and a commanded one
  have to say what they are some other way, or stop saying it.
- **The knob entities stay enabled on an existing install.** `entity_registry_enabled_default`
  applies when an entity is first registered and never again, which is correct — Home
  Assistant does not overrule a choice somebody may have made — but it means this only
  takes effect on a fresh setup.

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
