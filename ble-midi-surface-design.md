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

When a slot is auto-filled from a source, the action is derived from the entity's domain. **Every pad has a tap.** A thermostat originally had none, on the grounds that a single press has no sensible meaning for it, and the result was a lit pad that did nothing when pressed and was indistinguishable from a broken one. Anything that does still end up with no tap says so by shuddering (§5.2) rather than by sitting there.

| domain | tap | hold |
|---|---|---|
| `light` | toggle | focus + peek (§6) |
| `switch`, `input_boolean` | toggle | — |
| `media_player` | play/pause | focus + peek |
| `cover` | toggle open/close | focus + peek |
| `scene` | activate | — |
| `script` | run | — |
| `climate` | toggle | focus + peek |

---

## 2. Sources

A source yields an **ordered list of entity IDs**. Explicit `pads` config wins; remaining slots fill from the source in order; leftover slots stay dark.

v1:

- `area` — entities in `area_id`, ordered by `resolve.DOMAIN_ORDER`: `light`, `media_player`, `cover`, `climate`, `fan`, `switch`, `input_boolean`, `lock`, `scene`, `script`. Uses the HA area + entity registries. This is the "room page".
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
- **Shift gesture**: hold the `left` button → row 1 becomes a switcher for the rooms, each in its own colour, **and the rest of the grid goes dark**. Press one to go straight there; release without pressing and nothing has happened. Built 2026-09-12.
  - The rest of the grid goes dark because a switcher laid over a page still showing its own entities is half one thing and half another, with no way to tell which pad belongs to which. Dark says plainly that the surface is in a mode, and a dark pad already means "nothing here" everywhere else.
  - **Holding `left` no longer goes home.** It used to, and `stop` does the same thing, so one of the five buttons was spending its hold on a duplicate. That hold is what this gesture needs.
  - Four rooms fit across. A household with more reaches the rest through the index, which is the honest limit of a row four pads wide rather than a decision.
  - The pad you are already standing on stays lit and **shudders** if pressed, because lit-and-does-nothing is never allowed to be silent here (§5.2).
- **Idle timeout**: per page, default 30 s, `0` on pages that should persist (media). On expiry, return to home with the slow fade (§5).
- Navigation is drivable externally — see services in §4.

`home` is an ordinary page. Its default content is auto-generated `navigate` pads, one per configured area, each in that area's identity color; row 4 left for global scenes / all-off. The user may override it like any other page.

---

## 4. External interface

### 4.1 Bus events

Fire `ble_midi_event` on every transition. One event type per transition, never batched:

```yaml
type: page_entered | page_exited | focus_set | focus_cleared
    | pad_pressed | pad_held | knob_turned | tagged
device_id, address          # device_id is required by HA's own guidance on
                            # integration events, and is what lets the automation
                            # editor offer these against the device you are looking at
page_id, page_title, page_source, area_id, parent_page_id, depth
entity_id                 # focus target or toggled entity
pad, note, velocity       # pad events only
knob, delta, value        # knob events only
trigger: pad | service | idle | automation
previous: {page_id, area_id, entity_id}
```

`page_source` is included so external displays know whether they're rendering an area, a label, or something else.

### 4.2 State entities

Rewritten 2026-09-11. The rule that emerged, and that the rest of this section follows from:

> **A pad is a position; a page is a thing.** What sits on pad five is resolved from the live area registry, so it moves the day somebody adds a bulb to that room — no navigation, no reconfiguration, no notice. A page, a focus and a home button are facts about the profile and keep their meaning. So position may be named by position, and a target may never be.

That is why there is no `button.<name>_slot_5`. A dashboard button labelled "Kitchen lamp" that quietly starts closing a blind is the worst class of bug: plausible, silent, and it does something, just not the thing anybody believes it does. No naming scheme fixes it, because the author's intent and the slot's contents move along two independent axes.

What exists:

