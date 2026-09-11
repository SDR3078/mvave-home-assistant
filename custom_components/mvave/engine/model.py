"""What a surface is made of: actions, pads, pages, and the state of one entity.

These are the values that cross every boundary in the engine, so they are all frozen and
none of them knows anything about Home Assistant. An entity is a domain, a state string
and some attributes; whether that came from a live state machine or from a dictionary in a
test is not this package's business.

Slots are numbered in **reading order, zero based**, top-left to bottom-right, the same as
a frame. Configuration written by a person is one based, because "pad 1" is the top left;
that translation belongs to the config flow, not here.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Final

# --------------------------------------------------------------------------- actions


@dataclass(frozen=True, slots=True)
class Navigate:
    """Go to another page. The graph is arbitrary; any page may reach any other."""

    page_id: str


@dataclass(frozen=True, slots=True)
class Back:
    """Pop one level of navigation history."""


@dataclass(frozen=True, slots=True)
class Home:
    """Clear the navigation history back to the root page."""


@dataclass(frozen=True, slots=True)
class Toggle:
    """Flip an entity that has an on and an off."""

    entity_id: str


@dataclass(frozen=True, slots=True)
class Focus:
    """Point the knobs at an entity, and keep them there until something else takes it."""

    entity_id: str


@dataclass(frozen=True, slots=True)
class Activate:
    """Run something that has no lasting state of its own: a scene, a script."""

    entity_id: str


@dataclass(frozen=True, slots=True)
class Service:
    """Call a service directly. The escape hatch for anything not modelled above."""

    domain: str
    service: str
    data: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class EventOnly:
    """Fire an event on the bus and do nothing else, so an automation can decide."""

    tag: str


@dataclass(frozen=True, slots=True)
class Nothing:
    """This pad does nothing. Distinct from a pad with nothing assigned to it."""


#: Anything a pad or a button can be bound to. A plain assignment rather than a ``type``
#: statement, because this package has to import on Python 3.11.
PadAction = Navigate | Back | Home | Toggle | Focus | Activate | Service | EventOnly | Nothing

NOTHING: Final = Nothing()

#: The actions that name an entity, and so tell a pad whose state to show.
ENTITY_ACTIONS: Final = (Toggle, Focus, Activate)


def entity_of(action: PadAction) -> str | None:
    """The entity an action acts on, if it acts on one."""
    return action.entity_id if isinstance(action, ENTITY_ACTIONS) else None


# ----------------------------------------------------------------------- entity state

#: States that mean the surface cannot say anything true about an entity. ``unknown`` is
#: included on purpose: rendering it as off would be a claim, and a pad that claims a lamp
#: is off when nobody knows is worse than one that admits it does not know.
OPAQUE_STATES: Final = frozenset({"unavailable", "unknown", "none", ""})

#: Which state counts as active, where it is not simply "on". Taken from the same places
#: Home Assistant's own interface decides whether to colour a card.
ACTIVE_STATES: Final[Mapping[str, frozenset[str]]] = {
    "cover": frozenset({"open", "opening"}),
    "climate": frozenset({"heat", "cool", "heat_cool", "auto", "dry", "fan_only"}),
    "lock": frozenset({"unlocked", "unlocking", "open", "opening"}),
    "vacuum": frozenset({"cleaning", "returning"}),
    "alarm_control_panel": frozenset(
        {"armed_home", "armed_away", "armed_night", "armed_vacation", "triggered"}
    ),
}

#: Domains with no lasting state of their own. Pressing one starts something; there is
#: nothing for the pad to reflect afterwards.
STATELESS_DOMAINS: Final = frozenset({"scene", "button", "input_button"})

#: Domains a knob can meaningfully adjust, and so worth focusing on a hold.
FOCUSABLE_DOMAINS: Final = frozenset({"light", "media_player", "cover", "climate", "fan"})


@dataclass(frozen=True, slots=True)
class EntityState:
    """One entity, as much of it as the surface needs."""

    entity_id: str
    state: str
    attributes: Mapping[str, Any] = field(default_factory=dict)

    @property
    def domain(self) -> str:
        """The part before the dot."""
        return self.entity_id.split(".", 1)[0]

    @property
    def is_opaque(self) -> bool:
        """Whether the surface has nothing true to say about this entity."""
        return self.state in OPAQUE_STATES

    @property
    def is_active(self) -> bool:
        """Whether this entity counts as on, by the conventions of its own domain."""
        if self.is_opaque:
            return False
        active = ACTIVE_STATES.get(self.domain)
        if active is not None:
            return self.state in active
        if self.domain == "media_player":
            return self.state not in {"off", "idle", "standby"}
        return self.state == "on"


# ----------------------------------------------------------------------------- pages


class SourceKind(StrEnum):
    """How a page fills the slots its configuration does not claim."""

    #: Every entity in an area, which is what makes a room page work with no setup.
    AREA = "area"
    #: Every entity carrying a label, curated in an interface people already know.
    LABEL = "label"
    #: One pad per other page. This is what an index is.
    PAGES = "pages"
    #: Nothing. The configured pads are the whole page.
    EXPLICIT = "explicit"


@dataclass(frozen=True, slots=True)
class Source:
    """Where a page's unconfigured slots come from."""

    kind: SourceKind = SourceKind.EXPLICIT
    #: The area for AREA, the label for LABEL, unused otherwise.
    key: str | None = None


