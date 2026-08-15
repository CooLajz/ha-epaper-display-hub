"""Constants for E-paper Display Hub."""

from __future__ import annotations

from datetime import timedelta

DOMAIN = "coolajz_epaper_display_hub"
NAME = "E-paper Display Hub"
MANUFACTURER = "coolajz"
PROTOCOL_VERSION = 1
STORAGE_KEY = f"{DOMAIN}.state"
STORAGE_VERSION = 1

PLATFORMS = ["sensor", "binary_sensor", "switch"]
SUBENTRY_TYPE_DISPLAY = "display"

API_BASE = f"/api/{DOMAIN}/v1"
PAIR_REGISTER_PATH = f"{API_BASE}/pair/register"
PAIR_CLAIM_PATH = f"{API_BASE}/pair/claim"
CHECKIN_PATH = f"{API_BASE}/check-in"
TIME_SYNC_PATH = f"{API_BASE}/time-sync"

PAIRING_TTL = timedelta(minutes=10)
PAIRING_CODE_LENGTH = 8
CLAIM_TTL = timedelta(minutes=10)
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

DESIRED_WEB_ENABLED = "web_enabled"
DESIRED_SHOW_BATTERY_VOLTAGE = "show_battery_voltage"
DESIRED_AUTO_OTA = "auto_ota"
DESIRED_PARTIAL_REFRESHES = "partial_refreshes_between_full"
WAKE_SCHEDULE_FIELD_PREFIX = "wake_interval_"

DEFAULT_WAKE_SCHEDULE = {
    str(hour): DEFAULT_REFRESH_INTERVAL_MINUTES for hour in range(24)
}

DEFAULT_DESIRED = {
    DESIRED_WEB_ENABLED: True,
    DESIRED_SHOW_BATTERY_VOLTAGE: True,
    DESIRED_AUTO_OTA: False,
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
