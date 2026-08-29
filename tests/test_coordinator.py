"""Tests for the Pi-hole Lists coordinator."""

import asyncio
from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from hole.exceptions import HoleAuthenticationError, HoleConnectionError, HoleError
from homeassistant.config_entries import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import UpdateFailed

from custom_components.pi_hole_lists import coordinator as coordinator_module
from custom_components.pi_hole_lists.const import (
    GRAVITY_FAILED,
    GRAVITY_IDLE,
    GRAVITY_PENDING,
    GRAVITY_RUNNING,
)
from custom_components.pi_hole_lists.coordinator import PiHoleListsCoordinator
from custom_components.pi_hole_lists.models import PiHoleList


def _coordinator(api: MagicMock) -> PiHoleListsCoordinator:
    """Build a coordinator around a mocked API client."""
    return PiHoleListsCoordinator(
        hass=MagicMock(),
        api=api,
        config_entry=MagicMock(),
        update_interval=timedelta(minutes=5),
    )


def _api(lists: list[PiHoleList]) -> MagicMock:
    """Build an API mock returning the given lists."""
    api = MagicMock()
    api.get_lists = AsyncMock(return_value=lists)
    return api


@pytest.mark.asyncio
async def test_update_filters_block_lists():
    """Only type=block lists are kept, keyed by list id."""
    lists = [
        PiHoleList(id=1, type="block", enabled=True),
        PiHoleList(id=2, type="allow", enabled=True),
        PiHoleList(id=3, type="block", enabled=False),
    ]
    coordinator = _coordinator(_api(lists))

    data = await coordinator._async_update_data()

    assert data == {1: lists[0], 3: lists[2]}


@pytest.mark.asyncio
async def test_update_raises_config_entry_auth_failed():
    """Rejected credentials surface as ConfigEntryAuthFailed (-> reauth)."""
    api = MagicMock()
    api.get_lists = AsyncMock(
        side_effect=HoleAuthenticationError("Invalid password", status=401)
    )
    coordinator = _coordinator(api)

    with pytest.raises(ConfigEntryAuthFailed):
        await coordinator._async_update_data()


@pytest.mark.asyncio
async def test_update_raises_update_failed_on_connection_error():
    """Connection failures surface as UpdateFailed (-> entry unavailable)."""
    api = MagicMock()
    api.get_lists = AsyncMock(side_effect=HoleConnectionError("No route to host"))
    coordinator = _coordinator(api)

    with pytest.raises(UpdateFailed):
        await coordinator._async_update_data()


def _gravity_coordinator(api: MagicMock) -> PiHoleListsCoordinator:
    """Build a coordinator ready for gravity scheduling.

    Sets the data (so notifications have something to fan out), stubs the
    refresh, and routes gravity tasks through the real event loop.
    """
    coordinator = _coordinator(api)
    coordinator.data = {}
    coordinator.async_refresh = AsyncMock()
    coordinator.hass.async_create_task = MagicMock(side_effect=asyncio.create_task)
    return coordinator


def _gravity_api() -> MagicMock:
    """Build an API mock whose run_gravity succeeds by default."""
    api = MagicMock()
    api.run_gravity = AsyncMock(return_value=True)
    return api


def _record_transitions(coordinator: PiHoleListsCoordinator) -> list[str]:
    """Record the gravity state at every listener notification.

    The real notify path (async_set_updated_data) is kept, so the test covers
    the same fan-out the binary sensor relies on; there are no listeners
    registered, so the MagicMock-hass refresh scheduling is never reached.
    """
    states: list[str] = []
    real_set_updated_data = coordinator.async_set_updated_data

    def record(data: dict) -> None:
        states.append(coordinator.gravity_state)
        real_set_updated_data(data)

    coordinator.async_set_updated_data = record
    return states


@pytest.mark.asyncio
async def test_gravity_schedule_runs_to_idle():
    """A scheduled run transitions pending -> running -> idle and refreshes."""
    api = _gravity_api()
    coordinator = _gravity_coordinator(api)
    states = _record_transitions(coordinator)

    with patch.object(coordinator_module, "GRAVITY_DEBOUNCE_SECONDS", 0):
        coordinator.schedule_gravity_update()
        await coordinator._gravity_task

    assert states == [GRAVITY_PENDING, GRAVITY_RUNNING, GRAVITY_IDLE]
    api.run_gravity.assert_awaited_once()
    coordinator.async_refresh.assert_awaited_once()
    assert coordinator.gravity_state == GRAVITY_IDLE
    assert coordinator._gravity_task is None


