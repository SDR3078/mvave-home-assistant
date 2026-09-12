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

#: Which areas become pages, in the order they will appear on the index.
CONF_PAGES = "pages"
#: An area's identity colour, keyed by its id.
CONF_PAGE_COLOURS = "page_colours"
#: What a kind of thing looks like when it is on, keyed by domain.
CONF_DOMAIN_COLOURS = "domain_colours"
