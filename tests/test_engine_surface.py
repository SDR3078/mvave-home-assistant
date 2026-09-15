"""Navigating: where a press takes you, and which curtain covers the move.

Single presses are easy. What goes wrong on a control surface is sequences, which is the
whole reason the surface performs nothing itself and returns what it wants instead: a
hundred presses run in a millisecond here.
"""

from __future__ import annotations

from collections.abc import Sequence

import pytest
from engine.frames import expand, knob_pad, knobs_in_reading_order, wipe
from engine.model import (
    Activate,
    EntityState,
    EventOnly,
    Focus,
    Navigate,
    PadConfig,
    Page,
    Profile,
    Service,
    Source,
    SourceKind,
    Toggle,
    Watch,
)
from engine.palette import BLUE, GREEN, ORANGE, PURPLE, UNASSIGNED, WHITE, property_colour
from engine.properties import KNOB_COUNT
from engine.render import BACK_BUTTON, HOME_BUTTON
from engine.surface import (
    ButtonPress,
    ButtonRelease,
    ButtonTiming,
    Emit,
    EventType,
    Idle,
    Outcome,
    Press,
    Surface,
    Trigger,
    Turn,
    pad_showing,
)

#: What an ordinary entity of each domain says it can do. A fake without these is a bulb
#: that only switches, a blind that cannot be set to a position and a speaker with no
#: volume control — all real things, and all terrible defaults, because then every knob
#: test is really a test of the refusal. Values are Home Assistant's own: the colour modes
#: by name, and the feature bits from each domain's EntityFeature flag.
CAPABILITIES: dict[str, dict[str, object]] = {
    "light": {"supported_color_modes": ["hs", "color_temp"]},
    "media_player": {"supported_features": 4},  # VOLUME_SET
    "cover": {"supported_features": 4},  # SET_POSITION
    "climate": {"supported_features": 1},  # TARGET_TEMPERATURE
    "fan": {"supported_features": 1},  # SET_SPEED
}


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
        domain = entity_id.split(".", 1)[0]
        # What the test said wins; the capabilities only fill in what it did not mention.
        attributes = {**CAPABILITIES.get(domain, {}), **self.attributes.get(entity_id, {})}
        return EntityState(entity_id, state, attributes)


PROFILE = Profile(
    pages={
        "home": Page("home", "Home", BLUE, source=Source(SourceKind.PAGES)),
        "living": Page("living", "Living", ORANGE, source=Source(SourceKind.AREA, "living")),
        "kitchen": Page("kitchen", "Kitchen", GREEN, source=Source(SourceKind.AREA, "kitchen")),
        "office": Page("office", "Office", PURPLE, source=Source(SourceKind.AREA, "office")),
    },
    root_id="home",
)


#: The encoders in the order they are handed out: top left first, then across and down.
#: What each one adjusts depends on what is focused, so a test names the position it means
#: rather than a number, and stays true if the block is ever laid out differently.
ORDER = knobs_in_reading_order()

#: A colour lamp is the only thing in this house with more than one control, and its four
#: land on the first four encoders in this order.
BRIGHTNESS, COLOUR_TEMP, HUE, SATURATION = ORDER[:4]

#: Past a lamp's fourth control there is nothing left to hand out, so this one is dead on
#: any light — which is what makes it the encoder to turn when a test wants a refusal.
DEAD = ORDER[4]

#: Somewhere for a page to pin an encoder of its own, well clear of a lamp's four.
PINNED = ORDER[5]


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


def test_holding_back_opens_the_room_switcher_rather_than_going_home() -> None:
    # It used to go home, which the stop button already does. Two buttons for one thing is
    # a wasted gesture on a surface with five, and this is the one the switcher needs.
    view = surface()
    view.handle(Press(0))
    assert view.depth == 1
    view.handle(ButtonPress(BACK_BUTTON, held=True))
    assert view.shifted is True
    assert view.page.id == "living"  # it has not gone anywhere
    assert view.depth == 1


def test_letting_the_back_button_go_closes_the_switcher() -> None:
    view = surface()
    view.handle(ButtonPress(BACK_BUTTON, held=True))
    view.handle(ButtonRelease(BACK_BUTTON))
    assert view.shifted is False


def test_the_switcher_offers_the_rooms_across_the_top_and_nothing_else() -> None:
    view = surface()
    view.handle(ButtonPress(BACK_BUTTON, held=True))
    frame = view.rendering().frame
    # Three rooms in the profile, each in its own colour, and the rest of the grid dark so
    # that it plainly is not a page.
    assert frame[:3] == (ORANGE, GREEN, PURPLE)
    assert set(frame[3:]) == {UNASSIGNED}


def test_the_switcher_goes_straight_to_a_room_without_passing_home() -> None:
    view = surface()
    view.handle(Press(1))  # into the kitchen
    view.handle(ButtonPress(BACK_BUTTON, held=True))
    outcome = view.handle(Press(2))  # the third room across the top
    assert view.page.id == "office"
    assert view.shifted is False
    assert outcome.animation  # it arrives with a curtain like any other move


def test_the_switcher_pad_you_are_already_on_says_so() -> None:
    # Lit and doing nothing is never allowed to be silent on this surface.
    view = surface()
    view.handle(Press(0))  # into the living room, which is the first room
    view.handle(ButtonPress(BACK_BUTTON, held=True))
    outcome = view.handle(Press(0))
    assert outcome.animation  # a shudder, not a move
    assert view.page.id == "living"


def test_a_dark_pad_of_the_switcher_does_nothing_at_all() -> None:
    view = surface()
    view.handle(ButtonPress(BACK_BUTTON, held=True))
    outcome = view.handle(Press(9))  # well below the top row
    assert outcome == Outcome()
    assert view.shifted is True  # and the mode is still open


def test_letting_go_without_pressing_anything_is_a_no_op() -> None:
    view = surface()
    view.handle(Press(0))
    before = view.page.id
    view.handle(ButtonPress(BACK_BUTTON, held=True))
    view.handle(ButtonRelease(BACK_BUTTON))
    assert view.page.id == before
    assert view.rendering().frame == view._page_rendering().frame


def test_the_stop_button_is_still_the_way_home() -> None:
    view = surface()
    view.handle(Press(0))
    assert view.handle(ButtonPress(HOME_BUTTON)).animation
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


def test_the_idle_timeout_leaves_exactly_the_way_a_finger_would_have() -> None:
    # Frame for frame a back press, which is the point. Both documents used to promise a
    # quieter exit and the code used to ask for one — `animate=False`, never read, because
    # the branch that draws a curtain returned first. Made real on 2026-09-13 and judged at
    # the grid the same afternoon: "for this i actually want the animation back like
    # earlier". So the flag went instead, and the documents were corrected to match.
    #
    # What still separates the two is the event, not the light — see
    # test_what_moved_you_is_part_of_the_event. A room across the house should not
    # have to be watched to know whether anybody was standing at the grid.
    timed_out = surface()
    timed_out.handle(Press(0))
    outcome = timed_out.handle(Idle())
    assert timed_out.page.id == "home"

    pressed = surface()
    pressed.handle(Press(0))
    by_hand = pressed.handle(ButtonPress(BACK_BUTTON))
    assert outcome.animation == by_hand.animation
    assert outcome.animation  # and it is a curtain, not two empty tuples agreeing
    assert outcome.buttons is by_hand.buttons is ButtonTiming.START


