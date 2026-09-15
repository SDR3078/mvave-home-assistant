"""What a surface is made of: actions, pads, pages, and the state of one entity.

These are the values that cross every boundary in the engine, so they are all frozen and
none of them knows anything about Home Assistant. An entity is a domain, a state string
and some attributes; whether that came from a live state machine or from a dictionary in a
test is not this package's business.

Slots are numbered in **reading order, zero based**, top-left to bottom-right, the same as
a frame. Stored configuration is the same order, one based. Neither is the number printed
on the pad, which runs the other way up and is a fact about one piece of hardware — every
translation into it happens outside this package, which is why nothing here imports
``devices``.
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
class Watch:
    """Show an entity's state, and do nothing when pressed.

    A pad is allowed to be a readout: "is the back door open", "is anybody home" — things
    worth a glance that nothing can act on. It carries the entity so the pad can show it,
    and refuses under the finger, which is the plain truth about it: this one is telling
    you rather than offering you.

    Auto-fill never produces one, because a guess should only ever guess at controls, and
    a room full of sensors would spend every pad on something nobody can press. Pinning
    does, because pinning one entity to one pad is a statement rather than a guess.
    """

    entity_id: str


@dataclass(frozen=True, slots=True)
class Nothing:
    """This pad does nothing. Distinct from a pad with nothing assigned to it."""


#: Anything a pad or a button can be bound to. A plain assignment rather than a ``type``
#: statement, because this package has to import on Python 3.11.
PadAction = (
    Navigate | Back | Home | Toggle | Focus | Activate | Service | EventOnly | Watch | Nothing
)

NOTHING: Final = Nothing()

#: The actions that name an entity, and so tell a pad whose state to show.
ENTITY_ACTIONS: Final = (Toggle, Focus, Activate, Watch)

#: Actions a press cannot carry out. Both shudder under the finger rather than going
#: silent: a lit pad that does nothing is indistinguishable from a broken one, and a press
#: is a question this surface always answers.
INERT_ACTIONS: Final = (Nothing, Watch)


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
    # A tracker's state is the name of wherever it is, so "not on" is not a state it has.
    # Anything other than home is somewhere else — at work, in a named zone, or simply
    # away — and the pad is answering one question: is this one here.
    "device_tracker": frozenset({"home"}),
    "person": frozenset({"home"}),
    "sun": frozenset({"above_horizon"}),
}

#: Domains with no lasting state of their own. Pressing one starts something; there is
#: nothing for the pad to reflect afterwards, so it keeps its colour and never shows white.
#:
#: A script is here by decision rather than by fact. It *does* have a running state, and
#: showing it put a pad through purple-while-running and white-while-idle — the one pair
#: measured as too close to tell apart, and the exact case purple is reserved for stateless
#: domains to avoid. Since a script is idle almost always and usually runs for well under a
#: second, what that bought was a flash nobody could see, rendered in the colour nobody can
#: read. The acknowledgement latch answers "did it run" instead. What it gives up is "is it
#: still running", which only a script with waits in it could ever have shown.
STATELESS_DOMAINS: Final = frozenset({"scene", "script", "button", "input_button"})

#: Domains that rest at ``unknown`` rather than at a value, which is not the same as being
#: unreachable and must not be treated as it.
#:
#: Deliberately *not* the same set as above, though it was until scripts joined that one.
#: The two ask different questions. A scene that has never been run reports ``unknown``
#: forever, so refusing to press it would make it unpressable. A script reports ``off`` when
#: it is idle, so a script reporting ``unavailable`` really is unreachable, and pressing it
#: should refuse like anything else nobody can reach.
UNKNOWN_AT_REST: Final = frozenset({"scene", "button", "input_button"})

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


#: Seconds a page waits with nobody touching it before the surface goes back to rest.
IDLE_TIMEOUT: Final = 30.0


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
    #: Exactly the pins and nothing else, even though a source is named. A page somebody
    #: has edited: nothing on it moves or appears on its own after that. The source stays,
    #: so an event can still say which room the page came from.
    fixed: bool = False
    #: Knob number to entity, overriding whatever has focus. The one place a page beats
    #: the global rule, for "volume here always means this speaker".
    knobs: Mapping[int, str] = field(default_factory=dict)
    #: The three transport buttons that are not back and home.
    buttons: Mapping[str, PadAction] = field(default_factory=dict)
    #: Seconds of no input before returning to rest. Zero means never.
    idle_timeout: float = IDLE_TIMEOUT


@dataclass(frozen=True, slots=True)
class Profile:
    """Every page, which one is the root, and what colour anything defaults to."""

    pages: Mapping[str, Page]
    root_id: str
    #: What a pad shows when it is on, by the domain of what is behind it. A default that
    #: a pad's own configuration overrides. Held here rather than looked up from a constant
    #: so it can be configured, and resolved once when a page is laid out rather than on
    #: every render.
    colours: Mapping[str, int] = field(default_factory=dict)
    #: Where the surface rests, if not on the root: the page it wakes up on when it
    #: connects and the page the idle timeout returns to, sitting on top of the root so
    #: that back goes to the index from it. The home button is the root regardless; that
    #: split is the owner's, from the grid. Ignored if it names no page.
    default_page_id: str | None = None

    def page(self, page_id: str) -> Page | None:
        """One page by id, or None if nothing is configured under that name."""
        return self.pages.get(page_id)

    @property
    def root(self) -> Page | None:
        """The page an empty navigation stack shows."""
        return self.pages.get(self.root_id)

    @property
    def at_rest(self) -> list[str]:
        """The navigation stack when nobody has touched anything.

        The root alone, or the default page on top of it. A fresh list each time, because
        the surface mutates its stack in place and two surfaces must not share one.
        """
        default = self.default_page_id
        if default is not None and default != self.root_id and default in self.pages:
            return [self.root_id, default]
        return [self.root_id]


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
