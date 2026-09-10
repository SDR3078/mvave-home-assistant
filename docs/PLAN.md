# Implementation plan

Supersedes `ble-midi-integration-brief.md` wherever the two disagree. The brief was
written before Home Assistant 2026.9 and before the device was measured; this file is
what three review passes and the measurements actually support. Written 2026-09-10.

Every claim marked **[v]** was verified against the installed Home Assistant 2026.9.1,
`bleak_retry_connector` 4.7.0 or `habluetooth`, by reading the source or running a probe.
Anything not marked is a recommendation, not a measurement.

---

## 1. Development environment

**Run Home Assistant in the devcontainer.** This reverses the first version of this
plan, which recommended a virtual environment on the devbox. Recording why, because the
reasoning is the useful part.

The original argument was that the container's only real advantage, isolation from LAN
auto-discovery, is achieved more cheaply by switching discovery off in the
configuration. That part held: with `default_config:` removed, the instance loaded
neither zeroconf, SSDP, DHCP, USB nor Bluetooth, and it did not appear in an mDNS sweep
of the network **[v]**.

What the argument missed is system dependencies. Home Assistant sets up all 45 base
entity platforms regardless of the configuration file **[v]**, and several want system
libraries: a compiler for the voice pipeline, a JPEG library for cameras, `ffmpeg`. On
a bare devbox the voice packages fail to build, and then the web interface hangs on
load, because rendering it asks for the service descriptions, which imports every
loaded integration's module. The devcontainer's own definition installs exactly those
libraries, and its base image already ships a compiler **[v]**. The container was never
about isolation here; it was about the platform's assumed environment.

The virtual environments in the workspace are the one real cost, since their
interpreters point at host paths that do not exist inside the container. The
devcontainer definition pins the interpreter and hides them from the file watcher so
the editor cannot pick a broken one.

**The template's development script puts the wrong directory on the import path.** **[v]**
It exports `PYTHONPATH=$PWD/custom_components`, but `custom_components` has no
`__init__.py`, so Python looks for a folder *containing* one of that name. With the
template's value the import raises `ModuleNotFoundError`; Home Assistant suppresses that
and reports zero custom integrations. Introduced in April 2023 when the template moved
its configuration into a subfolder.

Do not fix the variable. Do what the rest of the ecosystem does and make the folder
appear where Home Assistant already looks:

```
ln -s ../custom_components config/custom_components
```

Verified to resolve `custom_components.ble_midi` with no environment variable at all
**[v]**. A devcontainer does the same thing with a bind mount, so this survives a move.

**Start with package installation disabled, after one seeding run.** Home Assistant
installs each integration's declared dependencies into the running interpreter at
startup. That interpreter is shared with the hardware tools in `scripts/`, so left alone
it rewrites the Bluetooth stack a measurement was taken with. The drift measured on the
host before the move **[v]**:

| Package | HA 2026.9.1 pins | Installed |
|---|---|---|
| habluetooth | 6.26.11 | 7.0.0 |
| aioesphomeapi | 46.2.0 | 46.3.0 |
| bleak-esphome | 4.0.0 | 4.1.0 |
| home-assistant-frontend | 20260826.6 | not installed |

Let Home Assistant win: one stack, not two. Seed once with installation enabled to pull
the frontend and settle versions, freeze a lock file, then run with `--skip-pip`
permanently and re-run the recording script once to confirm the older libraries still
drive the proxy.

**Development configuration** replaces `default_config:`, which pulls in 21 components
**[v]** including cloud, video streaming, voice pipelines and the three LAN discovery
ones. Restart is the inner loop, because Home Assistant does not reload Python for
custom integrations, so startup cost is paid on every iteration.

```yaml
homeassistant:
  name: DEV mvave

frontend:
config:

logger:
  default: warning
  logs:
    custom_components.mvave: debug
    homeassistant.components.bluetooth: debug
    habluetooth: debug
    bleak_esphome: debug
```

No `recorder`, so no database; Developer Tools shows live state and events. No
`bluetooth` key; it loads as a dependency when the ESPHome proxy entry is added. Note
that this trims what the configuration asks for, not what Home Assistant loads anyway:
the base platforms come regardless, which is why the container's system libraries
matter.

**Restart loop.** Home Assistant exits with code 100 to request a restart **[v]**, so
wrap the launch in a loop that relaunches on that code and you can restart from the
browser without leaving it. Run it under `tmux` so an SSH drop does not kill a live
radio link.

**Guardrails.** Never run bare `hass`; without `-c` it writes to `~/.homeassistant`,
outside the repo. Exclude `.venv314` from ruff (`extend-exclude`), which is why the
lint currently reports 9 errors **[v]**. Do not copy the template's `.ruff.toml`: a
`.ruff.toml` overrides `[tool.ruff]` in `pyproject.toml` entirely **[v]**, and the
template's enables every rule ruff has.

