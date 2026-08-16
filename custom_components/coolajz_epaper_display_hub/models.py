"""Protocol-independent models and normalization helpers."""

from __future__ import annotations

import re
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import UTC, datetime, time
from ipaddress import AddressValueError, IPv4Address
from typing import Any

from .const import (
    DEFAULT_DESIRED,
    DEFAULT_OTA_CHECK_TIME,
    DEFAULT_WAKE_SCHEDULE,
    DESIRED_PARTIAL_REFRESHES,
    MAX_PARTIAL_REFRESHES,
    MIN_PARTIAL_REFRESHES,
    OTA_COMMAND_SOURCE_AUTOMATIC,
    OTA_COMMAND_SOURCE_MANUAL,
    OTA_COMMAND_TYPE,
    OTA_STATUS_VALUES,
    PROTOCOL_VERSION,
    UNPAIR_COMMAND_TYPE,
    VALUE_SLOTS,
    WIFI_FULL_SCAN_COMMAND_TYPE,
)
from .scheduling import normalize_wake_schedule

MAC_RE = re.compile(r"^[0-9A-F]{12}$")
COLON_MAC_RE = re.compile(r"^(?:[0-9A-F]{2}:){5}[0-9A-F]{2}$")
KNOWN_VALUE_TYPES = {
    "temperature",
    "humidity",
    "carbon_dioxide",
    "volatile_organic_compounds",
    "pressure",
    "number",
    "state",
    "text",
}
MAX_COMMANDS_PER_RESPONSE = 16
MAX_COMMAND_ID_BYTES = 128
MAX_DISPLAY_VALUE_BYTES = 80
MAX_DISPLAY_LABEL_BYTES = 80
MAX_DISPLAY_UNIT_BYTES = 24
MAX_WEATHER_CONDITION_BYTES = 32


