"""The profile engine: what the surface means, with nothing about how it is driven.

Nothing under this package imports Home Assistant, and nothing here does I/O or reads a
clock. That is a deliberate seam rather than a style preference: the parts of a control
surface that are actually hard to get right are the orderings and the timings, and those
are only cheap to test if they are pure. Every animation in here was first written by hand
against the hardware and corrected by eye four or five times; the tests exist so that the
corrections stay made.

The platform reaches in through the two protocols in :mod:`engine.ports` and nowhere else.

What is settled so far, and recorded in ``ble-midi-surface-design.md`` sections 5 to 7:

- the palette, and which five colours can actually name a page;
- the frames, and the transitions between them;
- the two rhythms;
- pages, the sources that fill them, and rendering one to a frame.

Still to come: the navigation stack, gestures, and the knobs.
"""

from __future__ import annotations

from .frames import (
    COLUMNS,
    PAD_COUNT,
    ROWS,
    STEP_SECONDS,
    Frame,
    blank,
    clockwise_order,
    collapse,
    column_of,
    column_order,
    expand,
    overlay,
    position,
    refuse,
    row_of,
    sweep,
    uncover,
    value_bar,
    wipe,
)
from .model import (
    NOTHING,
    Activate,
    Back,
    EntityState,
    EventOnly,
    Focus,
    Home,
    Navigate,
    Nothing,
    PadAction,
    PadConfig,
    Page,
    Profile,
    Service,
    Slot,
    Source,
    SourceKind,
    Toggle,
    entity_of,
)
from .palette import (
    ACTION,
    BAR_ALERT,
    BAR_COLOURS,
    BLUE,
    GREEN,
    IDENTITY,
    OFF,
    ON,
    ORANGE,
    PURPLE,
    RED,
    STATE_OFF,
    UNASSIGNED,
    WHITE,
    colour_for,
    is_emittable,
)
from .ports import ActionSink, RegistryView
from .render import (
    BACK_BUTTON,
    BUTTONS,
    HOME_BUTTON,
    RESERVED_BUTTONS,
    Rendering,
    ViewState,
    assignable,
    changed_pads,
    colour_of,
    compose,
    counterpart,
    own_colour,
    render,
)
from .resolve import default_actions, resolve, source_entities
from .rhythms import ALERT, BREATHE, TICK_SECONDS, Motion, Rhythm

__all__ = [
    "ACTION",
    "ALERT",
    "BACK_BUTTON",
    "BAR_ALERT",
    "BAR_COLOURS",
    "BLUE",
    "BREATHE",
    "BUTTONS",
    "COLUMNS",
    "GREEN",
    "HOME_BUTTON",
    "IDENTITY",
    "NOTHING",
    "OFF",
    "ON",
    "ORANGE",
    "PAD_COUNT",
    "PURPLE",
    "RED",
    "RESERVED_BUTTONS",
    "ROWS",
    "STATE_OFF",
    "STEP_SECONDS",
    "TICK_SECONDS",
    "UNASSIGNED",
    "WHITE",
    "ActionSink",
    "Activate",
    "Back",
    "EntityState",
    "EventOnly",
    "Focus",
    "Frame",
    "Home",
    "Motion",
    "Navigate",
    "Nothing",
    "PadAction",
    "PadConfig",
    "Page",
    "Profile",
    "RegistryView",
    "Rendering",
    "Rhythm",
    "Service",
    "Slot",
    "Source",
    "SourceKind",
    "Toggle",
    "ViewState",
    "assignable",
    "blank",
    "changed_pads",
    "clockwise_order",
    "collapse",
    "colour_for",
    "colour_of",
    "column_of",
    "column_order",
    "compose",
    "counterpart",
    "default_actions",
    "entity_of",
    "expand",
    "is_emittable",
    "overlay",
    "own_colour",
    "position",
    "refuse",
    "render",
    "resolve",
    "row_of",
    "source_entities",
    "sweep",
    "uncover",
    "value_bar",
    "wipe",
]
