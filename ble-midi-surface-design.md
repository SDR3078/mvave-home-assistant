# `ble_midi` — control surface design

Companion to `ble-midi-integration-brief.md`. That document covers transport, repo, and testing. This one specifies the **profile engine**: what pads and knobs mean, how navigation works, what events are emitted, and how feedback is rendered.

Hardware assumption: 16 pads (4×4, RGB, velocity-sensitive) + 8 relative rotary encoders + 5 transport buttons. **The encoders have no LED rings.** Nothing here may assume ring feedback.

**What the LEDs can and cannot do, measured rather than assumed.** Section 5 was rewritten against the hardware on 2026-09-10; `docs/HARDWARE-BLE.md` sections 8.1 and 9.1 carry the evidence. In short:

- **There is no brightness channel, by any route.** Not in the palette, which is pastel throughout and has no shade families. Not on the MIDI channel, which is ignored on all sixteen. Not by dithering, which reads as flicker. The one path that does dim is dimmer everywhere and manages five full-grid updates a second against sixty, so it cannot animate. **"Colour = identity, brightness = state" cannot be built.**
- **Five colours are reliably distinguishable at a glance, across a room**: blue, green, orange, red-pink, purple. A sixth candidate always collapsed into one of those. White is a sixth usable value but reads like purple.
- **Animation is free.** A full grid redraws sixty times a second on the palette path.
- **The five transport buttons have LEDs** and all five light. They are single colour, green, on or off. Navigation lives there, which is why the grid reserves no pads at all.

---

## 1. Core model

The engine is a **graph of pages** plus a navigation stack. It has no concept of "room" — a room is a page whose source is an area.

State:

```
stack:  [home] → [home, living] → [home, living, tv]     # navigation history
focus:  entity_id | None                                  # sticky knob target
hud:    knob_id | None                                    # transient, see §6
```

A page is:

```
id, title, color            # identity color used by LEDs and animations
source                      # how unassigned slots are filled (§2)
area_id                     # optional; context for area sources
parent_page_id              # optional; used by `back` color, not by navigation
pads: {slot -> PadConfig}   # explicit overrides
knobs: {knob -> KnobConfig} # optional per-page overrides
idle_timeout                # seconds, 0 = never
```

`PadConfig` has separate `tap` and `hold` actions plus optional `color`. Actions:

`navigate(page_id)` · `back` · `home` · `toggle(entity)` · `focus(entity)` · `scene(entity)` · `script(entity)` · `service(domain, service, data)` · `event_only(tag)` · `none`

### Slot reservation

The engine reserves **no pads at all**. All 16 belong to the user or the auto-fill.

Navigation lives on the transport buttons, which have their own LEDs (left 25, right 26, play 27, stop 28, record 29, all lit by a note-on for that number once their Led byte is armed):

| Button | Role | Lit when |
|---|---|---|
| left | `back` | there is somewhere to go back to |
| stop | `home` | you are not already on the root page |
| right, play, record | free, per page | the page assigns them |

This was originally a reserved pad in the bottom-right corner. Moving it off the grid buys back a sixteenth of the surface and removes a colour collision: any colour the back pad could take was either a room's identity colour or white, and white is what "off" means.

### Defaults by domain

When a slot is auto-filled from a source, the action is derived from the entity's domain:

| domain | tap | hold |
|---|---|---|
| `light` | toggle | focus + peek (§6) |
| `switch`, `input_boolean` | toggle | — |
| `media_player` | play/pause | focus + peek |
| `cover` | toggle open/close | focus + peek |
| `scene` | activate | — |
| `script` | run | — |
| `climate` | — | focus + peek |

---

## 2. Sources

A source yields an **ordered list of entity IDs**. Explicit `pads` config wins; remaining slots fill from the source in order; leftover slots stay dark.

v1:

- `area` — entities in `area_id`, ordered `light`, `media_player`, `cover`, `switch`, `scene`, `script`. Uses the HA area + entity registries. This is the "room page".
- `label` — entities carrying an HA label (e.g. `pad:morning`). Curated in the UI users already know; works across domains. This is the "dashboard-like page".
- `explicit` — the `pads` config is the whole page, no auto-fill.

Deferred (must not require engine changes to add):

