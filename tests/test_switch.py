"""Tests for the Pi-hole list switch entity."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.pi_hole_lists.models import PiHoleList
from custom_components.pi_hole_lists.switch import (
    PiHoleListSwitch,
    async_setup_entry,
)

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


def _list(**overrides: object) -> PiHoleList:
    """Build the block-list model, optionally overriding fields."""
    return PiHoleList.from_dict({**BLOCK_LIST, **overrides})


def _coordinator(data: dict[int, PiHoleList]) -> MagicMock:
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


def _fake_create_task(created_tasks: list) -> MagicMock:
    """Fake hass.async_create_task that rejects non-coroutines like HA does."""

    def create_task(target, *args, **kwargs):
        if not asyncio.iscoroutine(target):
            raise TypeError("a coroutine was expected, got None")
        created_tasks.append(target)
        return target

    return MagicMock(side_effect=create_task)


def _capture_sync_listeners(sync_listeners: list) -> MagicMock:
    """Fake coordinator.async_add_listener capturing the _sync_lists callback."""

    def add_listener(cb):
        if cb.__name__ == "_sync_lists":
            sync_listeners.append(cb)
        return lambda: None

    return MagicMock(side_effect=add_listener)


async def _setup_platform(
    data: dict[int, PiHoleList],
    add_entities: MagicMock,
    created_tasks: list,
) -> tuple[MagicMock, list]:
    """Run async_setup_entry with a stub platform.

    Returns the coordinator and the platform's _sync_lists callbacks.
    """
    coordinator = _coordinator(data)
    sync_listeners: list = []
    coordinator.async_add_listener = _capture_sync_listeners(sync_listeners)
    entry = _entry()
    entry.runtime_data = MagicMock(coordinator=coordinator)
    hass = MagicMock()
    hass.async_create_task = _fake_create_task(created_tasks)
    await async_setup_entry(hass, entry, add_entities)
    return coordinator, sync_listeners


def test_is_on_maps_list_enabled():
    """is_on mirrors the list's global enabled flag."""
    coordinator = _coordinator({LIST_ID: _list(enabled=False)})
    entity = _entity(coordinator)

    assert entity.is_on is False

    # Models are frozen, so the coordinator data is replaced, not mutated.
    coordinator.data[LIST_ID] = _list(enabled=True)
    assert entity.is_on is True


def test_unique_id_and_state_attributes():
    """The unique id is entry-scoped and the attrs expose the list details."""
    entity = _entity(_coordinator({LIST_ID: _list()}))

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
    entity = _entity(_coordinator({LIST_ID: _list()}))
    assert entity.name == "Example blocklist"


def test_name_falls_back_to_humanized_address():
    """Without a comment the name comes from the list URL."""
    coordinator = _coordinator({LIST_ID: _list(comment="")})
    entity = _entity(coordinator)
    assert entity.name == "example.com/ads.txt"

    coordinator.data[LIST_ID] = _list(address="http://pi.hole", comment="")
    assert entity.name == "pi.hole"


def test_name_falls_back_to_github_owner_repo():
    """GitHub-hosted lists without a comment are named owner/repo."""
    coordinator = _coordinator({LIST_ID: _list(comment="")})
    entity = _entity(coordinator)

    coordinator.data[LIST_ID] = _list(
        address="https://raw.githubusercontent.com/StevenBlack/hosts/master/hosts",
        comment="",
    )
    assert entity.name == "StevenBlack/hosts"

    coordinator.data[LIST_ID] = _list(
        address="https://github.com/danhorton7/pihole-block-tiktok",
        comment="",
    )
    assert entity.name == "danhorton7/pihole-block-tiktok"


def test_device_info_per_entry():
    """One device per config entry, named after the Pi-hole host."""
    entity = _entity(_coordinator({LIST_ID: _list()}))
    info = entity.device_info

    assert info["identifiers"] == {("pi_hole_lists", ENTRY_ID)}
    assert info["name"] == "Pi-hole lists (pi.hole:8081)"