def test_going_idle_at_home_is_not_an_event() -> None:
    assert surface().handle(Idle()).animation == ()


# ------------------------------------------------------------ resting on a page


def resting_in_the_living_room() -> Surface:
    """The same house, told to rest in the living room rather than on the index."""
    profile = Profile(pages=PROFILE.pages, root_id="home", default_page_id="living")
    return Surface(profile, surface().registry)


def test_a_default_page_is_where_the_surface_wakes_up() -> None:
    # Connect, and you are in the living room with the index behind you: back is lit and
    # goes there, and so does stop. Two roads home, one of them a step shorter.
    view = resting_in_the_living_room()
    assert view.stack == ["home", "living"]
    assert view.page.id == "living"
    assert view.rendering().buttons[BACK_BUTTON] is True
    assert view.rendering().buttons[HOME_BUTTON] is True


def test_the_timeout_returns_to_the_default_page_and_stop_to_the_index() -> None:
    # The owner's split, at the grid on 2026-09-15: "button behaviour the same, timeout and
    # connect go to the living room". Wander three rooms deep, go idle, and you are back
    # in the living room with only the index behind you.
    view = resting_in_the_living_room()
    view.handle(ButtonPress(BACK_BUTTON, held=True))
    view.handle(Press(1))  # the kitchen, through the switcher
    view.handle(ButtonPress(BACK_BUTTON, held=True))
    view.handle(Press(2))  # then the office
    assert view.stack == ["home", "living", "kitchen", "office"]

    before = view.rendering().frame
    outcome = view.handle(Idle())
    assert view.stack == ["home", "living"]
    entered = emitted(outcome)["page_entered"]
    assert entered["page_id"] == "living"
    assert entered["trigger"] == str(Trigger.IDLE)
    # A plain wipe in the living room's colour. Going back to the index shrinks into the
    # room's own pad, but no pad on the living room page is the office, so there is nothing
    # honest to shrink into.
    assert outcome.animation == wipe(ORANGE, before, view.rendering().frame)

    view.handle(ButtonPress(HOME_BUTTON))
    assert view.stack == ["home"]  # stop is the index regardless


def test_resting_on_the_default_page_the_timeout_does_nothing() -> None:
    outcome = resting_in_the_living_room().handle(Idle())
    assert outcome.animation == ()
    assert outcome.emits == ()


def test_the_index_goes_back_to_rest_too() -> None:
    # Whether the index *fires* a timeout is the runner's business — the registry gives it
    # one only when a default page is set. When it does, the surface goes back to rest.
    view = resting_in_the_living_room()
    view.handle(ButtonPress(HOME_BUTTON))
    view.handle(Idle())
    assert view.stack == ["home", "living"]


def test_a_default_page_that_no_longer_exists_means_the_index() -> None:
    profile = Profile(pages=PROFILE.pages, root_id="home", default_page_id="attic")
    assert profile.at_rest == ["home"]
    assert Surface(profile, surface().registry).stack == ["home"]


def test_a_rebuild_keeps_you_where_you_stood_even_when_rest_moved() -> None:
    # Choosing a default page rebuilds the surface. Somebody standing in the kitchen stays
    # in the kitchen; only the next timeout goes to the new resting page.
    old = surface()
    old.handle(Press(1))  # kitchen
    fresh = resting_in_the_living_room()
    old.carry_into(fresh)
    assert fresh.stack == ["home", "kitchen"]
    fresh.handle(Idle())
    assert fresh.stack == ["home", "living"]


def test_at_rest_the_timeout_lets_go_of_a_forgotten_hold() -> None:
    # Hold the lamp on the resting page and walk away. Before the room page timed out to
    # the index, which cleared the focus; resting there, the timeout returned nothing at
    # all, and the knobs stayed on that lamp — with the breathe — until the next finger.
    view = resting_in_the_living_room()
    view.handle(Press(0, held=True))
    assert view.focus == "light.lamp"
    outcome = view.handle(Idle())
    assert view.focus is None
    assert outcome.animation == ()  # nothing moved, so nothing is drawn
    cleared = emitted(outcome)["focus_cleared"]
    assert cleared == {"entity_id": "light.lamp", "trigger": str(Trigger.IDLE)}
    assert view.stack == ["home", "living"]


def test_timing_out_on_the_resting_page_reached_through_other_rooms_is_silent() -> None:
    # Living -> kitchen -> living, by the switcher. The page you are on *is* the resting
    # page, so the history simply goes: no curtain over a page that did not change, and no
    # announcement of leaving and entering it, which an automation would act on.
    view = resting_in_the_living_room()
    view.handle(ButtonPress(BACK_BUTTON, held=True))
    view.handle(Press(1))  # kitchen
    view.handle(ButtonPress(BACK_BUTTON, held=True))
    view.handle(Press(0))  # living again
    assert view.stack == ["home", "living", "kitchen", "living"]
    outcome = view.handle(Idle())
    assert view.stack == ["home", "living"]
    assert outcome.emits == ()
    assert outcome.animation == ()


def test_the_timeout_waits_while_the_switcher_is_held() -> None:
    # A finger on the back button is not idleness. Left alone, thirty seconds of holding
    # moved the page under the hand and the next tap landed on a live control.
    view = surface()
    view.handle(Press(0))
    view.handle(ButtonPress(BACK_BUTTON, held=True))
    assert view.shifted is True
    outcome = view.handle(Idle())
    assert view.shifted is True
    assert view.stack == ["home", "living"]
    assert outcome.animation == () and outcome.emits == ()


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
    # Carrying the trigger like every other event, so an automation acting on a tag can
    # still tell its own effect from a finger.
    assert Emit(EventType.TAGGED, {"tag": "coffee", "trigger": "pad"}) in outcome.emits


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


def test_an_unreachable_pad_refuses_rather_than_pretending() -> None:
    # Commanding something that cannot answer would leave the pad moving forever, waiting
    # for a confirmation that is never coming. It says so under the finger instead, which
    # is the only moment anybody can act on it.
    registry = FakeRegistry(areas={"living": ("light.gone",)}, states={"light.gone": "unavailable"})
    view = Surface(PROFILE, registry)
    view.handle(Press(0))
    outcome = view.handle(Press(0))
    assert outcome.calls == ()
    assert view.pending == set()
    assert outcome.animation
    # It shudders and then puts everything back exactly as it was.
    assert outcome.animation[-1] == view.rendering().frame


def test_a_refusal_only_touches_the_pad_that_was_pressed() -> None:
    registry = FakeRegistry(
        areas={"living": ("light.gone", "light.fine")},
        states={"light.gone": "unavailable", "light.fine": "on"},
    )
    view = Surface(PROFILE, registry)
    view.handle(Press(0))
    before = view.rendering().frame
    for frame in view.handle(Press(0)).animation:
        assert frame[1:] == before[1:]
        assert frame[0] in (before[0], UNASSIGNED)


