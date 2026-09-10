# `ble_midi` — control surface design

Companion to `ble-midi-integration-brief.md`. That document covers transport, repo, and testing. This one specifies the **profile engine**: what pads and knobs mean, how navigation works, what events are emitted, and how feedback is rendered.

Hardware assumption: 16 pads (4×4, RGB, velocity-sensitive) + 8 relative rotary encoders. **The encoders have no LED rings.** The pad grid is the only visual output. Nothing here may assume ring feedback.

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

The engine reserves **exactly one pad**: `back` (default slot 16, configurable per page). Hold on it = `home`. All other 15 slots belong to the user or the auto-fill. No other slot is ever claimed by the engine.

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
- **Shift gesture**: hold the reserved `back` pad → row 1 temporarily becomes a switcher for top-level pages (the "tab bar"). Release without pressing = no-op.
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

### 5.1 Static rules

- **Color = identity** (which room / which page / which entity), **brightness = state** (on/off, active/inactive).
- Each page has one identity color, reused everywhere that page appears (its `navigate` pad on home, its ripple, its `back` collapse).
- Focused pad = slow pulse.
- Unassigned slots = off.

### 5.2 Transitions

Budget **~200 ms**. BLE MIDI sustains roughly 10–15 full-grid frames/second; anything longer reads as lag.

| transition | animation |
|---|---|
| enter page via pad | **ripple**: rings expand from the origin pad (Chebyshev distance 0→3), one ring per frame ~50 ms, in the destination page's color at 30–40% intensity, then resolve to real state |
| `back` via pad | **inverse ripple**: rings collapse toward the back pad, in the parent page's color |
| `home` via hold | collapse toward the reserved corner, white |
| navigate via service / automation / presence | **pulse**: one full-grid frame up, one down, ~120 ms — no ripple, because there is no origin pad and inventing one implies false causality |
| idle timeout to home | **fade**: slow, ~600 ms — nothing happened, it should not grab attention |
| focus change | no grid animation; only the focused pad changes to its pulse |

### 5.3 Rules

- Animations are frame generators in the engine; the coordinator plays them on a fixed tick and diffs against the last frame, exactly like static frames.
- **Any pad press aborts the running animation** and jumps to the resolved state. Input is never queued behind eye candy.
- Library is three primitives only: `ripple(origin, color, direction)`, `pulse(color)`, `fade(from, to, ms)`.
- Global setting `animations: full | minimal | off`; `minimal` = pulse only.

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

- On the **first tick**, overlay a value bar on the whole grid. Hold it while turning plus **~700 ms** after the last tick, then fade back to the page.
- 16 pads in reading order = 16 steps; the last lit pad dims proportionally for sub-step resolution.
- The HUD is **per-knob, not per-entity** — the knob you touched decides which property is shown. If two knobs are turned together, show the most recent.
- After a period of inactivity, the **first tick is a half step**, so "peek by nudging" is cheap.

### 6.2 Peek

`hold` on a pad shows that entity's primary value bar **immediately, before anything changes** — brightness for a light, volume for a player, position for a cover. Turning a knob while holding adjusts that entity. On release the HUD fades and the entity stays **sticky focus**, so a later bare knob turn still targets it. This replaces what encoder rings would have provided.

### 6.3 Color language per property

Constant across every page:

- brightness — warm white ramp
- color temp — amber → blue gradient
- hue — the actual color across the whole grid, no bar
- saturation — white → the current hue
- volume — green bar; top pad red above a configurable "loud" threshold
- cover position — bar fills **top-down** (it's a blind)
- climate — see below

### 6.4 Climate

Two numbers, so: **fill = setpoint**, one contrasting pad = **current temperature**, making the gap visible. Fill color by direction relative to current: blue when asking for cooling, red for heating, white within ±0.3 °C. Mode (cool/heat/auto) is **not** a knob — assign it to a pad.

### 6.5 Behavior

- Update the HUD on **every** tick (optimistic), but **debounce the service call ~250 ms** after the last tick. Turning a knob must never fire 40 `light.turn_on` calls.
- Min/max reached: one quick full-bar flash.
- If the target is off (light off, player muted), the first tick **turns it on at the lowest step** rather than adjusting an invisible value.
- The HUD interrupts page animations; any pad press cancels the HUD and executes immediately.

### 6.6 Optional persistent meters

Per-page opt-in: `meters: {row: 4, knobs: [5, 7]}` reserves a row as mini bars (two 2-pad or four 1-pad). Low resolution, but "is it loud" and "is the AC heating or cooling" read fine from brightness and color. **Off by default** — it costs pads.

---

## 7. Configuration surface

Config subentries, one per page, using HA selectors (`AreaSelector`, `EntitySelector`, `LabelSelector`, `ColorRGBSelector`). Shape:

```yaml
page: living
  title: Living room
  color: [255, 120, 0]
  source: area
  area: living
  parent: home
  back_pad: 16
  idle_timeout: 60
  pads:
    3:  {tap: {scene: scene.living_evening}, hold: {navigate: tv}}
    12: {tap: {event_only: coffee}}
  knobs:
    5: {target: media_player.living_tv}
```

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
- HUD: first-tick half step, debounce window, min/max flash, off-target first tick, pad press cancels HUD.
- Animations: frame counts and abort-on-input, `minimal` and `off` modes.
- Events: exactly one event per transition, correct `previous` payload, `trigger` field accuracy for pad vs service vs idle.
