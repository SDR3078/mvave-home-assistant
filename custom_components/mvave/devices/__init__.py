"""Device-specific knowledge: vendor protocols and control layouts.

Pure Python, no Home Assistant imports. One module per device family.
"""

from __future__ import annotations

import re
from typing import Final

from .layout import ButtonSpec, DeviceLayout, KnobSpec, PadSpec
from .smc_pad import SMC_PAD_FACTORY_LAYOUT

__all__ = [
    "FALLBACK_LAYOUT",
    "SMC_PAD_FACTORY_LAYOUT",
    "ButtonSpec",
    "DeviceLayout",
    "KnobSpec",
    "PadSpec",
    "resolve_layout",
]

# Matched against the name the device advertises, lowercased. The SMC-PAD advertises
# "SMC-PAD"; siblings in the family are unmeasured, so nothing else is claimed here.
_BY_NAME: dict[str, DeviceLayout] = {"smc-pad": SMC_PAD_FACTORY_LAYOUT}

#: Assumed when a device gave no name at all. See resolve_layout.
FALLBACK_LAYOUT: Final = SMC_PAD_FACTORY_LAYOUT

_ADDRESS = re.compile(r"^(?:[0-9A-F]{2}:){5}[0-9A-F]{2}$", re.IGNORECASE)


def resolve_layout(name: str) -> DeviceLayout | None:
    """The layout for a device, or None if its controls are unknown.

    A device that never told us its name is treated differently from one that told us a
    name we do not recognise. Bluetooth LE puts a device's friendly name in the scan
    response, which passive scanning does not request, so a device discovered passively
    is known only by its address. That is not evidence of anything, so the only supported
    layout is assumed. A name we simply do not know is evidence, and gets no layout.

    Either way the connection still works and MIDI is still decoded; what is missing is
    only the per-control entities.

    Provisional. The device can be asked for its own map over the vendor channel, which
    is both authoritative and survives the user changing preset (docs/NEXT.md).
    """
    cleaned = name.strip()
    if _ADDRESS.match(cleaned):
        return FALLBACK_LAYOUT
    return _BY_NAME.get(cleaned.lower())
