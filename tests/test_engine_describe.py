"""Saying what the grid means, for everybody who did not lay it out.

The thing under test is the one question the hardware itself can never answer. Sixteen
unlabelled pads showing five colours are learnable by the person who configured them and
opaque to everybody else, and a room page fills itself from the live registry, so not even
the configuration says what is on it. These tests are mostly about the cases where the pad
and the description deliberately disagree: a pad nobody can reach looks exactly like one
that is off, and is the whole reason somebody would ask.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from engine.describe import ACTION, EMPTY, OFF, ON, UNREACHABLE, action_name, describe_page
from engine.model import (
    Activate,
    EntityState,
    EventOnly,
    Focus,
    Home,
    Navigate,
    Nothing,
    PadConfig,
    Page,
    Profile,
    Service,
    Source,
    SourceKind,
    Toggle,
)
from engine.palette import BLUE, GREEN, ORANGE, PURPLE
from engine.surface import Surface, Trigger


class FakeRegistry:
    """A world made of dictionaries."""

    def __init__(
        self,
        areas: Mapping[str, Sequence[str]] | None = None,
        states: Mapping[str, str] | None = None,
        attributes: Mapping[str, Mapping[str, Any]] | None = None,
    ) -> None:
        self._areas = dict(areas or {})
        self._states = dict(states or {})
        self._attributes = dict(attributes or {})

    def entities_in_area(self, area_id: str) -> Sequence[str]:
        return self._areas.get(area_id, ())

    def entities_with_label(self, label: str) -> Sequence[str]:
        return ()

    def state_of(self, entity_id: str) -> EntityState | None:
        state = self._states.get(entity_id)
        if state is None:
            return None
        return EntityState(entity_id, state, self._attributes.get(entity_id, {}))


PROFILE = Profile(
    pages={
        "home": Page("home", "Home", BLUE, source=Source(SourceKind.PAGES)),
        "living": Page("living", "Living", ORANGE, source=Source(SourceKind.AREA, "living")),
        "kitchen": Page("kitchen", "Kitchen", GREEN, source=Source(SourceKind.AREA, "kitchen")),
    },
    root_id="home",
)

REGISTRY = FakeRegistry(
    areas={
        "living": ("light.lamp", "switch.fan", "scene.evening", "media_player.tv"),
        "kitchen": ("light.counter", "climate.ecobee", "cover.blind"),
    },
    states={
        "light.lamp": "on",
        "switch.fan": "off",
        "scene.evening": "unknown",
        "media_player.tv": "unavailable",
        "light.counter": "on",
        "climate.ecobee": "heat",
        "cover.blind": "open",
    },
    attributes={
        # Dimmable, so holding it has something to point the knobs at. A lamp with no
        # colour modes at all has no adjustable property, and holding one now refuses.
        "light.lamp": {"friendly_name": "Reading lamp", "supported_color_modes": ["brightness"]},
        # A temperature *range* rather than a setpoint: ClimateEntityFeature is
        # TARGET_TEMPERATURE_RANGE (2), not TARGET_TEMPERATURE (1). Home Assistant's own
        # demo Ecobee is exactly this, which is how the defect below was found.
        "climate.ecobee": {"supported_features": 2, "target_temp_low": 21, "target_temp_high": 24},
        # A blind that opens and closes but cannot be sent to a position: no SET_POSITION.
        "cover.blind": {"supported_features": 0},
    },
)


def described(page_id: str = "living") -> dict[str, Any]:
    page = PROFILE.pages[page_id]
    return describe_page(page, REGISTRY, PROFILE)


def slot(page_id: str, number: int) -> dict[str, Any]:
    return described(page_id)["slots"][number - 1]


def slot_for(page_id: str, entity_id: str) -> dict[str, Any]:
    """The pad a given entity landed on.

    By entity rather than by position on purpose. A page fills itself in domain order, so
    hard-coding a position here would be asserting the layout rule twice and would break
    these tests every time it was tuned.
    """
    found = next(entry for entry in described(page_id)["slots"] if entry["entity_id"] == entity_id)
    return found


# ------------------------------------------------------------------- one pad


def test_pads_are_numbered_the_way_a_person_counts_them() -> None:
    slots = described()["slots"]
    assert len(slots) == 16
    assert [entry["slot"] for entry in slots[:3]] == [1, 2, 3]


def test_a_lamp_that_is_on_says_so_and_carries_the_name_on_the_wall() -> None:
    assert slot_for("living", "light.lamp") == {
        "slot": 1,
        "entity_id": "light.lamp",
        "name": "Reading lamp",
        "tap": "toggle",
        "hold": "focus",
        "to_page": None,
        "shows": ON,
        "colour": "orange",
    }


def test_something_without_a_friendly_name_falls_back_to_its_id() -> None:
    assert slot_for("living", "switch.fan")["name"] == "switch.fan"


def test_a_switch_that_is_off_says_off() -> None:
    assert slot_for("living", "switch.fan")["shows"] == OFF


def test_a_scene_has_nothing_to_be_on_or_off_about() -> None:
    # "unknown" is a scene's resting state, and calling that unreachable would be wrong.
    entry = slot_for("living", "scene.evening")
    assert entry["shows"] == ACTION
    assert entry["tap"] == "activate"


def test_an_unreachable_pad_is_the_one_thing_the_grid_cannot_say() -> None:
    # On the device this is white, identical to "off", because a colour reserved for it
    # would cost a fifth of the vocabulary. Here it is the difference worth asking about.
    entry = slot_for("living", "media_player.tv")
    assert entry["shows"] == UNREACHABLE
    assert entry["colour"] == "white"


def test_the_page_lays_itself_out_in_the_order_a_person_would_reach_for_things() -> None:
    # Not incidental to the descriptions: this is the order somebody reading a cheat sheet
    # has to be able to match against the pads in front of them.
    assert [entry["entity_id"] for entry in described("living")["slots"][:4]] == [
        "light.lamp",
        "media_player.tv",
        "switch.fan",
        "scene.evening",
    ]


def test_an_empty_pad_is_dark_and_says_nothing() -> None:
    entry = slot("kitchen", 16)
    assert entry == {
        "slot": 16,
        "entity_id": None,
        "name": None,
        "tap": "nothing",
        "hold": "nothing",
        "to_page": None,
        "shows": EMPTY,
        "colour": "dark",
    }


def test_a_hold_that_would_refuse_is_reported_as_nothing() -> None:
    # This service exists to say what a pad would *do*, and `hold` used to say what the pad
    # was configured as. A focusable domain is not enough: a thermostat with a 21-24 range
    # has no single temperature for an encoder to hold, so the grid refuses the hold with
    # three blinks while this said `focus`. Found by holding the demo Ecobee on 2026-09-13.
    #
    # `shows: unreachable` has always warned that a *tap* will refuse. This is the same
    # warning for a hold, which is the half that was missing.
    thermostat = slot_for("kitchen", "climate.ecobee")
    assert thermostat["tap"] == "toggle"  # it can still be switched
    assert thermostat["hold"] == "nothing"
    assert thermostat["shows"] == ON  # and it is perfectly reachable, which is the point

    blind = slot_for("kitchen", "cover.blind")
    assert blind["hold"] == "nothing"  # opens and closes, but goes to no position

    # And a lamp that can be dimmed still says focus, or this would be reporting "nothing"
    # for everything and passing for the wrong reason.
    assert slot_for("living", "light.lamp")["hold"] == "focus"


def test_a_navigation_pad_says_where_it_goes() -> None:
    entry = slot("home", 1)
    assert entry["tap"] == "navigate"
    assert entry["to_page"] == "living"
    assert entry["shows"] == ACTION
    assert entry["colour"] == "orange"


def test_every_action_has_a_name_that_will_not_move_under_somebody() -> None:
    assert action_name(Toggle("light.a")) == "toggle"
    assert action_name(Navigate("kitchen")) == "navigate"
    assert action_name(Activate("scene.a")) == "activate"
    assert action_name(Focus("light.a")) == "focus"
    assert action_name(Home()) == "home"
    assert action_name(Service("light", "turn_on")) == "service"
    assert action_name(EventOnly("tag")) == "event"
    assert action_name(Nothing()) == "nothing"


# ------------------------------------------------------------------ one page


def test_a_page_says_what_it_is_and_where_it_came_from() -> None:
    page = described("living")
    assert page["page_id"] == "living"
    assert page["title"] == "Living"
    assert page["colour"] == "orange"
    assert page["source"] == "area"
    assert page["area_id"] == "living"


def test_an_index_has_no_area_behind_it() -> None:
    page = described("home")
    assert page["source"] == "pages"
    assert page["area_id"] is None


def test_a_pad_pinned_by_hand_is_described_with_its_own_colour() -> None:
    profile = Profile(
        pages={
            "living": Page(
                "living",
                "Living",
                ORANGE,
                source=Source(SourceKind.AREA, "living"),
                pads={0: PadConfig(tap=Toggle("light.lamp"), colour=PURPLE)},
            )
        },
        root_id="living",
    )
    entry = describe_page(profile.pages["living"], REGISTRY, profile)["slots"][0]
    assert entry["entity_id"] == "light.lamp"
    assert entry["colour"] == "purple"


# --------------------------------------------------------- driving from outside


def test_a_pressed_pad_and_a_service_pressed_pad_differ_only_in_who_asked() -> None:
    from engine.surface import Press

    by_finger = Surface(PROFILE, REGISTRY)
    by_service = Surface(PROFILE, REGISTRY)

    pressed = by_finger.handle(Press(0))
    driven = by_service.press(0)

    assert by_finger.page.id == by_service.page.id == "living"
    assert pressed.animation == driven.animation
    assert pressed.emits[-1].data["trigger"] == str(Trigger.PAD)
    assert driven.emits[-1].data["trigger"] == str(Trigger.SERVICE)


def test_a_service_can_hold_a_pad_too() -> None:
    view = Surface(PROFILE, REGISTRY)
    view.press(0)  # into the living room
    view.press(0, held=True)  # hold the lamp
    assert view.focus == "light.lamp"


def test_back_from_outside_goes_back_one_page_and_no_further() -> None:
    view = Surface(PROFILE, REGISTRY)
    view.press(0)
    assert view.depth == 1
    view.go_back()
    assert view.page.id == "home"
    assert view.depth == 0
    # Nothing underneath the root to pop.
    assert view.go_back().animation == ()


def test_pressing_a_pad_that_does_not_exist_does_nothing_rather_than_raising() -> None:
    view = Surface(PROFILE, REGISTRY)
    assert view.press(99).animation == ()
    assert view.page.id == "home"
