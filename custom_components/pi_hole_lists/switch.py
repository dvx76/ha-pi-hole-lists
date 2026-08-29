"""Switch platform for the Pi-hole Lists integration."""

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import PiHoleListsCoordinator
from .entity import STATE_ATTRIBUTES, PiHoleListsEntity, _humanize_address


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Pi-hole list switches from the current coordinator data."""
    coordinator: PiHoleListsCoordinator = entry.runtime_data.coordinator

    entities: dict[int, PiHoleListSwitch] = {
        list_id: PiHoleListSwitch(coordinator, entry, list_id)
        for list_id in coordinator.data
    }
    async_add_entities(entities.values())

    # Lists can be created or deleted in the Pi-hole UI; keep the platform in
    # sync by reconciling on every coordinator update.
    def _sync_lists() -> None:
        current_ids = set(coordinator.data)
        for list_id in current_ids - set(entities):
            entity = PiHoleListSwitch(coordinator, entry, list_id)
            entities[list_id] = entity
            # The platform callback is a plain function that schedules the add
            # internally and returns None — it must be called directly, not
            # wrapped in hass.async_create_task (which needs a coroutine).
            async_add_entities([entity])
        for list_id in set(entities) - current_ids:
            entity = entities.pop(list_id)
            # async_remove is a coroutine, so it does need scheduling.
            hass.async_create_task(entity.async_remove(force_remove=True))

    entry.async_on_unload(coordinator.async_add_listener(_sync_lists))


class PiHoleListSwitch(PiHoleListsEntity, SwitchEntity):
    """A Pi-hole blocklist as a switch entity."""

    def __init__(
        self,
        coordinator: PiHoleListsCoordinator,
        entry: ConfigEntry,
        list_id: int,
    ) -> None:
        """Initialize the switch."""
        super().__init__(coordinator, entry)
        self._list_id = list_id
        self._attr_unique_id = f"{entry.entry_id}-{list_id}"

    @property
    def is_on(self) -> bool:
        """Return whether the list is enabled in Pi-hole."""
        return bool(self.list_data.enabled)

    @property
    def name(self) -> str:
        """Return the list comment, or a humanized address."""
        list_data = self.list_data
        if comment := list_data.comment:
            return comment
        return _humanize_address(list_data.address)

    @property
    def extra_state_attributes(self) -> dict:
        """Return Pi-hole list details as state attributes."""
        list_data = self.list_data
        return {key: getattr(list_data, key) for key in STATE_ATTRIBUTES}

    async def async_turn_on(self, **kwargs) -> None:
        """Enable the list in Pi-hole."""
        await self._async_set_enabled(True)

    async def async_turn_off(self, **kwargs) -> None:
        """Disable the list in Pi-hole."""
        await self._async_set_enabled(False)

    async def _async_set_enabled(self, enabled: bool) -> None:
        """Toggle the list, update state from the response, and refresh."""
        list_data = self.list_data
        # The PUT payload is built from the model (PiHoleList.update_payload)
        # and always echoes the comment, so the toggle cannot wipe it.
        updated = await self.coordinator.api.set_list_enabled(list_data, enabled)
        # Merge the response over the current list so no details are lost if
        # the response is slim; the coordinator refresh below re-syncs anyway.
        self.coordinator.data[self._list_id] = list_data.merge_update(updated)
        self.async_write_ha_state()
        await self.coordinator.async_refresh()
        if enabled:
            # Pi-hole v6 does not rebuild gravity on a toggle: a list enabled
            # since the last gravity run contributes no rows to the gravity
            # table until a rebuild, so enabling is not effective until one
            # runs. Schedule a (debounced) rebuild; disabling is instant via
            # the view filter and needs nothing.
            self.coordinator.schedule_gravity_update()