def test_an_unreachable_pad_cannot_be_focused_either() -> None:
    registry = FakeRegistry(areas={"living": ("light.gone",)}, states={"light.gone": "unavailable"})
    view = Surface(PROFILE, registry)
    view.handle(Press(0))
    view.handle(Press(0, held=True))
    assert view.focus is None


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
    assert view.handle(Turn(BRIGHTNESS, 1)).calls == ()
    assert view.hud is None


def test_a_knob_pointed_at_something_without_that_property_is_inert() -> None:
    # Knob one is brightness everywhere. On a switch it does nothing rather than falling
    # back to something else, which would be the end of muscle memory.
    view = surface()
    view.handle(Press(0))
    view.handle(Press(1, held=True))  # focus switch.fan
    assert view.handle(Turn(BRIGHTNESS, 1)).calls == ()


def test_turning_a_knob_sets_a_value_and_puts_a_bar_on_the_grid() -> None:
    view = lit_lamp()
    view.handle(Press(0, held=True))
    outcome = view.handle(Turn(BRIGHTNESS, 2))
    call = outcome.calls[0]
    assert (call.domain, call.service) == ("light", "turn_on")
    # Half, plus two sixteenths, of 255.
    assert call.data["brightness"] == round((128 / 255 + 2 / 16) * 255)
    assert view.hud is not None and view.hud.property_key == "brightness"


def test_the_bar_covers_the_whole_page_and_does_not_move() -> None:
    view = lit_lamp()
    view.handle(Press(0, held=True))
    view.handle(Turn(BRIGHTNESS, 4))
    rendering = view.rendering()
    assert rendering.rhythms == {}
    # A run in the property's own colour on a dark track, and nothing of the page left.
    assert set(rendering.frame) <= {property_colour("brightness"), UNASSIGNED}


def test_the_bar_is_the_colour_the_map_promised_for_that_encoder() -> None:
    # The map is a promise about the bar you get if you turn that encoder, which is what
    # makes a colour the property's name rather than a decoration. One table serves both.
    view = lamp_that(["hs", "color_temp"])
    for knob, (_, key) in view.knob_map().items():
        view.legend = True
        on_the_map = view.rendering().frame[knob_pad(knob)]
        view.handle(Turn(knob, 1))
        in_the_bar = {pad for pad in view.rendering().frame if pad != UNASSIGNED}
        assert in_the_bar == {on_the_map}, key


def test_a_knob_cannot_be_turned_past_either_end() -> None:
    view = lit_lamp()
    view.handle(Press(0, held=True))
    view.handle(Turn(BRIGHTNESS, 99))
    assert view.hud is not None and view.hud.value == 1.0
    view.handle(Turn(BRIGHTNESS, -99))
    assert view.hud is not None and view.hud.value == 0.0


def test_turning_a_knob_on_something_switched_off_starts_it_at_the_bottom() -> None:
    # Otherwise the first click adjusts a value nobody can see, and the second one is the
    # first that appears to do anything.
    registry = FakeRegistry(areas={"living": ("light.lamp",)}, states={"light.lamp": "off"})
    view = Surface(PROFILE, registry)
    view.handle(Press(0))
    view.handle(Press(0, held=True))
    view.handle(Turn(BRIGHTNESS, 1))
    assert view.hud is not None and view.hud.value == 1 / 16


def in_colour_mode() -> Surface:
    """A lamp that is on, showing a colour, and so reporting no colour temperature at all.

    Not a contrivance: it is what Home Assistant does. ``light/__init__.py`` derives
    ``hs_color`` from a colour temperature but sets ``color_temp_kelvin`` to None whenever
    the light is in any other mode, and never derives it, because most colours have no
    meaningful temperature. So this is every coloured bulb whose hue has been touched.
    """
    registry = FakeRegistry(areas={"living": ("light.lamp",)}, states={"light.lamp": "on"})
    registry.attributes = {
        "light.lamp": {
            "color_mode": "hs",
            "brightness": 128,
            "hs_color": (200, 80),
            "color_temp_kelvin": None,
        }
    }
    view = Surface(PROFILE, registry)
    view.handle(Press(0))
    view.handle(Press(0, held=True))
    return view


def test_a_knob_on_a_value_the_entity_will_not_report_does_not_go_to_the_bottom() -> None:
    # Found at the grid — "i feel that the color_temp is resetting" — and it was. This fell
    # into the branch above and started at a sixteenth, because one branch was answering
    # for both "off" and "on but quiet". They are not the same situation: off has no
    # visible value, while this one has a visible light that simply will not name this
    # property. Starting at the bottom of the range threw it to deep orange every time.
    view = in_colour_mode()
    view.handle(Turn(COLOUR_TEMP, 1))
    assert view.hud is not None
    # The middle of this lamp's own range — a guess, and named as one in the code. It is
    # only reachable because this lamp has not named a colour temperature once.
    assert view.hud.value == 0.5 + 1 / 16


def test_holding_a_pad_learns_everything_the_state_is_naming_not_just_the_bar() -> None:
    # Holding a pad had the lamp's whole state in its hand and kept one property out of it.
    # So looking straight at a colour temperature and then giving the lamp a colour left
    # the knob guessing at a number it had been shown a minute earlier. Nobody turns the
    # temperature knob first — you hold the pad to see where things are.
    registry = FakeRegistry(areas={"living": ("light.lamp",)}, states={"light.lamp": "on"})
    registry.attributes = {
        "light.lamp": {"color_mode": "color_temp", "brightness": 128, "color_temp_kelvin": 4000}
    }
    view = Surface(PROFILE, registry)
    view.handle(Press(0))
    view.handle(Press(0, held=True))  # a hold, and nothing else

    assert view.last_known["light.lamp", "brightness"] == 128 / 255
    assert view.last_known["light.lamp", "color_temp"] == 2000 / 4500

    # Now somebody gives it a colour, and the knob still knows where white was.
    registry.attributes["light.lamp"] = {
        "color_mode": "hs",
        "brightness": 128,
        "hs_color": (200, 80),
        "color_temp_kelvin": None,
    }
    view.clear_hud()
    view.handle(Turn(COLOUR_TEMP, 1))
    assert view.hud is not None and view.hud.value == 2000 / 4500 + 1 / 16


def test_a_knob_resumes_from_the_last_value_the_house_reported() -> None:
    # The middle is only for a property the house has never named. Once it has named one,
    # that reading is where the knob is, and it stays there after the entity stops naming
    # it — which is the whole case, since a lamp stops naming its colour temperature the
    # moment somebody gives it a colour.
    registry = FakeRegistry(areas={"living": ("light.lamp",)}, states={"light.lamp": "on"})
    registry.attributes = {
        "light.lamp": {
            "color_mode": "color_temp",
            "brightness": 128,
            "color_temp_kelvin": 4000,  # 0.4 of the way up the default 2000-6500 span
        }
    }
    view = Surface(PROFILE, registry)
    view.handle(Press(0))
    view.handle(Press(0, held=True))
    view.handle(Turn(COLOUR_TEMP, 1))
    view.clear_hud()

    # Somebody gives it a colour, so it now reports no colour temperature at all.
    registry.attributes["light.lamp"] = {
        "color_mode": "hs",
        "brightness": 128,
        "hs_color": (200, 80),
        "color_temp_kelvin": None,
    }
    view.handle(Turn(COLOUR_TEMP, 1))

    # 4000 K is (4000 - 2000) / (6500 - 2000) of the way up the default span.
    assert view.hud is not None
    assert view.hud.value == 2000 / 4500 + 1 / 16


