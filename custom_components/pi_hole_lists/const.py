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

# Gravity rebuild (POST /api/action/gravity = pihole -g). pihole -g can take
# minutes on large lists, so the client's short poll timeout must not apply.
GRAVITY_TIMEOUT = 1800  # s; cap on a single gravity run
GRAVITY_DEBOUNCE_SECONDS = 10  # coalesce a burst of enable toggles into one run

# Gravity update binary_sensor status values.
GRAVITY_IDLE = "idle"
GRAVITY_PENDING = "pending"
GRAVITY_RUNNING = "running"
GRAVITY_FAILED = "failed"
