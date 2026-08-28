"""Base entity for the Pi-hole Lists integration."""

from urllib.parse import urlparse

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_URL
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import PiHoleListsCoordinator

# Attributes exposed on every list entity, copied from the Pi-hole list object.
STATE_ATTRIBUTES = (
    "id",
    "address",
    "type",
    "groups",
    "comment",
    "number",
    "invalid_domains",
    "status",
    "date_updated",
)


def _humanize_address(address: str) -> str:
    """Return a human-friendly name for a list URL.

    GitHub-hosted lists (``github.com``, ``raw.githubusercontent.com``) are
    named ``owner/repo``: most blocklists are hosted there, and the plain
    host/last-segment fallback would be ambiguous across repos (e.g. two
    lists both ending in ``hosts``).
    Everything else falls back to ``host/last-path-segment``.
    """
    parsed = urlparse(address)
    segments = [segment for segment in parsed.path.split("/") if segment]
    if (
        parsed.hostname in ("github.com", "raw.githubusercontent.com")
        and len(segments) >= 2
    ):
        return f"{segments[0]}/{segments[1]}"
    host = parsed.netloc or address
    if segments:
        return f"{host}/{segments[-1]}"
    return host


class PiHoleListsEntity(CoordinatorEntity[PiHoleListsCoordinator]):
    """Base entity: coordinator-driven, one device per config entry."""

    _attr_should_poll = False

    def __init__(self, coordinator: PiHoleListsCoordinator, entry: ConfigEntry) -> None:
        """Initialize the entity."""
        super().__init__(coordinator)
        self._entry_id = entry.entry_id
        self._host = urlparse(entry.data[CONF_URL]).netloc

    @property
    def device_info(self) -> DeviceInfo:
        """Return device information for this config entry."""
        return DeviceInfo(
            identifiers={(DOMAIN, self._entry_id)},
            name=f"Pi-hole lists ({self._host})",
            manufacturer="Pi-hole",
            model="v6",
        )

    @property
    def list_data(self) -> dict:
        """Return the latest Pi-hole data for this entity's list."""
        return self.coordinator.data[self._list_id]
