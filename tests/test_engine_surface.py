"""Navigating: where a press takes you, and which curtain covers the move.

Single presses are easy. What goes wrong on a control surface is sequences, which is the
whole reason the surface performs nothing itself and returns what it wants instead: a
hundred presses run in a millisecond here.
"""

from __future__ import annotations

from collections.abc import Sequence

import pytest
from engine.model import (
    Activate,
    EntityState,
    EventOnly,
    Navigate,
    PadConfig,
    Page,
    Profile,
    Service,
    Source,
    SourceKind,
    Toggle,
)
from engine.palette import BLUE, GREEN, ORANGE, PURPLE, UNASSIGNED, WHITE
from engine.render import BACK_BUTTON, HOME_BUTTON
from engine.surface import (
    ButtonPress,
    ButtonTiming,
    Idle,
    Press,
    Surface,
    Turn,
    pad_showing,
)


class FakeRegistry:
    """A world made of dictionaries."""

    def __init__(self, areas: dict[str, tuple[str, ...]], states: dict[str, str]) -> None:
        self._areas = areas
        self.states = states
        self.attributes: dict[str, dict[str, object]] = {}

    def entities_in_area(self, area_id: str) -> Sequence[str]:
        return self._areas.get(area_id, ())

    def entities_with_label(self, label: str) -> Sequence[str]:
        return ()

    def state_of(self, entity_id: str) -> EntityState | None:
        state = self.states.get(entity_id)
        if state is None:
            return None
        return EntityState(entity_id, state, self.attributes.get(entity_id, {}))


PROFILE = Profile(
    pages={
        "home": Page("home", "Home", BLUE, source=Source(SourceKind.PAGES)),
        "living": Page("living", "Living", ORANGE, source=Source(SourceKind.AREA, "living")),
        "kitchen": Page("kitchen", "Kitchen", GREEN, source=Source(SourceKind.AREA, "kitchen")),
        "office": Page("office", "Office", PURPLE, source=Source(SourceKind.AREA, "office")),
    },
    root_id="home",
)


def surface() -> Surface:
    registry = FakeRegistry(
        areas={"living": ("light.lamp", "switch.fan"), "kitchen": ("light.counter",)},
        states={"light.lamp": "off", "switch.fan": "on", "light.counter": "on"},
    )
    return Surface(PROFILE, registry)


# ------------------------------------------------------------------- the index


def test_the_root_lists_every_other_page_and_not_itself() -> None:
    view = surface()
    assert view.page.id == "home"
    assert [slot.tap for slot in view.slots() if slot] == [
        Navigate("living"),
        Navigate("kitchen"),
        Navigate("office"),
    ]


def test_there_is_nowhere_to_go_back_to_from_the_root() -> None:
    view = surface()
    assert view.rendering().buttons[BACK_BUTTON] is False
    assert view.handle(ButtonPress(BACK_BUTTON)).animation == ()


# ---------------------------------------------------------------- navigating


def test_pressing_a_room_goes_there_and_lights_the_way_out() -> None:
    view = surface()
    outcome = view.handle(Press(0))
    assert view.page.id == "living"
    assert view.depth == 1
    assert outcome.animation
    assert view.rendering().buttons[BACK_BUTTON] is True
    assert view.rendering().buttons[HOME_BUTTON] is True


def test_going_in_grows_out_of_the_pad_that_was_pressed() -> None:
    view = surface()
    # The kitchen sits on the second pad of the index, so its curtain starts there.
    first = view.handle(Press(1)).animation[0]
    assert first.count(GREEN) == 1
    assert first[1] == GREEN


def test_the_buttons_light_on_the_last_frame_going_in_and_the_first_coming_out() -> None:
    view = surface()
    assert view.handle(Press(0)).buttons is ButtonTiming.END
    assert view.handle(ButtonPress(BACK_BUTTON)).buttons is ButtonTiming.START


def test_coming_back_shrinks_into_the_pad_that_was_pressed_to_leave() -> None:
    view = surface()
    view.handle(Press(1))  # kitchen
    outcome = view.handle(ButtonPress(BACK_BUTTON))
    assert view.page.id == "home"
    # The last thing lit before the index settles is the kitchen's own pad.
    penultimate = outcome.animation[-2]
    assert penultimate.count(GREEN) == 1
    assert penultimate[1] == GREEN


def test_the_page_being_left_stays_lit_ahead_of_the_curtain() -> None:
    view = surface()
    before = view.rendering().frame
    first = view.handle(Press(0)).animation[0]
    # One pad has been covered; everything else is still the index.
    assert first[1:] == before[1:]


