"""The entities that report the surface rather than the hardware.

These are the answer to "the integration produces an entity per pad, and a pad is not a
thing". A pad is a position whose occupant is resolved from the live area registry, so it
moves the day somebody adds a lamp to a room; a page, a focus and a home button are facts
about the profile and keep their meaning. Everything here is about that distinction
holding.

Driven against a stub runner rather than a running instance on purpose: what is worth
testing is which questions these entities answer and what they do when the link is down,
and neither needs a radio.
"""

from __future__ import annotations

from typing import Any

import pytest
from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.const import PERCENTAGE
from homeassistant.core import CALLBACK_TYPE

from custom_components.mvave.button import MvaveBackButton, MvaveHomeButton
from custom_components.mvave.engine.model import Page, Profile, Source, SourceKind
from custom_components.mvave.engine.palette import BLUE, GREEN, ORANGE
from custom_components.mvave.runner import SurfaceView, page_labels
from custom_components.mvave.select import MvavePageSelect
from custom_components.mvave.sensor import MvaveBatterySensor, MvaveFocusSensor

ADDRESS = "AA:BB:CC:DD:EE:FF"

AT_HOME = SurfaceView(
    page_id="home",
    page_title="Home",
    source="pages",
    depth=0,
    root_page_id="home",
    pages=(("home", "Home"), ("kitchen", "Kitchen"), ("living_room", "Living room")),
)

IN_THE_KITCHEN = SurfaceView(
    page_id="kitchen",
    page_title="Kitchen",
    parent_page_id="home",
    source="area",
    area_id="kitchen",
    depth=1,
    root_page_id="home",
    focus="light.counter",
    pages=AT_HOME.pages,
)


class StubCoordinator:
    """Enough of a coordinator to hang an entity off."""

    def __init__(self, connected: bool = True) -> None:
        self.address = ADDRESS
        # What the device says about itself once something has connected and asked. None
        # before that, which is what every entity is built with.
        self.device_name = "SMC-PAD"
        self.manufacturer = "M-Vave"
        self.model = "SMC-PAD"
        self.battery: int | None = 82
        self.connected = connected

    def async_add_listener(self, listener: CALLBACK_TYPE) -> CALLBACK_TYPE:
        return lambda: None


class StubRunner:
    """Records what it was asked to do, and never touches a radio."""

    def __init__(self, view: SurfaceView = AT_HOME, connected: bool = True) -> None:
        self.coordinator = StubCoordinator(connected)
        self.view = view
        self.asked: list[str] = []

    def async_add_listener(self, listener: CALLBACK_TYPE) -> CALLBACK_TYPE:
        return lambda: None

    def drive(self, ask: Any) -> None:
        # The stub surface records the method rather than performing it, so a test can say
        # "this button went home" without an engine.
        self.asked.append(ask(_Recorder()))


class _Recorder:
    """Stands in for the engine, answering with the name of whatever was asked of it."""

    def go_home(self) -> str:
        return "home"

    def go_back(self) -> str:
        return "back"

    def navigate_to(self, page_id: str) -> str:
        return f"navigate:{page_id}"


# ------------------------------------------------------------------- labels


def test_pages_are_offered_by_the_name_a_person_gave_them() -> None:
    profile = Profile(
        pages={
            "home": Page("home", "Home", BLUE, source=Source(SourceKind.PAGES)),
            "kitchen": Page("kitchen", "Kitchen", GREEN),
        },
        root_id="home",
    )
    assert page_labels(profile) == (("home", "Home"), ("kitchen", "Kitchen"))


def test_a_room_called_home_does_not_collide_with_the_index() -> None:
    # Areas cannot share a name, so this only ever happens against the index's own title,
    # and a picker with two identical entries is one where choosing either does the same
    # thing.
    profile = Profile(
        pages={
            "home": Page("home", "Home", BLUE, source=Source(SourceKind.PAGES)),
            "home_area": Page("home_area", "Home", ORANGE),
        },
        root_id="home",
    )
    labels = page_labels(profile)
    assert labels == (("home", "Home"), ("home_area", "Home (home_area)"))
    assert len({label for _, label in labels}) == 2


# -------------------------------------------------------------------- page


def test_the_page_is_the_devices_main_feature_and_takes_its_name() -> None:
    select = MvavePageSelect(StubRunner())
    assert select.name is None
    assert select.has_entity_name is True


def test_the_page_offers_every_page_and_says_which_one_is_showing() -> None:
    select = MvavePageSelect(StubRunner(IN_THE_KITCHEN))
    assert select.options == ["Home", "Kitchen", "Living room"]
    assert select.current_option == "Kitchen"


def test_the_page_explains_itself_without_putting_it_in_the_database() -> None:
    select = MvavePageSelect(StubRunner(IN_THE_KITCHEN))
    assert select.extra_state_attributes == {
        "page_id": "kitchen",
        "parent_page_id": "home",
        "source": "area",
        "area_id": "kitchen",
        "depth": 1,
        "root_page_id": "home",
    }
    # The label is what a person picks and changes when a room is renamed; everything that
    # explains it is a debugging fact nobody charts.
    assert select.extra_state_attributes.keys() <= select._unrecorded_attributes