| Entity | What it is for |
|---|---|
| `select.<name>` — the page, settable, named after the device itself | The device's main feature and the one piece of state anybody else has reason to read *or write*. Setting has to be a first-class verb: a presence sensor pre-selecting a room is navigation that nobody pressed. Depth, area, source and parent are **unrecorded attributes** on it rather than sensors of their own — nobody automates on stack depth, and three more things to scroll past in the entity picker is a real cost |
| `sensor.<name>_focus` | Moves independently of the page, so it is a second fact, not an attribute of the first. This is also the only thing that can ever say what the eight unlabelled knobs are currently adjusting |
| `binary_sensor.<name>_connected` | Always available, because it is the entity that reports its own outage |
| `button.<name>_home`, `button.<name>_back` | The two gestures a page may never rebind (§3.4), which is exactly what makes them nameable — the line between a button entity that earns its place and sixteen that do not |
| `event.<name>_pad_1…16`, `event.<name>_<transport button>` | **Enabled.** The pad is the product: "pad 5 was pressed, whatever it means today" is the standard remote-control automation and is legitimately about position |
| `event.<name>_knob_1…8` | **Disabled by default.** One turn is over a thousand MIDI messages, and the engine consumes every one of them already. Core is split on this and the line it splits along is *does the device have an engine of its own* — Hue, Shelly and Z-Wave JS enable theirs and have none; Bang & Olufsen disables ~90 per remote and ESPHome's Voice PE withholds the press "used to control the device itself". This is the second kind |

Pads and buttons report `press_start`, `press_end`, `long_press_start` and `long_press_end`, on Home Assistant's own standard strings, at the same threshold the engine uses for a hold — one constant, or an automation watching the entity and a page reacting to the gesture would disagree about what just happened.

**Not an entity: the grid.** Sixteen live slots in one entity's attributes would be a database row for every light toggled anywhere on the visible page, forever. Home Assistant has had this argument three times — weather forecasts, calendar events, to-do items — and settled it the same way each time: the entity stays small, and the bulk rides an action. Hence `get_pages` below.

### 4.3 Device triggers

**Not built, deliberately.** Home Assistant stopped accepting new device automations in October 2025 ("Existing device automations will continue to work but new device automations won't be accepted"). Event entities cover the same ground since 2026.4, which is when `event.received` gained the ability to trigger on a specific event type aimed at a specific entity — before that release, one event entity per pad would not have been enough on its own.

A `logbook.py` describes every bus event as a sentence instead, because one event type carrying a `type` field is right for automations and unreadable in a timeline.

### 4.4 Services

```
mvave.navigate(device, page)                  # page id, not title: titles follow a rename
mvave.focus(device, entity_id)
mvave.home(device)
mvave.press_slot(device, slot: 1-16, action: tap | hold)
mvave.get_pages(device, page?) -> response    # what every pad means, right now
mvave.send_raw(device, data)                  # escape hatch
```

`press_slot` is named in hardware language on purpose. It presses **whatever is at that position**, and is for driving the surface when the pad is out of reach or out of battery; anything that wants one particular lamp should call that lamp's own action. Anything reaching the surface from outside is recorded with `trigger: service`, so an automation can never mistake its own effect for a person.

`get_pages` is pull-only (`SupportsResponse.ONLY`), and it is the answer to the problem this device creates by design: nothing is written on it, and a room page fills itself from the live registry, so **only the running integration can say what a pad would do** — not the configuration, and not anybody who was not there when it was set up. It returns the resolved answer, in the same vocabulary the LEDs use, so a card is a dumb renderer rather than a second implementation of the colour grammar. It also answers the one question the grid physically cannot: an unreachable pad and a pad that is off are the same white.

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
| Page: entity on | **its own colour**, defaulting by domain: orange for lights and switches, blue for a media player, green for a cover, red for a thermostat or a lock, purple for a scene or a script. A script is **drawn stateless**: its pad keeps its colour and never shows white, because purple running against white idle is the pair measured as too close to tell apart, and what showing it bought was a sub-second flash in the colour nobody can read. Overridable per pad | Colour says what it is. It appears only while the thing is on, which is what lets a pad carry identity without state losing its channel |
| Page: entity off | **white**, always, never configurable | The whole readability of a page rests on this. "Is anything on in here" becomes "is that pad white", which is one glance and one rule. It is also why purple is only ever a default for something stateless: purple against white is the one pair reported as too close, and a lamp coloured purple would be unreadable exactly when it mattered |
| Page: nothing assigned | **dark** | pressing it does nothing, and it must not look like an entity that is off. This is why "off" cannot also be dark |
| Page: entity nobody can reach | **white**, like one that is off, and it **shudders when pressed**: three quick blinks to dark and back, 540 ms, starting dark and ending lit | A colour reserved for this would cost a fifth of the entire vocabulary, permanently, for a condition that is rare and usually temporary, and it would still only tell somebody something they can act on at the moment they try. A refusal under the finger says it exactly then and says nothing the rest of the time. It cannot add to the density problem either: only the pad being pressed can refuse, and it is over before anybody looks away |
| Page: a scene or button just pressed | **holds the action colour, orange, for 0.8 s**, then lets go | A stateless pad has no on and no off, so pressing one changed nothing anywhere and was the only press on this surface with no result of any kind. It is a **latch, not a flash**: one transition in and one out, 0.8 s apart, so it never enters the flash arithmetic rather than merely passing it, and it adds no rhythm that would have to be told apart from the two that already move. It never shows white, which matters here more than anywhere — purple is the stateless default *because* those pads never go white, that being the one pair reported as too close. Drawn over the settled frame rather than animated, so the other fifteen pads keep reporting while it is held: a scene that switches three lamps on should be watchable doing it. Duration judged at the grid over three rounds, 1.5 s then 1.0 s then 0.8 s |
| Focused pad, the knob target | **breathing between on and off**: 1.4 s period, its own colour for about two thirds of it | slow and lopsided, so it cannot be mistaken for the alarm below. Confirmed legible in a full page without pulling the eye |
| Waiting, commanded but not confirmed | **swinging between on and off**, 2.5 Hz — 0.4 s, half lit | reads as "something is wrong or pending", which is exactly the meaning. It is the same rhythm the whole industry uses and Home Assistant's own interface pulses at 1 Hz for `locking` |
| `back` available | **left button lit** | see §1 |
| `home` available | **stop button lit** | |

