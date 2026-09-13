# M-Vave for Home Assistant

Turns an **M-Vave SMC-PAD** — an inexpensive Bluetooth LE MIDI pad controller — into a
control surface for your house. Sixteen backlit pads, eight endless encoders and five
transport buttons become a physical way to walk through your rooms, switch things on and
off, and dim a lamp with your hand rather than your phone.

No cloud, no polling, no MIDI software in between. Home Assistant connects to the pad over
Bluetooth, reads the device's own configuration out of its memory, and drives the LEDs
directly.

> **Status: works, not yet released.** Everything below is running on real hardware, and
> everything it claims was judged on the physical grid rather than reasoned about. See
> [`docs/NEXT.md`](docs/NEXT.md) for what is left.

---

## What it looks like on the device

You get a **grid of pages**. One page per room, plus an index listing them.

- Press a room on the index and it opens: the page **grows out of the pad you pressed**,
  one pad at a time, winding outwards, then a curtain opens left to right. Going back runs the same thing in
  reverse, shrinking into the pad the room lives on.
- Inside a room, every pad is something in that room. **Tap** toggles it. **Hold** points
  the knobs at it.
- **Turn a knob** and a value bar covers the whole grid for a second — sixteen pads filling
  from the bottom — then snaps back to the page.
- The **left** transport button is back. **Stop** is home. A button is lit only when
  pressing it would do something.
- **Hold left** and the top row becomes your rooms, each in its own colour, with the rest
  of the grid dark — press one to go straight there without passing home. Let go without
  pressing and nothing happened.
- Leave it alone inside a room for thirty seconds and it returns to the index the same way
  a press of **back** would have, curtain and all. The event it fires says `idle` rather
  than `button`, so an automation can still tell nobody was standing there.

Nothing needs configuring for this to work. A fresh install builds a page per room out of
your area registry and fills each one from what is actually in that room.

## What the colours mean

