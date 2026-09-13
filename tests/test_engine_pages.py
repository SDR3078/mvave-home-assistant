"""Pages: what fills them, and what the grid shows once they are filled.

The engine reaches Home Assistant only through the two protocols in ``engine.ports``, so
everything here runs against a dictionary. That is the point of the seam: questions like
"what does the grid do when three lamps in a room go unreachable" are one line to ask,
and would otherwise need a running instance and a flat battery.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

import pytest
from engine.frames import PAD_COUNT
from engine.model import (
    INERT_ACTIONS,
    NOTHING,
    Activate,
    EntityState,
    EventOnly,
    Focus,
    Navigate,
    Nothing,
    PadConfig,
    Page,
    Profile,
    Service,
    Slot,
    Source,
    SourceKind,
    Toggle,
    Watch,
)
from engine.palette import BLUE, GREEN, ON, ORANGE, PURPLE, STATE_OFF, UNASSIGNED
from engine.render import (
    BACK_BUTTON,
    HOME_BUTTON,
    Rendering,
    ViewState,
    changed_pads,
    colour_of,
    compose,
    render,
)
from engine.resolve import default_actions, resolve
from engine.rhythms import ALERT, BREATHE, Motion


class FakeRegistry:
    """A world made of dictionaries."""

    def __init__(
        self,
        areas: Mapping[str, Sequence[str]] | None = None,
        labels: Mapping[str, Sequence[str]] | None = None,
        states: Mapping[str, str] | None = None,
    ) -> None:
        self._areas = dict(areas or {})
        self._labels = dict(labels or {})
        self._states = dict(states or {})

    def entities_in_area(self, area_id: str) -> Sequence[str]:
        return self._areas.get(area_id, ())

    def entities_with_label(self, label: str) -> Sequence[str]:
        return self._labels.get(label, ())

    def state_of(self, entity_id: str) -> EntityState | None:
        state = self._states.get(entity_id)
        return None if state is None else EntityState(entity_id, state)


def page(page_id: str = "living", **kwargs: object) -> Page:
    """A page with the boring fields filled in."""
    defaults: dict[str, object] = {"title": page_id.title(), "colour": ORANGE}
    defaults.update(kwargs)
    return Page(id=page_id, **defaults)  # type: ignore[arg-type]


EMPTY = Profile(pages={}, root_id="home")


# ------------------------------------------------------------------- entity state


@pytest.mark.parametrize(
    ("entity_id", "state", "active"),
    [
        ("light.a", "on", True),
        ("light.a", "off", False),
        ("switch.a", "on", True),
        ("cover.a", "open", True),
        ("cover.a", "opening", True),
        ("cover.a", "closed", False),
        ("media_player.a", "playing", True),
        ("media_player.a", "paused", True),
        ("media_player.a", "idle", False),
        ("media_player.a", "off", False),
        ("climate.a", "heat", True),
        ("climate.a", "off", False),
        ("lock.a", "unlocked", True),
        ("lock.a", "locked", False),
    ],
)
def test_active_follows_the_conventions_of_each_domain(
    entity_id: str, state: str, active: bool
) -> None:
    assert EntityState(entity_id, state).is_active is active


@pytest.mark.parametrize("state", ["unavailable", "unknown", ""])
def test_an_entity_nobody_can_reach_is_neither_on_nor_off(state: str) -> None:
    # Rendering it as off would be a claim. A pad that says a lamp is off when nobody
    # knows is worse than one that admits it does not know.
    entity = EntityState("light.a", state)
    assert entity.is_opaque
    assert not entity.is_active


# ------------------------------------------------------------------------- slots


def test_a_slot_knows_whose_state_it_shows() -> None:
    assert Slot(tap=Toggle("light.a")).entity_id == "light.a"
    assert Slot(tap=NOTHING, hold=Focus("light.b")).entity_id == "light.b"
    assert Slot(tap=Navigate("kitchen")).entity_id is None
    assert Slot().entity_id is None


def test_a_scene_pad_is_stateless_but_a_lamp_is_not() -> None:
    assert Slot(tap=Activate("scene.evening")).is_stateless
    assert not Slot(tap=Toggle("light.a")).is_stateless
    assert Slot(tap=EventOnly("coffee")).is_stateless
    assert not Slot().is_stateless


# ---------------------------------------------------------------- default actions


@pytest.mark.parametrize(
    ("entity_id", "tap_type"),
    [
        ("light.a", Toggle),
        ("switch.a", Toggle),
        ("input_boolean.a", Toggle),
        ("scene.a", Activate),
        ("script.a", Activate),
        ("media_player.a", Service),
        ("cover.a", Service),
        ("climate.a", Toggle),
    ],
)
def test_a_pad_does_the_obvious_thing_for_what_is_behind_it(entity_id: str, tap_type: type) -> None:
    tap, _ = default_actions(entity_id)
    assert isinstance(tap, tap_type)


def test_holding_anything_a_knob_could_adjust_focuses_it() -> None:
    for entity_id in ("light.a", "media_player.a", "cover.a", "climate.a", "fan.a"):
        _, hold = default_actions(entity_id)
        assert hold == Focus(entity_id)
    # And holding something a knob cannot adjust does nothing rather than something odd.
    _, hold = default_actions("scene.a")
    assert isinstance(hold, Nothing)


@pytest.mark.parametrize(
    "entity_id", ["sensor.kitchen_temperature", "weather.home", "number.setpoint"]
)
def test_a_pad_never_pretends_it_can_control_something_it_cannot(entity_id: str) -> None:
    # This used to fall through to a plain toggle, so pinning a device tracker to a pad gave
    # a lit pad that called ``homeassistant.toggle`` on a phone and did nothing. Found by
    # doing exactly that and pressing it. Nothing is the honest answer for anything the grid
    # can neither act on nor show: 21.5 degrees is not a colour, and a pad that sat white
    # forever would be lying about it. Dark, and a shudder under the finger.
    tap, hold = default_actions(entity_id)
    assert isinstance(tap, Nothing)
    assert isinstance(hold, Nothing)

    slot = Slot(tap=tap, hold=hold)
    assert slot.entity_id is None
    assert colour_of(slot, FakeRegistry()) == UNASSIGNED


@pytest.mark.parametrize(
    ("entity_id", "on", "off"),
    [
        ("binary_sensor.front_door", "on", "off"),
        ("device_tracker.someones_phone", "home", "not_home"),
        ("person.anne", "home", "Work"),
        ("sun.sun", "above_horizon", "below_horizon"),
    ],
)
def test_a_pad_may_be_a_readout(entity_id: str, on: str, off: str) -> None:
    # A pad is allowed to tell you something it cannot change: is the back door open, is
    # anybody in. It needs no new colour and no new rule — state in colour, white when not,
    # and the shudder it already has, which for a door sensor is simply true.
    tap, hold = default_actions(entity_id)
    assert tap == Watch(entity_id)
    assert isinstance(hold, Nothing)

    slot = Slot(tap=tap, hold=hold, colour=GREEN)
    assert slot.entity_id == entity_id
    assert colour_of(slot, FakeRegistry(states={entity_id: on})) == GREEN
    # A tracker's "off" is the name of wherever else it is, not the word off.
    assert colour_of(slot, FakeRegistry(states={entity_id: off})) == STATE_OFF


def test_pressing_a_readout_shudders_rather_than_going_silent() -> None:
    # Silence is not a signal, it is the absence of one, and reading "nothing happened" as
    # "nothing was supposed to happen" needs knowledge a guest does not have. A press is a
    # question; this surface answers questions.
    assert isinstance(Watch("binary_sensor.front_door"), INERT_ACTIONS)


def test_a_room_offers_only_what_a_pad_could_control() -> None:
    # A real room holds a temperature, a door contact and somebody's phone alongside the
    # lamps, and a grid is sixteen pads. Spending one on a reading costs a lamp its place.
    registry = FakeRegistry(
        areas={
            "living": (
                "sensor.living_temperature",
                "light.lamp",
                "device_tracker.someones_phone",
                "switch.fan",
            )
        }
    )
    slots = resolve(page(source=Source(SourceKind.AREA, "living")), registry, EMPTY)
    assert [slot.entity_id for slot in slots if slot] == ["light.lamp", "switch.fan"]


# ---------------------------------------------------------------------- resolving


def test_a_room_page_needs_no_configuration_at_all() -> None:
    registry = FakeRegistry(areas={"living": ("light.lamp", "switch.fan")})
    slots = resolve(page(source=Source(SourceKind.AREA, "living")), registry, EMPTY)
    # Laid out with the actions its domain implies, and the colour its domain defaults to.
    assert slots[0] == Slot(*default_actions("light.lamp"), colour=ORANGE)
    assert slots[1] == Slot(*default_actions("switch.fan"), colour=ORANGE)
    assert slots[2] is None


def test_a_profile_can_change_what_a_domain_looks_like() -> None:
    # What the config flow writes: lights are green in this house.
    registry = FakeRegistry(areas={"living": ("light.lamp",)}, states={"light.lamp": "on"})
    profile = Profile(pages={}, root_id="home", colours={"light": GREEN})
    living = page(source=Source(SourceKind.AREA, "living"))
    assert render(living, resolve(living, registry, profile), registry).frame[0] == GREEN


def test_entities_are_laid_out_in_an_order_a_person_would_expect() -> None:
    # Lights first because they are what people reach for, and the press-once-and-forget
    # things last. The registry's own order is arbitrary.
    registry = FakeRegistry(
        areas={"living": ("script.z", "switch.b", "light.a", "scene.y", "media_player.tv")}
    )
    slots = resolve(page(source=Source(SourceKind.AREA, "living")), registry, EMPTY)
    assert [slot.entity_id for slot in slots[:5] if slot] == [
        "light.a",
        "media_player.tv",
        "switch.b",
        "scene.y",
        "script.z",
    ]


def test_configuration_wins_and_never_leaves_a_duplicate() -> None:
    # Pinning the lamp to a corner must not leave a second copy of it further down.
    registry = FakeRegistry(areas={"living": ("light.lamp", "switch.fan")})
    pinned = page(
        source=Source(SourceKind.AREA, "living"),
        pads={15: PadConfig(tap=Toggle("light.lamp"))},
    )
    slots = resolve(pinned, registry, EMPTY)
    assert slots[15] is not None and slots[15].entity_id == "light.lamp"
    assert [slot.entity_id for slot in slots if slot] == ["switch.fan", "light.lamp"]


def test_a_room_with_more_entities_than_pads_fills_the_grid_and_stops() -> None:
    registry = FakeRegistry(areas={"big": tuple(f"light.l{n:02d}" for n in range(30))})
    slots = resolve(page(source=Source(SourceKind.AREA, "big")), registry, EMPTY)
    assert all(slot is not None for slot in slots)
    assert len(slots) == PAD_COUNT


def test_an_explicit_page_is_only_what_was_configured() -> None:
    registry = FakeRegistry(areas={"living": ("light.lamp",)})
    slots = resolve(page(pads={3: PadConfig(tap=EventOnly("coffee"))}), registry, EMPTY)
    assert [index for index, slot in enumerate(slots) if slot] == [3]


def test_a_label_page_works_across_rooms() -> None:
    registry = FakeRegistry(labels={"pad:morning": ("light.hall", "scene.wake")})
    slots = resolve(page(source=Source(SourceKind.LABEL, "pad:morning")), registry, EMPTY)
    assert [slot.entity_id for slot in slots if slot] == ["light.hall", "scene.wake"]


def test_an_index_is_one_pad_per_page_in_that_page_s_own_colour() -> None:
    profile = Profile(
        pages={
            "home": page("home", colour=BLUE, source=Source(SourceKind.PAGES)),
            "living": page("living", colour=ORANGE),
            "kitchen": page("kitchen", colour=GREEN),
        },
        root_id="home",
    )
    slots = resolve(profile.pages["home"], FakeRegistry(), profile)
    assert slots[0] == Slot(tap=Navigate("living"), colour=ORANGE)
    assert slots[1] == Slot(tap=Navigate("kitchen"), colour=GREEN)
    # The root never lists itself.
    assert [slot for slot in slots if slot] == list(slots[:2])


# ---------------------------------------------------------------------- rendering


def test_a_page_shows_on_off_and_nothing_assigned_as_three_different_things() -> None:
    registry = FakeRegistry(
        areas={"living": ("light.on", "light.off")},
        states={"light.on": "on", "light.off": "off"},
    )
    living = page(source=Source(SourceKind.AREA, "living"))
    frame = render(living, resolve(living, registry, EMPTY), registry).frame
    assert frame[:3] == (ON, STATE_OFF, UNASSIGNED)
    assert len(set(frame[:3])) == 3


def test_a_pad_carries_the_colour_of_what_is_behind_it_only_while_it_is_on() -> None:
    # Colour says what it is, white says it is off. That split is what lets a pad have a
    # colour of its own without state losing its channel.
    registry = FakeRegistry(
        areas={"living": ("media_player.tv", "cover.blind", "climate.heat")},
        states={"media_player.tv": "playing", "cover.blind": "open", "climate.heat": "off"},
    )
    living = page(source=Source(SourceKind.AREA, "living"))
    frame = render(living, resolve(living, registry, EMPTY), registry).frame
    assert frame[:3] == (BLUE, GREEN, STATE_OFF)


def test_a_pad_can_be_given_a_colour_of_its_own() -> None:
    registry = FakeRegistry(states={"light.a": "on", "light.b": "off"})
    pinned = page(
        pads={
            0: PadConfig(tap=Toggle("light.a"), colour=GREEN),
            1: PadConfig(tap=Toggle("light.b"), colour=GREEN),
        }
    )
    frame = render(pinned, resolve(pinned, registry, EMPTY), registry).frame
    # Its own colour when on, and white when off like everything else.
    assert frame[:2] == (GREEN, STATE_OFF)


def test_an_entity_nobody_can_reach_looks_like_one_that_is_off() -> None:
    # It has to look like something, and a colour reserved for it would cost a fifth of
    # the whole vocabulary for a condition that is rare and usually temporary. It says so
    # under the finger instead, which is the only moment anybody can act on it.
    registry = FakeRegistry(
        areas={"living": ("light.typo", "light.flat")}, states={"light.flat": "unavailable"}
    )
    living = page(source=Source(SourceKind.AREA, "living"))
    frame = render(living, resolve(living, registry, EMPTY), registry).frame
    assert frame[:2] == (STATE_OFF, STATE_OFF)


def test_a_scene_that_has_never_been_run_is_not_broken() -> None:
    # A scene's resting state in Home Assistant is "unknown", which everywhere else here
    # means unreachable. On a fresh install that would light half the grid as broken, so
    # statelessness has to be decided before the state is looked at.
    registry = FakeRegistry(
        areas={"living": ("scene.evening", "script.wake")},
        states={"scene.evening": "unknown", "script.wake": "off"},
    )
    living = page(source=Source(SourceKind.AREA, "living"))
    frame = render(living, resolve(living, registry, EMPTY), registry).frame
    assert frame[0] == PURPLE
    # And an idle script keeps its colour too. It has a running state and is deliberately
    # not shown in it: purple running against white idle is the one pair measured as too
    # close to tell apart, so what that showed was a sub-second flash in the colour nobody
    # can read. Pressing it says it ran; the pad no longer tries to.
    assert frame[1] == PURPLE


def test_the_focused_pad_breathes_and_nothing_else_moves() -> None:
    registry = FakeRegistry(
        areas={"living": ("light.a", "light.b")}, states={"light.a": "on", "light.b": "on"}
    )
    living = page(source=Source(SourceKind.AREA, "living"))
    slots = resolve(living, registry, EMPTY)
    rendering = render(living, slots, registry, ViewState(focus="light.b"))
    assert rendering.rhythms == {1: Motion(BREATHE, STATE_OFF)}


def test_a_commanded_pad_blinks_until_it_is_confirmed_and_outranks_focus() -> None:
    registry = FakeRegistry(areas={"living": ("light.a",)}, states={"light.a": "off"})
    living = page(source=Source(SourceKind.AREA, "living"))
    slots = resolve(living, registry, EMPTY)
    view = ViewState(focus="light.a", pending=frozenset({"light.a"}))
    assert render(living, slots, registry, view).rhythms == {0: Motion(ALERT, ON)}


def test_a_moving_pad_swings_between_on_and_off_rather_than_going_dark() -> None:
    # A pad that blinks to darkness reads as a light going out, which is a lie when the
    # light is on and staying on. It is also the first thing anybody complains about.
    rendering = Rendering(frame=(ON,) * PAD_COUNT, rhythms={0: Motion(BREATHE, STATE_OFF)})
    assert compose(rendering, 0.0)[0] == ON
    assert compose(rendering, BREATHE.period * 0.9)[0] == STATE_OFF
    assert compose(rendering, 0.0)[1:] == compose(rendering, BREATHE.period * 0.9)[1:]


# ------------------------------------------------------------------------ buttons


def test_a_lit_button_means_pressing_it_will_do_something() -> None:
    buttons = render(page(), (None,) * PAD_COUNT, FakeRegistry()).buttons
    assert buttons[BACK_BUTTON] is False
    assert buttons[HOME_BUTTON] is False

    inside = render(
        page(), (None,) * PAD_COUNT, FakeRegistry(), ViewState(can_go_back=True, can_go_home=True)
    ).buttons
    assert inside[BACK_BUTTON] is True
    assert inside[HOME_BUTTON] is True


def test_a_page_can_light_the_three_buttons_it_owns() -> None:
    bound = page(buttons={"play": Service("media_player", "media_play_pause", {})})
    buttons = render(bound, (None,) * PAD_COUNT, FakeRegistry()).buttons
    assert buttons["play"] is True
    assert buttons["right"] is False
    assert buttons["record"] is False


def test_a_page_cannot_take_over_back_or_home() -> None:
    # Back and home are the two fixed things on the whole surface. A page binding them
    # would make the one gesture that always works stop always working.
    hostile = page(buttons={BACK_BUTTON: Navigate("elsewhere")})
    buttons = render(hostile, (None,) * PAD_COUNT, FakeRegistry(), ViewState()).buttons
    assert buttons[BACK_BUTTON] is False


# -------------------------------------------------------------------- diffing


def test_only_the_pads_that_changed_are_reported() -> None:
    before = (ON,) * PAD_COUNT
    after = (ON,) * 3 + (STATE_OFF,) + (ON,) * 12
    assert changed_pads(before, after) == {3: STATE_OFF}
    assert changed_pads(before, before) == {}


def test_nothing_that_moves_ever_blinks_an_entity_to_darkness() -> None:
    # The complaint that produced this rule: a lamp being switched appeared to go out
    # mid-press, which is a lie when it is on and staying on. Novation never blinks a pad
    # to off either; their flash alternates two colours.
    registry = FakeRegistry(
        areas={"living": ("light.on", "light.off")},
        states={"light.on": "on", "light.off": "off"},
    )
    living = page(source=Source(SourceKind.AREA, "living"))
    slots = resolve(living, registry, EMPTY)
    view = ViewState(focus="light.on", pending=frozenset({"light.off"}))
    rhythms = render(living, slots, registry, view).rhythms
    assert rhythms[0].other == STATE_OFF
    assert rhythms[1].other == ORANGE
    assert all(motion.other != UNASSIGNED for motion in rhythms.values())


def test_a_pad_with_no_two_states_never_moves_at_all() -> None:
    # A scene has nothing to be between, and swinging to darkness is what makes a pad look
    # like it is failing rather than working.
    registry = FakeRegistry(
        areas={"living": ("scene.evening",)}, states={"scene.evening": "unknown"}
    )
    living = page(source=Source(SourceKind.AREA, "living"))
    slots = resolve(living, registry, EMPTY)
    view = ViewState(focus="scene.evening", pending=frozenset({"scene.evening"}))
    assert render(living, slots, registry, view).rhythms == {}


# ------------------------------------------------- is this the same house as before


def test_two_profiles_of_the_same_house_are_equal() -> None:
    # The coordinator rebuilds the profile whenever the area, entity or device registry
    # moves, and rebuilds the *surface* only when the answer differs. That rests entirely
    # on this: if a profile compared by identity, every registry event would throw away
    # whatever animation was in flight, and they arrive in dozens.
    def built() -> Profile:
        return Profile(
            pages={
                "home": page("home", source=Source(SourceKind.PAGES)),
                "kitchen": page("kitchen", colour=GREEN, source=Source(SourceKind.AREA, "kitchen")),
            },
            root_id="home",
            colours={"light": ORANGE},
        )

    assert built() == built()


def test_a_renamed_room_is_a_different_house() -> None:
    before = Profile(pages={"kitchen": page("kitchen", title="Kitchen")}, root_id="kitchen")
    after = Profile(pages={"kitchen": page("kitchen", title="Scullery")}, root_id="kitchen")
    assert before != after


def test_a_room_that_went_away_is_a_different_house() -> None:
    both = Profile(pages={"a": page("a"), "b": page("b")}, root_id="a")
    one = Profile(pages={"a": page("a")}, root_id="a")
    assert both != one
