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
#: any of these can be changed. A pad may also carry a colour of its own — `PadConfig`
#: takes one and `resolve` honours it — but nothing sets one: `registry._pads_of` builds
#: every pinned pad from its domain alone, so per-pad colour is unreachable from
#: configuration and exists only for the engine's own tests.
#:
#: **Exactly the domains a pad can hold**, no more and no fewer — `resolve.PINNABLE`, which
#: a test asserts against. Two rows here were dead until 2026-09-13, `alarm_control_panel`
#: and `vacuum`, which nothing can put on a pad and which therefore offered a colour for
#: something nobody could ever see; `humidifier` was the other way round, reachable and
#: uncoloured, so it took the fallback and was orange by accident rather than by decision.
#:
#: Purple appears only where the pad is drawn as stateless. Everything with an on and an
#: off shows white when it is off, and purple against white is the one pair that was
#: reported as too close on the physical grid, so a lamp coloured purple would be
#: unreadable exactly when it mattered. A stateless pad never shows white and so never runs
#: into that — which is why `script` being purple while *not* being stateless was a defect,
#: found on 2026-09-13 and fixed by making a script stateless rather than by moving it.
DOMAIN_COLOURS: Final[Mapping[str, int]] = {
    "light": ORANGE,
    "switch": ORANGE,
    "input_boolean": ORANGE,
    "fan": ORANGE,
    "siren": ORANGE,
    "media_player": BLUE,
    "cover": GREEN,
    "humidifier": ORANGE,
    "climate": RED,
    "lock": RED,
    "scene": PURPLE,
    "script": PURPLE,
    "button": PURPLE,
    "input_button": PURPLE,
    # Readouts, all one colour. "This one is telling me rather than offering me" is then
    # one thing to learn instead of six, and it costs nothing from a budget that has
    # nothing left to spend. Green because green already means an opening and its
    # position, and a readout is overwhelmingly about exactly that: a door, a window,
    # whether anybody is in.
    #
    # It does collide, and the defence above is only half the argument. `cover` is green
    # too and a cover is *controllable*, so in a room holding a blind and a door sensor the
    # colour cannot say which of the two green pads will move something — only pressing
    # them can, one acting and the other shuddering. Left as it is because the pair is
    # coherent (both are "an opening, and whether it is open") and because the distinction
    # it would cost a colour to draw is already carried free by the shudder. A house that
    # minds can move either group on the colour screen, which is what that screen is for.
    "binary_sensor": GREEN,
    "device_tracker": GREEN,
    "person": GREEN,
    "sun": GREEN,
    "calendar": GREEN,
    "schedule": GREEN,
}

#: For anything not listed, and for a pad with no entity behind it at all.
ACTION: Final = ORANGE


def colour_for(domain: str) -> int:
    """The colour a domain shows when it is on, before any configuration is applied."""
    return DOMAIN_COLOURS.get(domain, ACTION)


#: What each velocity is called, for anything that has to describe the grid in words
#: rather than light it. A number is meaningless to somebody reading a mirror of the pad
#: on a screen, and these are the names the owner gave them at the grid.
NAMES: Final[Mapping[int, str]] = {
    OFF: "dark",
    BLUE: "blue",
    GREEN: "green",
    ORANGE: "orange",
    RED: "red",
    PURPLE: "purple",
    WHITE: "white",
}


def name_for(colour: int) -> str:
    """What a velocity is called, or the number itself if it is not one of the named."""
    return NAMES.get(colour, str(colour))


#: One flat colour per adjustable property, used in **both** places a property is ever
#: drawn: the knob map, and the value bar that appears when you turn that encoder. Not a
#: gradient — every ramp the design originally wanted needed graded steps along one hue,
#: which needs a brightness this device does not have.
#:
#: One rule, and it is the whole scheme: **orange is the level, and the other three are
#: colour.** Whatever you are holding, its main value is orange — brightness, volume, how
#: far a blind is open, a setpoint, a fan's speed. A lamp is the only thing in a house with
#: more than one control, and its other three are the colour ones.
#:
#: **Shared on purpose.** A colour on the map is a promise about the bar you will get if
#: you turn that encoder, which makes it the property's name rather than a decoration, and
#: makes the map teach the bar. The old bar colours could not do that job: brightness drew
#: a *white* bar, and white already means "this encoder does nothing" on the map, so the
#: commonest control and the absence of a control would have been the same colour. Hue and
#: saturation were both green, which never mattered while only one bar showed at a time and
#: matters completely once all four are on the grid together.
#:
#: Purple is deliberately unused. It is the one colour reported as too close to white on
#: the physical grid, and a map is mostly white.
PROPERTY_COLOURS: Final[Mapping[str, int]] = {
    "brightness": ORANGE,
    "volume": ORANGE,
    "position": ORANGE,
    "temperature": ORANGE,
    "percentage": ORANGE,
    "color_temp": BLUE,
    "hue": GREEN,
    "saturation": RED,
}

#: **Unused.** A bar was to switch to this above a configured threshold, a different colour
#: appearing rather than a shade changing. Nothing reads it and no threshold is configurable,
#: so it is a specification kept next to the values it would need rather than a behaviour.
BAR_ALERT: Final = RED

#: What an encoder shows on the map when it can do nothing to whatever is focused. The
#: grid's own rule — colour means it is there, white means it is not — applied to a knob.
MAP_DEAD: Final = WHITE


def property_colour(property_key: str) -> int:
    """The colour that names this property, on the map and on its own bar alike."""
    return PROPERTY_COLOURS.get(property_key, ORANGE)


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
    return velocity >= 0 and velocity not in UNUSABLE
