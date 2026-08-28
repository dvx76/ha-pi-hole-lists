"""Coordinator for the Pi-hole Lists integration."""

import logging
from datetime import timedelta

from hole.exceptions import HoleAuthenticationError, HoleError
from homeassistant.config_entries import ConfigEntry, ConfigEntryAuthFailed
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import PiHoleV6Lists
from .const import LIST_TYPE_BLOCK

_LOGGER = logging.getLogger(__name__)


class PiHoleListsCoordinator(DataUpdateCoordinator[dict[int, dict]]):
    """Poll the Pi-hole lists and index them by list id.

    The data is a mapping of list id to the full Pi-hole list object, limited
    to block lists (``type=block``). Lists created or deleted in the Pi-hole
    UI appear/disappear on the next poll.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        api: PiHoleV6Lists,
        config_entry: ConfigEntry,
        update_interval: timedelta,
    ) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            config_entry=config_entry,
            name="Pi-hole lists",
            update_interval=update_interval,
        )
        self.api = api

    async def _async_update_data(self) -> dict[int, dict]:
        """Fetch the lists, returning only block lists keyed by list id."""
        try:
            lists = await self.api.get_lists()
        except HoleAuthenticationError as err:
            raise ConfigEntryAuthFailed(
                "Authentication failed while fetching Pi-hole lists"
            ) from err
        except HoleError as err:
            raise UpdateFailed(f"Error communicating with Pi-hole: {err}") from err
        except Exception as err:
            raise UpdateFailed(
                f"Unexpected error fetching Pi-hole lists: {err}"
            ) from err

        return {lst["id"]: lst for lst in lists if lst.get("type") == LIST_TYPE_BLOCK}