def test_a_knob_does_not_resume_from_something_this_surface_merely_asked_for() -> None:
    # The point of the whole mechanism, and the owner's correction on 2026-09-13: a command
    # is an intention, not a fact. It can be clamped, ignored, or land on a lamp somebody
    # else is already moving, so resuming from it would put the knob where the house has
    # never been. Here nothing the surface sends is ever confirmed — the registry does not
    # move — so four clicks up must leave no trace at all.
    view = in_colour_mode()
    view.handle(Turn(COLOUR_TEMP, 4))
    assert view.hud is not None and view.hud.value == 0.5 + 4 / 16

    view.clear_hud()
    view.handle(Turn(COLOUR_TEMP, 1))

    # Brightness *is* remembered, because holding the pad read it off the house. Colour
    # temperature is not, because the house never named it — only this surface did.
    assert ("light.lamp", "brightness") in view.last_known
    assert ("light.lamp", "color_temp") not in view.last_known
    assert view.hud is not None and view.hud.value == 0.5 + 1 / 16


def test_the_entity_still_wins_over_what_the_knob_remembers() -> None:
    # The memory exists for one case only: on, and will not say. It must never override
    # what the house actually reports, or a value changed anywhere else would be silently
    # undone by the next click.
    view = in_colour_mode()
    view.handle(Turn(COLOUR_TEMP, 4))
    view.clear_hud()

    # Somebody set it elsewhere, so now the light is in colour-temperature mode and says so.
    view.registry.attributes["light.lamp"] = {  # type: ignore[attr-defined]
        "color_mode": "color_temp",
        "brightness": 128,
        "color_temp_kelvin": 2000,  # the bottom of the default span
    }
    view.handle(Turn(COLOUR_TEMP, 1))
    assert view.hud is not None and view.hud.value == 1 / 16


def test_holding_a_pad_peeks_at_its_value_without_changing_it() -> None:
    view = lit_lamp()
    outcome = view.handle(Press(0, held=True))
    assert outcome.calls == ()
    assert view.hud is not None
    assert view.hud.value == 128 / 255
    # And not the knob map: that is an answer to a question, and nobody has asked one yet.
    assert view.legend is False


def test_a_value_nobody_can_read_leaves_whatever_is_up_alone() -> None:
    # A lamp that cannot dim has no value to show, and clearing the grid to say so would
    # throw away an answer that may already be on it.
    view = lamp_that(["onoff"])
    view.handle(Turn(BRIGHTNESS, 1))  # every knob is dead here, so the map goes up
    assert view.legend is True
    view.peek("light.lamp")
    assert view.legend is True
    assert view.hud is None


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
                knobs={PINNED: "media_player.tv"},
            ),
        },
        root_id="home",
    )
    view = Surface(profile, registry)
    view.handle(Press(0))
    call = view.handle(Turn(PINNED, 1)).calls[0]
    assert (call.domain, call.service) == ("media_player", "volume_set")


def test_a_fast_turn_accumulates_even_though_the_entity_has_not_caught_up() -> None:
    # A knob sends around thirty messages a second and a lamp answers in a couple of
    # hundred milliseconds. Reading the entity on every step means most of a turn is
    # measured against a value that has not moved yet, so the bar crawls and then jumps
    # backwards when the answer lands. Once a bar is up it is the thing to count from.
    view = lit_lamp()
    view.handle(Press(0, held=True))
    for _ in range(4):
        view.handle(Turn(BRIGHTNESS, 1))
    assert view.hud is not None
    assert view.hud.value == pytest.approx(128 / 255 + 4 / 16)


def test_a_light_that_is_off_starts_at_the_bottom_and_then_climbs() -> None:
    registry = FakeRegistry(areas={"living": ("light.lamp",)}, states={"light.lamp": "off"})
    view = Surface(PROFILE, registry)
    view.handle(Press(0))
    view.handle(Press(0, held=True))
    view.handle(Turn(BRIGHTNESS, 1))
    view.handle(Turn(BRIGHTNESS, 1))
    # Not stuck at one step: the second click builds on the first, not on the entity,
    # which is still off as far as the registry is concerned.
    assert view.hud is not None
    assert view.hud.value == pytest.approx(2 / 16)


# ----------------------------------------------------------------- announcing


def emitted(outcome: object) -> dict[str, dict]:
    """Every event an outcome announced, by type."""
    return {str(emit.type): dict(emit.data) for emit in outcome.emits}  # type: ignore[attr-defined]


def test_going_into_a_room_announces_leaving_and_arriving_separately() -> None:
    # One event per transition and never batched, so an automation can trigger on exactly
    # one thing rather than unpicking a list.
    view = surface()
    events = emitted(view.handle(Press(0)))
    assert events["page_exited"]["page_id"] == "home"
    assert events["page_entered"]["page_id"] == "living"
    assert events["page_entered"]["previous"] == "home"
    assert events["page_entered"]["depth"] == 1


def test_a_page_event_says_which_depth_each_of_the_two_pages_was_at() -> None:
    # `page_exited` announces the depth of the page you left, `page_entered` the depth of
    # the one you arrived at. Obvious, and wrong until 2026-09-13: the departed page was
    # captured before the move and its depth read after it, as `self.depth + 1`. That is
    # the right answer for exactly one kind of move — a single step back — which is the
    # only kind anybody had checked, so leaving the index announced it at depth 2.
    #
    # The stack is a history rather than a tree: every page's parent is the root, and the
    # switcher deliberately pushes, so back walks the rooms you visited and the stop button
    # is the one press to the index. Depth is therefore how far you have wandered, and
    # these are the four ways it changes.
    view = surface()

    going_in = emitted(view.handle(Press(0)))  # home -> living
    assert going_in["page_exited"]["depth"] == 0
    assert going_in["page_entered"]["depth"] == 1

    sideways = emitted(  # living -> office, without passing home
        (view.handle(ButtonPress(BACK_BUTTON, held=True)), view.handle(Press(2)))[1]
    )
    assert sideways["page_exited"]["depth"] == 1
    assert sideways["page_entered"]["depth"] == 2
    assert view.stack == ["home", "living", "office"]

    coming_back = emitted(view.handle(ButtonPress(BACK_BUTTON)))  # office -> living
    assert coming_back["page_exited"]["depth"] == 2
    assert coming_back["page_entered"]["depth"] == 1

    view.handle(ButtonPress(BACK_BUTTON, held=True))
    view.handle(Press(1))  # wander again, so home is a jump rather than a step
    assert view.depth == 2
    straight_home = emitted(view.handle(ButtonPress(HOME_BUTTON)))
    assert straight_home["page_exited"]["depth"] == 2
    assert straight_home["page_entered"]["depth"] == 0