def test_an_animation_always_ends_on_the_page_it_was_going_to() -> None:
    view = surface()
    assert view.handle(Press(0)).animation[-1] == view.rendering().frame
    assert view.handle(ButtonPress(BACK_BUTTON)).animation[-1] == view.rendering().frame


def test_holding_back_goes_all_the_way_home() -> None:
    view = surface()
    view.handle(Press(0))
    view.handle(Press(0))  # a lamp; the stack does not move
    assert view.depth == 1
    view.handle(ButtonPress(BACK_BUTTON, held=True))
    assert view.page.id == "home"
    assert view.depth == 0


def test_the_stop_button_goes_home_from_anywhere() -> None:
    view = surface()
    view.handle(Press(0))
    assert view.handle(ButtonPress(HOME_BUTTON)).animation
    assert view.depth == 0


def test_navigating_to_where_you_already_are_does_nothing() -> None:
    view = surface()
    view.handle(Press(0))
    outcome = view.handle(Press(0))
    # Pad 0 of the living room is a lamp, not a navigation, so the stack must not move.
    assert view.depth == 1
    assert outcome.animation == ()


def test_navigating_to_a_page_that_does_not_exist_does_nothing() -> None:
    profile = Profile(
        pages={"home": Page("home", "Home", BLUE, pads={0: PadConfig(tap=Navigate("gone"))})},
        root_id="home",
    )
    view = Surface(profile, FakeRegistry({}, {}))
    assert view.handle(Press(0)).animation == ()
    assert view.depth == 0


def test_the_idle_timeout_goes_home_without_making_a_fuss() -> None:
    view = surface()
    view.handle(Press(0))
    outcome = view.handle(Idle())
    assert view.page.id == "home"
    # A plain wipe: no rings, because nothing happened and nobody pressed anything.
    assert len(outcome.animation) == 8
    assert outcome.buttons is ButtonTiming.START


def test_going_idle_at_home_is_not_an_event() -> None:
    assert surface().handle(Idle()).animation == ()


# ---------------------------------------------------------------- commanding


def test_pressing_a_lamp_asks_for_a_toggle_and_does_not_perform_it() -> None:
    view = surface()
    view.handle(Press(0))  # living
    outcome = view.handle(Press(0))
    assert [(call.domain, call.service) for call in outcome.calls] == [("light", "toggle")]
    assert outcome.calls[0].data == {"entity_id": "light.lamp"}
    # The registry is untouched: the engine asked, it did not act.
    assert view.registry.states["light.lamp"] == "off"


def test_a_commanded_pad_blinks_until_the_real_state_arrives() -> None:
    view = surface()
    view.handle(Press(0))
    view.handle(Press(0))
    assert view.rendering().rhythms
    view.settled("light.lamp")
    assert view.rendering().rhythms == {}


def test_holding_a_lamp_points_the_knobs_at_it() -> None:
    view = surface()
    view.handle(Press(0))
    view.handle(Press(0, held=True))
    assert view.focus == "light.lamp"


def test_a_scene_is_started_rather_than_toggled() -> None:
    profile = Profile(
        pages={"home": Page("home", "Home", BLUE, pads={0: PadConfig(tap=Activate("scene.x"))})},
        root_id="home",
    )
    outcome = Surface(profile, FakeRegistry({}, {})).handle(Press(0))
    assert [(call.domain, call.service) for call in outcome.calls] == [("scene", "turn_on")]


def test_an_event_only_pad_fires_an_event_and_calls_nothing() -> None:
    profile = Profile(
        pages={"home": Page("home", "Home", BLUE, pads={0: PadConfig(tap=EventOnly("coffee"))})},
        root_id="home",
    )
    outcome = Surface(profile, FakeRegistry({}, {})).handle(Press(0))
    assert outcome.calls == ()
    assert [emit.tag for emit in outcome.emits] == ["coffee"]


def test_pressing_an_empty_pad_does_nothing_at_all() -> None:
    view = surface()
    assert view.handle(Press(15)).animation == ()
    assert view.handle(Press(15)).calls == ()


def test_a_press_off_the_end_of_the_grid_is_ignored() -> None:
    view = surface()
    assert view.handle(Press(99)).calls == ()
    assert view.handle(Press(-1)).calls == ()


# ------------------------------------------------------------------- buttons


def test_a_page_can_use_the_three_buttons_it_owns() -> None:
    profile = Profile(
        pages={
            "home": Page(
                "home",
                "Home",
                BLUE,
                buttons={"play": Service("media_player", "media_play_pause", {"entity_id": "a"})},
            )
        },
        root_id="home",
    )
    view = Surface(profile, FakeRegistry({}, {}))
    assert [call.service for call in view.handle(ButtonPress("play")).calls] == ["media_play_pause"]


