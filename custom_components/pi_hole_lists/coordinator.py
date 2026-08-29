"""Coordinator for the Pi-hole Lists integration."""

import asyncio
import logging
from datetime import timedelta

from hole.exceptions import HoleAuthenticationError, HoleError
from homeassistant.config_entries import ConfigEntry, ConfigEntryAuthFailed
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import PiHoleV6Lists
from .const import (
    GRAVITY_DEBOUNCE_SECONDS,
    GRAVITY_FAILED,
    GRAVITY_IDLE,
    GRAVITY_PENDING,
    GRAVITY_RUNNING,
    LIST_TYPE_BLOCK,
)
from .models import PiHoleList

_LOGGER = logging.getLogger(__name__)


class PiHoleListsCoordinator(DataUpdateCoordinator[dict[int, PiHoleList]]):
    """Poll the Pi-hole lists and index them by list id.

    The data is a mapping of list id to the parsed ``PiHoleList`` model,
    limited to block lists (``type=block``). Lists created or deleted in the
    Pi-hole UI appear/disappear on the next poll.

    Also owns the debounced gravity-rebuild state machine: enabling a list in
    Pi-hole v6 only flips its ``enabled`` flag — FTL resolves blocking
    against the pre-existing gravity table, so a list enabled since the last
    ``pihole -g`` contributes no rows until gravity is rebuilt. Every
    ``turn_on`` schedules one (debounced) run via ``schedule_gravity_update``;
    the state (``idle|pending|running|failed``) is exposed to the Gravity
    update binary sensor.
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
        self._gravity_task: asyncio.Task[None] | None = None
        self._gravity_pending = False
        self._gravity_state = GRAVITY_IDLE

    @property
    def gravity_state(self) -> str:
        """Return the gravity rebuild state (GRAVITY_IDLE/PENDING/RUNNING/FAILED)."""
        return self._gravity_state

    def schedule_gravity_update(self) -> None:
        """Schedule a debounced gravity rebuild after a list was enabled.

        Enabling a list does not rebuild gravity in Pi-hole v6, so every
        ``turn_on`` calls this. Concurrent runs collide on Pi-hole's gravity
        lock ("already running"), so toggles landing while a run is alive
        only mark ``_gravity_pending`` — the in-flight POST is never
        cancelled (the fork would survive, but completion detection would be
        lost). The debounced task consumes the pending flag once after each
        run, so a burst of toggles triggers one run plus at most one trailing
        rerun.
        """
        if self._gravity_task is not None and not self._gravity_task.done():
            self._gravity_pending = True
            return
        self._gravity_task = self.hass.async_create_task(self._run_gravity_debounced())

    async def _run_gravity_debounced(self) -> None:
        """Wait out the debounce window, then run gravity to completion.

        Toggles landing during the debounce sleep need no timer reset: one
        run covers everything enabled before it starts. Toggles landing while
        the run is in flight (a PUT after the forked ``pihole -g`` already
        read the adlists) mark a single trailing rerun instead of stacking
        runs. Every state transition notifies the listeners through
        ``async_set_updated_data`` — the switches re-render harmlessly and
        the Gravity update binary sensor reflects the state.
        """
        self._gravity_state = GRAVITY_PENDING
        self.async_set_updated_data(self.data)
        try:
            await asyncio.sleep(GRAVITY_DEBOUNCE_SECONDS)
            while True:
                self._gravity_pending = False
                self._gravity_state = GRAVITY_RUNNING
                self.async_set_updated_data(self.data)
                try:
                    success = await self.api.run_gravity()
                except HoleError as err:
                    _LOGGER.warning("Gravity rebuild failed: %s", err)
                    self._set_gravity_state(GRAVITY_FAILED)
                    return
                except Exception as err:
                    # asyncio.CancelledError is a BaseException and is not
                    # caught here; it propagates to the finally below.
                    _LOGGER.warning("Unexpected error during gravity rebuild: %s", err)
                    self._set_gravity_state(GRAVITY_FAILED)
                    return

                if not success:
                    _LOGGER.warning("Gravity rebuild reported failures (see output)")
                    self._set_gravity_state(GRAVITY_FAILED)
                    return

                # Gravity rewrote the lists' date_updated/number/status rows.
                await self.async_refresh()
                self._set_gravity_state(GRAVITY_IDLE)
                if not self._gravity_pending:
                    return
        finally:
            task = self._gravity_task
            self._gravity_task = None
            if self._gravity_pending and task is not None and not task.cancelled():
                # A toggle landed in the instant between the last pending
                # check and task completion; this run skipped it, so cover it
                # with a fresh debounced run. Cancelled tasks (unload) never
                # restart.
                self._gravity_task = self.hass.async_create_task(
                    self._run_gravity_debounced()
                )

    def _set_gravity_state(self, state: str) -> None:
        """Record a gravity state transition and notify the listeners."""
        self._gravity_state = state
        self.async_set_updated_data(self.data)

    async def cancel_pending_gravity(self) -> None:
        """Cancel a live gravity task on unload.

        Cancellation kills our HTTP wait; the server-side ``pihole -g`` fork
        finishes on its own — acceptable on unload. Gathering with
        ``return_exceptions`` suppresses "task was destroyed" noise from the
        cancelled await.
        """
        if self._gravity_task is not None and not self._gravity_task.done():
            self._gravity_task.cancel()
            await asyncio.gather(self._gravity_task, return_exceptions=True)
        self._gravity_task = None

    async def _async_update_data(self) -> dict[int, PiHoleList]:
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

        return {lst.id: lst for lst in lists if lst.type == LIST_TYPE_BLOCK}
