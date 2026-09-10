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
from .palette import BAR_COLOURS, GREEN, WHITE


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
    colour: int = WHITE


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
        colour=BAR_COLOURS["brightness"],
    ),
    "color_temp": Property(
        "color_temp",
        frozenset({"light"}),
        _read_colour_temp,
        _write_colour_temp,
        colour=BAR_COLOURS["color_temp"],
    ),
    "hue": Property("hue", frozenset({"light"}), _read_hue, _write_hue, colour=GREEN),
    "saturation": Property(
        "saturation",
        frozenset({"light"}),
        _read_saturation,
        _write_saturation,
        colour=BAR_COLOURS["saturation"],
    ),
    "volume": Property(
        "volume",
        frozenset({"media_player"}),
        _read_volume,
        _write_volume,
        colour=BAR_COLOURS["volume"],
    ),
    "position": Property(
        "position",
        frozenset({"cover"}),
        _read_position,
        _write_position,
        colour=BAR_COLOURS["position"],
    ),
    "temperature": Property(
        "temperature",
        frozenset({"climate"}),
        _read_temperature,
        _write_temperature,
        step=TEMPERATURE_STEP,
        colour=BAR_COLOURS["temperature"],
    ),
    "percentage": Property(
        "percentage",
        frozenset({"fan"}),
        _read_percentage,
        _write_percentage,
        colour=BAR_COLOURS["brightness"],
    ),
}

#: The fixed assignment. Muscle memory lives here, so it does not change per page: knob one
#: is brightness everywhere, whatever the page. A page may redirect a knob to a different
#: *entity*, never to a different property.
KNOB_PROPERTIES: Final[Mapping[int, str]] = {
    1: "brightness",
    2: "color_temp",
    3: "hue",
    4: "saturation",
    5: "volume",
    6: "position",
    7: "temperature",
    # Knob eight is deliberately unassigned: it takes whatever the entity's own main
    # property is, so it works on anything without having to be configured.
}

#: What "the value" means for an entity when no particular property was asked for. Used by
#: knob eight, and by holding a pad to peek at what is behind it.
PRIMARY: Final[Mapping[str, str]] = {
    "light": "brightness",
    "media_player": "volume",
    "cover": "position",
    "climate": "temperature",
    "fan": "percentage",
}


def property_for(knob: int, state: EntityState) -> Property | None:
    """Which property a knob adjusts on this entity, if it adjusts one at all.

    A knob pointed at something without that property is inert. That is better than
    falling back to something else, which would mean the same knob doing different things
    depending on what happened to be focused.
    """
    key = KNOB_PROPERTIES.get(knob) or PRIMARY.get(state.domain)
    if key is None:
        return None
    candidate = PROPERTIES.get(key)
    if candidate is None or state.domain not in candidate.domains:
        return None
    return candidate


def primary_for(state: EntityState) -> Property | None:
    """The one value worth showing for an entity, for a peek."""
    key = PRIMARY.get(state.domain)
    return PROPERTIES.get(key) if key else None