@pytest.mark.asyncio
async def test_gravity_double_schedule_single_run():
    """Two schedules before the run starts coalesce into a single run."""
    api = _gravity_api()
    coordinator = _gravity_coordinator(api)

    with patch.object(coordinator_module, "GRAVITY_DEBOUNCE_SECONDS", 0):
        coordinator.schedule_gravity_update()
        coordinator.schedule_gravity_update()
        await coordinator._gravity_task

    api.run_gravity.assert_awaited_once()
    coordinator.async_refresh.assert_awaited_once()
    assert coordinator.gravity_state == GRAVITY_IDLE
    assert coordinator._gravity_task is None


@pytest.mark.asyncio
async def test_gravity_schedule_during_run_triggers_single_trailing_rerun():
    """A toggle landing while the run is in flight causes exactly one rerun."""
    api = _gravity_api()
    coordinator = _gravity_coordinator(api)
    calls = 0

    async def fake_run_gravity() -> bool:
        nonlocal calls
        calls += 1
        if calls == 1:
            # A second toggle lands while the first run is in flight; it must
            # not cancel the POST, only mark one trailing rerun.
            coordinator.schedule_gravity_update()
        return True

    api.run_gravity = AsyncMock(side_effect=fake_run_gravity)

    with patch.object(coordinator_module, "GRAVITY_DEBOUNCE_SECONDS", 0):
        coordinator.schedule_gravity_update()
        await coordinator._gravity_task

    assert calls == 2
    assert coordinator.async_refresh.await_count == 2
    assert coordinator.gravity_state == GRAVITY_IDLE
    assert coordinator._gravity_task is None


@pytest.mark.asyncio
async def test_gravity_failed_run_sets_failed_state():
    """A run reporting [✗] failures surfaces as failed, without a refresh."""
    api = _gravity_api()
    api.run_gravity = AsyncMock(return_value=False)
    coordinator = _gravity_coordinator(api)
    states = _record_transitions(coordinator)

    with patch.object(coordinator_module, "GRAVITY_DEBOUNCE_SECONDS", 0):
        coordinator.schedule_gravity_update()
        await coordinator._gravity_task

    assert coordinator.gravity_state == GRAVITY_FAILED
    assert states == [GRAVITY_PENDING, GRAVITY_RUNNING, GRAVITY_FAILED]
    api.run_gravity.assert_awaited_once()
    coordinator.async_refresh.assert_not_awaited()
    assert coordinator._gravity_task is None


@pytest.mark.asyncio
async def test_gravity_raising_run_sets_failed_state():
    """A raising run_gravity surfaces as failed, without a refresh."""
    api = _gravity_api()
    api.run_gravity = AsyncMock(side_effect=HoleError("Gravity failed", status=500))
    coordinator = _gravity_coordinator(api)

    with patch.object(coordinator_module, "GRAVITY_DEBOUNCE_SECONDS", 0):
        coordinator.schedule_gravity_update()
        await coordinator._gravity_task

    assert coordinator.gravity_state == GRAVITY_FAILED
    api.run_gravity.assert_awaited_once()
    coordinator.async_refresh.assert_not_awaited()
    assert coordinator._gravity_task is None


@pytest.mark.asyncio
async def test_gravity_unexpected_exception_sets_failed_state():
    """An unexpected exception from run_gravity surfaces as failed, not a crash."""
    api = _gravity_api()
    api.run_gravity = AsyncMock(side_effect=RuntimeError("boom"))
    coordinator = _gravity_coordinator(api)

    with patch.object(coordinator_module, "GRAVITY_DEBOUNCE_SECONDS", 0):
        coordinator.schedule_gravity_update()
        await coordinator._gravity_task

    assert coordinator.gravity_state == GRAVITY_FAILED
    coordinator.async_refresh.assert_not_awaited()
    assert coordinator._gravity_task is None


@pytest.mark.asyncio
async def test_cancel_pending_gravity_cancels_live_task():
    """Cancelling a live run does not raise and does not restart a task."""
    api = _gravity_api()
    coordinator = _gravity_coordinator(api)
    blocked = asyncio.Event()
    api.run_gravity = AsyncMock(side_effect=blocked.wait)

    with patch.object(coordinator_module, "GRAVITY_DEBOUNCE_SECONDS", 0):
        coordinator.schedule_gravity_update()
        # Let the debounced task reach the running state (it now blocks in
        # run_gravity for the rest of the test).
        for _ in range(100):
            if coordinator.gravity_state == GRAVITY_RUNNING:
                break
            await asyncio.sleep(0)
        assert coordinator.gravity_state == GRAVITY_RUNNING

        await coordinator.cancel_pending_gravity()

        assert coordinator._gravity_task is None
        # The in-flight run was cut short and no rerun task was spawned.
        await asyncio.sleep(0)
        assert api.run_gravity.await_count == 1
