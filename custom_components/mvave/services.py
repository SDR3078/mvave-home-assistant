"""Services for the M-Vave integration.

Escape hatches: anything the entities and the profile engine do not model can be done by
sending MIDI to the device directly.

Registered from ``async_setup`` rather than ``async_setup_entry``, so the services exist
and give a useful error even when no device is set up, rather than appearing and
disappearing with the entry.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import voluptuous as vol
from bleak import BleakError
from homeassistant.const import ATTR_DEVICE_ID
from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
    callback,
)
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import service

from .const import DOMAIN, LOGGER
from .devices.smc_pad import PAD_NUMBER_BY_READING_ORDER
from .engine.describe import describe_page
from .engine.frames import PAD_COUNT

if TYPE_CHECKING:
    from . import MvaveConfigEntry, MvaveData
    from .runner import SurfaceRunner

SERVICE_SEND_RAW = "send_raw"
SERVICE_NAVIGATE = "navigate"
SERVICE_FOCUS = "focus"
SERVICE_HOME = "home"
SERVICE_PRESS_SLOT = "press_slot"
SERVICE_GET_PAGES = "get_pages"
ATTR_DATA = "data"
ATTR_PAGE = "page"
ATTR_ENTITY = "entity_id"
ATTR_SLOT = "slot"
ATTR_ACTION = "action"

#: What a simulated press can be. An enum rather than a boolean called ``hold``: the two
#: gestures do genuinely different things, and ``hold: false`` reads like the absence of
#: something rather than the presence of the other one.
TAP = "tap"
HOLD = "hold"

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


def _data_for_device(hass: HomeAssistant, device_id: str) -> MvaveData:
    """Resolve the device a call targets, with translated errors for the usual mistakes."""
    _, entry = service.async_get_device_and_config_entry(hass, DOMAIN, device_id)
    config_entry: MvaveConfigEntry = entry
    return config_entry.runtime_data


async def _async_send_raw(call: ServiceCall) -> None:
    """Send raw MIDI bytes to one or more devices."""
    midi = _parse_midi(call.data[ATTR_DATA])
    for device_id in _target_devices(call):
        coordinator = _data_for_device(call.hass, device_id).coordinator
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


# The surface has to be drivable from outside or it is only half a control surface. A
# presence sensor pre-selecting a room, a wall tablet steering the pad, and an automation
# pushing to a media page when the television comes on are all navigation that nobody
# pressed, and the engine already distinguishes them from a press so an automation cannot
# mistake its own effect for a person.
TARGET_SCHEMA = vol.Schema(
    {vol.Optional(ATTR_DEVICE_ID): vol.Any(None, cv.string, [cv.string])}, extra=vol.ALLOW_EXTRA
)
NAVIGATE_SCHEMA = TARGET_SCHEMA.extend({vol.Required(ATTR_PAGE): cv.string})
FOCUS_SCHEMA = TARGET_SCHEMA.extend({vol.Required(ATTR_ENTITY): cv.entity_id})


def _surfaces(call: ServiceCall) -> list[SurfaceRunner]:
    """Every surface the call is aimed at, ready to be told something.

    A surface that has not been built yet says so. It used to say nothing at all, and an
    automation aimed at a device that was still connecting simply had no effect and left
    nothing behind to explain why.
    """
    runners = []
    for device_id in _target_devices(call):
        data = _data_for_device(call.hass, device_id)
        if data.runner.surface is None:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="surface_not_ready",
                translation_placeholders={"address": data.coordinator.address},
            )
        runners.append(data.runner)
    return runners


async def _async_navigate(call: ServiceCall) -> None:
    """Send one or more surfaces to a page."""
    page = call.data[ATTR_PAGE]
    for runner in _surfaces(call):
        if not runner.knows(page):
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="unknown_page",
                translation_placeholders={"page": page, "pages": runner.page_names()},
            )
        runner.drive(lambda surface, page=page: surface.navigate_to(page))


async def _async_focus(call: ServiceCall) -> None:
    """Point one or more surfaces' knobs at an entity."""
    entity_id = call.data[ATTR_ENTITY]
    for runner in _surfaces(call):
        runner.drive(lambda surface, entity_id=entity_id: surface.focus_on(entity_id))


async def _async_home(call: ServiceCall) -> None:
    """Send one or more surfaces back to their root page."""
    for runner in _surfaces(call):
        runner.drive(lambda surface: surface.go_home())


def _whole_pad_number(value: Any) -> int:
    """A pad number as printed, and nothing that merely rounds to one.

    `Coerce(int)` truncated: a template producing 3.7 pressed pad 3, the neighbour of the
    one asked for, and `True` pressed pad 1. A number that is not exactly a whole one is
    refused before the handler sees it.
    """
    if isinstance(value, bool):
        raise vol.Invalid("a pad number is required")
    try:
        number = float(value)
    except (TypeError, ValueError) as err:
        raise vol.Invalid("a pad number is required") from err
    if not number.is_integer():
        raise vol.Invalid(f"{value} is not a whole pad number")
    return int(number)