@pytest.mark.asyncio
async def test_turn_on_enables_list():
    """turn_on PUTs the model with enabled=true, adopts the response, refreshes."""
    current = _list(enabled=False)
    coordinator = _coordinator({LIST_ID: current})
    entity = _entity(coordinator)
    updated = _list(enabled=True)
    coordinator.api.set_list_enabled.return_value = updated

    await entity.async_turn_on()

    coordinator.api.set_list_enabled.assert_awaited_once_with(current, True)
    assert coordinator.data[LIST_ID] == updated
    assert entity.is_on is True
    entity.async_write_ha_state.assert_called_once()
    coordinator.async_refresh.assert_awaited_once()


@pytest.mark.asyncio
async def test_turn_off_disables_list():
    """turn_off PUTs the model with enabled=false, adopts the response, refreshes."""
    current = _list()
    coordinator = _coordinator({LIST_ID: current})
    entity = _entity(coordinator)
    updated = _list(enabled=False)
    coordinator.api.set_list_enabled.return_value = updated

    await entity.async_turn_off()

    coordinator.api.set_list_enabled.assert_awaited_once_with(current, False)
    assert coordinator.data[LIST_ID] == updated
    assert entity.is_on is False
    entity.async_write_ha_state.assert_called_once()
    coordinator.async_refresh.assert_awaited_once()


@pytest.mark.asyncio
async def test_turn_on_merges_slim_response_into_current_data():
    """A slim response must not wipe details of the current list data."""
    coordinator = _coordinator({LIST_ID: _list()})
    entity = _entity(coordinator)
    # Real FTL responses carry the full row, but stay safe if they do not.
    coordinator.api.set_list_enabled.return_value = PiHoleList.from_dict(
        {"id": LIST_ID, "status": "enabled"}
    )

    await entity.async_turn_on()

    data = coordinator.data[LIST_ID]
    assert data.address == BLOCK_LIST["address"]
    assert data.comment == BLOCK_LIST["comment"]
    assert data.status == "enabled"
    # Without "enabled" in the response, the old value is kept until the
    # coordinator refresh re-syncs the row.
    assert data.enabled is BLOCK_LIST["enabled"]


@pytest.mark.asyncio
async def test_sync_lists_adds_new_list():
    """A list created in the Pi-hole UI becomes an entity on the next poll."""
    # The real HA platform callback schedules the add itself and returns None
    # (it is not a coroutine); the code must call it directly.
    add_entities = MagicMock(return_value=None)
    created_tasks: list = []
    coordinator, sync_listeners = await _setup_platform(
        {LIST_ID: _list()}, add_entities, created_tasks
    )

    add_entities.reset_mock()
    coordinator.data[2] = _list(id=2, address="https://example.com/new.txt")
    for sync in sync_listeners:
        sync()

    [entity] = list(add_entities.call_args.args[0])
    assert isinstance(entity, PiHoleListSwitch)
    assert entity.unique_id == f"{ENTRY_ID}-2"
    # Entity adds go through the platform callback only; no manual task.
    assert created_tasks == []


@pytest.mark.asyncio
async def test_sync_lists_removes_gone_list():
    """A list deleted in the Pi-hole UI is removed on the next poll."""
    add_entities = MagicMock(return_value=None)
    created_tasks: list = []
    with patch.object(PiHoleListSwitch, "async_remove", new=AsyncMock()) as remove:
        coordinator, sync_listeners = await _setup_platform(
            {LIST_ID: _list()}, add_entities, created_tasks
        )
        del coordinator.data[LIST_ID]
        for sync in sync_listeners:
            sync()

    assert len(created_tasks) == 1
    await asyncio.gather(*created_tasks)
    remove.assert_awaited_once_with(force_remove=True)


@pytest.mark.asyncio
async def test_sync_lists_noop_when_unchanged():
    """No entities are added or removed when the polled lists are unchanged."""
    add_entities = MagicMock(return_value=None)
    created_tasks: list = []
    coordinator, sync_listeners = await _setup_platform(
        {LIST_ID: _list()}, add_entities, created_tasks
    )

    add_entities.reset_mock()
    for sync in sync_listeners:
        sync()

    add_entities.assert_not_called()
    assert created_tasks == []
