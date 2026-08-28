"""The Pi-hole Lists integration."""

import asyncio
import logging
from datetime import timedelta

import aiohttp
from hole.exceptions import HoleError
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_URL, CONF_VERIFY_SSL, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import aiohttp_client

from .api import PiHoleV6Lists
from .const import (
    CONF_APP_PASSWORD,
    CONF_SCAN_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
)
from .coordinator import PiHoleListsCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.SWITCH]


class PiHoleListsRuntimeData:
    """Runtime data attached to each config entry."""

    def __init__(
        self,
        api: PiHoleV6Lists,
        coordinator: PiHoleListsCoordinator,
    ) -> None:
        """Initialize runtime data."""
        self.api = api
        self.coordinator = coordinator


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Pi-hole Lists from a config entry."""
    api = PiHoleV6Lists(
        entry.data[CONF_URL],
        entry.data[CONF_APP_PASSWORD],
        session=aiohttp_client.async_get_clientsession(hass),
        verify_ssl=entry.data.get(CONF_VERIFY_SSL, True),
    )

    coordinator = PiHoleListsCoordinator(
        hass,
        api,
        entry,
        update_interval=timedelta(
            minutes=entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
        ),
    )

    # Raises ConfigEntryNotReady on transient failures and
    # ConfigEntryAuthFailed when the credentials are rejected (which triggers
    # the reauthentication flow).
    await coordinator.async_config_entry_first_refresh()

    entry.runtime_data = PiHoleListsRuntimeData(api, coordinator)

    # Reload when the options (scan interval) or data (app password) change.
    entry.async_on_unload(entry.add_update_listener(async_update_listener))

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry; release the Pi-hole session."""
    if not await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        return False

    api: PiHoleV6Lists = entry.runtime_data.api
    try:
        await api.logout()
    except (HoleError, aiohttp.ClientError, asyncio.TimeoutError):
        _LOGGER.debug("Error logging out of Pi-hole during unload", exc_info=True)
    return True


async def async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the entry when its options or data change."""
    await hass.config_entries.async_reload(entry.entry_id)
