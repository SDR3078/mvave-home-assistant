"""The runner's clockwork around a rebuild.

The first tests the runner has of its own. It owns every clock the engine is not allowed
to have, and a rebuild is the one moment the engine's state and those clocks are set by
different code — which is exactly where the first defect in the default-page feature sat,
found at the grid the minute the feature was tried.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from homeassistant.core import HomeAssistant

from custom_components.mvave.engine.model import IDLE_TIMEOUT, Page, Profile, Source, SourceKind
from custom_components.mvave.engine.palette import BLUE, ORANGE
from custom_components.mvave.engine.surface import Surface
from custom_components.mvave.registry import ROOT_ID, HomeAssistantRegistry
from custom_components.mvave.runner import SurfaceRunner


def profile(default: str | None = None) -> Profile:
    """A house with one room, resting on the index or on that room."""
    pages = {
        ROOT_ID: Page(
            ROOT_ID,
            "Home",
            BLUE,
            source=Source(SourceKind.PAGES),
            # What the registry does: the index never times out unless the pad rests
            # somewhere else, in which case it is a page you visit.
            idle_timeout=IDLE_TIMEOUT if default else 0,
        ),
        "living": Page(
            "living", "Living", ORANGE, source=Source(SourceKind.AREA, "living"), parent_id=ROOT_ID
        ),
    }
    return Profile(pages=pages, root_id=ROOT_ID, default_page_id=default)


@pytest.fixture
def runner(hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch) -> SurfaceRunner:
    """A runner standing on the index, with no radio and no LEDs — only its clocks."""
    made = SurfaceRunner.__new__(SurfaceRunner)
    made.hass = hass
    made.coordinator = SimpleNamespace(address="AA:BB:CC:DD:EE:FF")  # type: ignore[assignment]
    made.surface = Surface(profile(), HomeAssistantRegistry(hass))
    made._timers = {}
    made._lit = set()
    made._logged_page = None
    made._shown = None
    monkeypatch.setattr(made, "_redraw", lambda: None)
    return made


async def test_a_rebuild_arms_the_clock_of_the_page_you_are_standing_on(
    runner: SurfaceRunner,
) -> None:
    # Standing on the index, which never times out, so nothing is counting.
    assert "idle" not in runner._timers

    # Choose a default page. The index is somewhere you visit now, with a timeout it did
    # not have a second ago — and before 2026-09-15 nothing started it. The pad sat on the
    # index until the next press, and only then began going back to rest: "it did not
    # immediately change towards the screen ... once i moved screens it started".
    runner.reconfigure(profile(default="living"))
    assert runner.surface is not None
    assert runner.surface.stack == [ROOT_ID]  # still where you were standing
    assert "idle" in runner._timers  # but the clock is running now

    for cancel in runner._timers.values():
        cancel()


async def test_a_rebuild_onto_a_page_with_no_clock_arms_nothing(runner: SurfaceRunner) -> None:
    runner.reconfigure(profile())
    assert "idle" not in runner._timers