def test_a_page_cannot_rebind_back_or_home() -> None:
    profile = Profile(
        pages={
            "home": Page("home", "Home", BLUE, buttons={BACK_BUTTON: Toggle("light.a")}),
            "living": Page("living", "Living", ORANGE, source=Source(SourceKind.PAGES)),
        },
        root_id="home",
    )
    view = Surface(profile, FakeRegistry({}, {"light.a": "off"}))
    # It behaves as back, which from the root means nothing, and never as the toggle.
    assert view.handle(ButtonPress(BACK_BUTTON)).calls == ()


def test_an_unknown_button_is_ignored() -> None:
    assert surface().handle(ButtonPress("nonsense")).calls == ()


# --------------------------------------------------------------------- helper


def test_the_pad_a_page_sits_on_is_findable_and_absent_when_it_is_not_there() -> None:
    view = surface()
    assert pad_showing(view.slots(), "kitchen") == 1
    assert pad_showing(view.slots(), "nowhere") is None


# --------------------------------------------------------------------- focus


def test_holding_the_focused_pad_again_releases_it() -> None:
    # Without this there is no way out of focus, and a surface you can get into a state
    # you cannot get out of is one people stop trusting.
    view = surface()
    view.handle(Press(0))
    view.handle(Press(0, held=True))
    assert view.focus == "light.lamp"
    view.handle(Press(0, held=True))
    assert view.focus is None


def test_focus_does_not_follow_you_between_pages() -> None:
    view = surface()
    view.handle(Press(0))
    view.handle(Press(0, held=True))
    view.handle(ButtonPress(BACK_BUTTON))
    assert view.focus is None


def test_a_focused_pad_swings_between_on_and_off_rather_than_going_dark() -> None:
    view = surface()
    view.handle(Press(0))
    view.handle(Press(0, held=True))
    # Holding also peeks, so the bar is covering the page until it is taken away.
    view.clear_hud()
    motion = next(iter(view.rendering().rhythms.values()))
    # light.lamp starts off, so it alternates towards on.
    assert motion.other == ORANGE


# ------------------------------------------------------------- unreachable pads


def test_an_unreachable_pad_is_inert_rather_than_merely_unhelpful() -> None:
    # Commanding something that cannot answer would leave the pad moving forever, waiting
    # for a confirmation that is never coming.
    registry = FakeRegistry(areas={"living": ("light.gone",)}, states={"light.gone": "unavailable"})
    view = Surface(PROFILE, registry)
    view.handle(Press(0))
    outcome = view.handle(Press(0))
    assert outcome.calls == ()
    assert view.pending == set()


def test_an_unreachable_pad_cannot_be_focused_either() -> None:
    registry = FakeRegistry(areas={"living": ("light.gone",)}, states={"light.gone": "unavailable"})
    view = Surface(PROFILE, registry)
    view.handle(Press(0))
    view.handle(Press(0, held=True))
    assert view.focus is None
    assert view.rendering().rhythms == {}


def test_a_scene_stays_pressable_even_though_its_state_is_unknown() -> None:
    registry = FakeRegistry(
        areas={"kitchen": ("scene.dinner",)}, states={"scene.dinner": "unknown"}
    )
    view = Surface(PROFILE, registry)
    view.handle(Press(1))
    assert [call.service for call in view.handle(Press(0)).calls] == ["turn_on"]


# ---------------------------------------------------------------------- knobs


def lit_lamp() -> Surface:
    """A living room with one dimmable lamp on at half brightness."""
    registry = FakeRegistry(
        areas={"living": ("light.lamp",)},
        states={"light.lamp": "on"},
    )
    registry.attributes = {"light.lamp": {"brightness": 128}}
    view = Surface(PROFILE, registry)
    view.handle(Press(0))
    return view


def test_a_knob_with_nothing_to_point_at_does_nothing() -> None:
    view = surface()
    view.handle(Press(0))
    assert view.handle(Turn(1, 1)).calls == ()
    assert view.hud is None


def test_a_knob_pointed_at_something_without_that_property_is_inert() -> None:
    # Knob one is brightness everywhere. On a switch it does nothing rather than falling
    # back to something else, which would be the end of muscle memory.
    view = surface()
    view.handle(Press(0))
    view.handle(Press(1, held=True))  # focus switch.fan
    assert view.handle(Turn(1, 1)).calls == ()


