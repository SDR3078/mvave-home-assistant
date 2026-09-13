# M-Vave SMC-PAD over Bluetooth LE — measured reference

What the SMC-PAD does on its Bluetooth LE MIDI interface. All of it measured on the
device itself, because nothing about it is documented and the USB behaviour in
`mvave-smc-pad-ableton/docs/HARDWARE.md` turned out not to carry over. That document
stays authoritative for USB; this one is for the radio.

**How this was measured.** With `scripts/record_packets.py` on the devbox, through an
ESP32 running the ready-made ESPHome Bluetooth proxy firmware (ESPHome 2026.7.4,
project `esphome.bluetooth-proxy` 26.8.2), using `bleak-esphome` 4.1.0 and
`habluetooth` 7.0.0, the same client path Home Assistant uses. The pad was on its
factory default configuration, with individual pads re-typed in MidiSuite where a
section says so. Measured 2026-09-09. Raw captures are in
`tests/fixtures/ble_midi_packets/capture-20260909-*.txt` and are decoded by the test
suite on every run.

**How to read the claims.** Stated flatly means observed directly. **Hypothesis** means
inferred and not yet tested. **Unknown** means exactly that.

---

## 1. Advertising and connection

- Advertises as `SMC-PAD` with the BLE-MIDI service UUID `03b80e5a-ede8-4b33-a751-6ce34ec4c700`
  in the complete 128-bit UUID list, plus two manufacturer-data fields reading `sinco` and
  `JLAISDK`. The latter names the Bluetooth chip vendor, Jieli, which also explains the two
  vendor services below.
- Address: written as `AA:BB:CC:DD:EE:FF` throughout this repository, including in the
  capture fixtures. The real one is redacted, for the reason the next sentence gives: the
  top bits marked it as a random address, but it behaved as a **static** one — identical
  across the whole day, dozens of connections, MidiSuite sessions, three power cycles and a
  factory reset. That is the finding, and it is why the address can be the config entry's
  unique id. It is also exactly what makes a BLE address a permanent name for one device in
  somebody's house, so the value itself is not something to publish. Nothing here depends
  on it; substitute your own when reading a capture back.
- Connects without pairing or bonding. Nothing was ever prompted.
- **MTU 512**, so even a long SysEx fits in one packet.
- The initial read of the MIDI characteristic returns 0 bytes, as RP-052 section 5 requires.
- Stops advertising while another central holds it. During a MidiSuite session the proxy
  saw nothing; afterwards it advertised again with the same address.
- Nothing is sent unprompted: no clock, no active sensing, in any idle period measured.
- **An idle link holds.** A connection with nothing sent for nine and a half minutes
  stayed up, and pad presses at four and at nine minutes arrived normally. The pad does
  not sleep or drop the link on that timescale.

## 2. GATT services

| Service | Characteristics | Notes |
|---|---|---|
| Generic Access `1800` | `2A00`, `2A01`, `2A04` read | standard |
| Generic Attribute `1801` | `2A05` indicate | standard |
| Device Information `180A` | `2A29` manufacturer = `sincoaudio`, `2A24` model = `ble device` | |
| Battery `180F` | `2A19` read, notify | read 77–86 % over the day |
| Vendor `AE40` | `AE41` write-without-response, `AE42` notify | Jieli data tunnel; the configuration memory is read and written through it, section 9 |
| BLE-MIDI `03B80E5A…` | `7772E5DB…` read, write-without-response, notify | MIDI both ways |
| Vendor `AE00` | `AE01` write-without-response, `AE02` notify | Jieli; never used, nothing ever arrived on `AE02` |

## 3. BLE-MIDI framing as the pad does it

- **Timestamps are always zero.** Every packet starts `80 80`. The pad does not implement
  the timing half of the spec, so tap-versus-hold must be measured by arrival time.
- One message per packet for isolated events.
- **Running status inside packets for bursts**, up to 12 messages per packet observed during
  encoder turns, without timestamp bytes between them, for example
  `80 80 B0 1E 6B 1E 6C 1E 6D …`.
- A timestamp byte does precede a new status inside a packet, as the spec requires, for
  example `80 80 B9 00 2A 20 00 80 C9 7F`.
- No SysEx has spanned a packet so far; with MTU 512 none needs to.
- **Program pads corrupt the stream, deterministically.** The first press of a
  Program-typed pad is framed correctly: `B9 00 nn 20 00 80 C9 7F`, bank select MSB equal
  to the record's note byte, LSB 0, then program change 127, the record's MaxVel. Every
  later press comes out as `C9 00 nn 20 00 7F`: the program-change status in front of all
  the data bytes, the control-change statuses gone. Reproduced on two pads across two
  presets, three presses each. Worse, **channel messages sent afterwards on the same
  channel carry the stuck status too**: CC Toggle and Momentary pads pressed after a
  Program pad sent `C9 28 7F` and `C9 29 00` where they send `B9 …` when no Program pad
  has fired. The packets are syntactically valid, so a decoder reads them as program
  changes and cannot tell. Bounded by two more recordings: **notes are never affected**,
  PAD7's note-on and note-off were `99` and `89` right after a Program press; **a single
  note message clears it**, a CC Toggle pressed after a Program pad sent `C9 28 …`, and
  after one note from PAD7 sent `B9 28 …` again; and a fresh connection that followed
  notes started clean. Consequence: the integration must not configure Program-typed
  pads, and should warn if the map on display contains one, since CC pads next to it
  will be mislabelled until a note is played.
