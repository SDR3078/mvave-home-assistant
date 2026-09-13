"""Turning the rooms somebody picked into pages they own.

The one piece of code here that runs exactly once on a real installation and cannot be run
again to see what it did. Pages were two lists in the options — which areas got one, and
what colour each was — and they are subentries now. If this drops something, the thing it
dropped is already gone.
"""

from __future__ import annotations

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.mvave import async_migrate_entry
from custom_components.mvave.const import (
    CONF_ADDRESS,
    CONF_AREA,
    CONF_COLOUR,
    CONF_DOMAIN_COLOURS,
    CONF_LABEL,
    CONF_PAGE_COLOURS,
    CONF_PAGES,
    DOMAIN,
    SUBENTRY_PAGE,
)
from custom_components.mvave.engine.palette import BLUE, GREEN, ORANGE, PURPLE, RED

ADDRESS = "AA:BB:CC:DD:EE:FF"


@pytest.fixture
def rooms(hass: HomeAssistant) -> dict[str, str]:
    """Three rooms, by name."""
    registry = ar.async_get(hass)
    return {name: registry.async_get_or_create(name).id for name in ("Kitchen", "Living", "Study")}


def old_entry(hass: HomeAssistant, **options: object) -> MockConfigEntry:
    """An installation from before pages were things you add."""
    made = MockConfigEntry(
        domain=DOMAIN,
        unique_id=ADDRESS.lower(),
        title="SMC-PAD",
        data={CONF_ADDRESS: ADDRESS},
        options=options,
        minor_version=1,
    )
    made.add_to_hass(hass)
    return made


def pages(entry: MockConfigEntry) -> list[tuple[str, object, object]]:
    """Every page, in the order it was made: title, colour, room."""
    return [
        (page.title, page.data.get(CONF_COLOUR), page.data.get(CONF_AREA))
        for page in entry.subentries.values()
        if page.subentry_type == SUBENTRY_PAGE
    ]


async def test_rooms_become_pages_in_the_order_they_were_picked(
    hass: HomeAssistant, rooms: dict[str, str]
) -> None:
    # The order rooms were picked is the order they sit on the index, and somebody has
    # learned where they are. A migration that reorders them moves every room in the house.
    entry = old_entry(hass, **{CONF_PAGES: [rooms["Study"], rooms["Kitchen"], rooms["Living"]]})
    assert await async_migrate_entry(hass, entry)

    assert [title for title, _, _ in pages(entry)] == ["Study", "Kitchen", "Living"]
    assert [area for _, _, area in pages(entry)] == [
        rooms["Study"],
        rooms["Kitchen"],
        rooms["Living"],
    ]


async def test_a_colour_somebody_chose_survives_and_the_rest_are_handed_out(
    hass: HomeAssistant, rooms: dict[str, str]
) -> None:
    entry = old_entry(
        hass,
        **{
            CONF_PAGES: [rooms["Kitchen"], rooms["Living"], rooms["Study"]],
            CONF_PAGE_COLOURS: {rooms["Living"]: RED},
        },
    )
    assert await async_migrate_entry(hass, entry)

    colours = [colour for _, colour, _ in pages(entry)]
    # Chosen where chosen; otherwise the identity colours in order, by position.
    assert colours == [BLUE, RED, GREEN]


async def test_a_room_that_has_gone_still_becomes_a_page(hass: HomeAssistant) -> None:
    # A page outliving its room is the whole reason pages stopped being rooms. Dropping it
    # here would silently delete a page from somebody's index during an upgrade.
    entry = old_entry(hass, **{CONF_PAGES: ["area_that_was_deleted"]})
    assert await async_migrate_entry(hass, entry)

    assert pages(entry) == [("area_that_was_deleted", BLUE, "area_that_was_deleted")]


async def test_a_page_made_from_a_room_fills_itself_from_that_room(
    hass: HomeAssistant, rooms: dict[str, str]
) -> None:
    entry = old_entry(hass, **{CONF_PAGES: [rooms["Kitchen"]]})
    assert await async_migrate_entry(hass, entry)

    page = next(iter(entry.subentries.values()))
    assert page.data[CONF_AREA] == rooms["Kitchen"]
    assert page.data[CONF_LABEL] is None  # a room, not a label: one source, never both


async def test_settings_that_are_not_pages_stay_where_they_were(
    hass: HomeAssistant, rooms: dict[str, str]
) -> None:
    # What each kind of thing looks like is a genuine setting and still belongs in options.
    entry = old_entry(
        hass,
        **{
            CONF_PAGES: [rooms["Kitchen"]],
            CONF_PAGE_COLOURS: {rooms["Kitchen"]: PURPLE},
            CONF_DOMAIN_COLOURS: {"light": ORANGE},
        },
    )
    assert await async_migrate_entry(hass, entry)

    assert entry.options == {CONF_DOMAIN_COLOURS: {"light": ORANGE}}
    assert CONF_PAGES not in entry.options
    assert CONF_PAGE_COLOURS not in entry.options


async def test_migrating_twice_does_not_make_the_pages_twice(
    hass: HomeAssistant, rooms: dict[str, str]
) -> None:
    # The version is what stops it, and the failure it stops is doubling somebody's index.
    entry = old_entry(hass, **{CONF_PAGES: [rooms["Kitchen"], rooms["Living"]]})
    assert await async_migrate_entry(hass, entry)
    assert entry.minor_version == 2

    assert await async_migrate_entry(hass, entry)
    assert [title for title, _, _ in pages(entry)] == ["Kitchen", "Living"]


async def test_an_installation_with_no_rooms_picked_migrates_to_no_pages(
    hass: HomeAssistant,
) -> None:
    # Which is not an empty surface: with nothing configured the profile is built from the
    # area registry instead, so this has to leave *no* pages rather than one blank one.
    entry = old_entry(hass)
    assert await async_migrate_entry(hass, entry)

    assert pages(entry) == []
    assert entry.minor_version == 2


async def test_rooms_stored_in_the_entry_rather_than_the_options_migrate_too(
    hass: HomeAssistant, rooms: dict[str, str]
) -> None:
    # The list was read from either, so both shapes exist in the wild.
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=ADDRESS.lower(),
        title="SMC-PAD",
        data={CONF_ADDRESS: ADDRESS, CONF_PAGES: [rooms["Kitchen"], rooms["Study"]]},
        minor_version=1,
    )
    entry.add_to_hass(hass)
    assert await async_migrate_entry(hass, entry)

    assert [title for title, _, _ in pages(entry)] == ["Kitchen", "Study"]
    # And the old list is gone from where it was, rather than half surviving its own
    # migration and waiting to be read again.
    assert CONF_PAGES not in entry.data
    assert entry.data[CONF_ADDRESS] == ADDRESS


async def test_an_entry_that_has_already_migrated_is_left_alone(
    hass: HomeAssistant, rooms: dict[str, str]
) -> None:
    # The version check on its own, with everything else arranged so that nothing but the
    # check could stop it. Without this the guard can be deleted and the suite stays green,
    # because the other shape happens to clear the list it would have read.
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=ADDRESS.lower(),
        title="SMC-PAD",
        data={CONF_ADDRESS: ADDRESS, CONF_PAGES: [rooms["Kitchen"], rooms["Living"]]},
        minor_version=2,
    )
    entry.add_to_hass(hass)
    assert await async_migrate_entry(hass, entry)

    assert pages(entry) == []