def test_turning_a_knob_sets_a_value_and_puts_a_bar_on_the_grid() -> None:
    view = lit_lamp()
    view.handle(Press(0, held=True))
    outcome = view.handle(Turn(1, 2))
    call = outcome.calls[0]
    assert (call.domain, call.service) == ("light", "turn_on")
    # Half, plus two sixteenths, of 255.
    assert call.data["brightness"] == round((128 / 255 + 2 / 16) * 255)
    assert view.hud is not None and view.hud.property_key == "brightness"


def test_the_bar_covers_the_whole_page_and_does_not_move() -> None:
    view = lit_lamp()
    view.handle(Press(0, held=True))
    view.handle(Turn(1, 4))
    rendering = view.rendering()
    assert rendering.rhythms == {}
    assert set(rendering.frame) <= {WHITE, UNASSIGNED}


def test_a_knob_cannot_be_turned_past_either_end() -> None:
    view = lit_lamp()
    view.handle(Press(0, held=True))
    view.handle(Turn(1, 99))
    assert view.hud is not None and view.hud.value == 1.0
    view.handle(Turn(1, -99))
    assert view.hud is not None and view.hud.value == 0.0


def test_turning_a_knob_on_something_switched_off_starts_it_at_the_bottom() -> None:
    # Otherwise the first click adjusts a value nobody can see, and the second one is the
    # first that appears to do anything.
    registry = FakeRegistry(areas={"living": ("light.lamp",)}, states={"light.lamp": "off"})
    view = Surface(PROFILE, registry)
    view.handle(Press(0))
    view.handle(Press(0, held=True))
    view.handle(Turn(1, 1))
    assert view.hud is not None and view.hud.value == 1 / 16


def test_holding_a_pad_peeks_at_its_value_without_changing_it() -> None:
    view = lit_lamp()
    outcome = view.handle(Press(0, held=True))
    assert outcome.calls == ()
    assert view.hud is not None
    assert view.hud.value == 128 / 255


def test_the_bar_snaps_away_rather_than_animating() -> None:
    # It snaps on, so it snaps off. It also appears every time anybody touches a knob,
    # which is often enough that a transition would stop being a flourish.
    view = lit_lamp()
    view.handle(Press(0, held=True))
    outcome = view.clear_hud()
    assert view.hud is None
    assert outcome.animation == ()
    assert view.rendering() == view._page_rendering()


def test_clearing_a_bar_that_is_not_there_is_not_an_event() -> None:
    assert surface().clear_hud().animation == ()


def test_the_bar_does_not_follow_you_between_pages() -> None:
    view = lit_lamp()
    view.handle(Press(0, held=True))
    view.handle(ButtonPress(BACK_BUTTON))
    assert view.hud is None


def test_a_page_can_point_a_knob_somewhere_regardless_of_focus() -> None:
    # The one place a page beats the global rule: volume here always means this speaker.
    registry = FakeRegistry(
        areas={"living": ("light.lamp",)},
        states={"light.lamp": "on", "media_player.tv": "playing"},
    )
    registry.attributes = {"media_player.tv": {"volume_level": 0.5}}
    profile = Profile(
        pages={
            "home": Page("home", "Home", BLUE, source=Source(SourceKind.PAGES)),
            "living": Page(
                "living",
                "Living",
                ORANGE,
                source=Source(SourceKind.AREA, "living"),
                knobs={5: "media_player.tv"},
            ),
        },
        root_id="home",
    )
    view = Surface(profile, registry)
    view.handle(Press(0))
    call = view.handle(Turn(5, 1)).calls[0]
    assert (call.domain, call.service) == ("media_player", "volume_set")


def test_a_fast_turn_accumulates_even_though_the_entity_has_not_caught_up() -> None:
    # A knob sends around thirty messages a second and a lamp answers in a couple of
    # hundred milliseconds. Reading the entity on every step means most of a turn is
    # measured against a value that has not moved yet, so the bar crawls and then jumps
    # backwards when the answer lands. Once a bar is up it is the thing to count from.
    view = lit_lamp()
    view.handle(Press(0, held=True))
    for _ in range(4):
        view.handle(Turn(1, 1))
    assert view.hud is not None
    assert view.hud.value == pytest.approx(128 / 255 + 4 / 16)


def test_a_light_that_is_off_starts_at_the_bottom_and_then_climbs() -> None:
    registry = FakeRegistry(areas={"living": ("light.lamp",)}, states={"light.lamp": "off"})
    view = Surface(PROFILE, registry)
    view.handle(Press(0))
    view.handle(Press(0, held=True))
    view.handle(Turn(1, 1))
    view.handle(Turn(1, 1))
    # Not stuck at one step: the second click builds on the first, not on the entity,
    # which is still off as far as the registry is concerned.
    assert view.hud is not None
    assert view.hud.value == pytest.approx(2 / 16)
