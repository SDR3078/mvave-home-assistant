"""The two holes in the engine where Home Assistant is allowed to reach in.

Everything the engine needs from the platform arrives through these, and nothing else in
this package imports anything from it. That is what lets the whole surface be exercised
against dictionaries in a test, at the speed of a unit test, including the parts that are
genuinely hard to reason about: which pad lights when three lamps in an area go
unavailable, what the grid does when a page is entered while an animation is still running.

Both are ``Protocol`` rather than base classes, so the coordinator's real implementations
never have to import this module either. The dependency points one way only.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Protocol, runtime_checkable

from .model import EntityState


@runtime_checkable
class RegistryView(Protocol):
    """Reading the world: what exists, where it lives, and what it is doing."""

    def entities_in_area(self, area_id: str) -> Sequence[str]:
        """Every entity assigned to an area, in whatever order the registry gives them.

        Ordering into something a person would expect is the engine's job, not the
        registry's, so this may return them in any order at all.
        """
        ...

    def entities_with_label(self, label: str) -> Sequence[str]:
        """Every entity carrying a label."""
        ...

    def state_of(self, entity_id: str) -> EntityState | None:
        """One entity's state, or None if nothing by that id exists.

        A missing entity and an unavailable one are different: the first is a
        configuration mistake worth surfacing, the second is a lamp with a flat battery.
        """
        ...


@runtime_checkable
class ActionSink(Protocol):
    """Changing the world. Every one of these is fire and forget.

    The engine never waits for a service call. It renders optimistically, marks the pad as
    commanded but unconfirmed, and lets the state change that follows correct it. A
    surface that waits for a Zigbee round trip before lighting the pad feels broken even
    when it is working.
    """

    def call(self, domain: str, service: str, data: Mapping[str, Any]) -> None:
        """Call a service."""
        ...

    def fire(self, event_type: str, data: Mapping[str, Any]) -> None:
        """Fire an event on the bus."""
        ...
