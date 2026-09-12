"""What a knob adjusts, read out of an entity and written back to it.

Eight relative encoders with no rings, so a knob has no position of its own and cannot be
read at a glance. Everything a knob knows comes from the entity it is pointed at, and
everything it shows goes on the grid.

Each property normalises to nought and one. That is not a convenience: it is what lets one
value bar, one clamp and one step size serve brightness, volume, a blind and a thermostat,
and it keeps the arithmetic that everybody gets wrong in one place with tests around it.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Final

from .model import EntityState
from .palette import property_colour

#: How many encoders there are. Eight, and they do not move.
KNOB_COUNT: Final = 8


def _anything(state: EntityState) -> bool:
    """Every entity of the right domain can do this."""
    return True


@dataclass(frozen=True, slots=True)
class Property:
    """One adjustable thing, and how to read and write it."""

    key: str
    #: Domains this applies to. A knob pointed at anything else is inert rather than
    #: doing something surprising.
    domains: frozenset[str]
    #: Entity state to a number between nought and one, or None if it cannot be read.
    read: Callable[[EntityState], float | None]
    #: Entity and a number between nought and one, to the service and data that set it.
    write: Callable[[EntityState, float], tuple[str, str, dict[str, Any]]]
    #: How far one click moves it. A sixteenth is one pad of the bar; a thermostat wants
    #: finer than that, because half a degree is what people expect from a click.
    step: float = 1 / 16
    #: Whether this particular entity can actually do it. The domain is not enough: a bulb
    #: that only switches on and off is still a light, and a knob that claims it can dim
    #: one is lying. Without this the surface sends a brightness to something that has
    #: none, and cannot answer "which knobs are live" with anything true.
    supports: Callable[[EntityState], bool] = _anything

    @property
    def colour(self) -> int:
        """The colour that names this property, wherever it is drawn.

        Looked up rather than stored, so the knob map and this property's own value bar
        cannot be given different colours by anybody editing one of them.
        """
        return property_colour(self.key)


# ------------------------------------------------------------------- capabilities

#: How Home Assistant itself decides what a light can do, from
#: ``homeassistant.components.light``: every colour mode except ``onoff`` and ``unknown``
#: carries a brightness, colour needs one of the five modes that carry a hue, and colour
#: temperature is a mode of its own. Held here as plain strings rather than imported,
#: because nothing in this package may touch the platform.
BRIGHTNESS_MODES: Final = frozenset(
    {"brightness", "color_temp", "hs", "xy", "rgb", "rgbw", "rgbww", "white"}
)
COLOUR_MODES: Final = frozenset({"hs", "xy", "rgb", "rgbw", "rgbww"})

#: Feature bits, each from its own domain's ``EntityFeature`` flag. Same reasoning: the
#: numbers are part of Home Assistant's published interface, the imports are not available.
VOLUME_SET: Final = 4  # MediaPlayerEntityFeature.VOLUME_SET
SET_POSITION: Final = 4  # CoverEntityFeature.SET_POSITION
TARGET_TEMPERATURE: Final = 1  # ClimateEntityFeature.TARGET_TEMPERATURE
SET_SPEED: Final = 1  # FanEntityFeature.SET_SPEED


def _colour_modes(state: EntityState) -> frozenset[str]:
    """What a light says it can be, normalised to plain strings.

    Home Assistant puts enum members in the attribute, and a test puts strings there. Both
    have to work, and comparing them by value is the only thing that does.
    """
    modes = state.attributes.get("supported_color_modes")
    if not isinstance(modes, (list, tuple, set, frozenset)):
        return frozenset()
    return frozenset(str(mode) for mode in modes)


def _feature(state: EntityState, bit: int) -> bool:
    """Whether an entity declares one of its domain's feature bits."""
    features = state.attributes.get("supported_features")
    # A flag enum is an int, so this covers both.
    return isinstance(features, int) and bool(features & bit)


def _dimmable(state: EntityState) -> bool:
    return not BRIGHTNESS_MODES.isdisjoint(_colour_modes(state))


