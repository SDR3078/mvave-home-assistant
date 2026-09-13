"""What the surface says on the bus, and what that reads like afterwards.

Two things that are easy to get wrong and invisible when they are. An event about a device
that does not carry a ``device_id`` cannot be offered against that device in the automation
editor, which Home Assistant's own guidance on integration events is explicit about. And
one event type carrying a ``type`` field is right for automations and unreadable in a
timeline: without a logbook platform, somebody asking why the kitchen light came on at
seven gets a row saying ``mvave_event`` and nothing else.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, ClassVar

import pytest
from homeassistant.components.logbook import LOGBOOK_ENTRY_MESSAGE, LOGBOOK_ENTRY_NAME
from homeassistant.const import ATTR_DEVICE_ID

from custom_components.mvave import registry as registry_module
from custom_components.mvave.const import DOMAIN
from custom_components.mvave.devices.smc_pad import PAD_NUMBER_BY_READING_ORDER
from custom_components.mvave.engine.surface import Emit, EventType, Outcome
from custom_components.mvave.logbook import FALLBACK_NAME, _message
from custom_components.mvave.registry import HomeAssistantSink
from custom_components.mvave.runner import SurfaceRunner, as_printed

ADDRESS = "AA:BB:CC:DD:EE:FF"
ENTRY_ID = "01JZZZCONFIGENTRY"
DEVICE_ID = "device-1234"


class FakeBus:
    """Records what was fired."""

    def __init__(self) -> None:
        self.fired: list[tuple[str, dict[str, Any]]] = []

    def async_fire(self, event_type: str, data: dict[str, Any]) -> None:
        self.fired.append((event_type, data))


class FakeHass:
    def __init__(self) -> None:
        self.bus = FakeBus()


class FakeDevice:
    def __init__(self, device_id: str) -> None:
        self.id = device_id


class FakeEntry:
    def __init__(self, entry_id: str) -> None:
        self.entry_id = entry_id


class FakeDeviceRegistry:
    """A registry that finds one device, counts the asking, and records how it was asked."""

    def __init__(self, device: FakeDevice | None) -> None:
        self.device = device
        self.lookups = 0
        self.asked: tuple[tuple[str, str], str] | None = None

    def async_get_device_by_identifier(
        self, identifier: tuple[str, str], config_entry_id: str
    ) -> FakeDevice | None:
        self.lookups += 1
        self.asked = (identifier, config_entry_id)
        return self.device


@pytest.fixture
def sink(monkeypatch: pytest.MonkeyPatch) -> HomeAssistantSink:
    """A sink whose device registry holds exactly one device."""
    devices = FakeDeviceRegistry(FakeDevice(DEVICE_ID))
    monkeypatch.setattr(registry_module.dr, "async_get", lambda hass: devices)
    made = HomeAssistantSink(FakeHass(), FakeEntry(ENTRY_ID), "mvave_event", ADDRESS)  # type: ignore[arg-type]
    made.devices = devices  # type: ignore[attr-defined]
    return made


# ---------------------------------------------------------------- the bus


def test_an_event_about_a_device_says_which_device(sink: HomeAssistantSink) -> None:
    sink.fire("page_entered", {"page_id": "kitchen"})
    event_type, data = sink.hass.bus.fired[0]  # type: ignore[attr-defined]
    assert event_type == "mvave_event"
    assert data[ATTR_DEVICE_ID] == DEVICE_ID
    assert data["type"] == "page_entered"
    assert data["page_id"] == "kitchen"


def test_the_device_is_asked_for_within_this_entry_and_not_across_all_of_them(
    sink: HomeAssistantSink,
) -> None:
    # An identifier is unique only inside a config entry — this pad is very likely also
    # known to the ESPHome proxy relaying it — so the unscoped lookup has to guess between
    # them, and Home Assistant deprecated it for exactly that. Found in the log a day after
    # the same call was fixed in the coordinator and this second one was missed, because
    # what got fixed was the file the warning named rather than everywhere it was called.
    sink.fire("page_entered", {"page_id": "kitchen"})
    asked = sink.devices.asked  # type: ignore[attr-defined]
    assert asked == ((DOMAIN, ADDRESS.lower()), ENTRY_ID)


def test_the_device_is_looked_up_once_and_kept(sink: HomeAssistantSink) -> None:
    # The device is registered when the first entity is added, which is after the surface
    # starts, so this cannot be resolved in the constructor. Its id never changes
    # afterwards, and a knob turn fires often enough for a lookup per event to matter.
    for _ in range(5):
        sink.fire("knob_turned", {"knob": 1})
    assert sink.devices.lookups == 1  # type: ignore[attr-defined]


def test_an_event_fired_before_the_device_exists_still_goes_out(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(registry_module.dr, "async_get", lambda hass: FakeDeviceRegistry(None))
    made = HomeAssistantSink(FakeHass(), FakeEntry(ENTRY_ID), "mvave_event", ADDRESS)  # type: ignore[arg-type]
    made.fire("pad_pressed", {"pad": 0})
    _, data = made.hass.bus.fired[0]  # type: ignore[attr-defined]
    # Better a nameless event than a swallowed one: the surface works before anything is
    # listening, and an automation on the raw type still fires.
    assert ATTR_DEVICE_ID not in data
    assert data["type"] == "pad_pressed"


# ------------------------------------------------------------- the logbook


@pytest.mark.parametrize(
    ("data", "expected"),
    [
        (
            {"type": "page_entered", "page_title": "Kitchen", "trigger": "pad"},
            "showed Kitchen from a pad",
        ),
        (
            {"type": "page_entered", "page_title": "Kitchen", "trigger": "service"},
            "showed Kitchen from an automation",
        ),
        (
            {"type": "page_exited", "page_title": "Kitchen", "trigger": "idle"},
            "left Kitchen after a while with nothing happening",
        ),
        # A page with no title falls back to its id rather than to nothing.
        ({"type": "page_entered", "page_id": "kitchen"}, "showed kitchen"),
        ({"type": "focus_set", "entity_id": "light.a"}, "pointed the knobs at light.a"),
        ({"type": "focus_cleared", "entity_id": "light.a"}, "let go of the knobs"),
        (
            {"type": "pad_pressed", "pad": 9, "entity_id": "light.a", "trigger": "pad"},
            "pressed pad 9 (light.a) from a pad",
        ),
        (
            {"type": "pad_held", "pad": 13, "entity_id": None, "trigger": "service"},
            "held pad 13 from an automation",
        ),
        (
            {"type": "knob_turned", "knob": 1, "property": "brightness", "entity_id": "light.a"},
            "turned knob 1 to set the brightness of light.a",
        ),
        ({"type": "knob_turned", "knob": 2}, "turned knob 2"),
        ({"type": "tagged", "tag": "movie"}, "fired the tag movie"),
    ],
)
def test_every_event_reads_as_a_sentence(data: dict[str, Any], expected: str) -> None:
    assert _message(data) == expected


def test_pads_are_named_in_the_timeline_by_the_number_printed_on_them() -> None:
    # Straight through: the event already carries the printed number, so this converts
    # nothing. Converting a second time is the mistake the line is shaped to avoid, and
    # there is no pad it would get away with: not one of the sixteen maps to itself, so a
    # second pass is wrong everywhere rather than wrong somewhere.
    assert "pad 13" in _message({"type": "pad_pressed", "pad": 13})  # top left
    assert "pad 1" in _message({"type": "pad_pressed", "pad": 1})  # bottom left
    # And a number off the grid does not put "pad 0" or a traceback in somebody's timeline.
    for nonsense in (0, 17, -1, None, "3"):
        assert "a pad" in _message({"type": "pad_pressed", "pad": nonsense})


# ------------------------------------------- from a frame index to a printed number


def test_the_bus_carries_the_number_printed_on_the_pad() -> None:
    # The engine counts pads from zero in reading order and has no idea what is written on
    # any of them. `as_printed` is the one place that converts, on the way out, and it is
    # the exact inverse of what `mvave.press_slot` does on the way in — so a pad taken off
    # the bus can be handed straight back to it.
    assert as_printed({"pad": 0})["pad"] == 13  # top left
    assert as_printed({"pad": 15})["pad"] == 4  # bottom right
    # The rest of the payload is untouched, and a knob was already numbered the device's
    # own way, so it must not be converted a second time.
    assert as_printed({"pad": 0, "entity_id": "light.a", "trigger": "pad"}) == {
        "pad": 13,
        "entity_id": "light.a",
        "trigger": "pad",
    }
    assert as_printed({"knob": 7, "steps": -2}) == {"knob": 7, "steps": -2}
    # Nothing to convert is not an error. The surface fires plenty of events with no pad.
    assert as_printed({"page_id": "kitchen"}) == {"page_id": "kitchen"}
    assert as_printed({"pad": None}) == {"pad": None}
    assert as_printed({"pad": 99}) == {"pad": 99}


def test_the_conversion_is_on_the_path_events_actually_take(sink: HomeAssistantSink) -> None:
    # `as_printed` being correct is worth nothing if the runner does not call it. This
    # drives the real `_perform` against the real sink, which is the whole path from an
    # engine outcome to the bus.
    runner = SurfaceRunner.__new__(SurfaceRunner)
    runner.sink = sink
    runner.coordinator = SimpleNamespace(address=ADDRESS)  # type: ignore[assignment]
    runner._perform(Outcome(emits=(Emit(EventType.PAD_PRESSED, {"pad": 0}),)))

    _, data = sink.hass.bus.fired[0]  # type: ignore[attr-defined]
    assert data["pad"] == 13
    assert data["address"] == ADDRESS


def test_a_pad_number_survives_the_trip_from_the_engine_to_the_timeline() -> None:
    # Both halves composed, for all sixteen, which is the thing that actually has to be
    # right: converted once on the way out and not again on the way in.
    for index, printed in enumerate(PAD_NUMBER_BY_READING_ORDER):
        fired = as_printed({"pad": index})
        assert f"pad {printed}" in _message({"type": "pad_pressed", **fired})


def test_an_event_nobody_taught_it_still_produces_a_line() -> None:
    assert _message({"type": "something_new"}) == "something_new"
    assert _message({}) == "did something"


def test_the_line_is_named_after_the_device(monkeypatch: pytest.MonkeyPatch) -> None:
    from custom_components.mvave import logbook as logbook_module

    described: dict[str, Any] = {}

    def capture(domain: str, event_name: str, callback: Any) -> None:
        described["callback"] = callback

    class Renamed:
        name = "SMC-PAD"
        name_by_user = "Hallway pad"

    class Devices:
        def async_get(self, device_id: str) -> Renamed | None:
            return Renamed() if device_id == DEVICE_ID else None

    monkeypatch.setattr(logbook_module.dr, "async_get", lambda hass: Devices())
    logbook_module.async_describe_events(FakeHass(), capture)  # type: ignore[arg-type]

    class FakeEvent:
        data: ClassVar[dict[str, Any]] = {"type": "focus_cleared", ATTR_DEVICE_ID: DEVICE_ID}

    line = described["callback"](FakeEvent())
    # What the owner renamed it to, not what the manufacturer called it.
    assert line[LOGBOOK_ENTRY_NAME] == "Hallway pad"
    assert line[LOGBOOK_ENTRY_MESSAGE] == "let go of the knobs"

    class Anonymous:
        data: ClassVar[dict[str, Any]] = {"type": "focus_cleared"}

    assert described["callback"](Anonymous())[LOGBOOK_ENTRY_NAME] == FALLBACK_NAME