This is the whole language, and it is short because the hardware is unforgiving — see
[the constraint](#the-constraint-that-shapes-everything) below.

| The pad shows | It means |
|---|---|
| **Its own colour** | the thing behind it is **on**. Orange for lights and switches, blue for media players, green for covers, red for thermostats and locks, purple for scenes and scripts. All twenty kinds of thing configurable |
| **White** | the thing behind it is **off**. Never configurable — the readability of every page rests on this one rule |
| **Dark** | nothing is assigned here. Pressing it does nothing |
| **Breathing slowly** | the knobs are pointed at this one |
| **Blinking fast** | commanded, not yet confirmed. It stops as soon as the entity reports back |
| **Holding orange for a moment** | a scene or button you just pressed, saying so. It has no on and off of its own, so this is the only thing it can tell you |
| **Three quick blinks under your finger** | it refused: either nobody can reach that entity, or this pad cannot be acted on |

"Is anything still on in the kitchen?" becomes "is any pad not white", which is one glance.

### Pads that tell you rather than offer you

A pad can be a **readout**: a door sensor, a motion sensor, whether somebody is home,
whether the sun is up. It shows its colour when the door is open or the person is in,
white when not — the same language as everything else — and it **shudders if you press
it**, which is the truth about it.

Readouts are green by default, all of them, so "green is something I watch" is one thing
to learn rather than six. They sit in the green box with blinds, and you can move them out.

Two rules keep them out of your way:

- **Auto-fill is a guess; pinning is a statement.** A room page fills itself only with
  things you can *control*, because a real kitchen holds a temperature, a damp sensor and
  two phones, and a grid holds sixteen pads. A readout only ever appears where you put one.
- **Anything the grid cannot show is never offered.** 21.5 °C has no on and no off, so a
  temperature can't be pinned at all — a pad sitting white forever would be lying.

## The knobs

Eight relative encoders, no rings, no markings, no labels. They sit two across and four up,
numbered from the bottom left — so encoder 7 is the top left one.

Whatever you are holding, its controls fill them **from the top left, with no gaps** — so
the first encoder always adjusts the main thing:

| | |
|---|---|
| **7** the main value | **8** colour temp |
| **5** hue | **6** saturation |
| **3** — | **4** — |
| **1** — | **2** — |

A lamp is the only thing in a house with more than one control, so on everything else —
a blind, a fan, a speaker, a thermostat — encoder 7 is the only live one. One thing to
learn instead of eight.

**Turn an encoder that does nothing and the grid draws that map**, each live one in the
colour of what it adjusts and the dead ones in white:

**orange** the level · **blue** colour temp · **green** hue · **red** saturation

Those are the same colours the value bar uses, so the map is a promise about the bar you
will get. It is the only thing on the device that can tell you which encoders are live —
there are no rings and no markings — and it is why a fan, where seven of the eight do
nothing, is usable at all.

A page can redirect a knob to a different *entity* — "volume here always means the kitchen
speaker" — in the engine. **There is no way to set it yet:** the page form asks for a name,
a colour, a source and sixteen pads, and nothing writes the knob or button overrides that
`Page` carries.

Turning a knob moves the light **while you turn**, throttled to about seven commands a
second. For comparison, Home Assistant's own brightness slider sends nothing at all until
you let go, and its colour-temperature wheel sends two a second.

---

## Installing

**HACS** (recommended): add `https://github.com/SDR3078/mvave-home-assistant` as a custom
repository of type *Integration*, install, restart.

**By hand:** copy `custom_components/mvave` into your `config/custom_components/`, restart.

Then: switch the pad on, make sure nothing else is connected to it, and Home Assistant
should discover it. Otherwise add it from **Settings → Devices & services → Add
integration → M-Vave**.

You need a Bluetooth adapter or an ESPHome Bluetooth proxy in range. The pad accepts one
connection at a time and stops advertising while it is held, which is normal and is why the
connectivity sensor reads the link directly rather than trusting the Bluetooth integration's
view of it.

### Configuring

Nothing, if the default is what you want. Otherwise **Settings → Devices & services →
M-Vave**.

**Pages are things you add**, each its own row under the integration with its own *Configure*
and *Delete* — the same shape Home Assistant gives a bulb on a hub, because that is what a
page is. **Add page** asks for four things:

| | |
|---|---|
| **Name** | what it is called on the index |
| **Colour** | names it on the index, and is the curtain that sweeps in and out |
| **Fill from a room** | it fills itself from that area, and keeps up as the area changes |
| **Fill from a label** | it fills itself from whatever carries that label, across rooms |

Give it neither and the page is yours to fill pad by pad. The next screen does that: sixteen
optional fields, with the grid drawn above them. A pad you pin is fixed; every pad you leave
empty still fills itself from the room. So pinning one thing does not mean pinning sixteen.

A page's identity is its own — a ULID, not the room's name — so it survives the area being
renamed, or deleted, or never having existed.

**Configure** on the integration itself is one screen, and it is shaped like the palette
rather than like your entity list: **a box per colour**, holding the kinds of thing that
colour means. The device has five colours and that is all it has, so a form shaped any other
way would be lying about the scarce thing.

Every kind of thing belongs in exactly one box. To paint your lights green you take them out
of orange and put them in green — and you see what they were sharing orange with, which a
list of dropdowns cannot show you. White is never a box, because white is what "off" means.
Purple only takes things with no on and off of their own: purple against white is the one
pair too close to tell apart on this hardware, so on anything switchable it would be
unreadable exactly when it mattered.

Saving anything rebuilds the surface in place and keeps you on the page you were standing on.
It does not reconnect, which would cost twenty seconds.

---

## What it gives Home Assistant

### Entities

The rule everything follows from: **a pad is a position; a page is a thing.** What sits on
pad five is resolved from your live area registry, so it moves the day you add a bulb to
that room. A page, a focus and a home button are facts about the profile and keep their
meaning. So position may be named by position, and a target may never be.

| Entity | |
|---|---|
| `select.<device>` | the page showing now, **settable**. Push the surface to a room from an automation, or read where somebody is standing |
| `sensor.<device>_focus` | which entity the knobs are on |
| `binary_sensor.<device>_connected` | the link |
| `sensor.<device>_battery` | what the pad says its charge is, asked every half hour because it declares notifications and never sends one. Deliberately not diagnostic, so it shows on the device card |
| `button.<device>_home`, `_back` | the two gestures a page may never rebind |
| `event.<device>_pad_1…16` | one per pad: `press_start`, `press_end`, `long_press_start`, `long_press_end` |
| `event.<device>_left`, `_right`, `_play`, `_stop`, `_record` | same, for the transport buttons |
| `event.<device>_knob_1…8` | direction and step count. **Disabled by default** — one turn is over a thousand MIDI messages and the surface consumes them all already |

There is deliberately no entity per *slot*. A dashboard button labelled "Kitchen lamp" that
quietly starts closing a blind is the worst kind of bug, and no naming scheme prevents it.

### Actions

```yaml
mvave.navigate:    {device_id, page}                      # page id, not title
mvave.focus:       {device_id, entity_id}
mvave.home:        {device_id}
mvave.press_slot:  {device_id, slot: 1-16, action: tap|hold}
mvave.get_pages:   {device_id, page?}                     # returns a response
mvave.send_raw:    {device_id, data}                      # raw MIDI, escape hatch
```

`mvave.get_pages` is the answer to the problem this device creates by design: **nothing is
written on it**, and a room page fills itself from the live registry, so the running
integration is the only thing that can say what a pad would do. It returns every page and
every pad, resolved, in the same vocabulary the LEDs use:

```yaml
slot: 1
entity_id: light.ceiling_lights
name: Ceiling Lights
tap: toggle
hold: focus
shows: "on"        # on | off | unreachable | action | empty
colour: orange
```

Including the one thing the grid physically cannot say: an unreachable pad and a pad that
is off are both white.

`mvave.press_slot` presses a **position**, for when the pad is out of reach or out of
battery. Anything that wants one particular lamp should call that lamp's own action.

### Events

Every transition fires `mvave_event` with a `device_id`, and carries a `trigger` of `pad`,
`button`, `service` or `idle` — so an automation can never mistake its own effect for a
person. A logbook platform renders each one as a sentence.

---

## The constraint that shapes everything

Measured on the hardware, and recorded in [`docs/HARDWARE-BLE.md`](docs/HARDWARE-BLE.md):

**There is no brightness channel on this device by any route.** Not in the palette, which is
pastel throughout with no shade families. Not on the MIDI channel, which is ignored on all
sixteen. Not by switching a pad fast enough to average it, which reads as flicker. The
vendor path that does dim is dimmer at every hue and manages about five full-grid updates a
second against sixty, so it cannot animate.

So "colour is identity, brightness is state" — which is how every design of this kind starts
— **cannot be built**. What is left is five distinguishable colours plus white and dark,
position, and slow motion. Every rule above is a consequence of that budget, and every one
of them was judged by eye on the physical grid rather than reasoned about.

---

## Development

```bash
scripts/setup      # devcontainer dependencies
scripts/develop    # Home Assistant with this integration loaded
pytest tests       # 455 tests
ruff check . && ruff format --check . && mypy
```

`transport/`, `devices/` and `engine/` import as **top-level packages with no Home
Assistant present** — no platform imports, no I/O, no clock. CI runs them on Python 3.11
through 3.14 with a step that *fails* if `homeassistant` is importable, because the point of
that job is the absence. That seam is what makes the hard parts cheap to test: "what does
the grid do when three lamps in a room go unavailable" is one line and needs no radio.

Two scripts hold a live link to the pad, which is what made designing by eye possible —
reconnecting between questions costs twenty seconds:

- `scripts/led_console.py` — takes one instruction at a time and renders frames, rhythms,
  bars and transitions. It can freeze an animation on a single step and log how long each
  frame was actually on screen.
- `scripts/surface_demo.py` — drives the real pad from the real engine against a pretend house.

**Nothing written to the pad is permanent.** Every device write is a volatile RAM edit, so a
power cycle restores your own presets exactly.

Further reading: [`ble-midi-surface-design.md`](ble-midi-surface-design.md) is the design
brief, [`docs/HARDWARE-BLE.md`](docs/HARDWARE-BLE.md) is everything measured about the
protocol, and [`docs/NEXT.md`](docs/NEXT.md) is the running to-do list.

## Licence

MIT. See [`LICENSE`](LICENSE) and [`NOTICE`](NOTICE).
