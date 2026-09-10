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
)
from engine.palette import ACTION, BLUE, GREEN, ON, ORANGE, RED, STATE_OFF, UNASSIGNED, UNAVAILABLE
from engine.render import (
    BACK_BUTTON,
    HOME_BUTTON,
    Rendering,
    ViewState,
    changed_pads,
    compose,
    render,
)
from engine.resolve import default_actions, resolve
from engine.rhythms import ALERT, BREATHE


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
        ("climate.a", Nothing),
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


# ---------------------------------------------------------------------- resolving


def test_a_room_page_needs_no_configuration_at_all() -> None:
    registry = FakeRegistry(areas={"living": ("light.lamp", "switch.fan")})
    slots = resolve(page(source=Source(SourceKind.AREA, "living")), registry, EMPTY)
    assert slots[0] == Slot(*default_actions("light.lamp"))
    assert slots[1] == Slot(*default_actions("switch.fan"))
    assert slots[2] is None


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


def test_a_page_shows_on_off_unassigned_and_unreachable_as_four_different_things() -> None:
    registry = FakeRegistry(
        areas={"living": ("light.on", "light.off", "light.gone")},
        states={"light.on": "on", "light.off": "off", "light.gone": "unavailable"},
    )
    living = page(source=Source(SourceKind.AREA, "living"))
    frame = render(living, resolve(living, registry, EMPTY), registry).frame
    assert frame[:4] == (ON, STATE_OFF, UNAVAILABLE, UNASSIGNED)
    assert len(set(frame[:4])) == 4


def test_an_entity_that_does_not_exist_looks_the_same_as_one_that_cannot_be_reached() -> None:
    # From where a person is standing they are the same thing: pressing it will not work.
    registry = FakeRegistry(areas={"living": ("light.typo",)})
    living = page(source=Source(SourceKind.AREA, "living"))
    frame = render(living, resolve(living, registry, EMPTY), registry).frame
    assert frame[0] == UNAVAILABLE


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
    assert frame[0] == ACTION
    # A script does have a lasting state: it is on while it is running.
    assert frame[1] == STATE_OFF


def test_a_forced_colour_opts_a_pad_out_of_state_entirely() -> None:
    registry = FakeRegistry(states={"light.a": "on"})
    pinned = page(pads={0: PadConfig(tap=Toggle("light.a"), colour=RED)})
    frame = render(pinned, resolve(pinned, registry, EMPTY), registry).frame
    assert frame[0] == RED


def test_the_focused_pad_breathes_and_nothing_else_moves() -> None:
    registry = FakeRegistry(
        areas={"living": ("light.a", "light.b")}, states={"light.a": "on", "light.b": "on"}
    )
    living = page(source=Source(SourceKind.AREA, "living"))
    slots = resolve(living, registry, EMPTY)
    rendering = render(living, slots, registry, ViewState(focus="light.b"))
    assert rendering.rhythms == {1: BREATHE}


def test_a_commanded_pad_blinks_until_it_is_confirmed_and_outranks_focus() -> None:
    registry = FakeRegistry(areas={"living": ("light.a",)}, states={"light.a": "off"})
    living = page(source=Source(SourceKind.AREA, "living"))
    slots = resolve(living, registry, EMPTY)
    view = ViewState(focus="light.a", pending=frozenset({"light.a"}))
    assert render(living, slots, registry, view).rhythms == {0: ALERT}


def test_a_rhythm_only_ever_darkens_a_pad() -> None:
    # Motion never invents a colour: the lit half of a rhythm is whatever the frame
    # already says, so a breathing lamp is still recognisably that lamp.
    rendering = Rendering(frame=(ON,) * PAD_COUNT, rhythms={0: BREATHE})
    assert compose(rendering, 0.0)[0] == ON
    assert compose(rendering, BREATHE.period * 0.9)[0] == UNASSIGNED
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
