"""Reading the device's memory over the vendor channel: what a bad answer does.

One corrupted chunk out of the twenty-eight an arming reads used to fail the whole thing,
and with the link kept nothing ever tried again. A bad reply is asked for again exactly
like a missing one.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from homeassistant.core import HomeAssistant

from custom_components.mvave.vendor import REPLY_ATTEMPTS, VendorError, VendorSession


class FakeClient:
    """Counts the read requests it is sent."""

    def __init__(self) -> None:
        self.writes = 0

    async def write_gatt_char(self, *args: Any, **kwargs: Any) -> None:
        self.writes += 1


def session_answering(replies: list[Any], monkeypatch: pytest.MonkeyPatch) -> VendorSession:
    """A session whose device answers with the given replies, in order."""
    made = VendorSession.__new__(VendorSession)
    made._client = FakeClient()  # type: ignore[assignment]
    made._address = "AA:BB:CC:DD:EE:FF"

    async def next_reply(address: int) -> Any:
        return replies.pop(0)

    monkeypatch.setattr(made, "_await_reply", next_reply)
    return made


async def test_a_bad_checksum_is_asked_for_again(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = session_answering(
        [
            SimpleNamespace(checksum_ok=False, data=b""),
            SimpleNamespace(checksum_ok=True, data=b"\x01\x02"),
        ],
        monkeypatch,
    )
    assert await session.read(0x10, 2) == b"\x01\x02"
    assert session._client.writes == 2  # type: ignore[attr-defined]


async def test_a_device_that_never_answers_well_is_given_up_on(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = session_answering(
        [SimpleNamespace(checksum_ok=False, data=b"")] * REPLY_ATTEMPTS, monkeypatch
    )
    with pytest.raises(VendorError):
        await session.read(0x10, 2)
    assert session._client.writes == REPLY_ATTEMPTS  # type: ignore[attr-defined]
