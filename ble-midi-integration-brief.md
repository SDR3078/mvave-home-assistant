# `ble_midi` — Home Assistant custom integration for BLE MIDI controllers

Handoff brief for the coding agent. Read fully before writing code. Sections marked **[verify]** are assumptions to confirm against current HA source/docs before relying on them.

## 1. Goal

A HACS-installable custom integration that turns any Bluetooth LE MIDI controller into a Home Assistant control surface. First target device: **M-VAVE SMC-PAD** (16 RGB velocity-sensitive pads, 8 relative rotary encoders, BLE + USB-C). Must work with any device that implements the standard BLE MIDI GATT profile.

Two layers, strictly separated:

1. **Transport** — connect over HA's Bluetooth stack, parse BLE MIDI, emit events, write MIDI back. Knows nothing about rooms, lights, or pages.
2. **Profile engine** — a declarative state machine (pages, selection, roles, LED feedback) driven by shipped profile files and HA registries. Knows nothing about Bluetooth.

Non-goals for v1: USB/ALSA transport, MQTT, SysEx editors, a custom frontend panel.

## 2. Architecture

```
BLE MIDI device
      │  GATT notify / write (via local adapter OR ESPHome Bluetooth proxy)
      ▼
homeassistant.components.bluetooth  (habluetooth + bleak)
      ▼
custom_components/ble_midi/
  ├── transport/      BleMidiClient: connect, notify, parse, write, reconnect
  ├── engine/         ProfileEngine: pages, selection state, roles, LED render
  ├── profiles/       shipped YAML grammar files (room_grid.yaml, ...)
  ├── config_flow.py  bluetooth discovery + user step + subentries
  ├── coordinator.py  glue: transport events -> engine -> HA actions / LED writes
  └── entities        binary_sensor (connected), sensor (per CC), select (page), event
```

Data flow: notify → `parse_ble_midi()` → `MidiEvent` → `ProfileEngine.handle()` → list of `HaAction` + `LedFrame` → coordinator executes service calls and writes LED bytes.

## 3. Transport spec

### 3.1 GATT

- Service UUID: `03b80e5a-ede8-4b33-a751-6ce34ec4c700`
- Characteristic UUID: `7772e5db-3868-4112-a1a9-f2669d106bf3` (notify + write-without-response)

### 3.2 HA Bluetooth integration points

`manifest.json`:

```json
{
  "domain": "ble_midi",
  "name": "BLE MIDI",
  "version": "0.1.0",
  "config_flow": true,
  "dependencies": ["bluetooth_adapters"],
  "bluetooth": [
    { "service_uuid": "03b80e5a-ede8-4b33-a751-6ce34ec4c700", "connectable": true }
  ],
  "requirements": ["bleak-retry-connector>=3.5.0"],
  "iot_class": "local_push",
  "integration_type": "device"
}
```

Connection:

```python
from homeassistant.components import bluetooth
from bleak_retry_connector import establish_connection, BleakClientWithServiceCache

ble_device = bluetooth.async_ble_device_from_address(hass, address, connectable=True)
client = await establish_connection(
    BleakClientWithServiceCache, ble_device, name,
    disconnected_callback=self._on_disconnect,
)
await client.start_notify(MIDI_CHAR, self._on_notify)
await client.read_gatt_char(MIDI_CHAR)   # some peripherals require a read before they start sending
```

Reconnect: register `bluetooth.async_register_callback(hass, cb, {"address": address}, BluetoothScanningMode.ACTIVE)` and reconnect when the device is seen again after a disconnect. Use `bluetooth.async_track_unavailable` to drive the `connected` binary sensor. Never busy-loop reconnects; back off.

Write: `await client.write_gatt_char(MIDI_CHAR, data, response=False)`.

**[verify]** exact `establish_connection` signature and whether `BleakClientWithServiceCache` is still the recommended class in the current `bleak-retry-connector`.

### 3.3 BLE MIDI framing (Apple BLE-MIDI spec, adopted by MMA)

Incoming packet (max = MTU):

```
[header][timestamp][midi msg bytes...]([timestamp][midi msg bytes...])*
```