- **Channel aftertouch** (`D9 nn`) came in bursts after every press in the owner's old
  configuration and almost never in the factory one, where a single `D9 00` appeared once
  in a hundred-odd presses, so its amount is a setting (section 10) and a consumer should
  ignore the message type in any case.

The parser in `custom_components/ble_midi/transport/parser.py` decoded every packet of
every capture of the day with zero errors.

## 4. Factory default map over Bluetooth

This is what the pad sends with the factory configuration, and it is nothing like USB
port 3, where the same pads are MCP-typed notes 1–16 on channel 1 at fixed velocity 127.

| Control | Sends |
|---|---|
| pads | notes **36–51 on channel 10**, velocity-sensitive (61–118 seen), release as a real note-off `89 nn 40` with velocity 64 |
| play button | CC 27 on channel 1, 127 on press, 0 on release |
| encoders, knob bank 1 | CC **30–37** on channel 1, ascending with the printed numbers, absolute 0–127 |
| encoders, knob bank 2 | CC **38–45** on channel 1, same |
| the five buttons | CC **25–29** on channel 1, 127 on press and 0 on release: left 25, right 26, play 27, stop 28, record 29 |
| PAD BANK, SHIFT | nothing; both are local, as on USB |

Pad orientation: note 36 is PAD1, the bottom-left pad, and note 51 is PAD16, the top-right,
the device's own numbering (HARDWARE.md 5.3). Settled by the memory image, where record 0
of the factory bank holds note 36, and by a colour written to record 15 lighting the
top-right pad.

The radio carries every pad type; what differs from USB is that the MCP type comes out as
a SysEx pair rather than as Mackie notes (section 5).

## 5. What each pad type does on the radio

Measured by re-typing pads 1–7 in MidiSuite, one type each, pressing each twice, with pads
8–16 untouched as the control group. The per-pad parameters are Type, Channel, Note,
MinVel, MaxVel, Color and Led (screenshot: `images/midisuite-pad-parameters.png`).

| Type | On the radio |
|---|---|
| Note | note-on with velocity, note-off with velocity 64, on the pad's channel |
| CC Toggle | CC (40 on the pad tested), 127 on one press, 0 on the next, nothing on release |
| Momentary | CC (41), 127 on press, 0 on release |
| Program | bank select CC 0 = 42 and CC 32 = 0, then program change 127; see the quirk in section 3 |
| Custom | the SysEx stored in the record, verbatim, once per press, nothing on release; **nothing** while that box is empty (section 7) |
| MCP | SysEx `F0 35 59 10 nn vv F7`, with `vv` = 7F on press and 00 on release, and `nn` = 25 on the pad whose note number is 37. Once, a release was sent twice. |
| Comb MCP | **nothing** |

**MCP is not silent, and it is not the USB port-3 stream either.** Instead of Mackie
note-on and note-off it sends a SysEx pair. Attribution was settled by a run pressing one pad
type per block with ten seconds of silence between blocks
(`capture-20260909-173924.txt`): the MCP block produced the three SysEx pairs, the Comb MCP
and Custom blocks produced nothing. An earlier version of this table had Custom as the SysEx
type. That was inferred from press order in a mixed run, and it was wrong; the owner's
recollection of which pad he had pressed was right.

The fourth payload byte is the pad's note number: PAD2 re-typed MCP by a RAM write with
its note 37 sent `… 10 25 …`, after pad 2 with note 37 had sent the same on preset 7.
MCP pads are therefore distinguishable.

## 6. LEDs: host control exists, gated by the Led parameter

**The editor's per-pad Led field is the note number the pad's LED answers to.** The owner's
finding, and the fact everything below hangs on: with Led equal to the pad's own note, a
host note-on for that note lights the pad, velocity selecting the colour. The default of 255
is outside 0–127, so by default nothing the host sends can match. Led and the pad's
transmit note are independent, measured in section 9, so LEDs can be addressed on a
separate note map.

In memory it is byte 8 of the 26-byte pad record, the byte HARDWARE.md 5.2 called the
tail and found equal to the note on MCP records and 0xFF on all others (section 9). That
is why MCP pads lit over USB and nothing else did: the MCP type sets Led to the note.

**Measured on pad 2, typed MCP, note 37, Led 37.** A first battery 300 ms apart lit the pad
and turned it green at one point; green is velocity 5 in the USB palette (HARDWARE.md 3.2).
An isolation run then sent one message every six seconds, all for note 37:

| Step | Message | Seen |
|---|---|---|
| 1 | channel 1, velocity 0 | off |
| 2 | channel 1, velocity 15 | orange |
| 3 | channel 10, velocity 21 | blue |
| 4 | the press SysEx echoed with value 24 | **no change** |
| 5 | channel 1, velocity 40 | indistinguishable from off |
| 6 | channel 1, velocity 0 | off |

So: the mechanism is note-on with velocity as a palette index, as on USB port 3; echoing
the pad's own SysEx does nothing; the note-on lit the pad from channel 1 and from channel 10
alike, and later from channel 5, so the channel is ignored on every channel tried. Velocity
40 and 0 looked the same on this MCP pad, where on a Note pad the palette walk in section 9
gave white at 40; whether MCP pads run a different table is open (section 10).