- `template` — Jinja returning a list of entity IDs.
- `dashboard_view` — read a Lovelace view from storage, map its cards to slots in order. Mapping is fuzzy for sections/nested cards; v2 at the earliest.

A page with no user config and `source: area` is fully usable out of the box. **Configuring means overriding, never building from zero.**

---

## 3. Navigation

- Any pad may `navigate` to any page — the structure is a graph, not a tree.
- `back` pops the stack. `home` clears to the root page.
- **Shift gesture**: hold the `left` button → row 1 temporarily becomes a switcher for top-level pages (the "tab bar"). Release without pressing = no-op.
- **Idle timeout**: per page, default 30 s, `0` on pages that should persist (media). On expiry, return to home with the slow fade (§5).
- Navigation is drivable externally — see services in §4.

`home` is an ordinary page. Its default content is auto-generated `navigate` pads, one per configured area, each in that area's identity color; row 4 left for global scenes / all-off. The user may override it like any other page.

---

## 4. External interface

### 4.1 Bus events

Fire `ble_midi_event` on every transition. One event type per transition, never batched:

```yaml
type: page_entered | page_exited | focus_set | focus_cleared
    | pad_pressed | pad_held | knob_turned
device_id, address
page_id, page_title, page_source, area_id, parent_page_id, depth
entity_id                 # focus target or toggled entity
pad, note, velocity       # pad events only
knob, delta, value        # knob events only
trigger: pad | service | idle | automation
previous: {page_id, area_id, entity_id}
```

`page_source` is included so external displays know whether they're rendering an area, a label, or something else.

### 4.2 State entities

Dashboards must work without writing an automation:

- `select.<name>_page` — current page, settable
- `sensor.<name>_area`, `sensor.<name>_focus`, `sensor.<name>_depth`
- `binary_sensor.<name>_connected`
- `event.<name>_pad`, `event.<name>_knob`

### 4.3 Device triggers

`entered page X`, `left page X`, `focused entity`, `pad N pressed/held`, `knob N turned` — so users can bypass the engine entirely in the automation UI.

### 4.4 Services

```
ble_midi.navigate(device, page_id)
ble_midi.focus(device, entity_id)
ble_midi.home(device)
ble_midi.set_pad_color(device, pad, rgb)      # escape hatch
ble_midi.send_raw(device, data)               # escape hatch
```

Symmetric in/out is a requirement, not a nice-to-have: presence sensors pre-selecting a room, a wall tablet steering the pad, and an automation pushing to a "movie" page when the TV turns on all depend on it.

---

## 5. LED language and animations

Rewritten 2026-09-10 against the hardware, judged by eye on the physical grid rather than reasoned about. The rule this replaces was "colour is identity, brightness is state", which the device cannot do at all.

### 5.1 The two modes, and why colour can be reused

The grid is only ever showing one of two kinds of page, and **they never mix**:

- an **index**, where every lit pad navigates somewhere and nothing has an on or off state;
- a **page**, where every lit pad is an entity that is on or off.

That separation is what makes five colours enough. Identity colours live on the index; the on and off colours live on a page; neither set ever has to be told apart from the other, because they never appear together.

### 5.2 Static rules

| Element | Treatment | Why |
|---|---|---|
| Index: each room or page | one of **blue, green, orange, red-pink, purple** | the five that survived being shown together, scattered, across a room |
| Index: beyond five pages | colours repeat, **fixed position identifies** | a sixth colour always collapsed into one of the five; position is a free channel and survives colour blindness |
| Page: entity on | **orange** | one pair everywhere, learned once, independent of which page you are on. Matches Home Assistant's own amber for active |
| Page: entity off | **white** | the only value distinct from all of blue, green, orange and red-pink |
| Page: nothing assigned | **dark** | pressing it does nothing, and it must not look like an entity that is off. This is why "off" cannot also be dark |
| Focused pad, the knob target | **breathing between on and off**: 1.4 s period, its own colour for about two thirds of it | slow and lopsided, so it cannot be mistaken for the alarm below. Confirmed legible in a full page without pulling the eye |
| Waiting, commanded but not confirmed | **swinging between on and off**, about 2 Hz | reads as "something is wrong or pending", which is exactly the meaning. It is the same rhythm the whole industry uses and Home Assistant's own interface pulses at 1 Hz for `locking` |
| `back` available | **left button lit** | see §1 |
| `home` available | **stop button lit** | |

