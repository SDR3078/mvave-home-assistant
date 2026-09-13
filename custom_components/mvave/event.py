"""Event entities: one per pad, button and encoder.

One entity per physical control rather than one shared entity with the control number as
an attribute. Home Assistant's event trigger filters on the event type only, aimed at an
entity, so a shared entity would make "pad 5 was pressed" inexpressible in the interface.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.components.event import (
    ButtonEventType,
    EventDeviceClass,
    EventEntity,
)
from homeassistant.core import callback
from homeassistant.helpers.event import async_call_later

from .const import LOGGER
from .devices import resolve_layout
from .devices.smc_pad import ENCODER_CENTRE, PAD_NUMBER_BY_READING_ORDER
from .entity import MvaveEntity
from .runner import HOLD_SECONDS

if TYPE_CHECKING:
    from datetime import datetime

    from homeassistant.core import CALLBACK_TYPE, HomeAssistant
    from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

    from . import MvaveConfigEntry
    from .coordinator import MvaveCoordinator
    from .devices.layout import ButtonSpec, DeviceLayout, KnobSpec, PadSpec
    from .transport import MidiEvent

# Nothing here talks to the device, so updates need no serialising.
PARALLEL_UPDATES = 0

CLOCKWISE = "clockwise"
COUNTER_CLOCKWISE = "counter_clockwise"

#: What a control that can be held reports. The two long forms are Home Assistant's own
#: standard strings for the gesture, which is what the automation editor and the logbook
#: know how to describe.
HOLDABLE_EVENTS = (
    ButtonEventType.PRESS_START,
    ButtonEventType.PRESS_END,
    ButtonEventType.LONG_PRESS_START,
    ButtonEventType.LONG_PRESS_END,
)

# The largest jump treated as one turn rather than a counter reset. The factory encoders
# are absolute and saturate at each end, so a bank change or a reconnect can show as a
# large step that means nothing (docs/HARDWARE-BLE.md section 7).
MAX_PLAUSIBLE_STEP = 32

# How long a knob must be still before its turn is reported. The device sends one message
# per unit of travel, so without this a single turn becomes hundreds of state changes.
KNOB_COALESCE_SECONDS = 0.2


async def async_setup_entry(
    hass: HomeAssistant,
    entry: MvaveConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Create one entity per control, if the device's layout is known."""
    coordinator = entry.runtime_data.coordinator
    layout = resolve_layout(coordinator.device_name)
    if layout is None:
        LOGGER.info(
            "%s: no control layout known for %r, so no per-control entities. "
            "The connection and its MIDI decoding still work",
            coordinator.address,
            coordinator.device_name,
        )
        return
    LOGGER.debug(
        "%s: using the %s layout: %d pads, %d buttons, %d knobs",
        coordinator.address,
        layout.model,
        len(layout.pads),
        len(layout.buttons),
        len(layout.knobs),
    )

    # The layout resolved here is a starting point only: entities re-read the device's own
    # map on every message, and it is only known once the device has been armed.
    entities: list[EventEntity] = [MvavePadEvent(coordinator, pad, layout) for pad in layout.pads]
    entities += [MvaveButtonEvent(coordinator, button, layout) for button in layout.buttons]
    entities += [MvaveKnobEvent(coordinator, knob, layout) for knob in layout.knobs]
    async_add_entities(entities)


