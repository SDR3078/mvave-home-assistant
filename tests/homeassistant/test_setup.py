"""Setting the integration up for real, and what an options save does to it.

Nothing else here runs `async_setup_entry`. Two README promises rest on one line inside
it — "saving anything rebuilds the surface in place" and "it does not reconnect" — and a
review found that replacing that line with the reload most integrations use, or deleting
it, left every test green.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from homeassistant.components import bluetooth
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_NAME
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.mvave.const import CONF_ADDRESS, DOMAIN
from custom_components.mvave.coordinator import MvaveCoordinator
from custom_components.mvave.runner import SurfaceRunner

ADDRESS = "AA:BB:CC:DD:EE:FF"


@pytest.fixture
async def loaded(
    hass: HomeAssistant, enable_bluetooth: None, monkeypatch: pytest.MonkeyPatch
) -> MockConfigEntry:
    """The integration set up for real, minus the radio.

    Set-up refuses to load until a scanner has seen the address, and only truth-tests the
    device it is handed, so a stand-in is enough there; and the coordinator's start is a
    no-op so nothing ever tries to connect. Everything else — platforms, entities, the
    runner, the update listener — is the real thing.
    """
    monkeypatch.setattr(
        bluetooth,
        "async_ble_device_from_address",
        lambda hass, address, connectable=True: SimpleNamespace(address=address, name="SMC-PAD"),
    )
    monkeypatch.setattr(MvaveCoordinator, "async_start", lambda self: lambda: None)
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=ADDRESS.lower(),
        title="SMC-PAD",
        data={CONF_ADDRESS: ADDRESS, CONF_NAME: "SMC-PAD"},
        minor_version=2,
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    return entry


async def test_saving_options_rebuilds_in_place_and_does_not_reload(
    hass: HomeAssistant, loaded: MockConfigEntry, monkeypatch: pytest.MonkeyPatch
) -> None:
    rebuilt: list[Any] = []
    reloaded: list[str] = []
    monkeypatch.setattr(SurfaceRunner, "reconfigure", lambda self, profile=None: rebuilt.append(1))
    monkeypatch.setattr(
        hass.config_entries, "async_reload", lambda entry_id: reloaded.append(entry_id)
    )
    runner_before = loaded.runtime_data.runner

    result = await hass.config_entries.options.async_init(loaded.entry_id)
    boxes = {
        str(key): (getattr(key, "description", None) or {}).get("suggested_value") or []
        for key in result["data_schema"].schema
        if str(key) != "default_page"
    }
    saved = await hass.config_entries.options.async_configure(result["flow_id"], boxes)
    await hass.async_block_till_done()

    assert saved["type"].value == "create_entry"
    assert rebuilt == [1]  # the listener is registered, and it rebuilds
    assert reloaded == []  # rather than reloading, which drops the link for twenty seconds
    assert loaded.runtime_data.runner is runner_before  # the same runner, the same link
    assert loaded.state is ConfigEntryState.LOADED
