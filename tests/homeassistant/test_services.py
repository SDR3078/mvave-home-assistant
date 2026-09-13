"""Driving the surface from outside, and asking it what it means.

Two services with opposite jobs. One presses a **position** on the grid, deliberately, so
nobody can mistake it for a way to reach a particular lamp: what is on a pad is resolved
from the live registry and moves the day a room gains a bulb. The other exists because
this device has nothing written on it, and the running engine is the only thing that can
say what a pad would do.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import pytest
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError

from custom_components.mvave import services as services_module
from custom_components.mvave.devices.smc_pad import PAD_NUMBER_BY_READING_ORDER
from custom_components.mvave.engine.model import EntityState, Page, Profile, Source, SourceKind
from custom_components.mvave.engine.palette import BLUE, GREEN, ORANGE
from custom_components.mvave.engine.surface import Surface, Trigger
from custom_components.mvave.services import (
    _async_get_pages,
    _async_press_slot,
    _surfaces,
)

DEVICE_ID = "device-1234"
ADDRESS = "AA:BB:CC:DD:EE:FF"


class FakeRegistry:
    def entities_in_area(self, area_id: str) -> Sequence[str]:
        return {"kitchen": ("light.counter", "switch.kettle")}.get(area_id, ())

    def entities_with_label(self, label: str) -> Sequence[str]:
        return ()

    def state_of(self, entity_id: str) -> EntityState | None:
        states = {"light.counter": "on", "switch.kettle": "off"}
        state = states.get(entity_id)
        if state is None:
            return None
        # The lamp dims. Without a colour mode it has no adjustable property at all, and
        # holding a pad with nothing for an encoder to move now refuses instead of
        # pointing the knobs at it.
        attributes = (
            {"supported_color_modes": ["brightness"]} if entity_id.startswith("light.") else {}
        )
        return EntityState(entity_id, state, attributes)


PROFILE = Profile(
    pages={
        "home": Page("home", "Home", BLUE, source=Source(SourceKind.PAGES)),
        "kitchen": Page("kitchen", "Kitchen", GREEN, source=Source(SourceKind.AREA, "kitchen")),
        "garage": Page("garage", "Garage", ORANGE, source=Source(SourceKind.AREA, "garage")),
    },
    root_id="home",
)


class StubRunner:
    """A real engine, with the clock and the radio taken out."""

    def __init__(self, surface: Surface | None) -> None:
        self.surface = surface
        self.driven: list[Any] = []

    @property
    def view(self) -> Any:
        from custom_components.mvave.runner import SurfaceView, page_labels

        if self.surface is None:
            return SurfaceView()
        return SurfaceView(
            page_id=self.surface.page.id,
            depth=self.surface.depth,
            focus=self.surface.focus,
            pages=page_labels(self.surface.profile),
        )

    def page_names(self) -> str:
        return ", ".join(sorted(self.surface.profile.pages)) if self.surface else ""

    def drive(self, ask: Any) -> None:
        assert self.surface is not None
        self.driven.append(ask(self.surface))


class StubCoordinator:
    address = ADDRESS


class StubData:
    def __init__(self, runner: StubRunner) -> None:
        self.runner = runner
        self.coordinator = StubCoordinator()


class FakeCall:
    """Only the two things a handler actually reads."""

    def __init__(self, **data: Any) -> None:
        self.hass = object()
        self.data = {"device_id": DEVICE_ID, **data}


@pytest.fixture
def runner(monkeypatch: pytest.MonkeyPatch) -> StubRunner:
    """A surface standing on the index, reachable by every service."""
    made = StubRunner(Surface(PROFILE, FakeRegistry()))
    monkeypatch.setattr(services_module, "_data_for_device", lambda hass, device_id: StubData(made))
    return made


@pytest.fixture
def unbuilt(monkeypatch: pytest.MonkeyPatch) -> StubRunner:
    """A device that is set up but whose surface has not been built yet."""
    made = StubRunner(None)
    monkeypatch.setattr(services_module, "_data_for_device", lambda hass, device_id: StubData(made))
    return made


# --------------------------------------------------------------- press a pad


async def test_pressing_a_pad_presses_whatever_is_on_that_position(
    runner: StubRunner,
) -> None:
    # PAD13 is the top-left pad — the number printed on it, which runs up the grid — and
    # the top-left pad of the index is the first room, so the surface went there.
    await _async_press_slot(FakeCall(slot=13, action="tap"))
    assert runner.surface is not None
    assert runner.surface.page.id == "kitchen"


async def test_a_pad_is_named_by_the_number_printed_on_it(runner: StubRunner) -> None:
    # Not by where it falls in reading order, which is the trap: 1 is a real pad and it is
    # the *bottom* left, three rows away from where somebody counting from the top would
    # put it. Taking the printed number is what makes an automation checkable by looking at
    # the hardware, and this test is the one that fails if that is ever quietly inverted.
    await _async_press_slot(FakeCall(slot=14, action="tap"))
    assert runner.surface is not None
    assert runner.surface.page.id == "garage"  # PAD14, second along the top row

    await _async_press_slot(FakeCall(slot=1, action="tap"))
    assert runner.surface.page.id == "garage"  # PAD1 is bottom left, and the index is empty there


async def test_a_held_pad_does_what_holding_it_does(runner: StubRunner) -> None:
    await _async_press_slot(FakeCall(slot=13, action="tap"))  # into the kitchen
    await _async_press_slot(FakeCall(slot=13, action="hold"))  # hold the lamp
    assert runner.surface is not None
    assert runner.surface.focus == "light.counter"


async def test_a_service_press_is_never_mistaken_for_a_finger(runner: StubRunner) -> None:
    await _async_press_slot(FakeCall(slot=13, action="tap"))
    emitted = runner.driven[0].emits[-1]
    assert emitted.data["trigger"] == str(Trigger.SERVICE)


async def test_pressing_a_surface_that_is_not_running_says_so(unbuilt: StubRunner) -> None:
    # It used to do nothing at all, which left an automation aimed at a device that was
    # still connecting with no effect and nothing to explain why.
    with pytest.raises(HomeAssistantError):
        await _async_press_slot(FakeCall(slot=1, action="tap"))


def test_every_service_refuses_a_surface_that_is_not_running(unbuilt: StubRunner) -> None:
    with pytest.raises(HomeAssistantError):
        _surfaces(FakeCall())


# ------------------------------------------------------------ what it means


async def test_every_page_and_every_pad_comes_back(runner: StubRunner) -> None:
    response = await _async_get_pages(FakeCall())
    assert response is not None
    surface = response[DEVICE_ID]
    assert surface["current_page"] == "home"
    assert surface["depth"] == 0
    assert surface["focus"] is None
    assert [page["page_id"] for page in surface["pages"]] == ["home", "kitchen", "garage"]
    assert all(len(page["slots"]) == 16 for page in surface["pages"])


async def test_it_answers_for_a_page_nobody_is_standing_on(runner: StubRunner) -> None:
    # The whole point: the kitchen's pads are resolved from the live registry, so nothing
    # anybody can read says what is on them except the running engine.
    response = await _async_get_pages(FakeCall(page="kitchen"))
    assert response is not None
    (page,) = response[DEVICE_ID]["pages"]
    assert page["page_id"] == "kitchen"
    assert [slot["entity_id"] for slot in page["slots"][:2]] == [
        "light.counter",
        "switch.kettle",
    ]
    assert [slot["shows"] for slot in page["slots"][:2]] == ["on", "off"]


async def test_it_describes_the_pads_in_the_colours_the_grid_uses(runner: StubRunner) -> None:
    # Resolved, not raw. A card reading this should not have to reimplement the whole
    # colour grammar in JavaScript to agree with the physical grid.
    response = await _async_get_pages(FakeCall(page="kitchen"))
    assert response is not None
    (page,) = response[DEVICE_ID]["pages"]
    assert page["colour"] == "green"
    assert page["slots"][0]["colour"] == "orange"  # a light that is on
    assert page["slots"][1]["colour"] == "white"  # a switch that is off


async def test_a_slot_that_comes_back_can_be_pressed_without_translating_it(
    runner: StubRunner,
) -> None:
    # The round trip, on one page. `get_pages` is how an automation finds out what is on a
    # pad and `press_slot` is how it presses one, so the two have to count the same way or
    # reading something at "slot 1" and pressing slot 1 reaches a different pad three rows
    # away. Both use the number printed on the hardware, which runs up the grid.
    response = await _async_get_pages(FakeCall(page="home"))
    assert response is not None
    (page,) = response[DEVICE_ID]["pages"]
    assert [slot["slot"] for slot in page["slots"]] == list(PAD_NUMBER_BY_READING_ORDER)

    # The first room sits on the top-left pad, and that pad says 13.
    first = next(slot for slot in page["slots"] if slot["to_page"] == "kitchen")
    assert first["slot"] == 13

    await _async_press_slot(FakeCall(slot=first["slot"], action="tap"))
    assert runner.surface is not None
    assert runner.surface.page.id == "kitchen"


async def test_asking_for_a_page_that_does_not_exist_lists_the_ones_that_do(
    runner: StubRunner,
) -> None:
    with pytest.raises(ServiceValidationError):
        await _async_get_pages(FakeCall(page="attic"))


async def test_asking_a_surface_that_is_not_running_says_so(unbuilt: StubRunner) -> None:
    with pytest.raises(HomeAssistantError):
        await _async_get_pages(FakeCall())