class _MvaveHoldableEvent(MvaveEntity, EventEntity):
    """Shared plumbing: subscribe to decoded MIDI while the entity is loaded.

    A control is looked up by key on every message rather than held onto. The note and
    controller numbers move with the preset and with the octave keys, so an entity holding
    the ones it was built with would quietly start matching the wrong pad, or none. The key
    does not move: pad 5 is pad 5 whatever it happens to be sending today.
    """

    def __init__(self, coordinator: MvaveCoordinator, key: str, fallback: DeviceLayout) -> None:
        """Initialise for one control, with the layout to use until the device is read."""
        super().__init__(coordinator, key)
        self._key = key
        self._fallback = fallback
        self._hold_timer: CALLBACK_TYPE | None = None
        self._long = False
        self._payload: dict[str, Any] = {}

    @property
    def layout(self) -> DeviceLayout:
        """What the device is sending now, or the guess made before it was ever read."""
        arming = self.coordinator.arming
        if arming is not None and arming.layout is not None:
            return arming.layout
        return self._fallback

    async def async_added_to_hass(self) -> None:
        """Start listening for MIDI, and for the link going up or down."""
        self.async_on_remove(self.coordinator.async_add_midi_listener(self._handle_midi))
        self.async_on_remove(self.coordinator.async_add_listener(self.async_write_ha_state))
        # A finger down when the entity is removed would otherwise leave a timer that
        # fires into a dead entity.
        self.async_on_remove(self._cancel_hold)

    def _handle_midi(self, event: MidiEvent) -> None:
        """Called for every decoded message. Subclasses pick out their own."""
        raise NotImplementedError

    def _fire(self, event_type: str, **payload: Any) -> None:
        """Trigger an event and publish it.

        `_trigger_event` records the event but does not write state, and it raises on a
        type the entity never declared, so unexpected input is dropped with a warning
        rather than taking the notification handler down.
        """
        if event_type not in (self.event_types or ()):
            LOGGER.warning("%s: undeclared event type %r", self.entity_id, event_type)
            return
        LOGGER.debug("%s: %s %s", self.entity_id, event_type, payload)
        self._trigger_event(event_type, payload)
        self.async_write_ha_state()

    # --------------------------------------------------------------------- holds

    def _pressed(self, **payload: Any) -> None:
        """A control went down. Starts the clock that turns a press into a hold.

        The threshold is imported from the runner rather than chosen again here, and that
        is the whole point: the same physical gesture has to become ``pad_held`` on the bus
        and ``long_press_start`` on this entity at the same instant, or an automation
        watching one and a page reacting to the other disagree about what just happened.
        """
        self._payload = payload
        self._long = False
        self._cancel_hold()
        self._hold_timer = async_call_later(self.hass, HOLD_SECONDS, self._became_a_hold)
        self._fire(ButtonEventType.PRESS_START, **payload)

    def _released(self, **payload: Any) -> None:
        """A control came back up, ending whichever kind of press it turned out to be."""
        self._cancel_hold()
        long, self._long = self._long, False
        self._fire(ButtonEventType.LONG_PRESS_END if long else ButtonEventType.PRESS_END, **payload)

    @callback
    def _became_a_hold(self, _now: datetime) -> None:
        """Fired while the finger is still down, not when it lifts.

        Waiting for the release would mean a hold is only announced once you stop holding,
        which is backwards, and it is also when the surface itself acts on one.
        """
        self._hold_timer = None
        self._long = True
        self._fire(ButtonEventType.LONG_PRESS_START, **self._payload)

    @callback
    def _cancel_hold(self) -> None:
        if self._hold_timer is not None:
            self._hold_timer()
            self._hold_timer = None


class MvavePadEvent(_MvaveHoldableEvent):
    """One velocity-sensitive pad."""

    _attr_device_class = EventDeviceClass.BUTTON
    _attr_translation_key = "pad"

    def __init__(
        self, coordinator: MvaveCoordinator, spec: PadSpec, fallback: DeviceLayout
    ) -> None:
        """Initialise the entity for one pad."""
        super().__init__(coordinator, spec.key, fallback)
        self._attr_event_types = list(HOLDABLE_EVENTS)
        # Named by where the pad *is*, not by the number in the device's own preset records.
        #
        # Those two disagree on all sixteen pads: the device counts from the bottom left, a
        # person reads from the top left, and until 2026-09-13 this entity was named after
        # the first while the configuration screen, `mvave.press_slot` and the logbook all
        # used the second. "Pad 1" meant the top-left pad on one screen and the bottom-left
        # pad on the other — opposite corners — and nothing said so. Nothing is written on
        # the pads themselves, so the device's numbering is a protocol detail nobody can
        # see, and reading order is the only one a person can check by looking.
        #
        # `spec.key` is deliberately left alone: it is the unique id, and its job is to stay
        # the same across a preset change rather than to be legible.
        reading_order = PAD_NUMBER_BY_READING_ORDER.index(spec.number) + 1
        self._attr_translation_placeholders = {"number": str(reading_order)}

    def _handle_midi(self, event: MidiEvent) -> None:
        spec = self.layout.pad(self._key)
        if spec is None or event.channel != spec.channel or event.data1 != spec.note:
            return
        if event.type == "note_on":
            # A note-on of velocity zero is a release, and the parser has already
            # normalised it to note_off, so velocity here is always a real strike.
            self._pressed(note=event.data1, velocity=event.data2, channel=event.channel + 1)
        elif event.type == "note_off":
            self._released(note=event.data1, channel=event.channel + 1)


class MvaveButtonEvent(_MvaveHoldableEvent):
    """One transport or function button, which sends a control change of 127 then 0."""

    _attr_device_class = EventDeviceClass.BUTTON

    def __init__(
        self, coordinator: MvaveCoordinator, spec: ButtonSpec, fallback: DeviceLayout
    ) -> None:
        """Initialise the entity for one button."""
        super().__init__(coordinator, spec.key, fallback)
        self._attr_event_types = list(HOLDABLE_EVENTS)
        self._spec = spec
        # A key rather than the layout's English name. The pads and the knobs are already
        # translated and these were the three words left in the interface that could not
        # be; a device whose entity names are half translated looks broken in a way that
        # is nobody's fault but ours.
        self._attr_translation_key = f"button_{spec.key}"

    def _handle_midi(self, event: MidiEvent) -> None:
        spec = self.layout.button(self._key)
        if (
            spec is None
            or event.type != "cc"
            or event.channel != spec.channel
            or event.data1 != spec.cc
        ):
            return
        payload = {"controller": event.data1, "channel": event.channel + 1}
        if event.data2:
            self._pressed(**payload)
        else:
            self._released(**payload)


