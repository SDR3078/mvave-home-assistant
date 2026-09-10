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
from .entity import MvaveEntity

if TYPE_CHECKING:
    from datetime import datetime

    from homeassistant.core import CALLBACK_TYPE, HomeAssistant
    from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

    from . import MvaveConfigEntry
    from .coordinator import MvaveCoordinator
    from .devices.layout import ButtonSpec, KnobSpec, PadSpec
    from .transport import MidiEvent

# Nothing here talks to the device, so updates need no serialising.
PARALLEL_UPDATES = 0

CLOCKWISE = "clockwise"
COUNTER_CLOCKWISE = "counter_clockwise"

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
    coordinator = entry.runtime_data
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

    entities: list[EventEntity] = [MvavePadEvent(coordinator, pad) for pad in layout.pads]
    entities += [MvaveButtonEvent(coordinator, button) for button in layout.buttons]
    entities += [MvaveKnobEvent(coordinator, knob) for knob in layout.knobs]
    async_add_entities(entities)


class _MvaveEventEntity(MvaveEntity, EventEntity):
    """Shared plumbing: subscribe to decoded MIDI while the entity is loaded."""

    async def async_added_to_hass(self) -> None:
        """Start listening for MIDI, and for the link going up or down."""
        self.async_on_remove(self.coordinator.async_add_midi_listener(self._handle_midi))
        self.async_on_remove(self.coordinator.async_add_listener(self.async_write_ha_state))

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


class MvavePadEvent(_MvaveEventEntity):
    """One velocity-sensitive pad."""

    _attr_device_class = EventDeviceClass.BUTTON
    _attr_translation_key = "pad"

    def __init__(self, coordinator: MvaveCoordinator, spec: PadSpec) -> None:
        """Initialise the entity for one pad."""
        super().__init__(coordinator, spec.key)
        self._attr_event_types = [ButtonEventType.PRESS_START, ButtonEventType.PRESS_END]
        self._spec = spec
        self._attr_translation_placeholders = {"number": str(spec.number)}

    def _handle_midi(self, event: MidiEvent) -> None:
        if event.channel != self._spec.channel or event.data1 != self._spec.note:
            return
        if event.type == "note_on":
            # A note-on of velocity zero is a release, and the parser has already
            # normalised it to note_off, so velocity here is always a real strike.
            self._fire(
                ButtonEventType.PRESS_START,
                note=event.data1,
                velocity=event.data2,
                channel=event.channel + 1,
            )
        elif event.type == "note_off":
            self._fire(ButtonEventType.PRESS_END, note=event.data1, channel=event.channel + 1)


class MvaveButtonEvent(_MvaveEventEntity):
    """One transport or function button, which sends a control change of 127 then 0."""

    _attr_device_class = EventDeviceClass.BUTTON

    def __init__(self, coordinator: MvaveCoordinator, spec: ButtonSpec) -> None:
        """Initialise the entity for one button."""
        super().__init__(coordinator, spec.key)
        self._attr_event_types = [ButtonEventType.PRESS_START, ButtonEventType.PRESS_END]
        self._spec = spec
        self._attr_name = spec.name

    def _handle_midi(self, event: MidiEvent) -> None:
        if (
            event.type != "cc"
            or event.channel != self._spec.channel
            or event.data1 != self._spec.cc
        ):
            return
        event_type = ButtonEventType.PRESS_START if event.data2 else ButtonEventType.PRESS_END
        self._fire(event_type, controller=event.data1, channel=event.channel + 1)


class MvaveKnobEvent(_MvaveEventEntity):
    """One rotary encoder, reported as a direction and a number of steps.

    The factory encoders are absolute: they report a position, so a direction has to be
    derived by comparing against the previous one. Switching them to relative mode makes
    the device report the step directly; this arithmetic gives the same answer either way.

    Turns are accumulated rather than reported one unit at a time. The device emits one
    message per unit of travel, so a single turn of a knob produced over a thousand
    messages when measured, and firing an event for each would put a thousand state
    changes through Home Assistant and trigger every attached automation that many times.
    Instead the steps are summed until the knob has been still for a moment, and one
    event carries the total. A reversal flushes what has accumulated first, so a turn one
    way followed by the other never cancels itself out into silence.
    """

    _attr_translation_key = "knob"

    def __init__(self, coordinator: MvaveCoordinator, spec: KnobSpec) -> None:
        """Initialise the entity for one encoder."""
        super().__init__(coordinator, spec.key)
        self._attr_event_types = [CLOCKWISE, COUNTER_CLOCKWISE]
        self._spec = spec
        self._attr_translation_placeholders = {"number": str(spec.number)}
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
        if event.type != "cc" or event.channel != self._spec.channel:
            return
        bank = self._spec.bank_of(event.data1)
        if bank is None:
            return

        previous = self._last.get(bank)
        self._last[bank] = event.data2
        if previous is None:
            # First message since the entity loaded: a position with nothing to compare
            # against says nothing about which way the knob turned.
            return
        steps = event.data2 - previous
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
            controller=self._spec.ccs[self._pending_bank],
            value=self._pending_value,
        )
