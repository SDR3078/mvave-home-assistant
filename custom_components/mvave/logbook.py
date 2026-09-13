"""Describing the surface's own events in the logbook.

Everything the engine announces goes onto the bus as one event type with a ``type`` field,
which is right for automations and unreadable in a timeline: without this, a person looking
at why the kitchen light came on at seven sees a row saying ``mvave_event`` and nothing
else. Lutron, ZHA, deCONZ and Shelly all ship one of these for the same reason.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.components.logbook import LOGBOOK_ENTRY_MESSAGE, LOGBOOK_ENTRY_NAME
from homeassistant.const import ATTR_DEVICE_ID
from homeassistant.core import callback
from homeassistant.helpers import device_registry as dr

from .const import DOMAIN
from .devices.smc_pad import PAD_NUMBER_BY_READING_ORDER
from .runner import EVENT_TYPE

if TYPE_CHECKING:
    from collections.abc import Callable

    from homeassistant.core import Event, HomeAssistant

#: What the surface calls itself in a timeline when the device cannot be resolved.
FALLBACK_NAME = "M-Vave"

#: Where a transition came from, as a clause. The whole point of recording the trigger is
#: that an automation's own effect must be distinguishable from a person's finger, and
#: that distinction is worth just as much to somebody reading back through the evening.
BY_TRIGGER = {
    "pad": " from a pad",
    "button": " from a button",
    "service": " from an automation",
    "idle": " after a while with nothing happening",
}


@callback
def async_describe_events(
    hass: HomeAssistant,
    async_describe_event: Callable[[str, str, Callable[[Event], dict[str, str]]], None],
) -> None:
    """Teach the logbook to read this integration's events."""

    @callback
    def async_describe_mvave_event(event: Event) -> dict[str, str]:
        """Turn one engine event into a line somebody can read."""
        data: dict[str, Any] = dict(event.data)
        return {
            LOGBOOK_ENTRY_NAME: _device_name(hass, data.get(ATTR_DEVICE_ID)),
            LOGBOOK_ENTRY_MESSAGE: _message(data),
        }

    async_describe_event(DOMAIN, EVENT_TYPE, async_describe_mvave_event)


def _device_name(hass: HomeAssistant, device_id: str | None) -> str:
    """Whatever the person called this surface, or what it calls itself."""
    if device_id is None:
        return FALLBACK_NAME
    device = dr.async_get(hass).async_get(device_id)
    if device is None:
        return FALLBACK_NAME
    return device.name_by_user or device.name or FALLBACK_NAME


def _message(data: dict[str, Any]) -> str:
    """One sentence for one thing that happened."""
    kind = data.get("type")
    origin = BY_TRIGGER.get(str(data.get("trigger")), "")
    page = data.get("page_title") or data.get("page_id") or "a page"

    if kind == "page_entered":
        return f"showed {page}{origin}"
    if kind == "page_exited":
        return f"left {page}{origin}"
    if kind == "focus_set":
        return f"pointed the knobs at {data.get('entity_id')}"
    if kind == "focus_cleared":
        return "let go of the knobs"
    if kind in ("pad_pressed", "pad_held"):
        # By the number printed on the pad, the same as everywhere else a person sees one.
        # The event carries a frame index, which counts from zero in reading order and is
        # the one numbering nothing on the hardware agrees with.
        pad = data.get("pad")
        printed = (
            PAD_NUMBER_BY_READING_ORDER[pad]
            if isinstance(pad, int) and 0 <= pad < len(PAD_NUMBER_BY_READING_ORDER)
            else None
        )
        where = f"pad {printed}" if printed is not None else "a pad"
        entity_id = data.get("entity_id")
        about = f" ({entity_id})" if entity_id else ""
        verb = "held" if kind == "pad_held" else "pressed"
        return f"{verb} {where}{about}{origin}"
    if kind == "knob_turned":
        knob = data.get("knob")
        which = f"knob {knob}" if knob is not None else "a knob"
        prop = data.get("property")
        entity_id = data.get("entity_id")
        if prop and entity_id:
            return f"turned {which} to set the {prop} of {entity_id}"
        return f"turned {which}"
    if kind == "tagged":
        return f"fired the tag {data.get('tag')}"
    return str(kind or "did something")