---

## 2. Bluetooth transport

This section is the one that matters most, because two of its findings are defects that
would have cost hours on hardware.

### 2.1 Reconnect would never fire **[v]**

Home Assistant's Bluetooth manager discards an advertisement byte-identical to the
previous one from that address, returning before dispatching to integrations. Our pad's
advertisement never varies: fixed name, fixed service list, fixed manufacturer data.

There is an escape hatch for a connectable device missing from connectable history, but
that history only clears when the address expires, and the scanner refreshes the expiry
timestamp on every advertisement **two lines before** the manager discards it as a
duplicate. So the entry never expires, the hatch never opens, and a callback registered
on that address never runs again after the first one.

**Fix:** call `bluetooth.async_clear_advertisement_history(hass, address)` in the
disconnect handler, before waiting for the device to reappear. Home Assistant exports it
**[v]** and documents this exact case.

### 2.2 The connected sensor must not come from platform availability **[v]**

We measured that the pad stops advertising while a central holds it. A remote scanner
expires an address after 195 seconds and the unavailable sweep runs every 300 seconds
**[v]**, with no exemption for an address you hold a connection to. So
`async_track_unavailable` fires several minutes into every working session.

**Fix:** drive `binary_sensor.<name>_connected` from the client's own state and the
disconnect callback. Guard the unavailable handler with an early return while connected,
as `specialized_turbo` does.

### 2.3 Close stale connections at setup **[v]**

One central at a time, and the pad goes silent while another client holds it. A reload
while connected leaves it held and setup fails. `yalexs_ble` calls
`close_stale_connections_by_address(address)` first thing, with a comment describing
exactly this. Do the same.

### 2.4 Corrections to the brief's connection code

- Pass plain `BleakClient`, not `BleakClientWithServiceCache`. The latter's own docstring
  says it is backwards-compatibility only since bleak 0.17 **[v]**, and no core
  integration references it.
- `establish_connection` remains mandatory; `habluetooth` now logs a warning if you
  connect without it. It takes no `timeout` argument, and `ble_device_callback` is
  declared but never read.
- Re-resolve the `BLEDevice` on every attempt. The ESPHome backend reads the address type
  out of its details, and the winning proxy can change.
- Use `BluetoothScanningMode.PASSIVE` for the reconnect watcher. A non-passive mode with
  an address matcher opts that address into active scanning on a shared proxy.
- Chunk writes to `client.mtu_size - 3` computed at write time. The proxy backend falls
  back to a 23-byte MTU if it has not landed yet, which would truncate a long SysEx.

### 2.5 Structure

Model on `specialized_turbo`, the one core integration that calls `establish_connection`
in-tree and holds a notify-driven link. Borrow stale-connection closing and stop-event
teardown from `ld2410_ble`, and the advertisement wiring from `yalexs_ble`.

The persistent connection permanently consumes one of the proxy's three connection
slots, and the proxy stops scanning entirely while a connect is in flight. Back off hard
on failure; a reconnect storm blinds every other Bluetooth integration on that proxy.

---

## 3. Integration architecture

**A control is either a trigger or a control, and the two need opposite treatment.**
Learned the hard way on hardware **[v]**: one turn of an encoder produced 1040 decoded
messages, because the device sends one per unit of travel. Firing an event entity for
each puts a thousand state changes through Home Assistant and triggers every attached
automation a thousand times.

- **As a trigger** (skip a track, run a scene), a turn is discrete. Accumulate the steps
  until the knob has been still for about two tenths of a second, then report one event
  carrying the total, flushing early if the direction or bank changes. Measured after
  the change: 126 messages became 2 events. The delay lands only at the end of the
  gesture, where nobody notices it.
- **As a control** (a lamp following your hand), a turn is continuous, and coalescing is
  exactly wrong: the value has to move while the knob is still moving. That path belongs
  to the profile engine, which subscribes to the same decoded stream and calls the target
  service directly, without an event entity in between. It throttles on a different
  principle: send the newest value at most every N milliseconds rather than wait for
  quiet. Twenty or thirty updates a second is already past what a lamp can follow, since
  most transition in about a tenth of a second, and a wireless bulb drops commands well
  before that.

Never route continuous control through the event entities, and never send a service call
per MIDI message.

**No polling coordinator.** The device has nothing to poll. The closest precedent, a
Bluetooth lock holding its connection open, uses no coordinator at all: a plain dataclass
in `entry.runtime_data`, with entities subscribing to callbacks in `async_added_to_hass`.
Keep a `coordinator.py` module for the connection lifecycle and fan-out, but it is not a
`DataUpdateCoordinator` that fetches.