**A Note-typed pad lights from the host too, once Led equals its note.** Pad 1, Type Note,
note 36, Led 36: note-on 36 at velocity 5 on channel 10 turned it green, note-on 36 at
velocity 15 on channel 1 six seconds later turned it orange, and it stayed orange. So over
the radio this pad offers what USB never did: **velocity-sensitive pads with host-driven
LEDs**, with no need for the MCP type. The channel of the lighting note-on was ignored in
both tests so far. CC Toggle and Momentary pads with Led set have not been tried.

Every negative result below was obtained with Led at 255 or at 0, never equal to the note
sent, so those runs only showed that Led gates the feedback. Sent to non-MCP pads with Led
at 255 or 0, nothing lit:

- note-on to a Note-typed pad on its own note and channel 10, at velocities 5, 15 and 127,
  with the pad's Led parameter at 255 (default) and at 0
- the same note-on on channel 1
- note-on to neighbouring notes 37–39 on channel 10
- CC 40 and CC 41 (the CC Toggle and Momentary pads) at 127 on channels 10 and 1, and
  CC 40 at value 1
- mirror mode: every note-on and CC the pad sent, answered back at 127 within milliseconds

The Led parameter does not change what a pad transmits.

**Consequence for this project:** no MidiSuite setup step at all. At connect time the
integration reads the preset on display through the vendor channel (section 9), learns
every pad's note and type and every encoder's CC and mode, arms each pad's Led byte to its
own note, and then has two ways to light pads: note-on with velocity as a palette index
for quick state changes, the write path the Ableton scripts already use, and the vendor
colour write for any 24-bit colour, including the dim levels and true red the palette
lacks. Both are volatile and redone after a power cycle.

## 7. Encoders

The encoders have no detents, so there is no "one click"; every count below is for a turn
of whatever size the owner made. Three sessions on encoder 1 in the factory absolute mode:

- Session 1, small turns each way on encoders 1 and 8 in both banks: four bursts of 92–124
  messages each, every value climbing by one from 1 to about 127 within half a second,
  packets 1–2 ms apart, nothing ever descending.
- Session 2, mirror mode active: one burst descending by one from 126 to 89 over 1.3 s.
- Session 3, nothing sent to the pad: a clockwise turn gave 38 single steps from 90 to 127
  in 0.4 s and then stopped at 127; a counter-clockwise turn gave 7 steps from 126 to 120;
  a slow quarter turn clockwise gave 7 steps from 121 to 127 and stopped again.

What that establishes:

- Absolute mode is a **0–127 counter kept in the device**. It persists across connections:
  session 2 ended at 89 and session 3 began at 90. It **saturates** at both ends.
- Every step is sent as its own single-increment message, 1–100 ms apart, so a receiver
  sees a ramp rather than a jump. How many steps a given angle produces was not measured.
- Absolute mode is therefore unusable for an endless control: once at 127, turning further
  sends nothing. The integration needs the encoders in relative mode.

**Relative mode, measured on the radio after switching it on by a RAM write.** An encoder
record is `flag, 02, 00, cc, min, max`; the factory has `00 … 00 7F`, the Ableton presets
`03 … 3F 41`. Writing only `3F 41` into the range left the encoder ramping; writing `03`
into the flag as well made it relative: a quarter turn each way gave forty messages of
65 and fifty-four of 63, one per step, no other values, no acceleration. So the flag byte
is the mode, the integration can set it at connect time like the Led bytes, and the
decoder needs only the centre-64 rule with steps of exactly one.

**The flag alone chooses relative mode, and the range bytes are the two step values.**
With the flag set and the factory range 00 7F left in place, clockwise sent 127 and
counter-clockwise sent 0; with 3F 41 they send 65 and 63. So `min` is the value per
counter-clockwise step and `max` per clockwise, and any convention can be configured,
centre-64 or two's complement alike. The encoders have no detents: a small turn gave
twenty-seven messages one way and thirty-five the other.

**Pad records, completed.** Byte 1 is the MIDI channel, 0-based: PAD1 written to 0 sent
its note on channel 1. HARDWARE.md 5.5's two records with 0x00 there were pads on channel
1, not disabled pads. Byte 3 and 4 are MinVel and MaxVel. A Custom pad, type 5, sends the
SysEx stored after a length byte at offset 9, up to sixteen bytes, verbatim and once per
press: `06 F0 7F 7F 06 01 F7` written there produced `F0 7F 7F 06 01 F7`.

## 8. What exists online (searched 2026-09-09)

The manufacturer documents nothing at the wire level. Everything below is third-party.

- **The user manual's editor section** (manuals.plus copies of the SMC-PAD Pocket manual)
  defines the pad types in one line each: Note "send standard MIDI note messages", CC Toggle
  "toggle between two CC values with each press", Momentary "send one CC value on press,
  another on release", Program Change, and Custom "input and send System Exclusive (SysEx)
  messages". It does not mention MCP, Comb MCP, the Led field, Color, or host feedback.
- **A Lumikit SHOW forum thread** (Portuguese) reports note-on, note-off and CC feedback all
  failing to light the pad, and that MidiSuite changes colours with SysEx. Nobody there found
  the Led field; the thread ends unsolved. Consistent with section 6.
- **DavidLanas/SMC-Pad-Pocket-bitwig-script** lights the Pocket's pads with plain note-on on
  channel 10 to the pad's own note, velocities used as brightness levels, including a flash
  to 127 on press and a return to the row's value 120 ms later. Independent confirmation of
  section 6's mechanism on the sibling model.