**Blink is scarce and must not be spent twice.** It is the only channel left after colour and position, it is the documented accessibility fallback, and a grid with several things blinking at once is the documented failure mode. One meaning only: not confirmed yet.

**Nothing ever blinks to darkness.** Both rhythms alternate the two state colours, orange and white. A pad blinking to black reads as a light going out, which is a lie about a lamp that is on and staying on, and it was the first thing anybody complained about when it was tried on the hardware. Novation reached the same rule independently: their flash alternates two colours and only their slow pulse goes dark. The two rhythms therefore differ in rate alone, by a factor of three and a half, which was enough.

**Only a pad in one of the two states may move.** Motion means "between on and off", so a pad that is in neither has nothing to be between. An unreachable pad is therefore completely still, and it is also completely inert: pressing or holding it does nothing. Commanding something that cannot answer would leave the pad moving forever, waiting for a confirmation that never comes. Pads that rest at `unknown` are exempt from the inertness — scenes, buttons and input buttons — because resting at `unknown` is not the same as being unreachable, and they are exactly the pads people press. **Scripts are not among them, though they are drawn stateless.** A script rests at `off`, so a script reporting `unavailable` really is unreachable and refuses like anything else. How a pad is *drawn* and whether it can be *reached* are two questions that shared one answer until 2026-09-13, and stopped when a script stopped showing its running state.

**A pad only goes solid once the entity's real state arrives.** The surface is deliberately not optimistic: it never claims a lamp changed because somebody asked. The cost is that a slow cloud-connected device will swing for as long as it takes to answer, and the timeout for it is the coordinator's job rather than the engine's, since the engine has no clock: `runner.CONFIRM_SECONDS`, six seconds, after which it gives up and the pad goes solid.

**Known collision, accepted.** A page's curtain (§5.3) is its identity colour, so it can match the colour of some of its own switched-on entities and the curtain's edge is then invisible on those pads for the length of the transition. It is transient and the alternative is spending colours the grid does not have.

**The colour budget, which is the whole constraint.** Five colours plus dark and that is all. White is spent on "off" and is not configurable. Dark is spent on "nothing here". The remaining four carry both page identity and entity identity, which they can do because a page's colour appears only on an index and as a curtain, and an entity's only inside a page. Nothing is left over for a fault, which is why a fault is a reaction rather than a colour.

### 5.3 Transitions

Sixty full-grid frames a second are available, so the budget is generous.

**One pad lights at a time, never more.** That is the single rule the shapes below exist to satisfy, and it was arrived at the hard way: rings and columns were built first, and both felt uneven no matter how evenly they were timed. They cannot help it. A ring around a corner pad is one pad wide and the next is three, then five, then seven, so the amount of light arriving changes at every step. Measuring the frames on the wire proved the timing was even to within a few milliseconds while it still read as a limp. A single pad per step cannot have that problem, and it removed a second one for free: entering from a middle pad used to take fewer steps than from a corner, so the same gesture had two different durations.