@dataclass(frozen=True, slots=True)
class PadConfig:
    """What a person configured for one slot. Overrides whatever the source would fill."""

    tap: PadAction = NOTHING
    hold: PadAction = NOTHING
    #: What this pad shows when what is behind it is on, overriding the default for its
    #: domain. Off is always white and is not configurable: the whole readability of a page
    #: rests on "is that pad white" answering "is it on".
    colour: int | None = None


@dataclass(frozen=True, slots=True)
class Page:
    """One screen: what it is called, what colour names it, and what is on it."""

    id: str
    title: str
    #: One of ``palette.IDENTITY``. Used on whatever index points here, and as the colour
    #: of the curtain that covers the grid on the way in and out.
    colour: int
    source: Source = field(default_factory=Source)
    parent_id: str | None = None
    pads: Mapping[int, PadConfig] = field(default_factory=dict)
    #: Knob number to entity, overriding whatever has focus. The one place a page beats
    #: the global rule, for "volume here always means this speaker".
    knobs: Mapping[int, str] = field(default_factory=dict)
    #: The three transport buttons that are not back and home.
    buttons: Mapping[str, PadAction] = field(default_factory=dict)
    #: Seconds of no input before returning to the root. Zero means never.
    idle_timeout: float = 30.0


@dataclass(frozen=True, slots=True)
class Profile:
    """Every page, and which one is the root."""

    pages: Mapping[str, Page]
    root_id: str

    def page(self, page_id: str) -> Page | None:
        """One page by id, or None if nothing is configured under that name."""
        return self.pages.get(page_id)

    @property
    def root(self) -> Page | None:
        """The page an empty navigation stack shows."""
        return self.pages.get(self.root_id)


@dataclass(frozen=True, slots=True)
class Slot:
    """One pad of a page after the source has been applied. Not configuration."""

    tap: PadAction = NOTHING
    hold: PadAction = NOTHING
    colour: int | None = None

    @property
    def entity_id(self) -> str | None:
        """The entity whose state this pad shows, if it shows one."""
        return entity_of(self.tap) or entity_of(self.hold)

    @property
    def is_stateless(self) -> bool:
        """Whether pressing this starts something rather than changing something."""
        entity_id = self.entity_id
        if entity_id is not None:
            return entity_id.split(".", 1)[0] in STATELESS_DOMAINS
        return not isinstance(self.tap, Nothing) or not isinstance(self.hold, Nothing)