def test_a_page_event_carries_enough_to_act_on_without_asking_anything_else() -> None:
    events = emitted(surface().handle(Press(0)))
    entered = events["page_entered"]
    assert entered["page_title"] == "Living"
    assert entered["page_source"] == "area"
    assert entered["area_id"] == "living"


def test_what_moved_you_is_part_of_the_event() -> None:
    # A presence sensor pre-selecting a room and a finger pressing a pad are not the same
    # thing, and an automation that cannot tell them apart will loop.
    view = surface()
    assert emitted(view.handle(Press(0)))["page_entered"]["trigger"] == str(Trigger.PAD)
    assert emitted(view.handle(ButtonPress(BACK_BUTTON)))["page_entered"]["trigger"] == str(
        Trigger.BUTTON
    )
    assert emitted(view.navigate_to("kitchen"))["page_entered"]["trigger"] == str(Trigger.SERVICE)
    assert emitted(view.handle(Idle()))["page_entered"]["trigger"] == str(Trigger.IDLE)


def test_every_press_is_announced_even_when_the_pad_does_nothing() -> None:
    # "Pad 5 was held" is exactly the thing somebody wants to hang an automation on,
    # whether or not the engine itself had anything to do with it.
    view = surface()
    view.handle(Press(0))
    # A pad with nothing on it stays silent, though. There is nothing to announce, and an
    # event for every dead pad would make the useful ones harder to find.
    assert emitted(view.handle(Press(15))) == {}
    # A frame index, zero based and in reading order, which is the only numbering this
    # package has: what is printed on a pad is a fact about one piece of hardware, and
    # nothing in `engine/` imports a device. `runner.as_printed` converts it on the way to
    # the bus, so an automation sees 14 where this sees 1. Do not "correct" these.
    pressed = emitted(view.handle(Press(1)))
    assert pressed["pad_pressed"]["pad"] == 1
    assert pressed["pad_pressed"]["entity_id"] == "switch.fan"
    held = emitted(view.handle(Press(0, held=True)))
    assert held["pad_held"]["pad"] == 0


def test_focus_announces_both_taking_and_releasing() -> None:
    view = lit_lamp()
    assert emitted(view.handle(Press(0, held=True)))["focus_set"]["entity_id"] == "light.lamp"
    assert emitted(view.handle(Press(0, held=True)))["focus_cleared"]["entity_id"] == "light.lamp"


def test_a_knob_turn_says_what_it_changed_and_to_what() -> None:
    view = lit_lamp()
    view.handle(Press(0, held=True))
    turned = emitted(view.handle(Turn(BRIGHTNESS, 2)))["knob_turned"]
    assert turned["knob"] == BRIGHTNESS
    assert turned["steps"] == 2
    assert turned["property"] == "brightness"
    assert turned["entity_id"] == "light.lamp"


def test_the_ways_in_from_outside_move_the_surface() -> None:
    view = surface()
    assert view.navigate_to("kitchen").animation
    assert view.page.id == "kitchen"
    view.focus_on("light.counter")
    assert view.focus == "light.counter"
    assert view.go_home().animation
    assert view.depth == 0


def test_navigating_from_outside_still_grows_from_the_page_s_own_pad() -> None:
    # Not an invented origin: the page lives on that pad, and it is the one a finger
    # would have used. What caused the move is in the event's trigger, not in the grid.
    view = surface()
    first = view.navigate_to("kitchen").animation[0]
    assert first[1] == GREEN
    assert first.count(GREEN) == 1


def test_a_page_with_nowhere_to_grow_from_gets_a_plain_wipe() -> None:
    # From inside the living room the kitchen is not on the grid at all, so there is no
    # pad that could honestly be the origin.
    #
    # Asserted against the real wipe rather than against its first frame. A wipe and a
    # spiral out of pad 0 both light pad 0 first, so the old assertion passed either way:
    # deleting the rule this test is named after left the whole suite green.
    view = surface()
    view.handle(Press(0))
    before = view.rendering().frame
    animation = view.navigate_to("kitchen").animation
    after = view.rendering().frame
    assert animation == wipe(GREEN, before, after)
    assert animation != expand(0, GREEN, before, after)


def scene_page() -> Surface:
    """A page with a scene beside a lamp, which is the arrangement that matters."""
    registry = FakeRegistry(
        areas={"living": ("light.lamp", "scene.evening")},
        states={"light.lamp": "on", "scene.evening": "unknown"},
    )
    view = Surface(PROFILE, registry)
    view.handle(Press(0))
    return view


def test_pressing_a_scene_holds_its_pad_rather_than_leaving_it_silent() -> None:
    # A scene has no on and no off, so pressing one used to change nothing anywhere: the
    # one pad on this surface that could be pressed with no result of any kind. Raised at
    # the grid — "i actually want a feedback mechanism after i touched the button".
    view = scene_page()
    assert view.rendering().frame[1] == PURPLE  # the scene, at rest

    outcome = view.handle(Press(1))
    assert outcome.calls[0].service == "turn_on"
    assert outcome.acknowledged == ("scene.evening",)
    assert view.rendering().frame[1] == ORANGE  # holding, to say it ran


def test_an_acknowledgement_is_a_latch_and_never_a_flash() -> None:
    # The distinction the whole design turns on. A flash is a *pair* of opposing changes;
    # this is one transition in and one out, separated by however long the caller holds it,
    # so it never enters the photosensitivity arithmetic at all. It also adds no rhythm: a
    # third moving thing would have to be told apart from the two that already move.
    view = scene_page()
    view.handle(Press(1))
    assert view.rendering().rhythms == {}

    view.release("scene.evening")
    assert view.rendering().frame[1] == PURPLE


def test_an_acknowledgement_never_shows_white() -> None:
    # Not decoration. Purple is the default for stateless domains *because* those pads
    # never go white, purple against white being the one pair the hardware notes record as
    # too close to tell apart. An acknowledgement that flashed white would land the whole
    # signal on the weakest pair the palette can produce.
    view = scene_page()
    view.handle(Press(1))
    assert view.rendering().frame[1] != WHITE


def test_the_rest_of_the_grid_keeps_reporting_while_a_pad_is_held() -> None:
    # Which is why this is a latch on one pad and not an animation over the grid. A scene
    # that switches lamps on should be watchable doing it; freezing sixteen pads for a
    # second and a half would hide the very thing the scene just did.
    view = scene_page()
    view.handle(Press(1))
    assert view.rendering().frame[0] == ORANGE  # the lamp, on

    view.registry.states["light.lamp"] = "off"  # type: ignore[attr-defined]
    assert view.rendering().frame[0] == WHITE  # it moved, mid-acknowledgement
    assert view.rendering().frame[1] == ORANGE  # and the scene is still holding