async def test_choosing_a_room_navigates_to_it() -> None:
    runner = StubRunner(AT_HOME)
    await MvavePageSelect(runner).async_select_option("Kitchen")
    assert runner.asked == ["navigate:kitchen"]


async def test_choosing_the_index_is_an_ordinary_navigation_here() -> None:
    # Pushing the root onto the stack would leave "back" going forwards into the room
    # somebody just left. This entity used to guard that on its own while `mvave.navigate`
    # did not; since 2026-09-15 the engine turns a navigation to the root into going home
    # for every caller (tests/test_engine_surface.py), so the entity just asks.
    runner = StubRunner(IN_THE_KITCHEN)
    await MvavePageSelect(runner).async_select_option("Home")
    assert runner.asked == ["navigate:home"]


async def test_choosing_something_that_is_not_a_page_does_nothing() -> None:
    runner = StubRunner(AT_HOME)
    await MvavePageSelect(runner).async_select_option("Greenhouse")
    assert runner.asked == []


def test_a_surface_with_no_link_has_no_page_and_no_options() -> None:
    select = MvavePageSelect(StubRunner(SurfaceView(), connected=False))
    assert select.available is False
    assert select.options == []
    assert select.current_option is None


# ------------------------------------------------------------------- focus


def test_focus_names_what_the_knobs_are_on() -> None:
    assert MvaveFocusSensor(StubRunner(IN_THE_KITCHEN)).native_value == "light.counter"


def test_focus_is_empty_when_the_knobs_follow_the_page() -> None:
    assert MvaveFocusSensor(StubRunner(AT_HOME)).native_value is None


# ----------------------------------------------------------------- buttons


@pytest.mark.parametrize(
    ("entity", "expected"),
    [(MvaveHomeButton, "home"), (MvaveBackButton, "back")],
)
async def test_the_two_buttons_a_page_may_never_rebind(entity: Any, expected: str) -> None:
    runner = StubRunner(IN_THE_KITCHEN)
    await entity(runner).async_press()
    assert runner.asked == [expected]


def test_every_surface_entity_belongs_to_the_one_device() -> None:
    runner = StubRunner()
    entities = [
        MvavePageSelect(runner),
        MvaveFocusSensor(runner),
        MvaveHomeButton(runner),
        MvaveBackButton(runner),
    ]
    identifiers = {
        tuple(sorted(entity.device_info["identifiers"]))  # type: ignore[index]
        for entity in entities
    }
    assert len(identifiers) == 1
    # Unique ids are per feature, not per device, or three of these would be one entity.
    assert len({entity.unique_id for entity in entities}) == len(entities)


# ------------------------------------------------------------------ the device


def test_the_device_card_carries_what_the_pad_says_about_itself() -> None:
    # None of this is on the advertisement: passive scanning never asks for the scan
    # response where a name would be, so before anything connects Home Assistant has a MAC
    # address and nothing else — which is what it was showing.
    info = MvaveFocusSensor(StubRunner()).device_info
    assert info is not None
    assert info["name"] == "SMC-PAD"
    assert info["manufacturer"] == "M-Vave"
    assert info["model"] == "SMC-PAD"


def test_the_battery_reports_what_the_device_last_said() -> None:
    battery = MvaveBatterySensor(StubCoordinator())
    assert battery.native_value == 82
    assert battery.device_class == SensorDeviceClass.BATTERY
    assert battery.native_unit_of_measurement == PERCENTAGE


def test_the_battery_says_nothing_rather_than_zero_before_it_has_been_asked() -> None:
    # Nothing has connected yet. Zero would be a claim, and an alarming one.
    coordinator = StubCoordinator()
    coordinator.battery = None
    assert MvaveBatterySensor(coordinator).native_value is None


def test_a_device_field_nobody_knows_yet_is_left_out_rather_than_sent_as_nothing() -> None:
    # DeviceInfo is a TypedDict, so `manufacturer=None` is a present key with a null value,
    # and the device registry writes anything that is not UNDEFINED. Entities are built
    # before anything has connected, so passing None wiped the manufacturer, model and name
    # a previous session had learned — on every restart, until the pad next connected, which
    # for a battery pad that is switched off may be never.
    entity = MvaveBatterySensor(StubCoordinator())  # type: ignore[arg-type]
    info = entity.device_info
    assert info is not None
    for field in ("manufacturer", "model", "name"):
        assert info.get(field)  # present, because this stub knows them

    nothing_known = StubCoordinator()
    nothing_known.manufacturer = None  # type: ignore[assignment]
    nothing_known.model = None  # type: ignore[assignment]
    blank = MvaveBatterySensor(nothing_known).device_info  # type: ignore[arg-type]
    assert blank is not None
    assert "manufacturer" not in blank
    assert "model" not in blank
