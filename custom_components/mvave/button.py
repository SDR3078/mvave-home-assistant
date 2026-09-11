"""The two commands that mean the same thing on every page.

Back and home are the only gestures a page is not allowed to rebind, because somebody who
has got lost needs one thing that works the same everywhere including on the page they got
lost on. That is exactly what makes them nameable, and nameable is the line between a
button entity that earns its place and sixteen that do not: "Home" means the same thing
tomorrow, while "Slot 5" means whatever the area registry decided this morning.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.components.button import ButtonEntity

from .entity import MvaveSurfaceEntity

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

    from . import MvaveConfigEntry
    from .runner import SurfaceRunner

# Nothing here talks to the device directly; the runner serialises its own writes.
PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: MvaveConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the navigation buttons."""
    runner = entry.runtime_data.runner
    async_add_entities([MvaveHomeButton(runner), MvaveBackButton(runner)])


class MvaveHomeButton(MvaveSurfaceEntity, ButtonEntity):
    """Clear the navigation history back to the index."""

    _attr_translation_key = "home"

    def __init__(self, runner: SurfaceRunner) -> None:
        """Initialise the button."""
        super().__init__(runner, "home")

    async def async_press(self) -> None:
        """Go home."""
        self.runner.drive(lambda surface: surface.go_home())


class MvaveBackButton(MvaveSurfaceEntity, ButtonEntity):
    """Pop one level of navigation history."""

    _attr_translation_key = "back"

    def __init__(self, runner: SurfaceRunner) -> None:
        """Initialise the button."""
        super().__init__(runner, "back")

    async def async_press(self) -> None:
        """Go back one page."""
        self.runner.drive(lambda surface: surface.go_back())
