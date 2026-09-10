"""Connection lifecycle for a BLE MIDI device.

This is the whole of docs/PLAN.md section 2. It connects, holds the link, reconnects
when the device advertises again, and fans decoded MIDI events out to entities.

It is built on ``ActiveBluetoothDataUpdateCoordinator``, whose "poll" is read here as
"connect": the base class asks on every advertisement whether work is needed, and the
answer is yes whenever the link is down. In exchange the base class provides the
advertisement subscription, unavailability tracking, a ten second debounce that serves
as the reconnect backoff, and log-once-on-failure semantics.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, override

from bleak import BleakClient, BleakError
from bleak_retry_connector import establish_connection
from homeassistant.components import bluetooth
from homeassistant.components.bluetooth.active_update_coordinator import (
    ActiveBluetoothDataUpdateCoordinator,
)
from homeassistant.core import callback

from .arming import async_arm
from .const import LOGGER, MIDI_CHAR_UUID
from .transport import MidiEvent, ParserState, frame_midi, parse_ble_midi
from .vendor import VendorSession

if TYPE_CHECKING:
    from collections.abc import Callable

    from bleak.backends.characteristic import BleakGATTCharacteristic
    from homeassistant.components.bluetooth import BluetoothServiceInfoBleak
    from homeassistant.core import CALLBACK_TYPE, HomeAssistant

    from .arming import ArmResult

# A `type` statement rather than a plain assignment: its right-hand side is evaluated
# lazily, so `Callable` may stay in the TYPE_CHECKING block. A plain alias would be
# evaluated at import time and raise NameError.
type MidiListener = Callable[[MidiEvent], None]


def _describe(event: MidiEvent) -> str:
    """One short, readable line per decoded event. Channels are shown 1-based."""
    channel = f"ch{event.channel + 1}" if event.channel is not None else ""
    if event.type in ("note_on", "note_off"):
        return f"{event.type} {channel} note {event.data1} vel {event.data2}"
    if event.type == "cc":
        return f"cc {channel} #{event.data1}={event.data2}"
    if event.type == "sysex":
        return f"sysex [{event.sysex_payload.hex(' ')}]"
    return f"{event.type} {channel} {event.data1},{event.data2}"


class MvaveCoordinator(ActiveBluetoothDataUpdateCoordinator[None]):
    """Hold a connection to one BLE MIDI device and publish what it sends."""

    def __init__(self, hass: HomeAssistant, address: str, name: str) -> None:
        """Initialise the coordinator for one device address."""
        super().__init__(
            hass,
            LOGGER,
            address=address,
            # PASSIVE is enough: the only thing wanted from an advertisement is that
            # the device is back. A non-passive mode with an address matcher opts the
            # address into active scanning, which costs radio time on a shared proxy.
            mode=bluetooth.BluetoothScanningMode.PASSIVE,
            needs_poll_method=self._needs_connect,
            poll_method=self._async_connect,
            connectable=True,
        )
        self.device_name = name
        self._client: BleakClient | None = None
        self._connect_lock = asyncio.Lock()
        self._parser = ParserState()
        self._shutdown = False
        self._midi_listeners: list[MidiListener] = []
        #: What the last connect found and did, or None if it never got that far.
        self.arming: ArmResult | None = None

    # ------------------------------------------------------------------ state

    @property
    def connected(self) -> bool:
        """Whether the GATT link is up, as this integration sees it."""
        return self._client is not None and self._client.is_connected

    @property
    def mtu(self) -> int | None:
        """The negotiated MTU, or None while disconnected."""
        return self._client.mtu_size if self._client is not None else None

    @callback
    def async_add_midi_listener(self, listener: MidiListener) -> CALLBACK_TYPE:
        """Subscribe to decoded MIDI events. Returns an unsubscribe callback."""
        self._midi_listeners.append(listener)

        @callback
        def _remove() -> None:
            self._midi_listeners.remove(listener)

        return _remove

    # ------------------------------------------------------------- connecting

    @callback
    def _needs_connect(
        self,
        service_info: BluetoothServiceInfoBleak,
        seconds_since_last_poll: float | None,
    ) -> bool:
        """Tell the base class whether to act on this advertisement."""
        return not self._shutdown and not self.connected

    async def _async_connect(self, service_info: BluetoothServiceInfoBleak) -> None:
        """Open the link and subscribe to MIDI notifications.

        Exceptions propagate: the base class logs the first failure and stays quiet
        until it recovers, and the debouncer paces the retries.
        """
        async with self._connect_lock:
            if self._shutdown or self.connected:
                return

            # Always take the BLEDevice from the advertisement rather than reusing an
            # old one. The proxy that hears the device can change between attempts, and
            # its backend reads the address type out of the device's details.
            client = await establish_connection(
                BleakClient,
                service_info.device,
                self.device_name,
                disconnected_callback=self._on_disconnect,
            )
            try:
                self._parser = ParserState()
                await client.start_notify(MIDI_CHAR_UUID, self._on_notify)
                # RP-052 section 5 requires the central to read the characteristic once
                # after connecting. Peripherals answer with an empty payload.
                await client.read_gatt_char(MIDI_CHAR_UUID)
            except Exception:
                await client.disconnect()
                raise
            self._client = client
            LOGGER.info(
                "%s: connected to %s (MTU %s)", self.address, self.device_name, client.mtu_size
            )
            await self._async_arm(client)

        self.async_update_listeners()

    async def _async_arm(self, client: BleakClient) -> None:
        """Read the device's own map and arm it for host control.

        Deliberately not fatal. A device whose configuration cannot be read is still a
        perfectly good source of MIDI, and dropping the link over it would cost more than
        the LEDs are worth.
        """
        session = VendorSession(client, self.address)
        if not session.available:
            LOGGER.debug(
                "%s: no vendor channel on this device, so its LEDs cannot be armed",
                self.address,
            )
            return
        try:
            async with session:
                self.arming = await async_arm(session)
        except Exception as err:
            LOGGER.warning("%s: could not read or arm the device: %r", self.address, err)

    def _on_disconnect(self, client: BleakClient) -> None:
        """Handle the link dropping. Called from outside the event loop."""
        self.hass.loop.call_soon_threadsafe(self._handle_disconnect)

    @callback
    def _handle_disconnect(self) -> None:
        """Clean up after a dropped link and re-arm the reconnect."""
        if self._client is None:
            return
        self._client = None
        LOGGER.info("%s: disconnected", self.address)

        # Without this the integration would never reconnect. The Bluetooth manager
        # discards an advertisement identical to the previous one from this address,
        # and a BLE MIDI controller's advertisement never varies. The escape hatch for
        # a device missing from connectable history never opens either, because the
        # scanner refreshes the expiry timestamp before the manager discards the
        # duplicate. Clearing the remembered advertisement makes the next one count.
        bluetooth.async_clear_advertisement_history(self.hass, self.address)
        self.async_update_listeners()

    # ------------------------------------------------------------- receiving

    def _on_notify(
        self,
        characteristic: BleakGATTCharacteristic,
        data: bytearray,
    ) -> None:
        """Decode one notification and hand its events to the listeners."""
        errors_before = self._parser.errors
        events = parse_ble_midi(bytes(data), self._parser)
        if self._parser.errors > errors_before:
            LOGGER.warning(
                "%s: undecodable packet %s: %s",
                self.address,
                bytes(data).hex(" ").upper(),
                self._parser.last_error,
            )
        if events and LOGGER.isEnabledFor(logging.DEBUG):
            LOGGER.debug(
                "%s: %s -> %s",
                self.address,
                bytes(data).hex(" ").upper(),
                "; ".join(_describe(event) for event in events),
            )
        for event in events:
            for listener in self._midi_listeners:
                listener(event)

    # -------------------------------------------------------------- sending

    async def async_send(self, midi: bytes) -> None:
        """Send one MIDI message to the device."""
        client = self._client
        if client is None:
            raise BleakError(f"{self.address}: not connected")
        await client.write_gatt_char(MIDI_CHAR_UUID, frame_midi(midi), response=False)

    # ---------------------------------------------------------- availability

    @callback
    @override
    def _async_handle_unavailable(self, service_info: BluetoothServiceInfoBleak) -> None:
        """Ignore the platform's unavailability signal while the link is up.

        A BLE MIDI peripheral accepts one central and stops advertising while held, so
        the platform expires the address a few minutes into every working session. That
        says nothing about whether the device is working.
        """
        if self.connected:
            return
        super()._async_handle_unavailable(service_info)

    # ------------------------------------------------------------- teardown

    async def async_shutdown(self) -> None:
        """Close the link. Safe to call when already disconnected."""
        self._shutdown = True
        async with self._connect_lock:
            client = self._client
            self._client = None
            if client is None:
                return
            # Each step gets its own handler so a failure in one cannot strand the other.
            try:
                await client.stop_notify(MIDI_CHAR_UUID)
            except (BleakError, EOFError, TimeoutError) as err:
                LOGGER.debug("%s: stop_notify on shutdown: %r", self.address, err)
            try:
                await client.disconnect()
            except (BleakError, EOFError, TimeoutError) as err:
                LOGGER.debug("%s: disconnect on shutdown: %r", self.address, err)