- **ZzpsS/m-vave-codex-bridge** (MIT) contains a **volatile RGB command** for the SMC-PAD,
  reproduced from MidiSuite USB captures on 2026-07-19 and verified by its author for pads
  1, 3 and 16, red, green, blue and off, with non-persistence across a power cycle. Over
  Bluetooth it is written raw to the vendor characteristic `AE41`; over USB it is the same
  bytes base-128 little-endian packed inside `F0 … F7`. Layout, from `lighting.ts`:

  ```
  address  = 0x0418 + pad_index * 0x1A        (0x1A = 26, the .spc pad record size)
  payload  = 05 | address LE ×4 | 03 00 00 | r g b
  checksum = (~sum(payload)) & 0xFF
  packet   = 00 59 22 | len(payload) LE ×3 | payload | checksum      (18 bytes)
  ```

  `pad_index` counts the physical pads left to right from the **bottom** row up, the
  device's own numbering (HARDWARE.md 5.3). The author deliberately supports only these
  sixteen addresses and no flash or preset-save commands. **Tried on this unit
  2026-09-09: the writes landed exactly where he said, in a preset that was not on
  display.** Each was acknowledged on `AE42` within about 410 ms with
  `00 59 00 01 00 00 00 FF` and no pad changed. Reading the memory back (section 9)
  showed the three colours sitting in the records of slot 0, bank 3, while the pad was
  playing slot 6. **Hypothesis:** his unit was on slot 0, preset 1. `scripts/decode_midisuite_sysex.py`
  and the wrap and unwrap helpers in `devices/smc_pad.py` remain for reading MidiSuite
  USB captures.
- **jvsobrinho/mvave-blackbox-ble** (CC BY-SA docs, GPL tools) documents the same vendor
  tunnel on the Blackbox pedal: `AE41` write, `AE42` notify, a memory map and checksums,
  with the official app relying on it entirely. **cbix/mvave-chocolate-sysex** has the USB
  SysEx form of the configuration protocol for the Chocolate footswitch. Together they say
  the `AE40` service is Jieli's generic data tunnel and MidiSuite's real channel.
- Nothing found documents the Led field, the palette, or the MCP SysEx. Sections 1 to 7
  remain the only measurements of this device's radio behaviour known to us.

### 8.1 How other grid controllers encode state (searched 2026-09-10)

Gathered to settle the LED language rather than the protocol, because the question of what
to do with seven colours and no brightness has been answered by other people already.