def test_a_script_says_it_ran_the_same_way_a_scene_does() -> None:
    # It used to blink and settle like a lamp, because a script has a running state. Showing
    # that state meant purple while running against white while idle, which is the one pair
    # measured as too close to tell apart — so the pad spent the colour nobody can read on a
    # flash that is usually over in well under a second. It is drawn stateless now, and
    # acknowledged like everything else you press once.
    registry = FakeRegistry(areas={"living": ("script.bedtime",)}, states={"script.bedtime": "off"})
    view = Surface(PROFILE, registry)
    view.handle(Press(0))
    outcome = view.handle(Press(0))

    assert outcome.calls[0].domain == "script"
    assert outcome.acknowledged == ("script.bedtime",)
    assert view.rendering().frame[0] == ORANGE  # holding, to say it ran


def test_an_unreachable_script_still_refuses_even_though_it_is_drawn_stateless() -> None:
    # The seam. How a pad is *drawn* and whether it can be *reached* stopped being one
    # question the moment a script stopped showing its running state. A scene rests at
    # "unknown" for ever, so refusing it would make it unpressable; a script rests at "off",
    # so one saying "unavailable" really is unreachable and has to say so under a finger.
    registry = FakeRegistry(
        areas={"living": ("script.gone", "scene.never_run")},
        states={"script.gone": "unavailable", "scene.never_run": "unknown"},
    )
    view = Surface(PROFILE, registry)
    view.handle(Press(0))
    # Laid out by DOMAIN_ORDER, which puts scenes before scripts.
    refused = view.handle(Press(1))
    assert refused.calls == ()
    assert refused.animation and refused.reaction is True

    ran = view.handle(Press(0))
    assert ran.calls[0].domain == "scene"


def test_a_pad_that_would_do_nothing_shudders_rather_than_looking_broken() -> None:
    # A lit pad that does nothing when pressed is indistinguishable from a broken one.
    # This was found by pressing a thermostat, which the design had deliberately given no
    # tap action, and watching it sit there.
    profile = Profile(
        pages={"home": Page("home", "Home", BLUE, pads={0: PadConfig(hold=Focus("light.a"))})},
        root_id="home",
    )
    view = Surface(profile, FakeRegistry({}, {"light.a": "on"}))
    outcome = view.handle(Press(0))
    assert outcome.calls == ()
    assert outcome.animation
    assert outcome.animation[-1] == view.rendering().frame
    # Holding it still works, so the shudder is about the tap and nothing else.
    view.handle(Press(0, held=True))
    assert view.focus == "light.a"


def test_a_refusal_says_it_is_a_reaction_so_it_cannot_restart_itself() -> None:
    # Three blinks in 540 ms is 5.6 Hz, legal only because three is the most a thing may
    # flash in one second. Restarting one partway through puts more than three there — and
    # pressing a dead pad twice is exactly what somebody does when the first press looked
    # like it did nothing. The engine cannot enforce that, having no clock, so it says which
    # animations are reactions and the runner declines to restart one with another.
    registry = FakeRegistry(areas={"living": ("light.gone",)}, states={"light.gone": "unavailable"})
    view = Surface(PROFILE, registry)
    going_in = view.handle(Press(0))
    # A page change is not a reaction: pressing a second room mid-curtain means it.
    assert going_in.animation and going_in.reaction is False

    refusal = view.handle(Press(0))
    assert refusal.animation and refusal.reaction is True


def test_a_readout_pad_shows_its_state_and_shudders_when_pressed() -> None:
    # A pad is allowed to tell you something nothing can change. It keeps the whole
    # language — its colour when the door is open, white when it is shut — and answers a
    # press the only honest way it can. Silence would be indistinguishable from a flat
    # battery, a dropped link, or a finger that missed.
    profile = Profile(
        pages={
            "home": Page(
                "home",
                "Home",
                BLUE,
                pads={0: PadConfig(tap=Watch("binary_sensor.back_door"), colour=GREEN)},
            )
        },
        root_id="home",
    )
    view = Surface(profile, FakeRegistry({}, {"binary_sensor.back_door": "on"}))
    assert view.rendering().frame[0] == GREEN

    outcome = view.handle(Press(0))
    assert outcome.calls == ()  # it cannot act, and does not pretend to
    assert outcome.animation  # but it says so
    assert outcome.animation[-1] == view.rendering().frame  # and puts the grid back

    # Holding it is no different: there is nothing behind it for a knob to adjust.
    assert view.handle(Press(0, held=True)).calls == ()
    assert view.focus is None


# ------------------------------------------------------------ reconfiguring


def test_a_stack_can_be_carried_across_a_rebuild() -> None:
    # What reconfiguring does. Changing a colour and being thrown back to the index is the
    # surface losing your place over something that had nothing to do with where you were.
    was = surface()
    was.handle(Press(0))
    assert was.page.id == "living"

    rebuilt = Surface(PROFILE, was.registry)
    was.carry_into(rebuilt)
    assert rebuilt.page.id == "living"


def test_a_rebuild_keeps_what_belongs_to_the_house_and_drops_what_belongs_to_a_finger() -> None:
    # The line this draws is what the whole method is for. Where somebody is standing, what
    # the knobs are on and where a knob was left are facts about the house, and an edit to
    # a colour is not a reason to lose any of them. A held shift, a bar that is up and a
    # legend are facts about a gesture, and the rebuild has ended the gesture.
    #
    # Asserted together because the failure mode is not getting one of them wrong, it is
    # adding state to Surface and never thinking about this at all.
    was = lit_lamp()
    was.handle(Press(0, held=True))
    was.handle(Turn(BRIGHTNESS, 2))
    was.pending.add("light.lamp")
    was.shifted = True
    was.legend = True

    rebuilt = Surface(PROFILE, was.registry)
    was.carry_into(rebuilt)

    assert rebuilt.stack == was.stack
    assert rebuilt.focus == "light.lamp"
    assert rebuilt.pending == {"light.lamp"}
    assert rebuilt.last_known == was.last_known != {}

    assert rebuilt.shifted is False
    assert rebuilt.legend is False
    assert rebuilt.hud is None


def test_what_a_rebuild_carries_is_copied_rather_than_shared() -> None:
    # Otherwise the surface that was thrown away is still holding the live objects, and a
    # late callback arriving on it — a knob's confirmation, say — writes into the one that
    # replaced it.
    was = lit_lamp()
    was.pending.add("light.lamp")
    was.last_known["light.lamp", "brightness"] = 0.5

    rebuilt = Surface(PROFILE, was.registry)
    was.carry_into(rebuilt)
    was.pending.clear()
    was.last_known.clear()

    assert rebuilt.pending == {"light.lamp"}
    assert rebuilt.last_known == {("light.lamp", "brightness"): 0.5}


def test_a_page_that_stopped_existing_drops_you_home() -> None:
    was = surface()
    was.handle(Press(2))  # office
    smaller = Profile(
        pages={
            "home": Page("home", "Home", BLUE, source=Source(SourceKind.PAGES)),
            "living": Page("living", "Living", ORANGE, source=Source(SourceKind.AREA, "living")),
        },
        root_id="home",
    )
    rebuilt = Surface(smaller, was.registry)
    was.carry_into(rebuilt)
    assert rebuilt.page.id == "home"
    assert rebuilt.depth == 0


