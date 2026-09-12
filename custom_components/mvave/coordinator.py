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
from datetime import timedelta
from typing import TYPE_CHECKING, override

from bleak import BleakClient, BleakError
from bleak_retry_connector import establish_connection
from homeassistant.components import bluetooth
from homeassistant.components.bluetooth.active_update_coordinator import (
    ActiveBluetoothDataUpdateCoordinator,
)
from homeassistant.core import callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import format_mac
from homeassistant.helpers.event import async_track_time_interval

from .arming import async_arm
from .const import (
    BATTERY_UUID,
    DEVICE_NAME_UUID,
    DOMAIN,
    LOGGER,
    MANUFACTURER_UUID,
    MIDI_CHAR_UUID,
    MODEL_UUID,
    USELESS_MODEL_NAMES,
)
from .transport import MidiEvent, ParserState, frame_many, frame_midi, parse_ble_midi
from .vendor import VendorSession

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from datetime import datetime

    from bleak.backends.characteristic import BleakGATTCharacteristic
    from homeassistant.components.bluetooth import BluetoothServiceInfoBleak
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import CALLBACK_TYPE, HomeAssistant

    from .arming import ArmResult

# A `type` statement rather than a plain assignment: its right-hand side is evaluated
# lazily, so `Callable` may stay in the TYPE_CHECKING block. A plain alias would be
# evaluated at import time and raise NameError.
type MidiListener = Callable[[MidiEvent], None]

