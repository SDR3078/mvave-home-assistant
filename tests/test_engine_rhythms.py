"""The two rhythms, which are the only thing on the grid allowed to move.

With no brightness channel, motion is the last channel left after colour and position.
These tests are less about arithmetic than about keeping the vocabulary small: the
documented failure mode on every comparable grid controller is not the blink rate but the
density, several things moving until none of them can be read.
"""

from __future__ import annotations

from engine import rhythms
from engine.rhythms import ALERT, BREATHE, Rhythm


def test_breathing_is_lit_for_most_of_its_cycle() -> None:
    # Lopsided on purpose. An even fifty percent at this speed reads as a slow blink, and
    # a slow blink is a fault that has not been fixed yet.
    assert BREATHE.lit(0.0)
    assert BREATHE.lit(BREATHE.period * 0.6)
    assert not BREATHE.lit(BREATHE.period * 0.9)


def test_the_alert_is_even_and_much_faster() -> None:
    assert ALERT.lit(0.0)
    assert not ALERT.lit(ALERT.period * 0.75)
    assert ALERT.period * 3 < BREATHE.period


def test_the_two_rhythms_cannot_be_confused() -> None:
    # They differ in rate and in duty cycle, not in one or the other. Rate alone is a poor
    # discriminator at a glance; character is what people actually read.
    assert BREATHE.period / ALERT.period > 3
    assert abs(BREATHE.duty - ALERT.duty) > 0.1


def test_there_are_exactly_two_rhythms() -> None:
    # This is the point of the test file. A third meaning assigned to motion makes the
    # first two harder to see, so adding one has to be a deliberate edit here.
    defined = {
        name
        for name, value in vars(rhythms).items()
        if isinstance(value, Rhythm) and not name.startswith("_")
    }
    assert defined == {"BREATHE", "ALERT"}


def test_a_rhythm_repeats() -> None:
    for cycle in range(1, 4):
        assert BREATHE.lit(0.1) == BREATHE.lit(0.1 + BREATHE.period * cycle)


def test_the_tick_is_fast_enough_for_the_shorter_rhythm() -> None:
    # A blink whose period is close to the redraw interval turns into aliasing rather than
    # into motion. The link sustains sixty full-grid frames a second, so this is
    # comfortable rather than tight.
    assert ALERT.period > rhythms.TICK_SECONDS * 4
