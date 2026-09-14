"""Turning a page's configuration into sixteen slots.

Configuring is overriding, never building from zero. A page with an area and no other
configuration at all is already usable: its slots fill themselves from what is in that
room, in an order a person would expect, and every pad does the obvious thing for what is
behind it. Anything explicitly configured wins, and whatever is left over stays dark.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import replace
from typing import Final

from .frames import PAD_COUNT
from .model import (
    FOCUSABLE_DOMAINS,
    NOTHING,
    Activate,
    Focus,
    Navigate,
    PadAction,
    Page,
    Profile,
    Service,
    Slot,
    Source,
    SourceKind,
    Toggle,
    Watch,
)
from .palette import colour_for
from .ports import RegistryView

#: The order entities are laid out in when a page fills itself from an area. Lights first
#: because they are what a person reaches for; the things you press once and forget last.
DOMAIN_ORDER: Final = (
    "light",
    "media_player",
    "cover",
    "climate",
    "fan",
    "switch",
    "input_boolean",
    "lock",
    "scene",
    "script",
)

#: Domains that take a plain toggle.
TOGGLEABLE: Final = frozenset(
    {"light", "switch", "input_boolean", "fan", "siren", "climate", "humidifier", "lock"}
)

#: Domains with no lasting state, where a tap starts something.
ACTIVATABLE: Final = frozenset({"scene", "script", "button", "input_button"})

#: What a tap does, for domains where it is not a toggle and not an activation.
TAP_SERVICES: Final = {
    "media_player": ("media_player", "media_play_pause"),
    "cover": ("cover", "toggle"),
}

#: Everything a pad can actually do something with.
#:
#: The list exists because the alternative is a lit pad that does nothing, which this
#: surface refuses to have. A room is full of things that are not controls — a temperature,
#: a door contact, somebody's phone — and asking for "everything in the kitchen" returns
#: all of them. Before this, anything unrecognised was given a plain toggle, so a device
#: tracker on a pad called ``homeassistant.toggle`` on a device tracker and sat there lit,
#: which is exactly how it was found: pinned by hand, pressed, and nothing happened.
CONTROLLABLE: Final = TOGGLEABLE | ACTIVATABLE | frozenset(TAP_SERVICES)

#: Domains a pad can show but never act on.
#:
#: Everything here has a lasting state that reads as on or off. Nothing numeric can be on
#: this list, because a pad cannot say 21.5 degrees — it has five colours, white and dark,
#: and a temperature is none of them.
WATCHABLE: Final = frozenset(
    {"binary_sensor", "device_tracker", "person", "sun", "calendar", "schedule"}
)

#: What a person may pin to a pad: everything a press can reach, and everything a glance
#: can. Deliberately wider than what auto-fill will offer, and that gap is the whole rule:
#: **auto-fill is a guess, pinning is a statement.** A guess should only guess at controls.
#: A statement may be a readout, because somebody chose this pad and this entity on purpose.
PINNABLE: Final = CONTROLLABLE | WATCHABLE


def default_actions(entity_id: str) -> tuple[PadAction, PadAction]:
    """What a pad does when nobody configured it, derived from the entity's domain.

    A hold focuses anything a knob could adjust, which is what makes "hold a lamp and turn
    knob one" work without a line of configuration.
    """
    domain = entity_id.split(".", 1)[0]
    hold: PadAction = Focus(entity_id) if domain in FOCUSABLE_DOMAINS else NOTHING

    if domain in TOGGLEABLE:
        return Toggle(entity_id), hold
    if domain in ACTIVATABLE:
        return Activate(entity_id), hold
    service = TAP_SERVICES.get(domain)
    if service is not None:
        return Service(service[0], service[1], {"entity_id": entity_id}), hold
    if domain in WATCHABLE:
        # A readout. It shows its state like everything else and shudders when pressed,
        # so it needs no new colour and no new rule: the shudder already means "I cannot
        # act on this", and for a door sensor that is simply true.
        return Watch(entity_id), NOTHING
    # Nothing rather than a toggle. Something outside CONTROLLABLE has no press that means
    # anything, and guessing one gives a pad that lights up and then ignores you — the
    # failure this surface will not have. Nothing makes it shudder instead, which says so.
    return NOTHING, hold


def _ordered(entity_ids: Sequence[str]) -> list[str]:
    """Sort entities into the order a page lays them out, keeping registry order within.

    Python's sort is stable, so the registry's own order survives inside each domain
    without having to be part of the key. It used to be, by way of ``list.index``, which
    made the whole thing quadratic: laying out a room of two hundred entities cost five
    times what one of twenty did, for no reason anybody would ever have noticed and no
    reason to keep.
    """
    ranked = {domain: rank for rank, domain in enumerate(DOMAIN_ORDER)}
    return sorted(
        entity_ids, key=lambda entity_id: ranked.get(entity_id.split(".", 1)[0], len(DOMAIN_ORDER))
    )


def source_entities(source: Source, registry: RegistryView) -> list[str]:
    """Every entity a source offers that a pad could do something with.

    A room holds far more than controls, and the ones that are not controls would otherwise
    take pads and then ignore every press. Filtered here rather than left to the renderer,
    because a pad spent on a temperature reading is a pad the room's actual lights did not
    get.
    """
    if source.kind is SourceKind.AREA and source.key:
        return _ordered(_controllable(registry.entities_in_area(source.key)))
    if source.kind is SourceKind.LABEL and source.key:
        return _ordered(_controllable(registry.entities_with_label(source.key)))
    return []


def _controllable(entity_ids: Sequence[str]) -> list[str]:
    """Only the ones a press could mean something to."""
    return [entity_id for entity_id in entity_ids if entity_id.split(".", 1)[0] in CONTROLLABLE]


def entity_colour(entity_id: str, profile: Profile) -> int:
    """What this entity shows when it is on, by its domain, as the profile has it."""
    domain = entity_id.split(".", 1)[0]
    return profile.colours.get(domain, colour_for(domain))


def _page_slots(page: Page, profile: Profile) -> list[Slot]:
    """One navigate pad per other page, in the profile's own order.

    This is what an index is, and it is why the root needs no configuration either: every
    page a person adds appears on it, in that page's own colour.
    """
    return [
        Slot(tap=Navigate(other.id), colour=other.colour)
        for other in profile.pages.values()
        if other.id != page.id and other.id != profile.root_id
    ]


def resolve(page: Page, registry: RegistryView, profile: Profile) -> tuple[Slot | None, ...]:
    """The sixteen slots of a page: configuration first, then the source, then nothing.

    An entity already placed by hand is never placed a second time by the source, so
    pinning the kitchen lamp to the corner does not leave a duplicate of it further down.
    A fixed page stops after the configuration: it is exactly what somebody saved.
    """
    slots: list[Slot | None] = [None] * PAD_COUNT
    for index, config in page.pads.items():
        if 0 <= index < PAD_COUNT:
            slot = Slot(tap=config.tap, hold=config.hold, colour=config.colour)
            if slot.colour is None and slot.entity_id is not None:
                slot = replace(slot, colour=entity_colour(slot.entity_id, profile))
            slots[index] = slot

    if page.fixed:
        # Somebody edited this page, so it holds exactly what they saved. The source is
        # still named — an event can say which room it came from — but supplies nothing:
        # an empty pad stays dark, and an entity cleared off the page does not come back
        # on the next free one.
        return tuple(slots)

    if page.source.kind is SourceKind.PAGES:
        filling: Iterable[Slot] = _page_slots(page, profile)
    else:
        placed = {slot.entity_id for slot in slots if slot is not None}
        # A generator rather than a list: a room can hold hundreds of entities and a grid
        # holds sixteen, and there is no reason to work out what the other hundreds would
        # have looked like.
        filling = (
            Slot(*default_actions(entity_id), colour=entity_colour(entity_id, profile))
            for entity_id in source_entities(page.source, registry)
            if entity_id not in placed
        )

    empty = (index for index in range(PAD_COUNT) if slots[index] is None)
    for slot, index in zip(filling, empty, strict=False):
        slots[index] = slot
    return tuple(slots)
