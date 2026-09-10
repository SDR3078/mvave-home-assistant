"""The two rhythms, which are what carries state now that brightness cannot.

A grid with no brightness channel has three channels left: colour, position and time. The
first two are spent on identity and on what a pad is. This module is the third.

There are exactly two rhythms and there will not be a third. Blinking is the scarce
channel: it is the one accessibility fallback that survives colour vision deficiency, it is
the only thing that reads as urgent, and the documented failure mode across every grid
controller people actually use is not the rate but the **density**, several things moving
at once until none of them can be read. Every extra meaning assigned to motion makes the
existing ones harder to see.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final


@dataclass(frozen=True, slots=True)
class Rhythm:
    """A pad alternating between its colour and off, on a fixed period."""

    #: Seconds for one full cycle.
    period: float
    #: The fraction of the cycle the pad is lit.
    duty: float

    def lit(self, elapsed: float) -> bool:
        """Whether the pad is showing its colour at this moment."""
        return (elapsed % self.period) < self.period * self.duty


#: The knob's current target. Slow and lopsided, lit for most of the cycle, so it reads as
#: something breathing rather than something wrong. Confirmed legible on a full page
#: without pulling the eye away from the rest of the grid.
BREATHE: Final = Rhythm(period=1.4, duty=0.65)

#: Commanded but not yet confirmed. Fast and even, which reads as a fault, which is exactly
#: the meaning wanted: the surface and the world disagree. This is the same convention
#: every comparable controller uses for a queued or pending state, and Home Assistant's own
#: interface pulses at a similar rate for locking and unlocking.
#:
#: It is deliberately the only other rhythm. The two differ in rate, in duty cycle and in
#: role, and nothing else on the grid is allowed to move.
ALERT: Final = Rhythm(period=0.4, duty=0.5)

#: How often a page carrying a rhythm has to be redrawn for it to look smooth. The link
#: sustains sixty full-grid frames a second, so this is comfortable rather than tight, and
#: a page with no rhythm on it is not redrawn at all.
TICK_SECONDS: Final = 1 / 30