class MvaveKnobEvent(_MvaveHoldableEvent):
    """One rotary encoder, reported as a direction and a number of steps.

    There are two encodings and the difference is not cosmetic. The factory encoders are
    **absolute**: they report a position, so a direction has to be derived by comparing
    against the previous one, and they saturate at both ends and then send nothing at all.
    Arming switches them to **relative**, where the device reports the step itself as a
    value either side of 64 and never changes: a stream of clockwise steps is the same
    number over and over.

    Subtracting consecutive values, which is right for the first, gives zero for every
    message of the second. Every turn is then silently discarded, which is exactly what
    happened here once arming was added and nobody turned a knob for a while. So the mode
    is read from what arming actually did rather than assumed.

    Turns are accumulated rather than reported one unit at a time. The device emits one
    message per unit of travel, so a single turn of a knob produced over a thousand
    messages when measured, and firing an event for each would put a thousand state
    changes through Home Assistant and trigger every attached automation that many times.
    Instead the steps are summed until the knob has been still for a moment, and one
    event carries the total. A reversal flushes what has accumulated first, so a turn one
    way followed by the other never cancels itself out into silence.
    """

    _attr_translation_key = "knob"
    #: Off unless somebody asks for it, and it is the noise that decides that rather than
    #: the usefulness. One turn of a knob is over a thousand MIDI messages; even coalesced
    #: into one event per gesture, a person adjusting a lamp for ten seconds writes a row
    #: for every pause. The surface itself consumes every turn already, so what is left
    #: here is the raw "knob three moved four steps" that only an automation wiring the
    #: hardware up to something else would ever want. The pads are the opposite case and
    #: stay on: they are the thing people point automations at.
    _attr_entity_registry_enabled_default = False

    def __init__(
        self, coordinator: MvaveCoordinator, spec: KnobSpec, fallback: DeviceLayout
    ) -> None:
        """Initialise the entity for one encoder."""
        super().__init__(coordinator, spec.key, fallback)
        self._attr_event_types = [CLOCKWISE, COUNTER_CLOCKWISE]
        self._attr_translation_placeholders = {"number": str(spec.number)}
        self._controller = 0
        self._last: dict[int, int] = {}
        self._pending_steps = 0
        self._pending_bank = 0
        self._pending_value = 0
        self._cancel_flush: CALLBACK_TYPE | None = None

    async def async_added_to_hass(self) -> None:
        """Subscribe, and make sure a pending turn cannot outlive the entity."""
        await super().async_added_to_hass()
        self.async_on_remove(self._cancel_pending)

    def _handle_midi(self, event: MidiEvent) -> None:
        spec = self.layout.knob(self._key)
        if spec is None or event.type != "cc" or event.channel != spec.channel:
            return
        bank = spec.bank_of(event.data1)
        if bank is None:
            return
        # Remembered rather than looked up when the turn is reported: by then the map may
        # have been replaced, and the number that arrived is the one worth reporting.
        self._controller = event.data1

        steps = self._steps(bank, event.data2)
        if steps == 0 or abs(steps) > MAX_PLAUSIBLE_STEP:
            return

        # A change of direction, or of bank, ends the turn that was accumulating.
        reversed_direction = self._pending_steps and (self._pending_steps > 0) != (steps > 0)
        if reversed_direction or (self._pending_steps and bank != self._pending_bank):
            self._flush()

        self._pending_steps += steps
        self._pending_bank = bank
        self._pending_value = event.data2
        self._schedule_flush()

    def _steps(self, bank: int, value: int) -> int:
        """How far the knob turned, by whichever rule this encoder is actually using."""
        previous = self._last.get(bank)
        self._last[bank] = value
        arming = self.coordinator.arming
        if arming is not None and arming.encoders_relative:
            # The value *is* the step, measured from the centre. Nothing to compare
            # against, so nothing is lost on the first message either.
            return value - ENCODER_CENTRE
        if previous is None:
            # A position with nothing to compare against says nothing about which way the
            # knob turned.
            return 0
        return value - previous

    @callback
    def _schedule_flush(self) -> None:
        """Restart the quiet period; the turn is reported once it expires."""
        self._cancel_pending()
        self._cancel_flush = async_call_later(
            self.hass, KNOB_COALESCE_SECONDS, self._flush_on_timer
        )

    @callback
    def _cancel_pending(self) -> None:
        if self._cancel_flush is not None:
            self._cancel_flush()
            self._cancel_flush = None

    @callback
    def _flush_on_timer(self, _now: datetime) -> None:
        self._cancel_flush = None
        self._flush()

    @callback
    def _flush(self) -> None:
        """Report the accumulated turn as one event."""
        self._cancel_pending()
        steps, self._pending_steps = self._pending_steps, 0
        if not steps:
            return
        self._fire(
            CLOCKWISE if steps > 0 else COUNTER_CLOCKWISE,
            steps=abs(steps),
            bank=self._pending_bank,
            controller=self._controller,
            value=self._pending_value,
        )