def _colourable(state: EntityState) -> bool:
    return not COLOUR_MODES.isdisjoint(_colour_modes(state))


def _tunable(state: EntityState) -> bool:
    return "color_temp" in _colour_modes(state)


def _span(
    state: EntityState, low: str, high: str, fallback: tuple[float, float]
) -> tuple[float, float]:
    """The range an attribute moves in, falling back when the entity does not say."""
    minimum = state.attributes.get(low)
    maximum = state.attributes.get(high)
    if (
        isinstance(minimum, (int, float))
        and isinstance(maximum, (int, float))
        and maximum > minimum
    ):
        return float(minimum), float(maximum)
    return fallback


def _number(state: EntityState, attribute: str) -> float | None:
    value = state.attributes.get(attribute)
    return float(value) if isinstance(value, (int, float)) else None


# ------------------------------------------------------------------------ lights


def _read_brightness(state: EntityState) -> float | None:
    value = _number(state, "brightness")
    return None if value is None else value / 255


def _write_brightness(state: EntityState, value: float) -> tuple[str, str, dict[str, Any]]:
    return "light", "turn_on", {"entity_id": state.entity_id, "brightness": round(value * 255)}


def _read_colour_temp(state: EntityState) -> float | None:
    kelvin = _number(state, "color_temp_kelvin")
    if kelvin is None:
        return None
    low, high = _span(state, "min_color_temp_kelvin", "max_color_temp_kelvin", (2000.0, 6500.0))
    return (kelvin - low) / (high - low)


def _write_colour_temp(state: EntityState, value: float) -> tuple[str, str, dict[str, Any]]:
    low, high = _span(state, "min_color_temp_kelvin", "max_color_temp_kelvin", (2000.0, 6500.0))
    return (
        "light",
        "turn_on",
        {"entity_id": state.entity_id, "color_temp_kelvin": round(low + value * (high - low))},
    )


def _hs(state: EntityState) -> tuple[float, float] | None:
    value = state.attributes.get("hs_color")
    if isinstance(value, (list, tuple)) and len(value) == 2:
        return float(value[0]), float(value[1])
    return None


def _read_hue(state: EntityState) -> float | None:
    pair = _hs(state)
    return None if pair is None else pair[0] / 360


def _write_hue(state: EntityState, value: float) -> tuple[str, str, dict[str, Any]]:
    saturation = (_hs(state) or (0.0, 100.0))[1]
    return (
        "light",
        "turn_on",
        {"entity_id": state.entity_id, "hs_color": [round(value * 360, 1), saturation]},
    )


def _read_saturation(state: EntityState) -> float | None:
    pair = _hs(state)
    return None if pair is None else pair[1] / 100


def _write_saturation(state: EntityState, value: float) -> tuple[str, str, dict[str, Any]]:
    hue = (_hs(state) or (0.0, 0.0))[0]
    return (
        "light",
        "turn_on",
        {"entity_id": state.entity_id, "hs_color": [hue, round(value * 100, 1)]},
    )


# ------------------------------------------------------------- everything else


def _read_volume(state: EntityState) -> float | None:
    return _number(state, "volume_level")


def _write_volume(state: EntityState, value: float) -> tuple[str, str, dict[str, Any]]:
    return (
        "media_player",
        "volume_set",
        {"entity_id": state.entity_id, "volume_level": round(value, 3)},
    )


def _read_position(state: EntityState) -> float | None:
    value = _number(state, "current_position")
    return None if value is None else value / 100


def _write_position(state: EntityState, value: float) -> tuple[str, str, dict[str, Any]]:
    return (
        "cover",
        "set_cover_position",
        {"entity_id": state.entity_id, "position": round(value * 100)},
    )


def _read_temperature(state: EntityState) -> float | None:
    value = _number(state, "temperature")
    if value is None:
        return None
    low, high = _span(state, "min_temp", "max_temp", (7.0, 35.0))
    return (value - low) / (high - low)