#: How often to ask the pad what its charge is while the link is up.
#:
#: It declares notifications on the standard Battery characteristic and does not send any:
#: measured over a quarter of an hour with none arriving, which matches everything else
#: about this device — "nothing is sent unprompted" (``docs/HARDWARE-BLE.md`` section 1).
#: The notification subscription stays anyway, because it costs nothing and a device that
#: does volunteer one should be heard.
#:
#: Without this the reading would be whatever it was at the last connect, and the link is
#: designed to stay up for days. It is not a fast-moving number — 77-86% one day and 46%
#: three days later, so roughly half a point an hour — and half an hour is finer than it
#: can actually move.
BATTERY_INTERVAL = timedelta(minutes=30)


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

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, address: str, name: str) -> None:
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
        self.entry = entry
        self.device_name = name
        self._client: BleakClient | None = None
        self._connect_lock = asyncio.Lock()
        self._parser = ParserState()
        self._shutdown = False
        self._midi_listeners: list[MidiListener] = []
        #: What the last connect found and did, or None if it never got that far.
        self.arming: ArmResult | None = None
        #: What the device says about itself, once somebody has connected and asked. None
        #: until then, which is most of the first minute after a restart.
        self.manufacturer: str | None = None
        self.model: str | None = None
        self.battery: int | None = None
        self._battery_timer: CALLBACK_TYPE | None = None

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
            await self._async_introduce(client)
            LOGGER.info(
                "%s: connected to %s (MTU %s)", self.address, self.device_name, client.mtu_size
            )
            await self._async_arm(client)

        self.async_update_listeners()

    async def _async_introduce(self, client: BleakClient) -> None:
        """Ask the device who it is and how much charge it has left.

        None of this is on the advertisement. Passive scanning never requests the scan
        response, which is where a friendly name would be, so until something connects the
        only thing Home Assistant has to call this device is its MAC — which is exactly
        what it was showing.

        Deliberately not fatal, the same as arming. A device that will not answer its
        Device Information service is still a perfectly good source of MIDI, and dropping
        the link over a cosmetic read would cost more than the name is worth.
        """
        self.device_name = await self._async_text(client, DEVICE_NAME_UUID) or self.device_name
        self.manufacturer = await self._async_text(client, MANUFACTURER_UUID)
        model = await self._async_text(client, MODEL_UUID)
        # "ble device" is the chip vendor's default, not a product. The name the device
        # advertises under is the one on the box.
        self.model = (
            model if model and model.lower() not in USELESS_MODEL_NAMES else self.device_name
        )

        await self._async_read_battery(client)
        try:
            await client.start_notify(BATTERY_UUID, self._on_battery)
        except (BleakError, EOFError, TimeoutError) as err:
            # Reading it once is most of the value; a pad that will not notify simply
            # reports whatever it had at the last connect.
            LOGGER.debug("%s: no battery notifications: %r", self.address, err)
        LOGGER.debug(
            "%s: introduced itself as %r by %r, model %r, battery %s",
            self.address,
            self.device_name,
            self.manufacturer,
            self.model,
            f"{self.battery}%" if self.battery is not None else "not reported",
        )
        self._stop_asking_about_the_battery()
        self._battery_timer = async_track_time_interval(
            self.hass, self._async_poll_battery, BATTERY_INTERVAL
        )
        self._describe_device()

    async def _async_poll_battery(self, _now: datetime) -> None:
        """Ask again. See BATTERY_INTERVAL: it will not tell us on its own."""
        client = self._client
        if client is None or not client.is_connected:
            return
        before = self.battery
        await self._async_read_battery(client)
        if self.battery != before:
            LOGGER.debug("%s: battery now %s%%", self.address, self.battery)
            self.async_update_listeners()

    @callback
    def _stop_asking_about_the_battery(self) -> None:
        """Cancel the poll. Safe to call when there is nothing to cancel."""
        if self._battery_timer is not None:
            self._battery_timer()
            self._battery_timer = None

    @callback
    def _describe_device(self) -> None:
        """Put what the device said about itself where Home Assistant will show it.

        The entities carry this in their ``DeviceInfo`` too, but they were created before
        anything had connected, when a MAC address was all there was. The registry keeps
        what it was told first, so it has to be told again.

        Only the *default* name is touched. Whatever the owner renamed the device to lives
        separately as ``name_by_user`` and outranks this, so a rename is never undone.
        """
        changes: dict[str, str] = {}
        if self.device_name and self.device_name != self.address:
            changes["name"] = self.device_name
        if self.manufacturer:
            changes["manufacturer"] = self.manufacturer
        if self.model:
            changes["model"] = self.model
        if not changes:
            return

        devices = dr.async_get(self.hass)
        device = devices.async_get_device(identifiers={(DOMAIN, format_mac(self.address))})
        if device is not None:
            devices.async_update_device(device.id, **changes)  # type: ignore[arg-type]

        # And the entry, which is what the integration's own page is headed with. It was
        # created from an advertisement that carried no name at all.
        if "name" in changes and self.entry.title == self.address:
            self.hass.config_entries.async_update_entry(self.entry, title=changes["name"])

    async def _async_text(self, client: BleakClient, uuid: str) -> str | None:
        """One standard string characteristic, or None if the device will not say."""
        try:
            raw = await client.read_gatt_char(uuid)
        except (BleakError, EOFError, TimeoutError) as err:
            LOGGER.debug("%s: could not read %s: %r", self.address, uuid, err)
            return None
        return bytes(raw).decode("utf-8", "replace").strip("\x00").strip() or None

    async def _async_read_battery(self, client: BleakClient) -> None:
        """Whatever charge the device reports, as a percentage."""
        try:
            raw = await client.read_gatt_char(BATTERY_UUID)
        except (BleakError, EOFError, TimeoutError) as err:
            LOGGER.debug("%s: could not read the battery: %r", self.address, err)
            return
        if raw:
            self.battery = raw[0]

    def _on_battery(self, _characteristic: BleakGATTCharacteristic, data: bytearray) -> None:
        """The device volunteering a new charge level."""
        if not data:
            return
        self.battery = data[0]
        LOGGER.debug("%s: battery %d%%", self.address, self.battery)
        self.async_update_listeners()

    async def _async_arm(self, client: BleakClient) -> None:
        """Read the device's own map and arm it for host control.

        Deliberately not fatal. A device whose configuration cannot be read is still a
        perfectly good source of MIDI, and dropping the link over it would cost more than
        the LEDs are worth.
        """
        session = VendorSession(client, self.address, stopping=lambda: self._shutdown)
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
        self._stop_asking_about_the_battery()
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
                try:
                    listener(event)
                except Exception:
                    # One listener must not silence the others. They are the event
                    # entities and the surface, which know nothing about each other, and
                    # without this a mistake in either stops the pad responding at all
                    # while the only sign of it is a line in the proxy's log. Broad on
                    # purpose: there is nothing useful to do with any of them here except
                    # keep going.
                    LOGGER.exception("%s: listener failed on %s", self.address, _describe(event))

    # -------------------------------------------------------------- sending

    async def async_send(self, midi: bytes) -> None:
        """Send one MIDI message to the device."""
        client = self._client
        if client is None:
            raise BleakError(f"{self.address}: not connected")
        await client.write_gatt_char(MIDI_CHAR_UUID, frame_midi(midi), response=False)

    async def async_send_many(self, messages: Sequence[bytes]) -> None:
        """Send several MIDI messages, packed into as few packets as will hold them.

        A whole sixteen-pad frame fits in one write, which is the difference between the
        grid redrawing at sixty frames a second and at four. Nothing is sent at all when
        there is nothing to say.
        """
        client = self._client
        if client is None:
            raise BleakError(f"{self.address}: not connected")
        if not messages:
            return
        for packet in frame_many(messages, client.mtu_size):
            await client.write_gatt_char(MIDI_CHAR_UUID, packet, response=False)

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
        """Close the link. Safe to call when already disconnected.

        The listeners are told, exactly as they are when the link drops by itself. Without
        that, anything watching this coordinator never learns the device has gone: at Home
        Assistant's own shutdown nothing else fires, so a surface with an animated pad on
        it would keep redrawing thirty times a second against a dead link.
        """
        self._shutdown = True
        # Before closing rather than after: a timer that fires between the two would find
        # the client gone, which is harmless, but a timer that outlives the coordinator
        # entirely is a wake-up Home Assistant keeps honouring after the entry is unloaded.
        self._stop_asking_about_the_battery()
        try:
            await self._async_close()
        finally:
            self.async_update_listeners()

    async def _async_close(self) -> None:
        """Drop the link itself."""
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