**Blink is scarce and must not be spent twice.** It is the only channel left after colour and position, it is the documented accessibility fallback, and a grid with several things blinking at once is the documented failure mode. One meaning only: not confirmed yet.

**Nothing ever blinks to darkness.** Both rhythms alternate the two state colours, orange and white. A pad blinking to black reads as a light going out, which is a lie about a lamp that is on and staying on, and it was the first thing anybody complained about when it was tried on the hardware. Novation reached the same rule independently: their flash alternates two colours and only their slow pulse goes dark. The two rhythms therefore differ in rate alone, by a factor of three and a half, which was enough.

**Only a pad in one of the two states may move.** Motion means "between on and off", so a pad that is in neither has nothing to be between. An unreachable pad is therefore completely still, and it is also completely inert: pressing or holding it does nothing. Commanding something that cannot answer would leave the pad moving forever, waiting for a confirmation that never comes. Scenes and scripts are exempt from the inertness, because their resting state in Home Assistant is `unknown`, which is not the same as unreachable, and they are exactly the pads people press.

**A pad only goes solid once the entity's real state arrives.** The surface is deliberately not optimistic: it never claims a lamp changed because somebody asked. The cost is that a slow cloud-connected device will swing for as long as it takes to answer, and there is as yet **no timeout** on that, which is the coordinator's job rather than the engine's since the engine has no clock.

**Known collision, accepted.** A page whose identity colour is orange has a curtain (§5.3) the same colour as its own switched-on entities, so the curtain's edge is invisible on those pads for the length of the transition. It is transient, it affects one page out of five, and the alternative is dropping to four identity colours. Reversible: remove orange from the identity set and the collision goes.

### 5.3 Transitions

Sixty full-grid frames a second are available, so the budget is generous.

**One pad lights at a time, never more.** That is the single rule the shapes below exist to satisfy, and it was arrived at the hard way: rings and columns were built first, and both felt uneven no matter how evenly they were timed. They cannot help it. A ring around a corner pad is one pad wide and the next is three, then five, then seven, so the amount of light arriving changes at every step. Measuring the frames on the wire proved the timing was even to within a few milliseconds while it still read as a limp. A single pad per step cannot have that problem, and it removed a second one for free: entering from a middle pad used to take fewer steps than from a corner, so the same gesture had two different durations.

**45 ms a pad**, so a page change is under a second and a half. Below about 40 the travelling edge stops reading as an edge and becomes a blur, which is the floor worth going to. A ring or a column at a time needed 350 ms a step to read at all; a pad at a time reads comfortably at a fraction of that, because there is no longer a jump to take in.

**Entering a page** answers two questions in order: which pad did I press, and what is in here.

1. **Close, winding out from the pressed pad.** A clockwise spiral: the pressed pad, then the ring around it entered from directly above and swept clockwise, then the next ring, until the grid is covered in the destination page's colour. The page you are leaving stays lit ahead of the curtain, so nothing blanks. Sixteen steps, wherever it starts.
2. **Open, left to right.** A column at a time, each filled from the top, uncovering the destination page, which is already in its real colours as it appears. Sixteen steps.
3. The transport buttons change **on the final frame**, as the last pad clears.

Entering a page from its own pad on the index means that pad already carries the curtain's colour, so the first frame changes nothing. That is deliberate and is what makes the curtain look like it grew out of the finger rather than appearing on top of it.

**Leaving a page** is the exact mirror, so that going back undoes going in:

1. **Close right to left**, a column at a time filled from the top, over the page being left.
2. **Wind back inward** to the pad that page occupies on the index, the spiral run in reverse, so the last pad still covered is the one originally pressed.
3. The transport buttons go dark **immediately**, on the first frame of the curtain, because the affordance has already been used.

| Transition | Animation |
|---|---|
| enter a page by pressing its pad | a clockwise spiral out from that pad, then a left-to-right open |
| `back` | right to left, then the spiral in reverse into that page's index pad |
| `home` | as `back`, but shrinking into the index's own root position |
| navigate by service, automation or presence | the same close and open, with **no origin**: both halves are column wipes, because inventing an origin pad implies a finger that was not there |
| idle timeout to home | a sideways wipe only, no spiral, and no button flash. Nothing happened, so it should not look like it did |
| focus change | no grid animation, only the focused pad starting to breathe |

