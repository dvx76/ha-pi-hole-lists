"""Tests for the Pi-hole Lists coordinator."""

from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest
from hole.exceptions import HoleAuthenticationError, HoleConnectionError
from homeassistant.config_entries import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import UpdateFailed

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
