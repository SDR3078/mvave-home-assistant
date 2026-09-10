"""Control layouts, and how a device is matched to one."""

from __future__ import annotations

import pytest
from devices import FALLBACK_LAYOUT, SMC_PAD_FACTORY_LAYOUT, resolve_layout


def test_the_factory_layout_matches_what_was_measured() -> None:
    layout = SMC_PAD_FACTORY_LAYOUT
    assert layout.model == "SMC-PAD"
    assert len(layout.pads) == 16
    assert len(layout.buttons) == 5
    assert len(layout.knobs) == 8

    # Pads: notes 36-51 on channel 10, numbered as the device numbers them.
    assert [pad.note for pad in layout.pads] == list(range(36, 52))
    assert {pad.channel for pad in layout.pads} == {9}
    assert layout.pads[0].number == 1 and layout.pads[0].key == "pad_1"

    # Buttons: CC 25-29 on channel 1, in the measured order.
    assert [(b.name, b.cc) for b in layout.buttons] == [
        ("Left", 25),
        ("Right", 26),
        ("Play", 27),
        ("Stop", 28),
        ("Record", 29),
    ]
    assert {button.channel for button in layout.buttons} == {0}

    # Encoders: eight physical knobs, each on one controller per bank.
    assert [knob.ccs for knob in layout.knobs] == [{1: 30 + i, 2: 38 + i} for i in range(8)]
    assert {knob.channel for knob in layout.knobs} == {0}


def test_every_control_key_is_unique() -> None:
    layout = SMC_PAD_FACTORY_LAYOUT
    keys = [c.key for c in (*layout.pads, *layout.buttons, *layout.knobs)]
    assert len(keys) == len(set(keys))


def test_a_controller_number_resolves_to_one_knob_and_bank() -> None:
    knob = SMC_PAD_FACTORY_LAYOUT.knobs[0]
    assert knob.bank_of(30) == 1
    assert knob.bank_of(38) == 2
    assert knob.bank_of(31) is None


@pytest.mark.parametrize("name", ["SMC-PAD", "smc-pad", "  SMC-Pad  "])
def test_a_known_name_resolves(name: str) -> None:
    assert resolve_layout(name) is SMC_PAD_FACTORY_LAYOUT


@pytest.mark.parametrize("name", ["AA:BB:CC:DD:EE:FF", "aa:bb:cc:dd:ee:ff"])
def test_a_bare_address_means_the_device_told_us_nothing(name: str) -> None:
    # Passive scanning never requests the scan response, where the friendly name lives,
    # so a passively discovered device is known only by its address. That is not evidence
    # of a different device, so the only supported layout is assumed.
    assert resolve_layout(name) is FALLBACK_LAYOUT


@pytest.mark.parametrize("name", ["SMC-Mixer", "Some Other Pad", "77:B4:86:09:DF"])
def test_an_unrecognised_name_gets_no_layout(name: str) -> None:
    # A name is evidence. Guessing a layout from one we do not know would invent
    # controls the device does not have.
    assert resolve_layout(name) is None
