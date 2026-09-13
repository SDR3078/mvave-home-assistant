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

from collections.abc import Callable, Mapping, Sequence
from typing import Any, Final

from .model import (
    NOTHING,
    UNKNOWN_AT_REST,
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
from .properties import can_focus
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


def describe_slot(
    index: int,
    slot: Slot | None,
    registry: RegistryView,
    numbering: Callable[[int], int] = lambda index: index + 1,
) -> dict[str, Any]:
    """One pad, in words.

    The engine counts pads from zero in reading order and has no idea what is printed on
    any of them — which numbering a person sees is a fact about the hardware, so it arrives
    as `numbering` rather than being known here. The default is the position, which is what
    every test in this package wants; the integration passes the number on the pad, so that
    a slot read out of `mvave.get_pages` can be handed straight back to `mvave.press_slot`.
    """
    described: dict[str, Any] = {
        "slot": numbering(index),
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
    # What a hold would *do*, not what it was configured as. A focusable domain whose
    # entity has no value an encoder can hold refuses on the grid, and this service exists
    # to say what a pad would do — so reporting the configured `focus` here would be the
    # one place that promised something the pad then refuses. `shows: unreachable` has
    # always warned that a tap will refuse; this is the same warning for a hold.
    if isinstance(slot.hold, Focus) and not can_focus(state):
        described["hold"] = action_name(NOTHING)
    if state is not None:
        # The registry's own name for it, which is what somebody reading this recognises.
        # An entity id is a handle; "Ceiling lights" is what is written on the wall.
        described["name"] = state.attributes.get("friendly_name") or entity_id
    domain = entity_id.split(".", 1)[0]
    opaque = state is None or state.is_opaque
    if opaque and domain not in UNKNOWN_AT_REST:
        # The same thing the pad itself says under a finger, and the reason this is worth
        # asking for: on the grid an unreachable pad and a pad that is off look identical.
        #
        # Asked before statelessness, not after. A script is *drawn* stateless and can still
        # be unreachable, so branching on how the pad is drawn made this report "action" for
        # a pad that refuses — while `Surface._press` says in as many words that this
        # service is what explains a refusal in words.
        described["shows"] = UNREACHABLE
    elif slot.is_stateless or state is None:
        described["shows"] = ACTION
    else:
        described["shows"] = ON if state.is_active else OFF
    return described


def describe_page(
    page: Page,
    registry: RegistryView,
    profile: Profile,
    slots: Sequence[Slot | None] | None = None,
    numbering: Callable[[int], int] = lambda index: index + 1,
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
        "slots": [
            describe_slot(index, slot, registry, numbering) for index, slot in enumerate(resolved)
        ],
    }
