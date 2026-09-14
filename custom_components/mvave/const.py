"""Constants for the BLE MIDI integration."""

from __future__ import annotations

from logging import Logger, getLogger
from typing import Final

DOMAIN: Final = "mvave"
LOGGER: Logger = getLogger(__package__)

# Apple's BLE-MIDI profile, adopted by the MIDI Manufacturers Association as RP-052.
MIDI_SERVICE_UUID: Final = "03b80e5a-ede8-4b33-a751-6ce34ec4c700"
MIDI_CHAR_UUID: Final = "7772e5db-3868-4112-a1a9-f2669d106bf3"

# The standard Bluetooth characteristics every device is supposed to carry, and this one
# actually does (``docs/HARDWARE-BLE.md`` section 2). None of them is on the advertisement:
# passive scanning never asks for the scan response where a friendly name would live, so
# until somebody connects, all Home Assistant has to call this device is a MAC address.
DEVICE_NAME_UUID: Final = "00002a00-0000-1000-8000-00805f9b34fb"
MANUFACTURER_UUID: Final = "00002a29-0000-1000-8000-00805f9b34fb"
MODEL_UUID: Final = "00002a24-0000-1000-8000-00805f9b34fb"
BATTERY_UUID: Final = "00002a19-0000-1000-8000-00805f9b34fb"

# What the pad calls itself in its Device Information service is "ble device", which names
# the chip's default rather than the product. The name it advertises under is the useful
# one, and it is the one printed on the box.
USELESS_MODEL_NAMES: Final = frozenset({"", "ble device", "unknown"})

CONF_ADDRESS: Final = "address"

#: One page. A subentry rather than an option, because a page is a thing somebody adds
#: rather than a setting somebody changes, and Home Assistant has a mechanism for exactly
#: that: its own row under the integration, with add, configure and delete.
#:
#: It is also what makes a page's identity its own. A subentry's id is a ULID, so a page
#: outlives the room it draws from being renamed, or deleted, or never having existed.
SUBENTRY_PAGE = "page"

#: What a page carries.
CONF_COLOUR = "colour"
#: The area a page fills itself from, if it fills itself from one.
CONF_AREA = "area"
#: The label a page fills itself from, which is how a page spans rooms.
CONF_LABEL = "label"
#: Entities pinned to particular pads, keyed by **position in reading order**: "1" is the
#: top left and "16" the bottom right. Deliberately not the number printed on the pad,
#: which is what the form's fields are labelled with and runs the other way up — keeping
#: the stored key off the label is what let the labels be corrected on 2026-09-13 without
#: migrating anybody's pages. The engine counts from zero, and that translation happens
#: once, here at the edge.
CONF_PADS = "pads"
#: Whether the page holds exactly its pins and nothing else. Set the moment somebody saves
#: a change on the pads screen: a page you have edited is yours, an empty field is a dark
#: pad, and nothing fills in behind it afterwards. Absent on a page nobody has edited, which
#: keeps following its room — and absent on every page made before 2026-09-14, so those
#: keep following too, pins and all, exactly as they did.
CONF_FIXED = "fixed"

#: Which areas become pages, in the order they will appear on the index. Superseded by
#: page subentries; kept so the one-time migration can still read what was there.
CONF_PAGES = "pages"
#: An area's identity colour, keyed by its id. Superseded in the same way.
CONF_PAGE_COLOURS = "page_colours"
#: What a kind of thing looks like when it is on, keyed by domain.
CONF_DOMAIN_COLOURS = "domain_colours"
