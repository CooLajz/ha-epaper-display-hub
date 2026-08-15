"""Constants for E-paper Display Hub."""

from __future__ import annotations

from datetime import timedelta

DOMAIN = "coolajz_epaper_display_hub"
NAME = "E-paper Display Hub"
MANUFACTURER = "coolajz"
PROTOCOL_VERSION = 1
STORAGE_KEY = f"{DOMAIN}.state"
STORAGE_VERSION = 1

PLATFORMS = ["sensor", "binary_sensor", "switch", "time"]
SUBENTRY_TYPE_DISPLAY = "display"

API_BASE = f"/api/{DOMAIN}/v1"
CHECKIN_PATH = f"{API_BASE}/check-in"
TIME_SYNC_PATH = f"{API_BASE}/time-sync"

NONCE_HISTORY_SIZE = 64
MIN_REPLAY_WINDOW = timedelta(hours=24)
MAX_REPLAY_WINDOW = timedelta(days=7)
REPLAY_GRACE = timedelta(minutes=10)
TIME_SYNC_RATE_LIMIT = 6
TIME_SYNC_RATE_WINDOW = timedelta(minutes=1)

DEFAULT_REFRESH_INTERVAL_MINUTES = 30
DEFAULT_PARTIAL_REFRESHES = 10
AVAILABILITY_TOLERANCE = timedelta(minutes=2)
WAKE_INTERVAL_OPTIONS = (5, 10, 15, 20, 30, 60)

CONF_DEVICE_ID = "device_id"
CONF_FRIENDLY_NAME = "friendly_name"
CONF_MODEL = "model"
CONF_HARDWARE_VARIANT = "hardware_variant"
CONF_FIRMWARE_VERSION = "firmware_version"
CONF_PROTOCOL_VERSION = "protocol_version"
CONF_CONTENT = "content"
CONF_DEVICE_IP = "device_ip"
CONF_PAIRING_PIN = "pairing_pin"
CONF_TRANSPORT_SECURITY = "transport_security"
CONF_HUB_URL = "hub_url"
CONF_ALLOW_INSECURE_TLS = "allow_insecure_tls"

TRANSPORT_HTTP = "http"
TRANSPORT_HTTPS_VERIFIED = "https_verified"
TRANSPORT_HTTPS_INSECURE = "https_insecure"
TRANSPORT_SECURITY_OPTIONS = (
    TRANSPORT_HTTPS_VERIFIED,
    TRANSPORT_HTTPS_INSECURE,
    TRANSPORT_HTTP,
)

DESIRED_SHOW_BATTERY_VOLTAGE = "show_battery_voltage"
DESIRED_PARTIAL_REFRESHES = "partial_refreshes_between_full"
AUTO_OTA_ENABLED = "auto_ota"
OTA_CHECK_TIME = "ota_check_time"
DEFAULT_OTA_CHECK_TIME = "03:00:00"
OTA_COMMAND_TYPE = "ota_check"
OTA_COMMAND_SOURCE_MANUAL = "manual"
OTA_COMMAND_SOURCE_AUTOMATIC = "automatic"
OTA_STATUS_VALUES = ("current", "updated", "failed")
WAKE_SCHEDULE_FIELD_PREFIX = "wake_interval_"

DEFAULT_WAKE_SCHEDULE = {
    str(hour): DEFAULT_REFRESH_INTERVAL_MINUTES for hour in range(24)
}

DEFAULT_DESIRED = {
    DESIRED_SHOW_BATTERY_VOLTAGE: True,
    DESIRED_PARTIAL_REFRESHES: DEFAULT_PARTIAL_REFRESHES,
}

SLOT_MAIN = "main"
SLOT_BOTTOM_LEFT = "bottom_left"
SLOT_BOTTOM_RIGHT = "bottom_right"
SLOT_WEATHER = "weather"
SLOT_EXTRA_HUMIDITY = "extra_humidity"
VALUE_SLOTS = (SLOT_MAIN, SLOT_BOTTOM_LEFT, SLOT_BOTTOM_RIGHT)

SIGNATURE_HEADER = "X-EPD-Signature"
TIMESTAMP_HEADER = "X-EPD-Timestamp"
NONCE_HEADER = "X-EPD-Nonce"
DEVICE_HEADER = "X-EPD-Device-ID"
PROTOCOL_HEADER = "X-EPD-Protocol-Version"