**45 ms a pad**, so a page change is 1.575 s: sixteen pads covered, three frames of the curtain held, sixteen uncovered. It read as under a second and a half here and in `frames.py` until 2026-09-13, both written before `CURTAIN_HOLD` was added. Below about 40 the travelling edge stops reading as an edge and becomes a blur, which is the floor worth going to. A ring or a column at a time needed 350 ms a step to read at all; a pad at a time reads comfortably at a fraction of that, because there is no longer a jump to take in.

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
| navigate by service, automation or presence | **the same as a press**, growing from the pad the destination occupies on the screen being left. That is not an invented origin: it is where the page lives, and the pad a finger would have used. What caused the move is carried by the event's `trigger` instead, which is where an automation needs it and where the grid cannot say it. A page that is not on the screen at all has nowhere honest to grow from and gets a plain sideways wipe |
| idle timeout to home | a sideways wipe only, no spiral, and no button flash. Nothing happened, so it should not look like it did |
| focus change | no grid animation, only the focused pad starting to breathe |

**Why a spiral one way and columns the other.** The spiral says where the finger was. Columns say here is a page, and left to right is how a grid is read.

### 5.4 Rules

- Animations are frame generators in the engine; the coordinator plays them on a fixed tick and diffs against the last frame, exactly like static frames.
- **Any pad press aborts the running animation** and jumps to the resolved state. Input is never queued behind eye candy.
- Anything that is not a grid frame, the transport buttons above all, must be schedulable **against a specific frame** of an animation rather than firing at its start or its end.
- Primitives: `sweep(order, before, after)` and the two orders it is given, a clockwise spiral from a pad and a column sweep sideways. Every transition is one of those; the shapes are the design and the mechanism underneath has nothing in it. Plus `breathe(pad)` and `blink(pad)`. No fade and no partial intensity: there is no intensity.
- ~~Global setting `animations: full | minimal | off`~~ — **never built.** The options screen has only colours on it. A single switch that turned all motion off is still worth having, for a different reason than this one: see `docs/NEXT.md`.

---

## 6. Knobs

**Encoders are relative and have no rings.** All feedback is on the grid. Knobs never change *meaning*, only *target*.

Global knob assignment (fixed, muscle memory lives here). The encoders are two across and four up, **numbered from the bottom left** — the same convention the pads use, where PAD1 is bottom left:

| | |
|---|---|
| **7** brightness | **8** color temp |
| **5** hue | **6** saturation |
| **3** volume | **4** cover position |
| **1** climate setpoint | **2** free / per-page |

Rewritten 2026-09-12. The list is a **ranking** — brightness first because it is what people want from a lamp nine times in ten — and it is handed out **down the reading order of the block**, not up the device's numbering. Assigning it up the wiring put the most wanted property on the least obvious encoder, and nobody could see that until the map below was on the grid and the owner said the shape looked wrong. The assignment is derived from the geometry in code, so the two cannot drift apart.