**Why a spiral one way and columns the other.** The spiral says where the finger was. Columns say here is a page, and left to right is how a grid is read.

### 5.4 Rules

- Animations are frame generators in the engine; the coordinator plays them on a fixed tick and diffs against the last frame, exactly like static frames.
- **Any pad press aborts the running animation** and jumps to the resolved state. Input is never queued behind eye candy.
- Anything that is not a grid frame, the transport buttons above all, must be schedulable **against a specific frame** of an animation rather than firing at its start or its end.
- Primitives: `sweep(order, before, after)` and the two orders it is given, a clockwise spiral from a pad and a column sweep sideways. Every transition is one of those; the shapes are the design and the mechanism underneath has nothing in it. Plus `breathe(pad)` and `blink(pad)`. No fade and no partial intensity: there is no intensity.
- Global setting `animations: full | minimal | off`; `minimal` = the sideways wipe only, no spiral.

---

## 6. Knobs

**Encoders are relative and have no rings.** All feedback is on the grid. Knobs never change *meaning*, only *target*.

Global knob assignment (fixed, muscle memory lives here):

| knob | property |
|---|---|
| 1 | brightness |
| 2 | color temp |
| 3 | hue |
| 4 | saturation |
| 5 | volume |
| 6 | cover position |
| 7 | climate setpoint |
| 8 | free / per-page |

