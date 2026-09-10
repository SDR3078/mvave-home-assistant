"""Turning a page's configuration into sixteen slots.

Configuring is overriding, never building from zero. A page with an area and no other
configuration at all is already usable: its slots fill themselves from what is in that
room, in an order a person would expect, and every pad does the obvious thing for what is
behind it. Anything explicitly configured wins, and whatever is left over stays dark.
"""

from __future__ import annotations

from collections.abc import Sequence
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
)
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
TOGGLEABLE: Final = frozenset({"light", "switch", "input_boolean", "fan", "siren"})

#: What a tap does, for domains where it is not a toggle and not an activation.
TAP_SERVICES: Final = {
    "media_player": ("media_player", "media_play_pause"),
    "cover": ("cover", "toggle"),
    "lock": ("lock", "open"),
}


def default_actions(entity_id: str) -> tuple[PadAction, PadAction]:
    """What a pad does when nobody configured it, derived from the entity's domain.

    A hold focuses anything a knob could adjust, which is what makes "hold a lamp and turn
    knob one" work without a line of configuration.
    """
    domain = entity_id.split(".", 1)[0]
    hold: PadAction = Focus(entity_id) if domain in FOCUSABLE_DOMAINS else NOTHING

    if domain in TOGGLEABLE:
        return Toggle(entity_id), hold
    if domain in ("scene", "script", "button", "input_button"):
        return Activate(entity_id), hold
    service = TAP_SERVICES.get(domain)
    if service is not None:
        return Service(service[0], service[1], {"entity_id": entity_id}), hold
    if domain == "climate":
        # No sensible single-press meaning. A hold hands it to the knobs, which is the
        # only thing anyone actually wants from a thermostat on a grid.
        return NOTHING, hold
    return Toggle(entity_id), hold


def _ordered(entity_ids: Sequence[str]) -> list[str]:
    """Sort entities into the order a page lays them out, keeping registry order within."""
    ranked = {domain: rank for rank, domain in enumerate(DOMAIN_ORDER)}
    return sorted(
        entity_ids,
        key=lambda entity_id: (
            ranked.get(entity_id.split(".", 1)[0], len(DOMAIN_ORDER)),
            entity_ids.index(entity_id),
        ),
    )


def source_entities(source: Source, registry: RegistryView) -> list[str]:
    """Every entity a source offers, in the order it wants them laid out."""
    if source.kind is SourceKind.AREA and source.key:
        return _ordered(registry.entities_in_area(source.key))
    if source.kind is SourceKind.LABEL and source.key:
        return _ordered(registry.entities_with_label(source.key))
    return []


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
    """
    slots: list[Slot | None] = [None] * PAD_COUNT
    for index, config in page.pads.items():
        if 0 <= index < PAD_COUNT:
            slots[index] = Slot(tap=config.tap, hold=config.hold, colour=config.colour)

    if page.source.kind is SourceKind.PAGES:
        filling: list[Slot] = _page_slots(page, profile)
    else:
        placed = {slot.entity_id for slot in slots if slot is not None}
        filling = [
            Slot(*default_actions(entity_id))
            for entity_id in source_entities(page.source, registry)
            if entity_id not in placed
        ]

    empty = (index for index in range(PAD_COUNT) if slots[index] is None)
    for slot, index in zip(filling, empty, strict=False):
        slots[index] = slot
    return tuple(slots)
