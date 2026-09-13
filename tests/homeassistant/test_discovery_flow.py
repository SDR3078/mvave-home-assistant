"""Getting the pad into Home Assistant in the first place.

Two ways in, and they must agree: the device announces itself and somebody confirms it, or
somebody adds it by hand from whatever is in range. Both end in the same entry, and neither
may produce a second one for a device that is already set up.

Built from real ``BluetoothServiceInfoBleak`` objects rather than stand-ins. A fake with
the three attributes this flow happens to read would pass whatever the flow did with them.
"""

from __future__ import annotations

from typing import Any

import pytest
from bleak.backends.device import BLEDevice
from bleak.backends.scanner import AdvertisementData
from habluetooth import BluetoothServiceInfoBleak
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.mvave import config_flow as flow_module
from custom_components.mvave.const import CONF_ADDRESS, DOMAIN, MIDI_SERVICE_UUID

ADDRESS = "AA:BB:CC:DD:EE:FF"
OTHER = "11:22:33:44:55:66"


def seen(address: str, name: str, *uuids: str) -> BluetoothServiceInfoBleak:
    """One device, as the bluetooth integration would hand it over."""
    advertisement = AdvertisementData(
        local_name=name,
        manufacturer_data={},
        service_data={},
        service_uuids=list(uuids),
        tx_power=-127,
        rssi=-50,
        platform_data=(),
    )
    return BluetoothServiceInfoBleak(
        name=name,
        address=address,
        rssi=-50,
        manufacturer_data={},
        service_data={},
        service_uuids=list(uuids),
        source="local",
        device=BLEDevice(address, name, {}),
        advertisement=advertisement,
        connectable=True,
        time=0,
        tx_power=-127,
    )


PAD = seen(ADDRESS, "SMC-PAD", MIDI_SERVICE_UUID)
#: Something else entirely, advertising a service this integration knows nothing about.
KETTLE = seen(OTHER, "Kettle", "0000180f-0000-1000-8000-00805f9b34fb")


@pytest.fixture
def in_range(monkeypatch: pytest.MonkeyPatch) -> list[BluetoothServiceInfoBleak]:
    """Whatever the radio can currently see, which the manual step asks for."""
    devices: list[BluetoothServiceInfoBleak] = []

    async def _no_scan(hass: HomeAssistant) -> None:
        """An active scan costs a radio; the test only needs the list."""

    monkeypatch.setattr(flow_module, "async_request_active_scan", _no_scan)
    monkeypatch.setattr(
        flow_module, "async_discovered_service_info", lambda hass, connectable=True: devices
    )
    return devices


async def start(hass: HomeAssistant, source: str, **kwargs: Any) -> dict[str, Any]:
    return await hass.config_entries.flow.async_init(DOMAIN, context={"source": source}, **kwargs)


# --------------------------------------------------------------- announced


async def test_a_discovered_pad_is_confirmed_before_it_is_added(hass: HomeAssistant) -> None:
    result = await start(hass, "bluetooth", data=PAD)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "bluetooth_confirm"
    # Named in the dialog, because "a device was found" is not a thing anybody can answer.
    assert result["description_placeholders"] == {"name": "SMC-PAD", "address": ADDRESS}

    created = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert created["type"] is FlowResultType.CREATE_ENTRY
    assert created["title"] == "SMC-PAD"
    assert created["data"][CONF_ADDRESS] == ADDRESS


async def test_a_pad_that_is_already_set_up_is_not_offered_again(hass: HomeAssistant) -> None:
    # It advertises whenever nothing holds it, so this fires repeatedly on a working setup.
    existing = MockConfigEntry(domain=DOMAIN, unique_id=ADDRESS.lower(), data={})
    existing.add_to_hass(hass)

    result = await start(hass, "bluetooth", data=PAD)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


# ----------------------------------------------------------------- by hand


async def test_adding_by_hand_offers_only_what_speaks_ble_midi(
    hass: HomeAssistant, in_range: list[BluetoothServiceInfoBleak]
) -> None:
    # A house is full of bluetooth. Everything without the BLE-MIDI service UUID is
    # something this integration would connect to and then have nothing to say to.
    in_range.extend([KETTLE, PAD])

    result = await start(hass, "user")
    assert result["type"] is FlowResultType.FORM
    choices = result["data_schema"].schema[CONF_ADDRESS].container
    assert list(choices) == [ADDRESS]
    assert choices[ADDRESS] == f"SMC-PAD ({ADDRESS})"


async def test_adding_by_hand_leaves_out_one_already_set_up(
    hass: HomeAssistant, in_range: list[BluetoothServiceInfoBleak]
) -> None:
    existing = MockConfigEntry(domain=DOMAIN, unique_id=ADDRESS.lower(), data={})
    existing.add_to_hass(hass)
    in_range.append(PAD)

    result = await start(hass, "user")
    # The only pad in range is the one already set up, so there is nothing left to offer.
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "no_devices_found"


async def test_nothing_in_range_says_so_rather_than_showing_an_empty_list(
    hass: HomeAssistant, in_range: list[BluetoothServiceInfoBleak]
) -> None:
    result = await start(hass, "user")
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "no_devices_found"


async def test_picking_one_by_hand_records_how_to_reach_it(
    hass: HomeAssistant, in_range: list[BluetoothServiceInfoBleak]
) -> None:
    in_range.append(PAD)
    result = await start(hass, "user")

    created = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_ADDRESS: ADDRESS}
    )
    assert created["type"] is FlowResultType.CREATE_ENTRY
    # An address and a name, and nothing that required connecting: the pad takes one
    # central at a time, and taking its slot during setup would hold it off whatever has it.
    assert created["data"] == {CONF_ADDRESS: ADDRESS, "name": "SMC-PAD"}
