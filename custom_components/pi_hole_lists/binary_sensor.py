"""Binary sensor platform for the Pi-hole Lists integration.

A single device-level sensor per config entry surfaces the debounced gravity
rebuild state machine from the coordinator: ``on`` while a rebuild is
pending/running, ``off`` otherwise, with a ``status`` attribute of
``idle|pending|running|failed``.
"""

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import GRAVITY_PENDING, GRAVITY_RUNNING
from .coordinator import PiHoleListsCoordinator
from .entity import PiHoleListsEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Gravity update binary sensor for this entry."""
    coordinator: PiHoleListsCoordinator = entry.runtime_data.coordinator
    async_add_entities([GravityUpdateSensor(coordinator, entry)])


class GravityUpdateSensor(PiHoleListsEntity, BinarySensorEntity):
    """On while a gravity rebuild is pending or running.

    Reuses the base entity purely for its per-entry device info (the same
    device as the switches); the switch-specific ``list_data`` property is
    never used here.
    """

    def __init__(
        self,
        coordinator: PiHoleListsCoordinator,
        entry: ConfigEntry,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}-gravity-update"
        self._attr_name = "Gravity update"

    @property
    def is_on(self) -> bool:
        """Return whether a gravity rebuild is pending or running."""
        return self.coordinator.gravity_state in (GRAVITY_PENDING, GRAVITY_RUNNING)

    @property
    def extra_state_attributes(self) -> dict:
        """Return the raw gravity state machine status."""
        return {"status": self.coordinator.gravity_state}

    def _handle_coordinator_update(self) -> None:
        """Re-render on every coordinator update (incl. gravity transitions)."""
        self.async_write_ha_state()