def test_a_bar_follows_the_entity_while_it_is_up() -> None:
    # Somebody moving the same lamp from a phone while you are holding its pad. Being
    # shown a stale value is the kind of thing that makes people stop trusting a display.
    view = lit_lamp()
    view.handle(Press(0, held=True))
    assert view.hud is not None and view.hud.value == 128 / 255

    view.registry.attributes["light.lamp"] = {"brightness": 255}
    view.settled("light.lamp")
    assert view.hud is not None and view.hud.value == 1.0


def test_a_bar_ignores_an_entity_it_is_not_showing() -> None:
    view = lit_lamp()
    view.handle(Press(0, held=True))
    before = view.hud
    view.settled("light.somewhere_else")
    assert view.hud == before


def test_a_bar_does_not_follow_backwards_while_the_knob_is_still_turning() -> None:
    # Commands are rationed on the way out, so what arrives mid-turn is where the knob was
    # a moment ago. Following it would drag the bar backwards under the finger.
    view = lit_lamp()
    view.handle(Press(0, held=True))
    view.handle(Turn(BRIGHTNESS, 4))
    asked = view.hud.value if view.hud else None

    view.registry.attributes["light.lamp"] = {"brightness": 128}  # the older value landing
    view.settled("light.lamp", follow=False)
    assert view.hud is not None and view.hud.value == asked


# ------------------------------------------------- which knobs are live, and saying so


def lamp_that(modes: list[str] | None, state: str = "on") -> Surface:
    """A living room with one lamp declaring exactly these colour modes, focused."""
    registry = FakeRegistry(areas={"living": ("light.lamp",)}, states={"light.lamp": state})
    registry.attributes = {"light.lamp": {"supported_color_modes": modes or []}}
    view = Surface(PROFILE, registry)
    view.handle(Press(0))  # into the living room
    view.handle(Press(0, held=True))  # point the knobs at the lamp
    return view


def test_a_full_colour_lamp_lights_up_four_knobs_and_the_spare() -> None:
    view = lamp_that(["hs", "color_temp"])
    assert view.knob_map() == {
        BRIGHTNESS: ("light.lamp", "brightness"),
        COLOUR_TEMP: ("light.lamp", "color_temp"),
        HUE: ("light.lamp", "hue"),
        SATURATION: ("light.lamp", "saturation"),
    }


def test_a_bulb_that_only_switches_has_no_live_knob_at_all() -> None:
    # A light with no brightness is a real thing, and the domain alone cannot tell you.
    # Before capabilities were checked, this reported all five and sent a brightness that
    # the bulb could only round to "on".
    assert lamp_that(["onoff"]).knob_map() == {}


def test_a_lamp_with_no_white_leds_loses_exactly_one_knob() -> None:
    # RGBWW carries a brightness and a colour but no colour temperature, so the colour
    # temperature encoder is dead on this lamp and live on the one beside it. Nothing on
    # the device says so, and nothing else can say it either.
    live = lamp_that(["rgbww"]).knob_map()
    # Three controls instead of four, so they close up: what was the colour temperature
    # encoder now carries hue, and the fourth encoder goes dark.
    assert live == {
        BRIGHTNESS: ("light.lamp", "brightness"),
        COLOUR_TEMP: ("light.lamp", "hue"),
        HUE: ("light.lamp", "saturation"),
    }
    assert "color_temp" not in {key for _, key in live.values()}


def test_a_lamp_that_says_nothing_about_itself_is_treated_as_unable() -> None:
    # Home Assistant's own helpers answer False for an empty set, and claiming a capability
    # nobody declared is how you end up sending a brightness into the dark.
    assert lamp_that(None).knob_map() == {}


def test_a_knob_that_cannot_touch_what_is_selected_answers_with_the_map() -> None:
    # "Not that one" is the wrong answer to somebody who is searching. The useful reply
    # names the ones that work, and it is a still frame rather than a flash.
    view = lamp_that(["hs", "color_temp"])
    view.clear_hud()
    outcome = view.handle(Turn(DEAD, 1))  # past a lamp's four controls, so nothing
    assert outcome.calls == ()
    assert outcome.animation == ()
    assert view.legend is True


def test_a_live_knob_replaces_the_map_with_its_value() -> None:
    view = lamp_that(["hs", "color_temp"])
    outcome = view.handle(Turn(BRIGHTNESS, 1))
    assert outcome.animation == ()
    assert outcome.calls
    assert view.legend is False
    assert view.hud is not None and view.hud.property_key == "brightness"


#: Where each knob lands on the grid, in knob order. Two across and four up, numbered from
#: the bottom left, matching the encoders themselves.
KNOB_PADS = (12, 13, 8, 9, 4, 5, 0, 1)


def test_the_map_names_the_live_knobs_in_the_colour_of_what_they_adjust() -> None:
    view = lamp_that(["hs", "color_temp"])
    view.handle(Turn(DEAD, 1))  # a dead encoder puts the map up
    frame = view.rendering().frame
    live = {knob for knob, _ in view.knob_map().items()}
    # The map agrees with the map: every live encoder lit in the thing's own colour, every
    # dead one white, each in the place its encoder actually occupies.
    for knob in range(1, KNOB_COUNT + 1):
        assert (frame[knob_pad(knob)] != WHITE) is (knob in live), knob
    # And on a colour light that means the first four encoders and nothing else.
    assert live == {BRIGHTNESS, COLOUR_TEMP, HUE, SATURATION}


def test_a_knob_with_nothing_selected_stays_silent_rather_than_shuddering() -> None:
    # On an index page nothing is focused and every knob is dead. Shuddering there would
    # fire whenever somebody brushed an encoder, which reads as a fault on a page where
    # nothing is wrong.
    view = surface()
    outcome = view.handle(Turn(BRIGHTNESS, 1))
    assert outcome.animation == ()
    assert outcome.calls == ()


def test_a_knob_pointed_at_something_unreachable_answers_the_same_way() -> None:
    # Every knob is dead, so the map is all white, which is true and is the answer.
    view = lamp_that(["hs", "color_temp"])
    view.registry.states["light.lamp"] = "unavailable"
    assert view.handle(Turn(BRIGHTNESS, 1)).animation == ()
    assert view.legend is True
    assert {view.rendering().frame[pad] for pad in KNOB_PADS} == {WHITE}


def test_holding_a_lamp_that_cannot_dim_refuses_rather_than_breathing_over_nothing() -> None:
    # This used to focus it: "the pad breathes and the gesture is not silent. There is
    # simply no value to show, which is the truth." Reversed on 2026-09-13 by review, and
    # the reversal is the more literal reading of the same rule. The breathe is this
    # surface's one way of saying *the knobs are on this pad*, and on a lamp that only
    # switches there is no knob it could mean — so the breathe was the untrue part, and the
    # shudder this surface already has for "pressing this does nothing" is the true one.
    view = lamp_that(["onoff"])
    assert view.focus is None
    assert view.hud is None