class ProtocolError(ValueError):
    """Raised when a protocol payload is invalid."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def utcnow() -> datetime:
    """Return an aware UTC datetime."""
    return datetime.now(UTC)


def format_device_id(value: str) -> str:
    """Normalize a MAC-derived device identifier."""
    compact = re.sub(r"[^0-9A-Fa-f]", "", value).upper()
    if not MAC_RE.fullmatch(compact):
        raise ProtocolError(
            "invalid_device_id", "Device ID must be a 48-bit MAC address"
        )
    return ":".join(compact[index : index + 2] for index in range(0, 12, 2))


def optional_number(value: Any) -> float | None:
    """Return a finite float or None."""
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number or number in (float("inf"), float("-inf")):
        return None
    return number


def normalize_ota_check_time(value: Any) -> str:
    """Return one local wall-clock time with second precision."""
    try:
        parsed = value if isinstance(value, time) else time.fromisoformat(str(value))
    except ValueError as err:
        raise ProtocolError("invalid_ota_time", "OTA check time is invalid") from err
    if parsed.tzinfo is not None:
        raise ProtocolError("invalid_ota_time", "OTA check time must be local")
    return parsed.replace(microsecond=0).isoformat(timespec="seconds")


def normalize_partial_refreshes(value: Any) -> int:
    """Validate the device-configurable partial refresh count."""
    if isinstance(value, bool):
        raise ProtocolError("invalid_partial_refreshes", "Invalid refresh count")
    try:
        numeric = float(value)
    except (TypeError, ValueError) as err:
        raise ProtocolError(
            "invalid_partial_refreshes", "Invalid refresh count"
        ) from err
    if not numeric.is_integer():
        raise ProtocolError("invalid_partial_refreshes", "Refresh count must be whole")
    result = int(numeric)
    if not MIN_PARTIAL_REFRESHES <= result <= MAX_PARTIAL_REFRESHES:
        raise ProtocolError(
            "invalid_partial_refreshes",
            f"Refresh count is outside {MIN_PARTIAL_REFRESHES} to "
            f"{MAX_PARTIAL_REFRESHES}",
        )
    return result


@dataclass(slots=True)
class DeviceRecord:
    """Persistent security and desired-state record for one display."""

    device_id: str
    secret: str
    desired: dict[str, Any] = field(default_factory=lambda: deepcopy(DEFAULT_DESIRED))
    wake_schedule: dict[str, int] = field(
        default_factory=lambda: deepcopy(DEFAULT_WAKE_SCHEDULE)
    )
    desired_revision: int = 1
    reported: dict[str, Any] = field(default_factory=dict)
    reported_revision: int = 0
    applied_revision: int = 0
    delivered_revision: int = 0
    nonces: list[str] = field(default_factory=list)
    pending_commands: list[dict[str, Any]] = field(default_factory=list)
    capabilities_seen: list[str] = field(default_factory=list)
    last_contact_at: str | None = None
    next_wake_at: str | None = None
    last_planned_interval_seconds: int | None = None
    last_entity_data: dict[str, Any] = field(default_factory=dict)
    show_weather: bool = True
    automatic_ota_enabled: bool = False
    ota_check_time: str = DEFAULT_OTA_CHECK_TIME
    last_automatic_ota_date: str | None = None

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> DeviceRecord:
        """Load a record from storage."""
        desired = deepcopy(DEFAULT_DESIRED)
        stored_desired = data.get("desired", {})
        if isinstance(stored_desired, Mapping):
            desired.update(
                {key: stored_desired[key] for key in desired if key in stored_desired}
            )
        desired[DESIRED_PARTIAL_REFRESHES] = normalize_partial_refreshes(
            desired[DESIRED_PARTIAL_REFRESHES]
        )
        desired_revision = max(1, int(data.get("desired_revision", 1)))
        applied_revision = max(0, int(data.get("applied_revision", 0)))
        return cls(
            device_id=format_device_id(str(data["device_id"])),
            secret=str(data["secret"]),
            desired=desired,
            wake_schedule=normalize_wake_schedule(data.get("wake_schedule")),
            desired_revision=desired_revision,
            reported=dict(data.get("reported", {})),
            reported_revision=max(0, int(data.get("reported_revision", 0))),
            applied_revision=applied_revision,
            delivered_revision=min(
                desired_revision,
                max(
                    applied_revision,
                    int(data.get("delivered_revision", applied_revision)),
                ),
            ),
            nonces=[str(item) for item in data.get("nonces", [])],
            pending_commands=[dict(item) for item in data.get("pending_commands", [])],
            capabilities_seen=[str(item) for item in data.get("capabilities_seen", [])],
            last_contact_at=data.get("last_contact_at"),
            next_wake_at=data.get("next_wake_at"),
            last_planned_interval_seconds=(
                int(data["last_planned_interval_seconds"])
                if data.get("last_planned_interval_seconds") is not None
                else None
            ),
            last_entity_data=(
                dict(data["last_entity_data"])
                if isinstance(data.get("last_entity_data"), Mapping)
                else {}
            ),
            show_weather=bool(data.get("show_weather", True)),
            automatic_ota_enabled=bool(data.get("automatic_ota_enabled", False)),
            ota_check_time=normalize_ota_check_time(
                data.get("ota_check_time", DEFAULT_OTA_CHECK_TIME)
            ),
            last_automatic_ota_date=(
                str(data["last_automatic_ota_date"])
                if data.get("last_automatic_ota_date")
                else None
            ),
        )

    def as_dict(self) -> dict[str, Any]:
        """Serialize a record for private Home Assistant storage."""
        return {
            "device_id": self.device_id,
            "secret": self.secret,
            "desired": self.desired,
            "wake_schedule": self.wake_schedule,
            "desired_revision": self.desired_revision,
            "reported": self.reported,
            "reported_revision": self.reported_revision,
            "applied_revision": self.applied_revision,
            "delivered_revision": self.delivered_revision,
            "nonces": self.nonces,
            "pending_commands": self.pending_commands,
            "capabilities_seen": self.capabilities_seen,
            "last_contact_at": self.last_contact_at,
            "next_wake_at": self.next_wake_at,
            "last_planned_interval_seconds": self.last_planned_interval_seconds,
            "last_entity_data": self.last_entity_data,
            "show_weather": self.show_weather,
            "automatic_ota_enabled": self.automatic_ota_enabled,
            "ota_check_time": self.ota_check_time,
            "last_automatic_ota_date": self.last_automatic_ota_date,
        }

    @property
    def configuration_pending(self) -> bool:
        """Return whether desired config still waits for Hub transmission."""
        return self.delivered_revision < self.desired_revision

    @property
    def configuration_application_pending(self) -> bool:
        """Return whether firmware has not reported the revision as applied."""
        return self.applied_revision < self.desired_revision

    def mark_configuration_delivered(self, revision: int) -> bool:
        """Record the latest desired revision included in a signed response."""
        delivered = min(max(0, revision), self.desired_revision)
        if delivered <= self.delivered_revision:
            return False
        self.delivered_revision = delivered
        return True

    @property
    def manual_ota_requested(self) -> bool:
        """Return whether a manual OTA command waits for firmware acknowledgement."""
        return any(
            item.get("type") == OTA_COMMAND_TYPE
            and item.get("source") == OTA_COMMAND_SOURCE_MANUAL
            for item in self.pending_commands
        )

    @property
    def wifi_full_scan_requested(self) -> bool:
        """Return whether a Wi-Fi full scan waits for firmware acknowledgement."""
        return any(
            item.get("type") == WIFI_FULL_SCAN_COMMAND_TYPE
            for item in self.pending_commands
        )

    def enqueue_wifi_full_scan_command(self, command_id: str) -> bool:
        """Append one durable Wi-Fi full scan command unless one is pending."""
        if not _valid_command_id(command_id):
            raise ValueError("Invalid command ID")
        if self.wifi_full_scan_requested:
            return False
        self.pending_commands.append(
            {
                "id": command_id,
                "type": WIFI_FULL_SCAN_COMMAND_TYPE,
                "delivered": False,
            }
        )
        return True

    def enqueue_ota_command(
        self,
        command_id: str,
        source: str,
        *,
        automatic_date: str | None = None,
    ) -> bool:
        """Append one durable OTA command unless another OTA check is pending."""
        if source not in {OTA_COMMAND_SOURCE_MANUAL, OTA_COMMAND_SOURCE_AUTOMATIC}:
            raise ValueError("Unsupported OTA command source")
        if not _valid_command_id(command_id):
            raise ValueError("Invalid command ID")
        if any(
            item.get("type") == OTA_COMMAND_TYPE for item in self.pending_commands
        ):
            return False
        command: dict[str, Any] = {
            "id": command_id,
            "type": OTA_COMMAND_TYPE,
            "source": source,
            "delivered": False,
        }
        if automatic_date is not None:
            command["automatic_date"] = automatic_date
        self.pending_commands.append(command)
        return True

    def mark_commands_delivered(self, command_ids: set[str]) -> bool:
        """Mark commands included in a successfully prepared signed response."""
        changed = False
        for item in self.pending_commands:
            if str(item.get("id", "")) in command_ids and not bool(
                item.get("delivered", False)
            ):
                item["delivered"] = True
                changed = True
        return changed

    def acknowledge_commands(self, command_ids: set[str]) -> bool:
        """Remove commands explicitly acknowledged by firmware."""
        retained = [
            item
            for item in self.pending_commands
            if str(item.get("id", "")) not in command_ids
        ]
        if len(retained) == len(self.pending_commands):
            return False
        self.pending_commands = retained
        return True

    def commands_for_delivery(self) -> list[dict[str, str]]:
        """Return only the public signed command schema."""
        return [
            {"id": str(item["id"]), "type": str(item["type"])}
            for item in self.pending_commands
            if _valid_command_id(item.get("id"))
            and item.get("type")
            in {OTA_COMMAND_TYPE, WIFI_FULL_SCAN_COMMAND_TYPE}
        ][:MAX_COMMANDS_PER_RESPONSE]

    def update_ota_settings(self, enabled: bool, check_time: Any) -> bool:
        """Update Hub-owned daily OTA scheduling settings."""
        normalized_time = normalize_ota_check_time(check_time)
        if (
            self.automatic_ota_enabled == enabled
            and self.ota_check_time == normalized_time
        ):
            return False
        self.automatic_ota_enabled = enabled
        self.ota_check_time = normalized_time
        return True

    def update_show_weather(self, enabled: bool) -> bool:
        """Update whether configured weather content is sent to the display."""
        if self.show_weather == enabled:
            return False
        self.show_weather = enabled
        return True

    def schedule_automatic_ota(self, local_now: datetime, command_id: str) -> bool:
        """Enqueue at most one automatic OTA command for a local calendar day."""
        if not self.automatic_ota_enabled or local_now.tzinfo is None:
            return False
        scheduled = time.fromisoformat(self.ota_check_time)
        today = local_now.date().isoformat()
        if local_now.time().replace(tzinfo=None) < scheduled:
            return False
        if self.last_automatic_ota_date == today:
            return False
        if not self.enqueue_ota_command(
            command_id,
            OTA_COMMAND_SOURCE_AUTOMATIC,
            automatic_date=today,
        ):
            return False
        self.last_automatic_ota_date = today
        return True

    def update_desired(self, changes: Mapping[str, Any]) -> bool:
        """Update desired values and increment revision only on a real change."""
        return self.update_configuration(changes)

    def update_configuration(
        self,
        changes: Mapping[str, Any],
        wake_schedule: Mapping[str, Any] | None = None,
        *,
        content_changed: bool = False,
    ) -> bool:
        """Update device configuration and hub-only planning in one revision."""
        normalized_changes = dict(changes)
        if DESIRED_PARTIAL_REFRESHES in normalized_changes:
            normalized_changes[DESIRED_PARTIAL_REFRESHES] = (
                normalize_partial_refreshes(
                    normalized_changes[DESIRED_PARTIAL_REFRESHES]
                )
            )
        updated_desired = self.desired | normalized_changes
        updated_schedule = (
            normalize_wake_schedule(wake_schedule)
            if wake_schedule is not None
            else self.wake_schedule
        )
        if (
            updated_desired == self.desired
            and updated_schedule == self.wake_schedule
            and not content_changed
        ):
            return False
        self.desired = updated_desired
        self.wake_schedule = updated_schedule
        self.desired_revision += 1
        return True

    def apply_reported(self, payload: Mapping[str, Any]) -> None:
        """Record configuration acknowledged and applied by the device."""
        reported = payload.get("reported_config")
        if isinstance(reported, Mapping):
            values = dict(reported.get("values", {}))
            revision = int(reported.get("revision", 0))
            self.reported = values
            self.reported_revision = max(self.reported_revision, revision)
            if revision <= self.desired_revision:
                self.delivered_revision = max(self.delivered_revision, revision)
            if (
                bool(reported.get("applied", False))
                and revision <= self.desired_revision
                and (revision < self.desired_revision or values == self.desired)
            ):
                self.applied_revision = max(self.applied_revision, revision)

    def remember_capabilities(self, telemetry: Mapping[str, Any]) -> None:
        """Remember optional sensors only after actual valid reporting."""
        seen = set(self.capabilities_seen)
        if bool(telemetry.get("environment_sensor_present")):
            if optional_number(telemetry.get("board_temperature")) is not None:
                seen.add("board_temperature")
            if optional_number(telemetry.get("board_humidity")) is not None:
                seen.add("board_humidity")
        self.capabilities_seen = sorted(seen)


@dataclass(slots=True)
class RevokedDeviceRecord:
    """Minimal credentials retained only to deliver a signed unpair command."""

    device_id: str
    secret: str
    wake_schedule: dict[str, int]
    nonces: list[str]
    unpair_command_id: str

    @classmethod
    def from_device(
        cls, record: DeviceRecord, command_id: str
    ) -> RevokedDeviceRecord:
        """Restrict one active device record to the revocation-only state."""
        return cls(
            device_id=record.device_id,
            secret=record.secret,
            wake_schedule=deepcopy(record.wake_schedule),
            nonces=list(record.nonces),
            unpair_command_id=command_id,
        )

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> RevokedDeviceRecord:
        """Load the current revocation record schema."""
        return cls(
            device_id=format_device_id(str(data["device_id"])),
            secret=str(data["secret"]),
            wake_schedule=normalize_wake_schedule(data.get("wake_schedule")),
            nonces=[str(item) for item in data.get("nonces", [])],
            unpair_command_id=str(data["unpair_command_id"]),
        )

    def as_dict(self) -> dict[str, Any]:
        """Serialize only the values required for authenticated unpairing."""
        return {
            "device_id": self.device_id,
            "secret": self.secret,
            "wake_schedule": self.wake_schedule,
            "nonces": self.nonces,
            "unpair_command_id": self.unpair_command_id,
        }

    def commands_for_delivery(self) -> list[dict[str, str]]:
        """Return the single stable public unpair command."""
        return [{"id": self.unpair_command_id, "type": UNPAIR_COMMAND_TYPE}]


def validate_checkin_payload(payload: Mapping[str, Any], device_id: str) -> None:
    """Validate the required check-in envelope while allowing optional telemetry."""
    if int(payload.get("protocol_version", 0)) != PROTOCOL_VERSION:
        raise ProtocolError("unsupported_protocol", "Unsupported protocol version")
    if format_device_id(str(payload.get("device_id", ""))) != device_id:
        raise ProtocolError("device_mismatch", "Body and signed device ID differ")
    for key in ("model", "hardware_variant", "firmware_version"):
        if not isinstance(payload.get(key), str) or not payload[key].strip():
            raise ProtocolError("invalid_payload", f"Missing {key}")
    telemetry = payload.get("telemetry", {})
    if not isinstance(telemetry, Mapping):
        raise ProtocolError("invalid_payload", "Telemetry must be an object")
    ip_address = telemetry.get("ip_address")
    if ip_address is not None:
        if not isinstance(ip_address, str):
            raise ProtocolError("invalid_payload", "Invalid IP address")
        try:
            IPv4Address(ip_address)
        except AddressValueError as err:
            raise ProtocolError("invalid_payload", "Invalid IP address") from err
    wifi_bssid = telemetry.get("wifi_bssid")
    if wifi_bssid is not None and (
        not isinstance(wifi_bssid, str) or not COLON_MAC_RE.fullmatch(wifi_bssid)
    ):
        raise ProtocolError("invalid_payload", "Invalid Wi-Fi BSSID")
    ota_status = telemetry.get("last_ota_status")
    if ota_status is not None and ota_status not in OTA_STATUS_VALUES:
        raise ProtocolError("invalid_payload", "Invalid OTA status")
    last_ota_check = telemetry.get("last_ota_check")
    if last_ota_check is not None:
        if not isinstance(last_ota_check, str):
            raise ProtocolError("invalid_payload", "Invalid OTA check timestamp")
        try:
            parsed_ota_check = datetime.fromisoformat(
                last_ota_check.replace("Z", "+00:00")
            )
        except ValueError as err:
            raise ProtocolError(
                "invalid_payload", "Invalid OTA check timestamp"
            ) from err
        if parsed_ota_check.tzinfo is None:
            raise ProtocolError("invalid_payload", "OTA timestamp must be aware")
    available_version = telemetry.get("available_firmware_version")
    if available_version is not None and (
        not isinstance(available_version, str) or len(available_version) > 64
    ):
        raise ProtocolError("invalid_payload", "Invalid available firmware version")
    acknowledgements = payload.get("command_acknowledgements", [])
    if not isinstance(acknowledgements, list) or any(
        not isinstance(item, str) or not item or len(item) > 128
        for item in acknowledgements
    ):
        raise ProtocolError("invalid_payload", "Invalid command acknowledgements")


def normalize_state(
    state: Any,
    *,
    configured_type: str = "auto",
    label: str | None = None,
    decimals: int = 1,
    unit: str | None = None,
) -> dict[str, Any]:
    """Normalize a Home Assistant numeric state for the device payload."""
    attributes = getattr(state, "attributes", {}) if state is not None else {}
    raw_state = getattr(state, "state", None)
    device_class = str(attributes.get("device_class", ""))
    value_type = configured_type if configured_type != "auto" else device_class
    if value_type not in KNOWN_VALUE_TYPES:
        value_type = "number"
    is_text = value_type in {"state", "text"}
    number = None if is_text else optional_number(raw_state)
    valid = raw_state not in ("unknown", "unavailable", None)
    if is_text:
        display_value = str(raw_state) if valid else None
        valid = display_value is not None and _valid_utf8_display_text(
            display_value, MAX_DISPLAY_VALUE_BYTES
        )
        if not valid:
            display_value = None
    else:
        valid = valid and number is not None
        display_value = (
            f"{number:.{decimals}f}" if valid and number is not None else None
        )
    if (
        display_value is not None
        and len(display_value.encode("utf-8")) > MAX_DISPLAY_VALUE_BYTES
    ):
        valid = False
        display_value = None
    native_unit = attributes.get("unit_of_measurement")
    default_label = None if is_text else attributes.get("friendly_name")
    return {
        "valid": valid,
        "display_value": display_value,
        "type": value_type,
        "label": _truncate_utf8(
            label or default_label, MAX_DISPLAY_LABEL_BYTES
        ),
        "unit": _truncate_utf8(
            unit if unit is not None else native_unit, MAX_DISPLAY_UNIT_BYTES
        ),
    }


def normalize_content(
    hass: Any,
    content: Mapping[str, Any],
    *,
    show_weather: bool = True,
) -> dict[str, Any]:
    """Resolve selected HA entities without exposing entity IDs to the display."""
    result: dict[str, Any] = {}
    for slot in VALUE_SLOTS:
        selection = content.get(slot, {})
        if not isinstance(selection, Mapping) or not selection.get("entity_id"):
            result[slot] = {"valid": False, "display_value": None}
            continue
        state = hass.states.get(selection["entity_id"])
        result[slot] = normalize_state(
            state,
            configured_type=str(selection.get("type", "auto")),
            label=selection.get("label"),
            decimals=max(0, min(3, int(selection.get("decimals", 1)))),
            unit=selection.get("unit"),
        )

    weather_id = content.get("weather") if show_weather else None
    weather_state = hass.states.get(weather_id) if isinstance(weather_id, str) else None
    condition = getattr(weather_state, "state", None) if weather_state else None
    weather_valid = (
        isinstance(condition, str)
        and condition not in ("unknown", "unavailable")
        and _valid_utf8_display_text(condition, MAX_WEATHER_CONDITION_BYTES)
    )
    result["weather"] = {
        "valid": weather_valid,
        "condition": condition if weather_valid else None,
    }

    humidity_id = content.get("extra_humidity")
    humidity_state = (
        hass.states.get(humidity_id) if isinstance(humidity_id, str) else None
    )
    result["extra_humidity"] = normalize_state(
        humidity_state, configured_type="humidity", decimals=0
    )
    return result


def _truncate_utf8(value: Any, maximum_bytes: int) -> str | None:
    """Return text shortened without splitting a UTF-8 character."""
    if value is None:
        return None
    sanitized = "".join(
        character
        for character in str(value)
        if ord(character) >= 0x20 and ord(character) != 0x7F
    )
    encoded = sanitized.encode("utf-8")
    if len(encoded) <= maximum_bytes:
        return sanitized
    return encoded[:maximum_bytes].decode("utf-8", errors="ignore")


def _valid_utf8_display_text(value: str, maximum_bytes: int) -> bool:
    """Match firmware validation for non-empty printable UTF-8 text."""
    return (
        0 < len(value.encode("utf-8")) <= maximum_bytes
        and all(
            ord(character) >= 0x20 and ord(character) != 0x7F
            for character in value
        )
    )


def _valid_command_id(value: Any) -> bool:
    """Match the firmware command ID bounds and printable ASCII contract."""
    if not isinstance(value, str):
        return False
    encoded = value.encode("ascii", errors="strict") if value.isascii() else b""
    return (
        0 < len(encoded) <= MAX_COMMAND_ID_BYTES
        and all(0x21 <= character <= 0x7E for character in encoded)
    )
