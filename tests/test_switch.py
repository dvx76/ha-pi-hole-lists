"""Tests for the Pi-hole list switch entity."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.pi_hole_lists.switch import PiHoleListSwitch

LIST_ID = 1
ENTRY_ID = "entry-1"

BLOCK_LIST = {
    "id": LIST_ID,
    "address": "https://example.com/ads.txt",
    "enabled": True,
    "groups": [1, 2],
    "type": "block",
    "comment": "Example blocklist",
    "number": 12345,
    "invalid_domains": 3,
    "status": "enabled",
    "date_added": 1750000000,
    "date_modified": 1750000000,
    "date_updated": 1750000000,
}


def _coordinator(data: dict[int, dict]) -> MagicMock:
    """Build a coordinator mock carrying the given list data."""
    coordinator = MagicMock()
    coordinator.data = data
    coordinator.api = MagicMock()
    coordinator.api.set_list_enabled = AsyncMock()
    coordinator.async_refresh = AsyncMock()
    return coordinator


def _entry() -> MagicMock:
    """Build a config entry mock."""
    entry = MagicMock()
    entry.entry_id = ENTRY_ID
    entry.data = {"url": "http://pi.hole:8081"}
    return entry


def _entity(coordinator: MagicMock, entry: MagicMock | None = None) -> PiHoleListSwitch:
    """Build a switch entity with a stubbed state writer."""
    entity = PiHoleListSwitch(coordinator, entry or _entry(), LIST_ID)
    # async_write_ha_state is fire-and-forget in the code, so the double must
    # be a sync mock (an AsyncMock would leak an unawaited coroutine).
    entity.async_write_ha_state = MagicMock()
    return entity


def test_is_on_maps_list_enabled():
    """is_on mirrors the list's global enabled flag."""
    coordinator = _coordinator({LIST_ID: {**BLOCK_LIST, "enabled": False}})
    entity = _entity(coordinator)

    assert entity.is_on is False

    coordinator.data[LIST_ID]["enabled"] = True
    assert entity.is_on is True


def test_unique_id_and_state_attributes():
    """The unique id is entry-scoped and the attrs expose the list details."""
    entity = _entity(_coordinator({LIST_ID: BLOCK_LIST}))

    assert entity.unique_id == f"{ENTRY_ID}-{LIST_ID}"

    attrs = entity.extra_state_attributes
    assert attrs["id"] == LIST_ID
    assert attrs["address"] == BLOCK_LIST["address"]
    assert attrs["type"] == "block"
    assert attrs["groups"] == [1, 2]
    assert attrs["comment"] == "Example blocklist"
    assert attrs["number"] == 12345
    assert attrs["invalid_domains"] == 3
    assert attrs["status"] == "enabled"
    assert attrs["date_updated"] == 1750000000


def test_name_uses_comment():
    """The entity name is the list comment when set."""
    entity = _entity(_coordinator({LIST_ID: BLOCK_LIST}))
    assert entity.name == "Example blocklist"


def test_name_falls_back_to_humanized_address():
    """Without a comment the name is host plus the last path segment."""
    coordinator = _coordinator({LIST_ID: {**BLOCK_LIST, "comment": ""}})
    entity = _entity(coordinator)
    assert entity.name == "example.com/ads.txt"

    coordinator.data[LIST_ID]["address"] = "http://pi.hole"
    assert entity.name == "pi.hole"


def test_device_info_per_entry():
    """One device per config entry, named after the Pi-hole host."""
    entity = _entity(_coordinator({LIST_ID: BLOCK_LIST}))
    info = entity.device_info

    assert info["identifiers"] == {("pi_hole_lists", ENTRY_ID)}
    assert info["name"] == "Pi-hole lists (pi.hole:8081)"


@pytest.mark.asyncio
async def test_turn_on_enables_list():
    """turn_on PUTs enabled=true, adopts the response, and refreshes."""
    coordinator = _coordinator({LIST_ID: {**BLOCK_LIST, "enabled": False}})
    entity = _entity(coordinator)
    updated = {**BLOCK_LIST, "enabled": True}
    coordinator.api.set_list_enabled.return_value = updated

    await entity.async_turn_on()

    coordinator.api.set_list_enabled.assert_awaited_once_with(
        BLOCK_LIST["address"], "block", True
    )
    assert coordinator.data[LIST_ID] == updated
    assert entity.is_on is True
    entity.async_write_ha_state.assert_called_once()
    coordinator.async_refresh.assert_awaited_once()


@pytest.mark.asyncio
async def test_turn_off_disables_list():
    """turn_off PUTs enabled=false, adopts the response, and refreshes."""
    coordinator = _coordinator({LIST_ID: BLOCK_LIST})
    entity = _entity(coordinator)
    updated = {**BLOCK_LIST, "enabled": False}
    coordinator.api.set_list_enabled.return_value = updated

    await entity.async_turn_off()

    coordinator.api.set_list_enabled.assert_awaited_once_with(
        BLOCK_LIST["address"], "block", False
    )
    assert coordinator.data[LIST_ID] == updated
    assert entity.is_on is False
    entity.async_write_ha_state.assert_called_once()
    coordinator.async_refresh.assert_awaited_once()


@pytest.mark.asyncio
async def test_turn_on_merges_slim_response_into_current_data():
    """A slim response must not wipe details of the current list data."""
    coordinator = _coordinator({LIST_ID: BLOCK_LIST})
    entity = _entity(coordinator)
    # Real FTL responses carry the full row, but stay safe if they do not.
    coordinator.api.set_list_enabled.return_value = {
        "id": LIST_ID,
        "status": "enabled",
    }

    await entity.async_turn_on()

    data = coordinator.data[LIST_ID]
    assert data["address"] == BLOCK_LIST["address"]
    assert data["comment"] == BLOCK_LIST["comment"]
    assert data["status"] == "enabled"
    # Without "enabled" in the response, the old value is kept until the
    # coordinator refresh re-syncs the row.
    assert data["enabled"] is BLOCK_LIST["enabled"]
