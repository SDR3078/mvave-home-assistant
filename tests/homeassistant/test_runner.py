"""The runner's clockwork around a rebuild.

The first tests the runner has of its own. It owns every clock the engine is not allowed
to have, and a rebuild is the one moment the engine's state and those clocks are set by
different code — which is exactly where the first defect in the default-page feature sat,
found at the grid the minute the feature was tried.
"""

from __future__ import annotations

import time
from collections.abc import Coroutine
from types import SimpleNamespace
from typing import Any

import pytest
from homeassistant.core import HomeAssistant

from custom_components.mvave.engine.frames import PAD_COUNT
from custom_components.mvave.engine.model import IDLE_TIMEOUT, Page, Profile, Source, SourceKind
from custom_components.mvave.engine.palette import BLUE, ORANGE
from custom_components.mvave.engine.surface import Outcome, Surface
from custom_components.mvave.registry import ROOT_ID, HomeAssistantRegistry
from custom_components.mvave.runner import SurfaceRunner, SurfaceView

EMPTY_FRAME = (0,) * PAD_COUNT


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
    made._holds = {}
    made._fired = set()
    made._playing = None
    made._reacting = False
    made._reaction_started = None
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


# ------------------------------------------------------------- fingers and the clock


async def test_a_finger_down_stops_the_idle_clock(
    runner: SurfaceRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A press that began at 29.95 s of a 30 s timeout had the page move under it before
    # the release landed the tap — on a different lamp. A finger down is activity.
    def no_task(work: Coroutine[Any, Any, None], what: str) -> Any:
        work.close()
        return SimpleNamespace(cancel=lambda: None)

    monkeypatch.setattr(runner, "_task", no_task)
    runner._restart("idle", 30.0, runner._timed_out)
    assert "idle" in runner._timers
    runner._down("pad:1")
    assert "idle" not in runner._timers
    assert "pad:1" in runner._holds


async def test_the_clock_waits_while_a_finger_is_down(
    runner: SurfaceRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A hold firing re-arms the clock while the finger is still there — the switcher hangs
    # off exactly such a hold — so the clock has to check for hands before it moves a page.
    dispatched: list[object] = []
    monkeypatch.setattr(runner, "_dispatch", dispatched.append)
    runner._holds["button:left"] = SimpleNamespace(cancel=lambda: None)  # type: ignore[assignment]
    runner.surface = Surface(profile(default="living"), HomeAssistantRegistry(runner.hass))
    runner._timed_out(None)
    assert dispatched == []  # nothing moved under the hand
    assert "idle" in runner._timers  # asked again later instead
    for cancel in runner._timers.values():
        cancel()


async def test_a_second_refusal_inside_a_second_is_held(runner: SurfaceRunner) -> None:
    # Three blinks in 540 ms is the whole flash budget. The guard used to clear the instant
    # a refusal finished, so a second press right after put six flashes into one second.
    refusal = Outcome(animation=(EMPTY_FRAME,), reaction=True)
    assert runner._holds_reaction(refusal) is False  # nothing shown yet
    runner._reaction_started = time.monotonic()
    assert runner._holds_reaction(refusal) is True  # one just started
    runner._reaction_started = time.monotonic() - 2.0
    assert runner._holds_reaction(refusal) is False  # long enough ago
    assert (
        runner._holds_reaction(Outcome(animation=(EMPTY_FRAME,))) is False
    )  # a page change never waits


# ------------------------------------------------------------------ the link


async def test_a_release_whose_press_was_never_seen_is_nothing(
    runner: SurfaceRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A finger down while the grid was dark during arming, up after "surface ready": the
    # press was dropped, and the release used to become a tap on whatever now sat there.
    dispatched: list[object] = []
    monkeypatch.setattr(runner, "_dispatch", dispatched.append)
    runner._up("pad:1")
    assert dispatched == []
    # A release that follows a press this runner saw is still a tap.
    runner._holds["pad:1"] = SimpleNamespace(cancel=lambda: None)  # type: ignore[assignment]
    runner._up("pad:1")
    assert len(dispatched) == 1


async def test_a_reconnect_puts_you_back_where_you_stood_and_starts_the_clock(
    runner: SurfaceRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The link dropped for ten seconds three times in one afternoon. The surface used to
    # come back at rest with no event for the move, so anything mirroring the page from
    # the bus was wrong until the next press. Now it is the same carry a rebuild does.
    import custom_components.mvave.runner as runner_module
    from custom_components.mvave.devices.smc_pad import SMC_PAD_FACTORY_LAYOUT

    house = profile()
    monkeypatch.setattr(runner_module, "build_profile", lambda hass, entry: house)
    runner.entry = None  # type: ignore[assignment]
    runner._watchers = []
    runner._announced = SurfaceView()
    runner.coordinator = SimpleNamespace(  # type: ignore[assignment]
        address="AA:BB:CC:DD:EE:FF", connected=True, arming=SimpleNamespace(layout=None)
    )
    assert runner.surface is not None
    runner.surface.navigate_to("living")
    assert runner.surface.stack == [ROOT_ID, "living"]

    runner._playing = SimpleNamespace(cancel=lambda: None)  # type: ignore[assignment]
    runner._reacting = True
    runner._reaction_started = time.monotonic()
    runner.coordinator.connected = False
    runner._on_connection()  # the drop
    assert runner.surface is None
    # Whatever was playing was playing to a dark grid, and the guard forgets it too.
    assert runner._playing is None and runner._reacting is False
    assert runner._reaction_started is None

    runner.coordinator.connected = True
    runner.coordinator.arming = SimpleNamespace(layout=SMC_PAD_FACTORY_LAYOUT)
    runner._on_connection()  # and back
    assert runner.surface is not None
    assert runner.surface.stack == [ROOT_ID, "living"]  # where you stood, not at rest
    assert "idle" in runner._timers  # and the room's clock is running
    running = runner._timers["idle"]

    # A coordinator callback that builds nothing — a battery reading, every half hour —
    # must not restart that clock: armed at the bottom of `_on_connection`, it was reset
    # on every callback and, on a chattier device, would never have fired at all.
    runner._on_connection()
    assert runner._timers["idle"] is running
    for cancel in runner._timers.values():
        cancel()