If the focus lacks a property, that knob is inert. Per-page `knobs` config overrides the target for specific knobs (e.g. volume always hits the room's media player regardless of focus) — this is the one place per-page config beats the global rule.

### 6.0 The knob map

**Turning an encoder that does nothing draws the eight encoders on the grid**, in the arrangement above: live ones in the colour of whatever is focused, dead ones white, the rest of the grid dark. It holds as long as a value bar does and snaps back the same way.

This is the answer to "which knobs are live", which was open from the first day and which the hardware cannot answer for itself: eight identical encoders, no rings, no markings. The fixed assignment means it only has to be learned once, which is a fine answer on the thousandth day and no answer at all on the first — and on a fan, where only the free encoder does anything, seven of the eight are dead with no way to tell.

**It is deliberately not a refusal**, and that is the one place this surface departs from "anything that does nothing shudders" (§5.2). A pad shudders under the finger that pressed it and the other fifteen keep reporting, so the signal has a referent and costs almost nothing. An encoder has no pad, so the only surface available is the whole grid — and a whole grid blinking dark is not a louder version of that signal but a different one. It already means "nothing is driving this device"; it is what the photosensitivity thresholds (WCAG 2.3.1, Section 508 §408.3) are written about, three blinks being three flashes in one second across the entire surface; and it was read on the hardware exactly as it reads everywhere else, as the thing failing. "Not that one" is also the wrong answer to somebody who is searching. The map names the ones that work, once, instead of saying no seven times.

### 6.1 HUD

Since there is no persistent readout, the grid becomes a transient one.

- On the **first tick**, overlay a value bar on the whole grid. Keep it for as long as a finger is on a pad, and for roughly **a second** after the last tick or the last release, then **snap back** to the page. A bar that vanishes under a hand that is still holding the pad is the surface deciding somebody has finished looking. Nothing is animated in either direction: the bar appears every time anybody touches a knob, which is often enough that a transition stops being a flourish and becomes something to sit through. It was built with a column wipe on the way out first, and that is exactly how it felt.
- **16 pads = 16 steps, filling upwards from the bottom row.** About six percent a pad. Level rises, so the bar rises; cover position filling downwards (§6.3) is then a deliberate exception rather than an arbitrary one.
- **No sub-step resolution.** The original design dimmed the last lit pad proportionally, which needs a brightness this device does not have. Two substitutes were built and tried on the hardware and both were rejected: blinking the pad above the run read as a fault, because blink already means "not confirmed" (§5.2), and capping the run with a second colour read as a pad that did not belong to the bar. Sixteen steps is finer than a dimmer needs.
- The HUD is **per-knob, not per-entity** — the knob you touched decides which property is shown. If two knobs are turned together, show the most recent.
- The bar is a single contiguous run in one colour growing from one edge, which is a shape a page never produces, so it is recognisable as "not a page" before its colour is even read.

### 6.2 Peek

`hold` on a pad shows that entity's primary value bar **immediately, before anything changes** — brightness for a light, volume for a player, position for a cover. Turning a knob while holding adjusts that entity. On release the HUD snaps away — nothing on this device fades — and the entity stays **sticky focus**, so a later bare knob turn still targets it. This replaces what encoder rings would have provided.

### 6.3 Color language per property

Constant across every page. **One flat colour per property, not a gradient**: every ramp in the original design needed many graded steps along one hue, and the palette has neither brightness nor controllable saturation. What survives is one fixed colour naming which property you are holding, and the length of the bar carrying the value.

| Property | Colour | Note |
|---|---|---|
| brightness | orange | and so is every other "main value": volume, blind position, setpoint, fan speed. **Orange is the level** |
| colour temp | blue | |
| hue | green | |
| saturation | red-pink | |
| volume | orange | ~~pads above a configurable "loud" threshold switch to red-pink~~ — **never built.** `palette.BAR_ALERT` exists, is exported, and is read by nothing; no threshold is configurable anywhere |
| cover position | orange | fills upward like every other bar. ~~Top-down, because it is a blind~~ was specified and **never built**: `value_bar` takes no direction |
| climate | orange | see §6.4 |

Rewritten 2026-09-12. **One table serves the bar and the knob map (§6.0) alike**, which is the point: a colour on the map is a promise about the bar you get if you turn that encoder, so the colour becomes the property's *name* rather than a decoration, and the map teaches the bar. The previous set could not do that job. Brightness drew a *white* bar, and white already means "this encoder does nothing" on the map, so the commonest control and the absence of a control would have been the same colour. Hue and saturation were both green, which never mattered while only one bar showed at a time and matters completely once all four are on the grid together. Purple is deliberately unused: it is the one colour reported as too close to white on the physical grid, and a map is mostly white.

**Hue is cut entirely.** It wanted the grid to show the actual colour being chosen, sweeping across all 16 pads. Usable hue repeats every 13 or 14 palette steps with only five unambiguous entries, so 16 pads would show two or three repeats of a handful of colours, reading as "these pads are grouped" rather than as a continuous dial. The one property where seeing the result was the whole point is the one the palette cannot show. Hue gets an ordinary bar or a pad-per-preset instead.

**There is no red at any index.** Everywhere the original design says red, it means red-pink, which is as close as the palette gets.

### 6.4 Climate

**Never built.** A thermostat gets the same orange bar every other property gets, and
`PROPERTY_COLOURS["temperature"]` is flat orange. What follows is the specification, kept
because the reasoning still holds if anybody builds it.

Two numbers, so: **fill = setpoint**, one contrasting pad = **current temperature**, making the gap visible. Fill colour by direction relative to current: blue when asking for cooling, red-pink for heating, white within ±0.3 °C. The current-temperature marker is a **white pad on the same 16-step scale**, so the distance still to travel is the gap between the top of the fill and the marker. Mode (cool/heat/auto) is **not** a knob — assign it to a pad.

### 6.5 Behavior

- Update the HUD on **every** tick (optimistic), and **throttle** the service call rather than debounce it: `KNOB_THROTTLE_SECONDS = 0.15` sends about seven a second *while* the knob turns, and `KNOB_SETTLE_SECONDS = 0.25` sends a last one once it stops. Debouncing was the plan and would have meant the lamp not moving until you let go, which is what Home Assistant's own slider does and what this is better than.
- Min/max reached: ~~one quick full-bar flash~~ — **cut 2026-09-12, nothing replaces it.** A full-bar flash is a whole-surface luminance change, and it would fire again for every click somebody kept turning at the limit. Those two properties together are what made the knob refusal read as the device failing (§6.0), and they are what the photosensitivity thresholds are written about. The signal is also already there and free: a bar at either end is sixteen pads lit or sixteen dark, which is as unambiguous as this grid gets.
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