**One independent measurement of this device's palette exists.**
[SDR3078/mvave-smc-pad-ableton](https://github.com/SDR3078/mvave-smc-pad-ableton) reports
the hue cycling roughly every 13 to 14 steps, everything above about 64 as one flat blue,
and that there is no saturated red, 14 being as close as the palette gets. That agrees with
§9 and §9.1 on every point, arrived at separately.
[bogdanr/esphome-ble-midi](https://github.com/bogdanr/esphome-ble-midi) targets the same
pad over BLE MIDI with a full input map but records LED control as not working, blaming the
vendor service `AE40`, which §6 contradicts.

**Every shipped grid separates three channels, and they are not interchangeable.** Hue
carries identity, shade carries steady state, and motion carries pending against running,
which is the one thing a static colour cannot say. Novation, Ableton, Akai and monome
converge on the same grammar independently: static means idle or loaded, flash means
queued or commanded but not yet committed, pulse means running now. Novation states it in
the [Launchpad Mini user guide](https://userguides.novationmusic.com/hc/en-gb/articles/23731303692306-Using-Launchpad-Mini-s-Session-mode)
("flash green, indicating that the clip is queued … when a clip is playing, the pad will
pulse green"). Ableton implements it in shipped code, where every `…Triggered` state is a
`Blink()` and every engaged toggle is a `Pulse()`
([Push2/skin_default.py](https://github.com/gluon/AbletonLive11_MIDIRemoteScripts/blob/master/Push2/skin_default.py)).
monome, which has no colour at all, still made its Terms app flash a clip until it starts.
Home Assistant's own frontend uses exactly one animation, a 1 Hz pulse, and only for
`locking` and `unlocking`.

**Their brightness is palette layout, not a dimming register**, which is why our §9.1
result matters so much. Launchpad, APC and Push palettes are hue families of three or four
shades, and Ableton's `shade(n)` is literally `palette_index + n`
([pushbase/colors.py](https://github.com/gluon/AbletonLive11_MIDIRemoteScripts/blob/master/pushbase/colors.py)).
This device's palette is not built that way, so the shade channel those products rely on
does not exist here by any route.

**Their animation and brightness modes are selected by MIDI channel.** Push 2 uses channels
6 to 10 for pulsing and 11 to 15 for blinking
([push-interface](https://github.com/Ableton/push-interface/blob/main/doc/AbletonPush2MIDIDisplayInterface.asc));
the APC40 mk2 spends 15 of its 16 channels on temporal modes; the APC mini mk2 puts seven
static brightness steps plus nine blink and pulse rates on the channel. This device ignores
the channel on all sixteen (§9.1), so none of that is available and the timing has to live
in the coordinator instead.

**Five frames a second is not actually the end of animation.** The original Launchpad's own
manual documents 400 messages a second, so "it will take 200 milliseconds to update a
Launchpad completely", the same figure our vendor path measures. Novation's answer was to
move animation into firmware rather than abandon it. A square blink at *f* Hz costs 2*f*
frames a second, so even five frames a second affords the whole 0.4 to 2.5 Hz band. What
five frames a second does kill is smooth ramps and the ripple in the design document, and
no shipped product has ripples.

**Blink rates converge tightly across every industry**, which is worth copying rather than
guessing: IEC 60073 slow 0.4 to 0.8 Hz and normal 1.4 to 2.8 Hz, IEC 60601-1-8 high
priority 1.4 to 2.8 Hz, FAA AC 25-11B 0.8 to 4.0 Hz, and
[WCAG 2.3.1](https://www.w3.org/TR/WCAG21/#three-flashes-or-below-threshold) capping at
three flashes a second. Novation's hardware flash is one beat, 2 Hz at 120 bpm; Ableton's
`Blink(…, 24)` is a quarter note and `Pulse(…, 48)` a half note, so 2 Hz and 1 Hz.
MIL-STD-1472H adds two rules worth keeping: no more than two flash rates, at least 2 Hz
apart, and items flashing at the same rate must be synchronised. Smith and Mosier are
blunter still, that it is safer to treat blinking as a two-level code, blinking against not
blinking.

**The documented failure mode is density, not rate.** Users report individual blinks as
readable and a grid full of them as unreadable, which is why MIL-STD-1472H says only a
small area should flash at any time. Two further traps are recorded by users of other
grids and both apply directly here: dithered dimming reads as blinking and collides with
the semantic, which §9.1 confirms on this hardware, and host redraw fighting a firmware
animation layer produces flicker.

**Colour vision deficiency is the strongest argument for keeping a lightness channel, and
this device has none.** The canonical case is a
[2010 Ableton forum thread](https://forum.ableton.com/viewtopic.php?t=141471) in which a
colour-blind Launchpad user reports that the mixer page was readable because the LED
intensity differed, while "in clip view the leds are so bright that I cannot register the
Green from the Amber pads". The community fix, and the one the thread celebrated, was
blink. Mutable Instruments later shipped a colour-blind firmware built on brightness plus
blink pattern, and its author concluded that blink character, smooth against sharp, was
more discriminable than rate. [WCAG 1.4.1](https://www.w3.org/WAI/WCAG22/Understanding/use-of-color.html)
certifies lightness difference as a redundant channel and does not list blink; IEC 60073
and the FAA human factors standards do list flashing. Roughly one man in twelve is
affected, and [Okabe and Ito](https://jfly.uni-koeln.de/color/) put the ceiling at eight
reliably distinguishable hues even with a free choice of colour.

**Home Assistant precedents are thin but consistent.** No integration exists for any MIDI
device, and the three hobby Launchpad projects have under one star each. The most
considered is [marcostevanon/launchpad-ha](https://github.com/marcostevanon/launchpad-ha),
which encodes on, off and unavailable as three shades of one hue, reserves pulse for two
states only, and deliberately never flashes. The popular category is Stream Deck, where
[cgiesche/streamdeck-homeassistant](https://github.com/cgiesche/streamdeck-homeassistant)
carries binary state with an **icon swap** rather than colour, gives transitional states
their own static glyph rather than motion, and uses amber for active and grey for inactive,
matching Home Assistant's own frontend palette. Home Assistant's own Voice hardware encodes
faults by the **number** of lit LEDs and magnitude by an arc, using brightness for nothing.

## 9. The vendor channel, measured

Reads change nothing, so the memory was explored by reading. All measured 2026-09-09 over
the proxy on the `AE40` service, `AE41` write-without-response, replies on `AE42`.

**Framing.** `00 59 <op> <len ×3 LE> <payload> <checksum>`; `op` 0x23 reads, 0x22 writes;
the payload is `region, address ×4 LE, count ×3 LE` and, for a write, the data; the
checksum is the one's complement of the payload sum, so payload plus checksum sums to
0xFF in the low byte. A read is answered in the same framing with the data appended; a
write is answered by `00 59 00 01 00 00 00 FF`. 256-byte reads were answered in one
notification each, 112 of them without a checksum error. The Blackbox protocol notes by
jvsobrinho (section 8) describe the same framing on another M-Vave device.

**Region 5 is the preset store**, eight slots of 3539 bytes back to back, each the image
MidiSuite exports as `.spc` (HARDWARE.md 5.2): five 23-byte button records, sixteen 6-byte
encoder records at 115, eight banks of sixteen 26-byte pad records at 211. A pad record is
`type, channel, note, min velocity, max velocity, r, g, b, led, payload length, payload`,
the channel 0-based and the payload up to sixteen bytes; a button record is `type, channel,
number, 00, 7F, payload length, sixteen payload bytes, led`; an encoder record is `mode,
02, 00, cc, min, max`. The type byte is the editor's Type dropdown index: 0 Note, 1 CC
Toggle, 2 Momentary, 3 Program, 4 MCP, 5 Custom, 6 Comb MCP. Records run PAD1 to PAD16 in
the device's own numbering, bottom-left first. Every field was confirmed by writing it and
watching the wire or the LED (sections 6 and 7).

**What the dump held**, decoded by
`devices/smc_pad.py` and checked by `tests/test_smc_pad.py`):

| Slot | Contents |
|---|---|
| 0, 2 | the Ableton sequencer preset: buttons 117–121, relative encoders, bank 3 MCP notes 101–116 |
| 1 | a hand-edited preset with MCP banks 3 and 8 |
| 3, 4, 5 | factory default: buttons CC 25–29, absolute encoders CC 30–45, bank 3 notes 36–51, Led 0xFF |
| 6 | **the preset in use**: factory default with the type experiment on pads 1–7 and Led 36 on pad 1 |
| 7 | factory default with bank 3 typed MCP |

The buttons' CC numbers, the encoders' CC and absolute or relative mode, and every pad's
note, type, colour and Led are therefore readable over Bluetooth at any time, with no
editor involved. Region 4 at the same address holds pointers and is something else.

**Writes are volatile RAM edits of the stored image, and the slot on display shows them
at once.** Colours written to slot 0 stayed in memory across several connections and did
not show, because slot 6 was on display. Green written to PAD16 in slot 6, bank 3, showed
immediately and stayed. Then PAD8, whose Led byte was 0xFF, had that byte written to its
note 43 the same way, and a note-on 43 at velocity 5 sent right after lit it green. So both
halves of host lighting are reachable over the radio with no editor involved:

- **24-bit colour per pad**, three bytes at record offset 5, shown at once;
- **arming note-on feedback**, one byte at record offset 8, after which the MIDI path of
  section 6 works, with velocity as the palette index.

An integration can therefore read the pad's map, arm every pad and paint the grid at
connect time, and repeat that after a power cycle since the edits are volatile. Which slot
and which pad bank are on display comes from the state block below.

**The Led byte selects one of two exclusive LED modes per pad.** Measured on PAD16
(unarmed) and PAD8 (armed), then by interleaving both write paths on PAD8 six seconds
apart: blue by RGB write, orange by note-on at velocity 15, purple by RGB write, off by
note-on at velocity 0, black by RGB write. Only the orange and the off were seen.

| Led byte | LED shows | On its own press | Host note-on | RGB write |
|---|---|---|---|---|
| 0xFF, unarmed | the record's RGB field, 24-bit, at rest | flashes white, then returns | ignored | shown at once |
| a note, armed | the palette colour of the last note-on for that note, velocity 0 off | nothing; the host owns it | shown at once | stored, not shown |

Verified again after a factory reset, on slot 0, with the writes made through the vendor
channel rather than the editor:

- **The Led byte is a note selector, not a mode bit.** PAD2 armed to note 100, a note no
  pad sends, lit orange on note 100 at velocity 15 sent on channel 5, ignored its own
  note 37 at velocity 21, and went off on note 100 at velocity 0. So LEDs can be
  addressed on any note map, and the channel was ignored on every channel tried, 1, 5
  and 10.
- **Arming switches the LED off at once**, even when an RGB colour was showing, and
  **disarming shows the RGB field again**, including a colour written while armed and
  never displayed. PAD16: blue by RGB, armed and dark, red by RGB unseen, green by its own
  note at velocity 5, disarmed and red. Re-arming an already armed pad to another note
  leaves its LED as it was: PAD2, lit green while armed to note 100, stayed green when a
  bank write armed it to its own note 37.
- Any pad type can be set live by writing its type byte; PAD2 typed MCP this way sent
  the MCP SysEx on its next press. The encoders' range bytes alone did not switch them to
  relative mode; their flag byte did (section 7).
- **The five buttons have a Led byte too**, the last byte of their 23-byte record, offset
  22, 0xFF by default. The play button's record written to 27, its own CC number, then
  note-on 27 at velocity 5, lit it green; velocity 0 put it out and 127 lit it again. The
  owner reports green as its only colour, so the buttons are single-colour LEDs and
  velocity is on or off there. **All five confirmed 2026-09-10**: every button's Led byte
  written to its own controller number, left 25, right 26, play 27, stop 28, record 29,
  then a note-on for each, and all five lit. So navigation can live entirely off the grid
  and all sixteen pads stay available for content.

The palette's green at velocity 5 is a little darker than a written `00 FF 00`, consistent
with the editor's palette topping out at 0xF0. Vendor writes were acknowledged about
410 ms after sending; how soon the LED changes was not timed.

**The palette on the radio, walked on a Note pad armed by its Led byte** (PAD2 armed to
note 100 on slot 0, one velocity at a time, the owner naming each colour, doubtful values
retried from a known state):

| Velocity | Shows |
|---|---|
| 0 | off |
| 1–63 | the USB palette of HARDWARE.md 3.2, every value named matched: 1 yellow, 3 white-yellow, 5 green, 7 and 9 light blue, 11 light pink, 13 white, 14 red-pink, 15 orange, 17 yellow, 19 green, 21 blue, 24 light purple, 32 yellow-white, 40 white, 48 colder white, 60 yellow |
| 64–95 | one flat white-blue |
| 96–126 | **ignored**: the pad keeps its colour |
| 127 | **off**, the same as 0 |

Two consequences for a renderer: 127 is a second off, and 96 to 126 must never be sent as
colours. The USB palette was measured on MCP-typed pads and showed green at 127; that
may be a different mode's table, so the two are recorded separately.

**How fast each path can drive the grid, measured 2026-09-10** with
`scripts/bench_leds.py` **[v]**. Two things make a naive measurement worthless: a write
without a response through a proxy returns when the ESP32 has it, not when the pad has
it, and the failure mode is silent dropping rather than an error. So each rate is driven
for two seconds and then the device's own memory is read back, both to verify the last
frame and to time a round trip that can only answer once the queue has drained.

| Target | 24-bit over the vendor channel, 16 writes per frame | Palette over MIDI, 1 write per frame |
|---|---|---|
| 5 frames/s | correct, drain at the idle floor | drain at the idle floor |
| 10 frames/s | **3 pads wrong** | drain at the idle floor |
| 20 frames/s | correct, but **5.2 s of backlog** | drain at the idle floor |
| 30 frames/s | correct, 1.3 s of backlog | drain at the idle floor |
| 60 frames/s | **9 pads wrong**, 10.2 s of backlog | drain at the idle floor |

Idle round trip 102 ms, which is the floor every drain figure is measured against.

**The palette path is effectively free.** RP-052 lets the whole grid share one packet, so
a full frame is a single 65-byte write, and at 120 frames in two seconds the link never
fell behind. Confirmed by eye: smooth alternation with no visible stutter at the top rate.

**The vendor path sustains about five full-grid updates a second.** Above that it either
drops writes or falls seconds behind, and the apparent successes at 20 and 30 are worse
than the failures, because the grid was still catching up seconds after the input stopped.

**Consequence for the renderer, and it is a choice rather than a tuning knob:**

| | Palette over MIDI | 24-bit over the vendor channel |
|---|---|---|
| Colours | 7 usable, all full brightness | any, with real brightness |
| Full-grid rate | 60/s or better | ~5/s |
| On its own press | host owns the LED, no flash | flashes white locally |
| Requires | the Led byte armed | the Led byte at 0xFF |

A pad answers to one or the other, and animation is only possible on the first. A design
that wants both brightness as a state channel and a ripple animation cannot have them:
the palette path is the one that animates, so state has to be carried by colour rather
than by level.

### 9.1 The palette judged by eye, measured 2026-09-10

The velocity walk above lit one pad at a time, six seconds apart, which is the worst way
to judge colour: memory for a hue across that gap is poor and every value looked distinct
at the time. What decides a control surface is whether colours separate **side by side, at
a glance, across the room**. So the whole palette was shown sixteen values at once on the
lit grid, with `scripts/led_console.py` holding the link open so the owner could look at
one frame, say what they saw, and only then be shown the next. Every result below is the
owner's own report on the physical grid.

- **Every entry in the palette is pastel.** Values 1 to 64 were shown in four consecutive
  frames of sixteen. Not one saturated colour at any index. This is not a limitation of
  the walk: it is the palette.
- **There are no shade families.** Launchpad, APC and Push palettes are hue families of
  three or four brightness steps each, which is where their apparent dimming comes from
  (§8). This one is not built that way. Values the walk had named alike were shown
  together, two greens (5, 19), four yellows (1, 17, 4, 60), three whites (13, 40, 32) and
  four blues (7, 9, 21, 48), and within every row they read as **genuinely different
  colours rather than one colour at different levels**.
- **The MIDI channel is ignored on all sixteen channels.** Earlier runs tried channels 1,
  5 and 10; every comparable controller puts brightness and blink on the channel, so all
  sixteen were tried at once, one per pad, same note and same velocity. The grid came up
  uniform. There is no firmware brightness step and no firmware blink or pulse mode.
- **The five candidate identity colours do separate.** Blue 21, purple 24, green 5,
  red-pink 14 and orange 15, scattered across the grid with gaps between them, read as
  five different colours.
- **Purple 24 against white 40 is the weak pair.** With each of the five shown beside
  white, only purple was reported as close to it. That matters because white is the
  obvious candidate for "this entity is off".

**The vendor path is saturated but dim, and that is the whole trade.** The factory's own
stored colours are `(150, 200, 240)` in bank 1 and `(240, 0, 240)` in bank 3, so 0xF0 is
the ceiling the firmware itself uses, and a saturated magenta is an ordinary RGB write with
no hidden mechanism. Written at full power beside their nearest palette equivalents:

| Written | Against | Reported |
|---|---|---|
| `(240, 0, 0)` | palette red-pink 14 | deeper red, but dimmer |
| `(240, 240, 240)` | palette white 40 | palette white is brighter |
| `(255, 255, 255)` | `(240, 240, 240)` | only very slightly brighter |

So the palette path drives the LEDs harder even at equal hue and equal channel count, and
the ceiling is real rather than an artefact of writing 240 instead of 255. A deep red is
dim partly because one channel is lit where a pastel lights three, but white against white
removes that explanation and the palette still wins.

**A whole page was then rendered both ways**, sixteen pads laid out as a room, and the
colour version was reported as dim throughout, including its lit pads. Asked which they
would rather have on a wall every day, the owner chose the palette version.

**Dithering does not buy back a brightness channel.** Sixty frames a second is fast enough
to switch a pad within a frame or two, so three duty cycles were tried against a steady
reference row, at two thirds, one half and one third. All three read as **flicker, not as
dimming**. That matches the complaints §8 found against the same trick on other hardware.

**Conclusion, and the LED language rests on it.** There is no brightness channel on this
device by any route: not in the palette, not on the channel, not by dithering, and the one
path that does dim is dim everywhere and too slow to animate. State must be carried by
colour, by position and by slow motion. Blink is the only lightness-based channel left,
which is worth knowing because §8 records it as the redundancy that colour-blind users of
comparable grids asked for by name.

**Region 4 begins with a live state block, and it holds the three registers the
integration needs.** Read as `78 00 32 04 00 00 KK 00 01 01 SS BB 00 …`:

- **byte 10 is the preset slot on display, counted from zero.** It read 6 while the pad
  was on preset 7, 2 while on preset 3 and 0 on preset 1, each read before the preset
  was known.
- **byte 6 is the base pad bank, counted from zero**, default 2 for image bank 3, and the
  Shift+octave keys step it. One octave up read 3 and PAD1 sent note 52, the first note
  of image bank 4; one octave down from the default read 1 and PAD1 sent note 20, the
  first note of image bank 2. So "octave" on this device means the next or previous
  sixteen notes, which is the next or previous image bank, and no transposition happens.
- **byte 11 is the PAD BANK toggle.** It went 0 to 1 on a PAD BANK press, alone among the
  header bytes, and read 1 again after two further presses. State 0 shows the base bank;
  state 1 shows **image bank 8**: blue written to bank 4's PAD1 and red to bank 8's
  showed red while toggled, and the notes 52–67 seen while toggled are bank 8's. Only
  measured with the base bank at its default; whether the toggle target moves with the
  base bank is untested. `devices/smc_pad.py` reads the block with `state_read_packet` and
  `parse_state`, whose `bank` property applies the rule.

Bytes 8 and 9, `01 01`, never moved and are not explained; candidates are swing and
velocity, the other Shift functions.

**PAD BANK is momentary on a Note-typed bank and latches on an MCP-typed bank.** On the
factory presets it shows bank 8 only while held: pad 1 sent 52 while the button was held
and the state byte read 0 afterwards. On the sequencer preset, whose bank 3 is MCP, one
press left the state byte at 1. The single-variable test: on the factory slot 0, all
sixteen bank-3 pads typed MCP by RAM write, a press and release, and the state byte read 1.
On preset 7, with two MCP pads among Note pads, it did not latch, so the rule needs more
than a couple of MCP pads; whether it needs all sixteen is untested. A preset image holds
no other global bytes: the sequencer and factory images differ in no byte outside the
known record fields.

A block at 0x0360 in region 4 changed from sixteen varied byte triples, four bytes apart,
to sixteen identical ones at the bank switch. **Hypothesis:** the live LED frame buffer.

Regions 0, 3, 4 and 5 answer reads; 1, 2, 6 and 7 do not. Region 3 is a repeating table,
region 0 looks unstructured.

**Volatility, proven by diff.** With a pad colour, three Led bytes, an encoder's mode and
range, and a button's Led byte all edited by RAM writes, slot 0 was dumped, the pad was
power-cycled, and slot 0 dumped again. Exactly eleven bytes differed, every one an edit
reverting to its factory value, and the address and the state block were unchanged. Twice
now, once before and once after a factory reset, nothing written over the radio has
outlived a power cycle, and nothing else has changed either.

**Bulk writes work, and the pad queues them.** The factory bank 3, 416 bytes with every
pad set to Note on channel 10 and its Led byte equal to its note, was written in one
packet: acknowledged in 307 ms and read back identical, and note-ons then lit the pads.
All sixteen encoder records, 96 bytes, went relative in one packet the same way. Sixteen
single-byte Led writes fired 50 ms apart, then sixteen more, all landed and each was
acknowledged in turn. So the connect-time setup is three packets, one per record table,
and about a second, with no waiting on acknowledgements.

**Boundaries kept on purpose.** Only region 5 records were written: pad colour, pad Led,
pad type, pad channel, pad payload, encoder records, button Led, and whole record tables
built from the factory image, each at addresses confirmed by reading first. No flash
command exists in the code, and region 4 was never written.

## 10. Known unknowns

Settled and moved into the sections above: the slot and bank registers, address
stability, volatility, Led as a note selector, buttons' Led bytes, relative encoders by
RAM write, the encoder step values, the MCP SysEx byte, the pad channel byte, the Custom
payload, the palette on a Note pad, what makes PAD BANK latch, and that an armed pad never
shows its RGB field. Settled 2026-09-10 in section 9.1: that the whole palette is pastel
with no saturated entry, that it has no shade families, that the MIDI channel is ignored on
all sixteen channels rather than the three tried before, that the vendor path is dimmer
than the palette path at equal hue, and that dithering reads as flicker rather than as
dimming. Together those mean the device has no brightness channel by any route.

1. Shift with pads 1 to 8 selects the preset; with pads 9 to 16 it sets swing, velocity
   and the base bank, the owner's account plus section 9. What the swing and velocity
   settings do to the stream, and whether the transpose function differs from the octave
   one, is untested; bytes 8 and 9 of the state block are the candidates for where they
   live.
2. Whether a Comb MCP pad sends anything once its SysEx box is filled, and what "Comb"
   combines (section 5).
3. How many MCP pads in a bank make PAD BANK latch: sixteen do, two do not (section 9).
4. Whether MCP-typed pads use a different palette table; velocity 40 looked off on one and
   is white on a Note pad, and the USB table showed green at 127 (sections 6 and 9).
5. Whether the pad keeps advertising and transmitting on the radio while on USB power and
   while a DAW holds the USB ports.
6. Where the aftertouch and pad-curve settings live; the old configuration sent channel
   aftertouch bursts, the factory one sends none (section 3). A MidiSuite change and a
   diff would find them.
7. The block at 0x0360 in region 4 that changed at a bank switch (section 9).
8. The LED latency of the vendor path, whose acknowledgement lags the write by about
   410 ms, against the MIDI path.
9. Whether CC Toggle and Momentary pads take Led feedback like Note pads; only Note, MCP
   and the play button were armed (section 6).
10. The second vendor service, `AE00`, never used and never heard from.
