"""What the coordinator does when arming fails.

Arming reads the device's own map and turns its LEDs over to the host. It is deliberately
not fatal — a pad whose map cannot be read is still a good source of MIDI — but until
2026-09-16 it was also never retried: the link was kept, the runner saw no arming and
built no surface, and the grid stayed dark until the link happened to drop, which is
designed to be days.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from homeassistant.core import HomeAssistant

import custom_components.mvave.coordinator as coordinator_module
from custom_components.mvave.coordinator import ARM_ATTEMPTS, MvaveCoordinator
from custom_components.mvave.vendor import VendorError


class FakeSession:
    """A vendor channel that is there; whether arming works is the test's business."""

    available = True

    def __init__(self, client: Any, address: str, stopping: Any) -> None:
        pass

    async def __aenter__(self) -> FakeSession:
        return self

    async def __aexit__(self, *exc: Any) -> None:
        pass


class FakeClient:
    """Remembers whether it was dropped."""

    def __init__(self) -> None:
        self.disconnects = 0

    async def disconnect(self) -> None:
        self.disconnects += 1


@pytest.fixture
def coordinator(hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch) -> MvaveCoordinator:
    monkeypatch.setattr(coordinator_module, "VendorSession", FakeSession)
    made = MvaveCoordinator.__new__(MvaveCoordinator)
    made.address = "AA:BB:CC:DD:EE:FF"
    made._shutdown = False
    made.arming = None
    made._arm_failures = 0
    return made


async def test_a_failed_arm_drops_the_link_so_the_reconnect_tries_again(
    coordinator: MvaveCoordinator, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def cannot(session: Any) -> Any:
        raise VendorError("bad checksum reading 0x0000")

    monkeypatch.setattr(coordinator_module, "async_arm", cannot)
    client = FakeClient()
    await coordinator._async_arm(client)  # type: ignore[arg-type]
    assert coordinator.arming is None
    assert client.disconnects == 1  # the ordinary reconnect path arms again
    assert coordinator._arm_failures == 1


async def test_a_device_that_can_never_be_armed_keeps_its_link_in_the_end(
    coordinator: MvaveCoordinator, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Bounded, or a device with no readable map would be dropped and reconnected for ever.
    async def cannot(session: Any) -> Any:
        raise VendorError("no reply")

    monkeypatch.setattr(coordinator_module, "async_arm", cannot)
    client = FakeClient()
    for _ in range(ARM_ATTEMPTS + 1):
        await coordinator._async_arm(client)  # type: ignore[arg-type]
    assert client.disconnects == ARM_ATTEMPTS  # tried that many times
    assert coordinator._arm_failures == ARM_ATTEMPTS + 1  # then kept the link, unarmed


async def test_a_successful_arm_forgets_earlier_failures(
    coordinator: MvaveCoordinator, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def armed(session: Any) -> Any:
        return SimpleNamespace(layout=None)

    monkeypatch.setattr(coordinator_module, "async_arm", armed)
    coordinator._arm_failures = 2
    await coordinator._async_arm(FakeClient())  # type: ignore[arg-type]
    assert coordinator.arming is not None
    assert coordinator._arm_failures == 0
