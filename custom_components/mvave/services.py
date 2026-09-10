"""Services for the M-Vave integration.

Escape hatches: anything the entities and the profile engine do not model can be done by
sending MIDI to the device directly.

Registered from ``async_setup`` rather than ``async_setup_entry``, so the services exist
and give a useful error even when no device is set up, rather than appearing and
disappearing with the entry.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import voluptuous as vol
from bleak import BleakError
from homeassistant.const import ATTR_DEVICE_ID
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import service

from .const import DOMAIN, LOGGER

if TYPE_CHECKING:
    from . import MvaveConfigEntry
    from .coordinator import MvaveCoordinator

SERVICE_SEND_RAW = "send_raw"
ATTR_DATA = "data"

# The device arrives through the call's target, which Home Assistant merges into the
# data. It can be absent, null, a single id or a list of them depending on how the call
# was made, so it is validated here rather than by the schema, which can only complain
# about shapes and not explain what to do about it.
SEND_RAW_SCHEMA = vol.Schema(
    {
        vol.Optional(ATTR_DEVICE_ID): vol.Any(None, cv.string, [cv.string]),
        vol.Required(ATTR_DATA): cv.string,
    },
    extra=vol.ALLOW_EXTRA,
)


def _target_devices(call: ServiceCall) -> list[str]:
    """Every device the call is aimed at, or a readable complaint if there are none."""
    raw = call.data.get(ATTR_DEVICE_ID)
    ids = [raw] if isinstance(raw, str) else list(raw or ())
    devices = [device_id for device_id in ids if device_id]
    if not devices:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="no_target_device",
        )
    return devices


def _parse_midi(text: str) -> bytes:
    """Turn a hex string into MIDI bytes, or explain why it is not one.

    Accepts any spacing: "90 3C 64", "903c64" and "90-3C-64" all work.
    """
    cleaned = text.replace("-", " ").replace(",", " ").replace(":", " ").replace(" ", "")
    try:
        midi = bytes.fromhex(cleaned)
    except ValueError as err:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="invalid_midi_hex",
            translation_placeholders={"data": text},
        ) from err
    if not midi:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="empty_midi",
        )
    if not midi[0] & 0x80:
        # Without a status byte the device has no way to know what the bytes mean, and
        # the framing this is wrapped in cannot supply one.
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="midi_needs_status_byte",
            translation_placeholders={"first": f"0x{midi[0]:02X}"},
        )
    return midi


def _coordinator_for_device(hass: HomeAssistant, device_id: str) -> MvaveCoordinator:
    """Resolve the device a call targets, with translated errors for the usual mistakes."""
    _, entry = service.async_get_device_and_config_entry(hass, DOMAIN, device_id)
    config_entry: MvaveConfigEntry = entry
    return config_entry.runtime_data


async def _async_send_raw(call: ServiceCall) -> None:
    """Send raw MIDI bytes to one or more devices."""
    midi = _parse_midi(call.data[ATTR_DATA])
    for device_id in _target_devices(call):
        coordinator = _coordinator_for_device(call.hass, device_id)
        if not coordinator.connected:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="not_connected",
                translation_placeholders={"address": coordinator.address},
            )
        try:
            await coordinator.async_send(midi)
        except (BleakError, EOFError, TimeoutError) as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="send_failed",
                translation_placeholders={"address": coordinator.address, "error": str(err)},
            ) from err
        LOGGER.debug("%s: sent %s", coordinator.address, midi.hex(" ").upper())


@callback
def async_setup_services(hass: HomeAssistant) -> None:
    """Register the integration's services. Called once, from async_setup."""
    hass.services.async_register(DOMAIN, SERVICE_SEND_RAW, _async_send_raw, schema=SEND_RAW_SCHEMA)
