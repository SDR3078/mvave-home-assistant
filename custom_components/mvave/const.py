"""Constants for the BLE MIDI integration."""

from __future__ import annotations

from logging import Logger, getLogger
from typing import Final

DOMAIN: Final = "mvave"
LOGGER: Logger = getLogger(__package__)

# Apple's BLE-MIDI profile, adopted by the MIDI Manufacturers Association as RP-052.
MIDI_SERVICE_UUID: Final = "03b80e5a-ede8-4b33-a751-6ce34ec4c700"
MIDI_CHAR_UUID: Final = "7772e5db-3868-4112-a1a9-f2669d106bf3"

CONF_ADDRESS: Final = "address"

#: Which areas become pages, in the order they will appear on the index.
CONF_PAGES = "pages"
#: An area's identity colour, keyed by its id.
CONF_PAGE_COLOURS = "page_colours"
#: What a kind of thing looks like when it is on, keyed by domain.
CONF_DOMAIN_COLOURS = "domain_colours"
