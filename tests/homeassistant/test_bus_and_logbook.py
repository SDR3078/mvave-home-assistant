"""What the surface says on the bus, and what that reads like afterwards.

Two things that are easy to get wrong and invisible when they are. An event about a device
that does not carry a ``device_id`` cannot be offered against that device in the automation
editor, which Home Assistant's own guidance on integration events is explicit about. And
one event type carrying a ``type`` field is right for automations and unreadable in a
timeline: without a logbook platform, somebody asking why the kitchen light came on at
seven gets a row saying ``mvave_event`` and nothing else.
"""

from __future__ import annotations

from typing import Any, ClassVar

import pytest
from homeassistant.components.logbook import LOGBOOK_ENTRY_MESSAGE, LOGBOOK_ENTRY_NAME
from homeassistant.const import ATTR_DEVICE_ID

from custom_components.mvave import registry as registry_module
from custom_components.mvave.const import DOMAIN
from custom_components.mvave.logbook import FALLBACK_NAME, _message
from custom_components.mvave.registry import HomeAssistantSink

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
            # Frame index 4 is the first pad of the second row, which has 9 printed on it.
            {"type": "pad_pressed", "pad": 4, "entity_id": "light.a", "trigger": "pad"},
            "pressed pad 9 (light.a) from a pad",
        ),
        (
            {"type": "pad_held", "pad": 0, "entity_id": None, "trigger": "service"},
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
    # The event carries a frame index, which is zero based and in reading order; nobody has
    # ever called the top-left pad "pad 0", and on this hardware nobody can call it "pad 1"
    # either, because 1 is printed on the pad three rows below it.
    assert "pad 13" in _message({"type": "pad_pressed", "pad": 0})  # top left
    assert "pad 4" in _message({"type": "pad_pressed", "pad": 15})  # bottom right
    # And an index off the end of the grid does not put a traceback in somebody's timeline.
    assert "a pad" in _message({"type": "pad_pressed", "pad": 99})
    assert "a pad" in _message({"type": "pad_pressed", "pad": -1})


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