**One event entity per control** **[v]**. The brief plans a single entity carrying the pad
number as an attribute. Home Assistant's event trigger filters on event type only, aimed
at an entity, so with a shared entity "pad 5 pressed" is not expressible. Ship 16 pad
entities and 8 encoder entities, as Philips Hue does for its remotes.

Use `EventDeviceClass.BUTTON` and the standard vocabulary added in 2026.9 **[v]**:
`press_start`, `press_end`, `long_press_start`, `long_press_end`, `multi_press_end`.
Note that triggering an event type not in the entity's declared list raises **[v]**, so a
pad in an unexpected bank must not reach it unguarded.

**Drop two planned features.** Device triggers are legacy; one integration in core now
raises a repair issue telling users to use the event entity instead. Config subentries
are stable but no device or Bluetooth integration uses them; per-pad overrides belong in
the options flow.

**Identity.** `unique_id = format_mac(address)`; device `identifiers` on the same value
plus `connections={(dr.CONNECTION_BLUETOOTH, address)}` so the device merges with the
proxy's view of it. Entity unique ids hang off it and must never encode the profile,
page or a pad's current role.

**Other corrections:** selected area and target entity should be `select` entities rather
than sensors, so they are settable as well as readable; the connected sensor needs the
connectivity device class and the diagnostic category, and is the one entity that stays
available while the link is down; every service call the engine makes should carry a
per-gesture `Context`, or the logbook cannot attribute a light change to a pad press.

---

## 4. Packaging

**Manifest.** The brief's is missing `documentation`, `codeowners` and `issue_tracker`,
all effectively required, and its keys are in the wrong order; hassfest enforces domain,
name, then alphabetical. Drop `requirements`: Home Assistant already pins the retry
connector **[v]** and no core Bluetooth integration re-declares it. `config_flow: true`
requires `config_flow.py` to exist. Registering services requires `services.yaml`.

**`hacs.json`** needs only a name; add the minimum Home Assistant version, the minimum
HACS version, and hide the default branch once releases exist. Skip zip releases: the
repo is 444 KB and they add three failure modes.

**Layout.** The extra top-level directories are harmless. hassfest looks only under
`custom_components/*/manifest.json`, and HACS extracts only `custom_components/ble_midi/`.
Ship brand images at `custom_components/ble_midi/brand/icon.png` so the validation
ignore can be dropped.

**The extraction seam.** Make `transport/__init__.py` the public facade and have tools and
tests import the package rather than the module. When the parser moves to PyPI, only that
one file and the manifest change.

**CI.** Four workflows: hassfest and HACS validation; ruff and mypy; tests in two jobs,
the pure ones across Python 3.11 to 3.14 and the Home Assistant ones pinned to 3.14; and
a release check that the manifest version equals the git tag, because HACS reads the
version from the tag while Home Assistant reads it from the manifest.

**Licence: MIT, with a `NOTICE` file.** Three attributions. The template we copy scripts
from is MIT and its notice condition binds. The RGB command layout came from an MIT
project, already credited in the module docstring. The Blackbox protocol notes are
CC BY-SA, which covers their prose and not the facts, and our hardware document is
written as original measurement, so the share-alike term does not attach; keep it that
way by not copying their tables. The RP-052 test data set has no licence at all, which is
why it stays fetched on demand and git-ignored.

---

## 5. Testing

**The helper the brief names does not exist** **[v]**. `inject_bluetooth_service_info` is
not in `pytest-homeassistant-custom-component`; it lives in Home Assistant's own test
suite, which is not shipped. Vendor the handful of functions into
`tests/bluetooth_helpers.py` and re-sync them when Home Assistant is bumped.

**Two tiers.** The parser, device module and engine tests stay free of any
`homeassistant` import, run in milliseconds and are the regression suite for the future
library; they run on 3.11 through 3.14, and 3.11 works today **[v]**. Everything needing
`hass` uses the custom-component test harness, which blocks real sockets, so a unit test
can never accidentally grab the pad.

**Add a regression test for the reconnect defect**: inject the identical advertisement
twice with a disconnect between, and assert reconnect happens only when the
advertisement history was cleared.

**Hardware sessions** stay deliberate: disable the config entry to release the pad,
record, promote the capture into the fixtures, re-enable. Once the transport lands, the
services replace most of that from inside the process that already holds the link.

---

## 6. Open decisions

1. `tests/fixtures/smc_pad_presets.bin` is a dump of the owner's own presets. Three tests
   depend on it **[v]**, so dropping it means rewriting them. Keep or rewrite.
2. Development interface on the LAN, which allows a phone at the pad, or loopback only.
3. Whether the pad's service UUID is in the primary advertisement or only in the scan
   response. This decides whether passive scanning is enough for discovery.