PRESS_SLOT_SCHEMA = TARGET_SCHEMA.extend(
    {
        vol.Required(ATTR_SLOT): vol.All(_whole_pad_number, vol.Range(min=1, max=PAD_COUNT)),
        vol.Optional(ATTR_ACTION, default=TAP): vol.In([TAP, HOLD]),
    }
)


async def _async_press_slot(call: ServiceCall) -> None:
    """Press a pad that nobody touched.

    A **position**, on purpose, and named so nobody mistakes it for anything else. What a
    pad means is resolved from the live registry and moves the day somebody adds a lamp to
    a room, so a service that claimed to reach a particular light through a pad number
    would be lying by the end of the month. Anything that wants a specific light should
    call that light's own service; this is for driving the surface itself, which is what
    you need when the pad is out of reach, out of battery, or not in your hands.
    """
    slot: int = call.data[ATTR_SLOT]
    held = call.data[ATTR_ACTION] == HOLD
    # The number printed on the pad on the way in, because that is the one an automation's
    # author can check by looking; zero based in reading order everywhere inside, because
    # that is how a frame is indexed. The two disagree on all sixteen pads — PAD1 is the
    # bottom-left — so this conversion is the whole of the difference.
    index = PAD_NUMBER_BY_READING_ORDER.index(slot)
    for runner in _surfaces(call):
        runner.drive(lambda surface, pad=index: surface.press(pad, held=held))


GET_PAGES_SCHEMA = TARGET_SCHEMA.extend({vol.Optional(ATTR_PAGE): cv.string})


async def _async_get_pages(call: ServiceCall) -> ServiceResponse:
    """Say what every page means, without having to walk to the device and look.

    Pulled rather than published. This is the one thing about the surface that is both
    bulky and almost never changing, which is exactly the shape Home Assistant already
    moved out of entity attributes and into an action for weather forecasts, calendar
    events and to-do items. Somebody generating a cheat sheet asks once; nobody charts it.
    """
    wanted: str | None = call.data.get(ATTR_PAGE)
    response: dict[str, Any] = {}
    for device_id in _target_devices(call):
        data = _data_for_device(call.hass, device_id)
        surface = data.runner.surface
        if surface is None:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="surface_not_ready",
                translation_placeholders={"address": data.coordinator.address},
            )
        if wanted is not None and surface.profile.page(wanted) is None:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="unknown_page",
                translation_placeholders={
                    "page": wanted,
                    "pages": data.runner.page_names(),
                },
            )
        pages = [
            page for page in surface.profile.pages.values() if wanted is None or page.id == wanted
        ]
        view = data.runner.view
        response[device_id] = {
            "current_page": view.page_id,
            "focus": view.focus,
            "depth": view.depth,
            # Same reason as the pads: nothing is written on the encoders either, and only
            # the running engine knows which of them would do anything right now.
            "knobs": [
                {"knob": knob, "entity_id": entity_id, "property": prop}
                for knob, entity_id, prop in view.knobs
            ],
            "pages": [
                # Reported by the number printed on the pad, the same one `press_slot`
                # takes, so a slot read out of this response can be pressed without
                # anybody having to know the two count in opposite directions.
                describe_page(
                    page,
                    surface.registry,
                    surface.profile,
                    numbering=lambda index: PAD_NUMBER_BY_READING_ORDER[index],
                )
                for page in pages
            ],
        }
    return response


@callback
def async_setup_services(hass: HomeAssistant) -> None:
    """Register the integration's services. Called once, from async_setup."""
    hass.services.async_register(DOMAIN, SERVICE_SEND_RAW, _async_send_raw, schema=SEND_RAW_SCHEMA)
    hass.services.async_register(DOMAIN, SERVICE_NAVIGATE, _async_navigate, schema=NAVIGATE_SCHEMA)
    hass.services.async_register(DOMAIN, SERVICE_FOCUS, _async_focus, schema=FOCUS_SCHEMA)
    hass.services.async_register(DOMAIN, SERVICE_HOME, _async_home, schema=TARGET_SCHEMA)
    hass.services.async_register(
        DOMAIN, SERVICE_PRESS_SLOT, _async_press_slot, schema=PRESS_SLOT_SCHEMA
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_GET_PAGES,
        _async_get_pages,
        schema=GET_PAGES_SCHEMA,
        # Nothing about this belongs in the state machine: it is big, it is per-call, and
        # it changes only when somebody reconfigures the surface.
        supports_response=SupportsResponse.ONLY,
    )
