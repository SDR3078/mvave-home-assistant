"""The colours the grid can actually show, and what each one means.

Every value here is a velocity, sent as the second data byte of a note-on to the note a
pad's Led byte is armed to. The names are the owner's, from walking the palette one value
at a time and then judging the whole thing side by side on the physical grid
(``docs/HARDWARE-BLE.md`` section 9.1).

Two facts shape everything below. The palette is pastel throughout, with no saturated
entry at any index and no true red. And there is **no brightness channel on this device by
any route**: not in the palette, which has no shade families, not on the MIDI channel,
which is ignored on all sixteen, and not by switching a pad fast enough to average it,
which reads as flicker. So state is carried by colour, by position and by slow motion.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Final

#: Off. Velocity 127 is a second off, and 96 to 126 leave the pad unchanged, so neither
#: may ever be used as a colour (HARDWARE-BLE.md section 9).
OFF: Final = 0

BLUE: Final = 21
GREEN: Final = 5
ORANGE: Final = 15
RED: Final = 14  # red-pink; the palette has no saturated red at any index
PURPLE: Final = 24
WHITE: Final = 40

#: The five that survived being shown together, scattered across the grid, judged from
#: across a room. Every sixth candidate tried collapsed into one of these: a second green
#: into GREEN, a pink into RED, a turquoise into BLUE, a light yellow into WHITE.
#:
#: Ordered so that a household with only a few pages never reaches the pair most at risk
#: under red-green colour vision deficiency.
IDENTITY: Final = (BLUE, PURPLE, GREEN, ORANGE, RED)

#: What an entity looks like inside a page.
#:
#: **Colour says what it is, white says it is off.** That split is what lets a pad carry
#: its own colour without state losing its channel: the question "is anything on in here"
#: stays answerable at a glance, because it becomes "is that pad white". White is the one
#: value that is never configurable, and the whole readability of a page rests on it.
ON: Final = ORANGE
STATE_OFF: Final = WHITE

#: A pad with nothing behind it. Distinct from STATE_OFF on purpose: pressing it does
#: nothing, and it must not look like an entity that happens to be switched off.
UNASSIGNED: Final = OFF

#: What a pad shows when it is on, by the domain of what is behind it. A default only:
#: any pad can be given a colour of its own, and any of these can be changed.
#:
#: Purple appears only where the entity is stateless. Everything with an on and an off
#: shows white when it is off, and purple against white is the one pair that was reported
#: as too close on the physical grid, so a lamp coloured purple would be unreadable
#: exactly when it mattered. A scene never shows white and so never runs into that.
DOMAIN_COLOURS: Final[Mapping[str, int]] = {
    "light": ORANGE,
    "switch": ORANGE,
    "input_boolean": ORANGE,
    "fan": ORANGE,
    "siren": ORANGE,
    "media_player": BLUE,
    "cover": GREEN,
    "climate": RED,
    "lock": RED,
    "alarm_control_panel": RED,
    "vacuum": RED,
    "scene": PURPLE,
    "script": PURPLE,
    "button": PURPLE,
    "input_button": PURPLE,
}

#: For anything not listed, and for a pad with no entity behind it at all.
ACTION: Final = ORANGE


def colour_for(domain: str) -> int:
    """The colour a domain shows when it is on, before any configuration is applied."""
    return DOMAIN_COLOURS.get(domain, ACTION)


#: Bar colours, one flat colour per property rather than a gradient. Every ramp the design
#: originally wanted needed many graded steps along one hue, which needs a brightness or a
#: saturation control the device does not have.
BAR_COLOURS: Final = {
    "brightness": WHITE,
    "color_temp": ORANGE,
    "saturation": GREEN,
    "volume": GREEN,
    "position": BLUE,
    "temperature": BLUE,
}

#: Above a configured threshold a bar switches to this, a different colour appearing
#: rather than a shade changing.
BAR_ALERT: Final = RED

#: Values the device ignores or treats as off, which a renderer must never emit as a
#: colour. 64 to 95 are one flat white-blue and are usable but pointless.
UNUSABLE: Final = frozenset(range(96, 128))


def is_emittable(velocity: int) -> bool:
    """Whether this velocity can be sent as a colour.

    Zero is allowed: it is how a pad is put out. Everything from 96 up is not, because
    96 to 126 leave the pad showing whatever it showed before, which would silently
    desynchronise the grid from the frame the engine thinks it drew, and 127 is a second
    off that would look like a bug the first time somebody used it as a brightness.
    """
    return 0 <= velocity < 96
