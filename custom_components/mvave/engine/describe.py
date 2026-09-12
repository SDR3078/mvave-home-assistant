"""Saying in words what the grid says in light.

This device has no screen and nothing written on it, which is the whole premise: sixteen
pads, five colours, and a person who is expected to learn what they mean. That works for
whoever laid it out. It does not work for everybody else in the house, and it does not work
for the person who laid it out six months later.

Only the integration can answer "what would that pad do", because a page's contents are
resolved from the live registry rather than from a file: nothing anybody can read tells you
what is in an auto-filled room page except the running engine. So this exists to be asked.

Pure, and deliberately separate from :mod:`engine.render`. Rendering answers "what colour
is this pad now"; this answers "what is this pad *for*", which is a different question with
a different audience, and it is the one nobody can otherwise get an answer to.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Final

from .model import (
    Activate,
    Back,
    EventOnly,
    Focus,
    Home,
    Navigate,
    Nothing,
    PadAction,
    Page,
    Profile,
    Service,
    Slot,
    SourceKind,
    Toggle,
    Watch,
)
from .palette import name_for
from .ports import RegistryView
from .render import colour_of
from .resolve import resolve

#: What each kind of action is called. Spelled out rather than derived from the class name
#: so that renaming a dataclass cannot silently change an interface other people's
#: templates are reading.
ACTION_NAMES: Final[Mapping[type, str]] = {
    Navigate: "navigate",
    Back: "back",
    Home: "home",
    Toggle: "toggle",
    Focus: "focus",
    Activate: "activate",
    Service: "service",
    EventOnly: "event",
    Watch: "watch",
    Nothing: "nothing",
}

#: What a pad is saying about the thing behind it, in the same vocabulary the LEDs use.
ON: Final = "on"
OFF: Final = "off"
UNREACHABLE: Final = "unreachable"
#: Something with no on and off to report: a scene, a script, a navigation pad.
ACTION: Final = "action"
EMPTY: Final = "empty"


def action_name(action: PadAction) -> str:
    """What an action is called."""
    return ACTION_NAMES.get(type(action), type(action).__name__.lower())


def describe_slot(index: int, slot: Slot | None, registry: RegistryView) -> dict[str, Any]:
    """One pad, in words.

    Numbered from one, because that is how a person counts pads and how the configuration
    is written, while everything inside the engine counts from zero.
    """
    described: dict[str, Any] = {
        "slot": index + 1,
        "entity_id": None,
        "name": None,
        "tap": "nothing",
        "hold": "nothing",
        "to_page": None,
        "shows": EMPTY,
        "colour": name_for(colour_of(slot, registry)),
    }
    if slot is None:
        return described

    described["tap"] = action_name(slot.tap)
    described["hold"] = action_name(slot.hold)
    for action in (slot.tap, slot.hold):
        if isinstance(action, Navigate):
            described["to_page"] = action.page_id
            break

    entity_id = slot.entity_id
    described["entity_id"] = entity_id
    if entity_id is None:
        described["shows"] = EMPTY if isinstance(slot.tap, Nothing) else ACTION
        return described

    state = registry.state_of(entity_id)
    if state is not None:
        # The registry's own name for it, which is what somebody reading this recognises.
        # An entity id is a handle; "Ceiling lights" is what is written on the wall.
        described["name"] = state.attributes.get("friendly_name") or entity_id
    if slot.is_stateless:
        described["shows"] = ACTION
    elif state is None or state.is_opaque:
        # The same thing the pad itself says under a finger, and the reason this is worth
        # asking for: on the grid an unreachable pad and a pad that is off look identical.
        described["shows"] = UNREACHABLE
    else:
        described["shows"] = ON if state.is_active else OFF
    return described


def describe_page(
    page: Page,
    registry: RegistryView,
    profile: Profile,
    slots: Sequence[Slot | None] | None = None,
) -> dict[str, Any]:
    """One page and everything on it, resolved as it stands right now."""
    resolved = resolve(page, registry, profile) if slots is None else slots
    return {
        "page_id": page.id,
        "title": page.title,
        "colour": name_for(page.colour),
        "source": str(page.source.kind),
        "area_id": page.source.key if page.source.kind is SourceKind.AREA else None,
        "parent_page_id": page.parent_id,
        "slots": [describe_slot(index, slot, registry) for index, slot in enumerate(resolved)],
    }