- `header`   = `1 0 tttttt` — high bit set, bit 6 clear, low 6 bits = timestamp high
- `timestamp`= `1 ttttttt` — high bit set, low 7 bits = timestamp low (ms, 13-bit wrapping)
- Each MIDI message is preceded by its own timestamp byte; the first shares the header.
- Running status applies **within a packet** — a data byte after a status byte with no new status reuses the last status.
- SysEx may span packets (`0xF0 … 0xF7`); v1 may collect and discard, but must not corrupt parsing.
- Real-time messages (`0xF8–0xFF`) can appear anywhere; ignore them.

Outgoing: `bytes([0x80, 0x80]) + midi_message` is sufficient (timestamp 0 is acceptable to peripherals).

`parse_ble_midi(packet: bytes, state: ParserState) -> list[MidiEvent]` must be pure and fully unit-tested with recorded packets. `MidiEvent` fields: `type` (`note_on|note_off|cc|program_change|pitch_bend|aftertouch|poly_aftertouch`), `channel`, `data1`, `data2`, `timestamp`. Treat `note_on` with velocity 0 as `note_off`.

### 3.4 Device quirks (SMC-PAD) — to be discovered, not assumed

- Pad/Knob Bank buttons are local: they change which notes/CCs are sent, no bank-change message. Design the engine so banks can be either ignored or mapped to pages.
- Encoders are relative (likely 2's-complement or 1/127 style). Implement a small `RelativeEncoderDecoder` with acceleration (ticks/second → step multiplier).
- RGB pad colour is probably velocity-mapped on note-on, possibly SysEx. First task on hardware: capture what the M-VAVE editor sends, store findings in `profiles/devices/smc_pad.yaml` as a colour map.
- Unknown whether the pad emits on USB and BLE simultaneously. Not this integration's problem, but document it in README.

## 4. Profile engine spec

### 4.1 Principles

- Profiles describe **roles and sources**, never specific entities. A profile that names `light.kitchen` is a bug.
- Everything derivable from HA registries (areas, entities in an area, entity state/colour) is derived at runtime, not configured.
- User configuration is limited to: device, profile choice, area selection, optional per-pad overrides (subentries).
- Engine is pure Python with no HA imports in its core; HA access goes through a small `RegistryView` / `ActionSink` interface so it is unit-testable.

### 4.2 Profile grammar (v1)

```yaml
# custom_components/ble_midi/profiles/room_grid.yaml
name: Room grid
layout: { pads: 16, knobs: 8 }        # minimum device capabilities
pages:
  home:
    pads:
      "1-4":  { role: select_area,   source: configured_areas }
      "5-16": { role: select_entity, source: area_lights }     # of selected_area
    knobs:
      "1": { role: attribute, attr: brightness, target: selected_entity }
      "2": { role: attribute, attr: color_temp_kelvin, target: selected_entity }
      "8": { role: attribute, attr: volume_level, target: area_media_player }
    press:
      select_entity: { tap: toggle, hold: select }              # hold = make it the knob target
    leds:
      select_area:   { selected: [255,255,255], other: [20,20,20], empty: off }
      select_entity: { on: entity_rgb, off: [8,8,8], selected: pulse }
```

Roles for v1: `select_area`, `select_entity`, `toggle`, `scene`, `attribute`, `page`. Sources: `configured_areas`, `area_lights`, `area_switches`, `area_media_player`, `area_scenes`. Ranges like `"5-16"` expand in reading order left→right, top→bottom.

Gestures: `tap`, `hold` (default 400 ms), `double` (optional). Engine emits actions only; debouncing lives in the engine.

### 4.3 LED rendering

`render() -> LedFrame` = list of `(pad_index, rgb|off)`. Coordinator diffs against last frame and writes only changes. Colour → MIDI mapping comes from the device quirk file (`velocity` lookup or SysEx template). Re-render on: any engine state change, and on state changes of entities currently displayed (subscribe via `async_track_state_change_event`).

## 5. Config flow

- `async_step_bluetooth`: triggered by manifest UUID match. `unique_id = address`. Confirm form: name, profile (select from shipped profiles), areas (`AreaSelector(multiple=True)`, max per profile layout).
- `async_step_user`: list `bluetooth.async_discovered_service_info(hass)` filtered on service UUID for manual add.
- Options flow: change profile/areas.
- Subentries **[verify current API]**: `pad_override` subentry type — pad index, role, target via `EntitySelector`. Overrides win over profile-derived assignments.

## 6. Entities

Per device (one HA device per BLE address):

- `binary_sensor.<name>_connected`
- `select.<name>_page` (engine page; settable from HA for automations)
- `sensor.<name>_selected_area`, `sensor.<name>_selected_entity`
- `event.<name>_pad` with `event_types` `pressed|released|held`, attributes `pad`, `note`, `velocity`, `channel`
- `event.<name>_knob` with attributes `knob`, `cc`, `delta`
- Device triggers for `pad N pressed/held` so users can bypass the engine in automations.

Services: `ble_midi.send_raw` (bytes/hex), `ble_midi.set_pad_color` (pad, rgb) — escape hatches for anything the engine doesn't model.

## 7. Repo, dev environment, testing

### 7.1 Repo

Scaffold from `ludeeus/integration_blueprint`. Keep its `.devcontainer/`, `scripts/setup`, `scripts/develop`, `config/configuration.yaml` (debugpy + logger), `hacs.json`, hassfest and HACS validation workflows. Rename domain to `ble_midi`.

```
.
├── .devcontainer/
├── config/                     dev HA config
├── custom_components/ble_midi/
│   ├── transport/{client.py, parser.py, encoder.py}
│   ├── engine/{engine.py, profile.py, roles.py, render.py, gestures.py}
│   ├── profiles/{room_grid.yaml, devices/smc_pad.yaml}
│   ├── __init__.py, config_flow.py, coordinator.py, const.py, manifest.json
│   ├── binary_sensor.py, sensor.py, select.py, event.py, device_trigger.py, services.yaml
│   └── strings.json, translations/en.json
├── tests/
│   ├── fixtures/ble_midi_packets/   recorded notify payloads (hex, one per line)
│   ├── test_parser.py, test_engine.py, test_config_flow.py, test_coordinator.py
└── scripts/
```

### 7.2 Dev environment

- Devcontainer runs in Docker inside a Proxmox LXC (`features: nesting=1,keyctl=1`), opened via VS Code Remote-SSH → Reopen in Container. Port 8123 forwarded.
- **No local Bluetooth hardware.** Live testing uses an ESPHome Bluetooth proxy on the LAN, added to the dev HA by IP (mDNS does not cross the Docker bridge). The proxy must be dedicated to dev while the pad is in use — BLE MIDI peripherals accept one central.
- `scripts/develop` starts `hass` with `custom_components/` on the path. Use integration reload for most iterations.

### 7.3 Testing requirements

- `test_parser.py`: table-driven over recorded packets, including multi-message packets, running status, note_on vel 0, packet-spanning SysEx, real-time bytes interleaved.
- `test_engine.py`: pure-Python; fake `RegistryView` with two areas and a handful of lights; assert actions and LED frames for tap/hold/knob sequences; page switching; overrides.
- `test_config_flow.py`: use `pytest-homeassistant-custom-component` and `inject_bluetooth_service_info` to simulate the advertisement; assert discovery, unique_id, and the confirm form.
- `test_coordinator.py`: fake `BleakClient` that replays fixtures into the notify callback and records writes; assert LED diff writes and reconnect behaviour.
- CI: hassfest + HACS action + pytest.

## 8. Milestones

1. **Parser + fixtures** — pure, tested, no HA. Deliver `parser.py` and a `scripts/record_packets.py` (bleak-only) to capture packets from the pad on any laptop.
2. **Transport in HA** — discovery, connect, `connected` sensor, `event` entities firing, `send_raw` service. Validate via proxy.
3. **Device quirks** — SMC-PAD colour map and encoder decoding captured into `profiles/devices/smc_pad.yaml`.
4. **Engine** — `room_grid` profile end to end: area select, light toggle, brightness knob, LED feedback.
5. **Config polish** — options flow, subentry overrides, translations, README, HACS release.

## 9. Open questions to resolve on hardware (do not guess)

- Does the SMC-PAD require bonding/pairing, or connect open?
- Exact note/CC numbers per pad/knob and per bank.
- Encoder delta encoding and value range.
- Colour control mechanism (velocity map vs SysEx) and whether LEDs can be set while the pad's own feedback mode is active.
- Does the device stay connected/advertising while also on USB?

## 10. Conventions

- Python ≥ 3.13 (match current HA), `ruff` + `mypy --strict` on `transport/` and `engine/`.
- No blocking I/O in the event loop; all BLE calls awaited; parser and engine synchronous and pure.
- Log at `debug` for every BLE packet (hex) behind a `const.LOG_PACKETS` flag; never at `info`.
- Keep `transport/` and `engine/` free of `homeassistant.*` imports.