def _write_temperature(state: EntityState, value: float) -> tuple[str, str, dict[str, Any]]:
    low, high = _span(state, "min_temp", "max_temp", (7.0, 35.0))
    degrees = low + value * (high - low)
    return (
        "climate",
        "set_temperature",
        {"entity_id": state.entity_id, "temperature": round(degrees * 2) / 2},
    )


def _read_percentage(state: EntityState) -> float | None:
    value = _number(state, "percentage")
    return None if value is None else value / 100


def _write_percentage(state: EntityState, value: float) -> tuple[str, str, dict[str, Any]]:
    return "fan", "set_percentage", {"entity_id": state.entity_id, "percentage": round(value * 100)}


#: Half a degree per click, expressed against the usual thermostat range, because that is
#: what a click of a thermostat means to everybody who has ever used one.
TEMPERATURE_STEP: Final = 0.5 / 28

PROPERTIES: Final[Mapping[str, Property]] = {
    "brightness": Property(
        "brightness",
        frozenset({"light"}),
        _read_brightness,
        _write_brightness,
        supports=_dimmable,
    ),
    "color_temp": Property(
        "color_temp",
        frozenset({"light"}),
        _read_colour_temp,
        _write_colour_temp,
        supports=_tunable,
    ),
    "hue": Property("hue", frozenset({"light"}), _read_hue, _write_hue, supports=_colourable),
    "saturation": Property(
        "saturation",
        frozenset({"light"}),
        _read_saturation,
        _write_saturation,
        supports=_colourable,
    ),
    "volume": Property(
        "volume",
        frozenset({"media_player"}),
        _read_volume,
        _write_volume,
        supports=lambda state: _feature(state, VOLUME_SET),
    ),
    "position": Property(
        "position",
        frozenset({"cover"}),
        _read_position,
        _write_position,
        supports=lambda state: _feature(state, SET_POSITION),
    ),
    "temperature": Property(
        "temperature",
        frozenset({"climate"}),
        _read_temperature,
        _write_temperature,
        step=TEMPERATURE_STEP,
        supports=lambda state: _feature(state, TARGET_TEMPERATURE),
    ),
    "percentage": Property(
        "percentage",
        frozenset({"fan"}),
        _read_percentage,
        _write_percentage,
        supports=lambda state: _feature(state, SET_SPEED),
    ),
}

#: What the encoders adjust, most reached-for first. A ranking, not a list: brightness is
#: at the front because it is what people want from a lamp nine times in ten.
RANKED_PROPERTIES: Final = (
    "brightness",
    "color_temp",
    "hue",
    "saturation",
    "volume",
    "position",
    "temperature",
    "percentage",
)


def packed(state: EntityState) -> list[Property]:
    """Everything this entity can actually be adjusted by, most reached-for first.

    The encoders are handed these in order, from the top left, with no gaps. So the first
    encoder always adjusts the main thing — brightness on a lamp, volume on a speaker,
    position on a blind, the setpoint on a thermostat, speed on a fan — and anything else
    the entity has follows beside it.

    This replaced a fixed global assignment, where each encoder owned one property for
    ever. That sounded like the thing muscle memory wants and was not: four of the five
    kinds of thing in a house have exactly one adjustable value, so a fixed table put that
    one value on four *different* encoders and left seven of the eight dead in every case.
    What somebody actually learns from this is one fact instead of seven, and it is the
    useful one. A lamp, the only thing here with more than one control, is unaffected:
    its four still land on the same four encoders they always did.

    Two ways for an entity not to offer something, and both matter. The domain can be
    wrong — a lamp has no volume. Or the domain can be right and the *entity* still cannot:
    a bulb that only switches is a light with no brightness, and a colour bulb with no
    white LEDs is a light with no colour temperature.
    """
    return [
        candidate
        for key in RANKED_PROPERTIES
        if (candidate := PROPERTIES.get(key)) is not None
        and state.domain in candidate.domains
        and candidate.supports(state)
    ]


def primary_for(state: EntityState) -> Property | None:
    """The one value worth showing for an entity, for a peek.

    Which is simply the first thing it offers: the ranking already puts the main one at the
    front, and the first encoder is the one that gets it.
    """
    offered = packed(state)
    return offered[0] if offered else None
