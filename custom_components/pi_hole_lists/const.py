"""Constants for the Pi-hole Lists integration."""

DOMAIN = "pi_hole_lists"

CONF_APP_PASSWORD = "app_password"
CONF_SCAN_INTERVAL = "scan_interval"

# Scan interval in minutes (configurable via the options flow).
DEFAULT_SCAN_INTERVAL = 5
MIN_SCAN_INTERVAL = 1
MAX_SCAN_INTERVAL = 60

# Pi-hole v6 list types.
LIST_TYPE_BLOCK = "block"