def test_a_page_pointing_a_knob_elsewhere_is_reported_against_that_entity() -> None:
    registry = FakeRegistry(
        areas={"living": ("light.lamp",)},
        states={"light.lamp": "on", "media_player.speaker": "playing"},
    )
    profile = Profile(
        pages={
            "living": Page(
                "living",
                "Living",
                ORANGE,
                source=Source(SourceKind.AREA, "living"),
                knobs={PINNED: "media_player.speaker"},
            )
        },
        root_id="living",
    )
    view = Surface(profile, registry)
    view.handle(Press(0, held=True))
    live = view.knob_map()
    assert live[BRIGHTNESS] == ("light.lamp", "brightness")
    # Volume follows the page's speaker rather than the lamp that has focus.
    assert live[PINNED] == ("media_player.speaker", "volume")


# ------------------------------------------------- what a press actually asks for


def test_a_lock_pad_locks_and_unlocks_rather_than_opening_the_door() -> None:
    # It called `lock.open` on every press until 2026-09-13. That is not a toggle and not
    # even an unlock: it retracts the latch. So the pad opened the front door, opened it
    # again on a second press, and nothing anywhere on this surface locked anything — on a
    # pad that locks reach by auto-fill, with nobody having opted in.
    for state, expected in (("locked", "unlock"), ("unlocked", "lock")):
        registry = FakeRegistry(areas={"living": ("lock.front",)}, states={"lock.front": state})
        view = Surface(PROFILE, registry)
        view.handle(Press(0))
        call = view.handle(Press(0)).calls[0]
        assert (call.domain, call.service) == ("lock", expected)
        assert call.service != "open"


def test_a_knob_uses_a_value_it_can_read_even_when_the_thing_is_switched_off() -> None:
    # "Switched off" was standing in for "there is no value to read". That is true of a
    # lamp's brightness and false of a thermostat's setpoint, which Home Assistant reports
    # perfectly well while the heating is off — so one click replaced a 21 degree setpoint
    # with 7.5 and left it there for the next time the heating came on.
    registry = FakeRegistry(areas={"living": ("climate.hall",)}, states={"climate.hall": "off"})
    registry.attributes = {
        "climate.hall": {
            "supported_features": 1,
            "temperature": 21.0,
            "min_temp": 7.0,
            "max_temp": 35.0,
        }
    }
    view = Surface(PROFILE, registry)
    view.handle(Press(0))
    view.focus = "climate.hall"
    call = view.handle(Turn(BRIGHTNESS, 1)).calls[0]
    assert call.service == "set_temperature"
    assert call.data["temperature"] > 21.0  # it went up from where it was, not down to the floor


def test_a_knob_on_something_off_with_nothing_to_read_still_starts_at_the_bottom() -> None:
    # The other half, unchanged: a lamp that is off reports no brightness at all, so there
    # is genuinely nothing to continue from and the first click has to make it visible.
    registry = FakeRegistry(areas={"living": ("light.lamp",)}, states={"light.lamp": "off"})
    view = Surface(PROFILE, registry)
    view.handle(Press(0))
    view.handle(Press(0, held=True))
    view.handle(Turn(BRIGHTNESS, 1))
    assert view.hud is not None and view.hud.value == 1 / 16


def test_holding_a_blind_that_cannot_be_positioned_refuses() -> None:
    # The hold was granted by domain alone, so anything in FOCUSABLE_DOMAINS breathed —
    # including the very common blind that only opens and closes and reports no
    # SET_POSITION. The pad then showed this surface's one signal for "the knobs are on
    # this" while no encoder could move anything, and the map that would have explained it
    # only appears once you turn one.
    registry = FakeRegistry(areas={"living": ("cover.garage",)}, states={"cover.garage": "open"})
    registry.attributes = {"cover.garage": {"supported_features": 0}}  # no SET_POSITION
    view = Surface(PROFILE, registry)
    view.handle(Press(0))

    outcome = view.handle(Press(0, held=True))
    assert view.focus is None
    assert view.rendering().rhythms == {}
    assert outcome.animation and outcome.reaction is True


def test_pointing_the_knobs_at_something_nobody_can_reach_refuses_too() -> None:
    # Same gate, the other reason: §5.2 says an unreachable pad is completely still, and
    # focus was the one route that could make one breathe.
    registry = FakeRegistry(areas={"living": ("light.gone",)}, states={"light.gone": "unavailable"})
    view = Surface(PROFILE, registry)
    view.handle(Press(0))

    assert view.handle(Press(0, held=True)).reaction is True
    assert view.focus is None


def test_a_knob_turned_before_anything_is_held_draws_the_map_on_a_room_page() -> None:
    # The state the device is in the first time anybody touches it, and again in every new
    # room. Eight unmarked encoders answered nothing at all, which is what a dead knob also
    # looks like. The map says "none of these, yet", which is true and is the only thing on
    # this device that can say it.
    registry = FakeRegistry(areas={"living": ("light.lamp",)}, states={"light.lamp": "on"})
    view = Surface(PROFILE, registry)
    view.handle(Press(0))

    assert view.focus is None
    assert view.handle(Turn(BRIGHTNESS, 1)).animation or view.legend


def test_a_knob_turned_on_the_index_still_says_nothing() -> None:
    # The half the old guard got right: nothing can ever be focused on an index, so a map
    # here would fire every time somebody brushed an encoder against a page with no answer.
    view = surface()
    assert view.page.source.kind is SourceKind.PAGES
    assert view.handle(Turn(BRIGHTNESS, 1)) == Outcome()


def test_every_event_says_what_caused_it() -> None:
    # The README promises a `trigger` on all of them "so an automation can never mistake
    # its own effect for a person". Four carried none: focus_set, focus_cleared, tagged and
    # knob_turned — so an automation triggering on focus_set and calling mvave.focus
    # re-triggered itself, which is the exact loop the promise is about.
    registry = FakeRegistry(areas={"living": ("light.lamp",)}, states={"light.lamp": "on"})
    view = Surface(PROFILE, registry)
    view.handle(Press(0))

    set_focus = view.focus_on("light.lamp")
    assert set_focus.emits[0].data["trigger"] == "service"
    cleared = view.focus_on("light.lamp")  # the same gesture lets it go
    assert cleared.emits[-1].data["trigger"] == "service"

    view.handle(Press(0, held=True))  # a finger, on the same entity
    assert view.handle(Turn(BRIGHTNESS, 1)).emits[0].data["trigger"] == "pad"


def test_a_press_takes_the_grid_back_from_the_bar() -> None:
    # Both documents say a pad press cancels the value bar. It did not: the toggle happened
    # underneath a grid still covered by the bar, the pressed pad showed nothing, and the
    # bar's expiry was rearmed by that very press — so tapping pads kept it up for ever.
    registry = FakeRegistry(
        areas={"living": ("light.lamp", "switch.fan")},
        states={"light.lamp": "on", "switch.fan": "on"},
    )
    registry.attributes = {"light.lamp": {"brightness": 128}}
    view = Surface(PROFILE, registry)
    view.handle(Press(0))
    view.handle(Press(0, held=True))
    view.handle(Turn(BRIGHTNESS, 2))
    assert view.showing  # the bar is up

    view.handle(Press(1))  # tap the switch beside it
    assert not view.showing
    # And the page is back, rather than sixteen pads of bar.
    assert view.rendering().frame == view._page_rendering().frame