If the focus lacks a property, that knob is inert. Per-page `knobs` config overrides the target for specific knobs (e.g. volume always hits the room's media player regardless of focus) — this is the one place per-page config beats the global rule.

### 6.1 HUD

Since there is no persistent readout, the grid becomes a transient one.

- On the **first tick**, overlay a value bar on the whole grid. Hold it while turning plus roughly **a second** after the last tick, then **snap back** to the page. Nothing is animated in either direction: the bar appears every time anybody touches a knob, which is often enough that a transition stops being a flourish and becomes something to sit through. It was built with a column wipe on the way out first, and that is exactly how it felt.
- **16 pads = 16 steps, filling upwards from the bottom row.** About six percent a pad. Level rises, so the bar rises; cover position filling downwards (§6.3) is then a deliberate exception rather than an arbitrary one.
- **No sub-step resolution.** The original design dimmed the last lit pad proportionally, which needs a brightness this device does not have. Two substitutes were built and tried on the hardware and both were rejected: blinking the pad above the run read as a fault, because blink already means "not confirmed" (§5.2), and capping the run with a second colour read as a pad that did not belong to the bar. Sixteen steps is finer than a dimmer needs.
- The HUD is **per-knob, not per-entity** — the knob you touched decides which property is shown. If two knobs are turned together, show the most recent.
- The bar is a single contiguous run in one colour growing from one edge, which is a shape a page never produces, so it is recognisable as "not a page" before its colour is even read.

### 6.2 Peek

`hold` on a pad shows that entity's primary value bar **immediately, before anything changes** — brightness for a light, volume for a player, position for a cover. Turning a knob while holding adjusts that entity. On release the HUD fades and the entity stays **sticky focus**, so a later bare knob turn still targets it. This replaces what encoder rings would have provided.

### 6.3 Color language per property

Constant across every page. **One flat colour per property, not a gradient**: every ramp in the original design needed many graded steps along one hue, and the palette has neither brightness nor controllable saturation. What survives is one fixed colour naming which property you are holding, and the length of the bar carrying the value.

| Property | Bar colour | Note |
|---|---|---|
| brightness | white | |
| colour temp | orange | |
| saturation | green | |
| hue | **cut** | see below |
| volume | green | pads above a configurable "loud" threshold switch to red-pink, an extra colour appearing rather than a shade changing |
| cover position | blue | fills **top-down**, the one exception, because it is a blind |
| climate | see §6.4 | |

**Hue is cut entirely.** It wanted the grid to show the actual colour being chosen, sweeping across all 16 pads. Usable hue repeats every 13 or 14 palette steps with only five unambiguous entries, so 16 pads would show two or three repeats of a handful of colours, reading as "these pads are grouped" rather than as a continuous dial. The one property where seeing the result was the whole point is the one the palette cannot show. Hue gets an ordinary bar or a pad-per-preset instead.

**There is no red at any index.** Everywhere the original design says red, it means red-pink, which is as close as the palette gets.

### 6.4 Climate

Two numbers, so: **fill = setpoint**, one contrasting pad = **current temperature**, making the gap visible. Fill colour by direction relative to current: blue when asking for cooling, red-pink for heating, white within ±0.3 °C. The current-temperature marker is a **white pad on the same 16-step scale**, so the distance still to travel is the gap between the top of the fill and the marker. Mode (cool/heat/auto) is **not** a knob — assign it to a pad.

### 6.5 Behavior

- Update the HUD on **every** tick (optimistic), but **debounce the service call ~250 ms** after the last tick. Turning a knob must never fire 40 `light.turn_on` calls.
- Min/max reached: one quick full-bar flash.
- If the target is off (light off, player muted), the first tick **turns it on at the lowest step** rather than adjusting an invisible value.
- The HUD interrupts page animations; any pad press cancels the HUD and executes immediately.
- The HUD **snaps on in one frame** and never animates in. How the grid arrived is a channel of its own: snap means HUD, rings mean you navigated, a bare column wipe means something else moved you.

### 6.6 Optional persistent meters

**Cut.** The original was a per-page opt-in reserving a row as mini bars, justified as "is it loud" and "is the AC heating or cooling" reading fine from brightness and colour. The brightness half does not exist, and a two-pad meter with no gradation cannot show a level. It was opt-in and off by default; it does not earn the pads it costs.

---

## 7. Configuration surface

Config subentries, one per page, using HA selectors (`AreaSelector`, `EntitySelector`, `LabelSelector`, `SelectSelector`). Shape:

```yaml
page: living
  title: Living room
  colour: orange          # one of blue, green, orange, red, purple
  source: area
  area: living
  parent: home
  idle_timeout: 60
  pads:
    3:  {tap: {scene: scene.living_evening}, hold: {navigate: tv}}
    12: {tap: {event_only: coffee}}
  knobs:
    5: {target: media_player.living_tv}
  buttons:
    right: {navigate: tv}
```

**Colour is a choice of five, not a colour picker.** The original used `ColorRGBSelector`, which would offer sixteen million colours the device cannot show and let a user pick two that look identical on the grid. A `SelectSelector` over the five measured colours cannot produce an unreadable surface. Warn at config time when a colour is used by more than one page, since past five, position rather than colour is doing the identifying.

There is no `back_pad`: back and home are the transport buttons and are not configurable. `right`, `play` and `record` are, per page.

Unlisted pads auto-fill from the source. Unlisted knobs use the global assignment against `focus`.

---

## 8. Implementation constraints

- `engine/` contains **no** `homeassistant.*` imports. HA access goes through `RegistryView` (areas, entities, labels, states) and `ActionSink` (service calls) interfaces so the whole engine is unit-testable with fakes.
- Engine is synchronous and pure: `handle(input_event) -> (list[HaAction], list[LedFrame])`. All async lives in the coordinator.
- LED writes are diffed against the last frame; never rewrite the full grid when three pads changed.
- Entity state changes for currently-displayed entities re-render via `async_track_state_change_event`, scoped to the visible set — resubscribe on page change, not a global listener.

## 9. Test requirements (additions to the brief)

- Navigation: stack push/pop/clear, graph jumps, shift gesture, idle timeout, external `navigate` service.
- Sources: area/label/explicit fill order, explicit-overrides-source, more entities than slots, fewer entities than slots.
- Gestures: tap vs hold thresholds, hold-then-turn, release ordering.
- HUD: debounce window, min/max flash, off-target first tick, pad press cancels HUD, 16 steps with no sub-step.
- Animations: frame counts and abort-on-input, `minimal` and `off` modes. Plus, because every one of these was got wrong by hand first: **every step of an animation lasts the same time**, entering is rings-then-columns and leaving is columns-then-rings, the outgoing page stays lit ahead of the curtain rather than blanking, the incoming page is behind it in its real colours, and the transport buttons land on the frame they are supposed to.
- LED language: no palette index above 95 is ever emitted, 127 and 96–126 are never used as colours, and only the five measured identity colours are offered in config.
- Events: exactly one event per transition, correct `previous` payload, `trigger` field accuracy for pad vs service vs idle.
