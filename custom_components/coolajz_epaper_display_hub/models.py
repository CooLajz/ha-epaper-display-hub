"""Protocol-independent models and normalization helpers."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from .const import DEFAULT_DESIRED, PROTOCOL_VERSION, VALUE_SLOTS

MAC_RE = re.compile(r"^[0-9A-F]{12}$")
KNOWN_VALUE_TYPES = {
    "temperature",
    "humidity",
    "carbon_dioxide",
    "volatile_organic_compounds",
    "pressure",
    "number",
}


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


@dataclass(slots=True)
class DeviceRecord:
    """Persistent security and desired-state record for one display."""

    device_id: str
    secret: str
    desired: dict[str, Any] = field(default_factory=lambda: dict(DEFAULT_DESIRED))
    desired_revision: int = 1
    reported: dict[str, Any] = field(default_factory=dict)
    reported_revision: int = 0
    applied_revision: int = 0
    nonces: list[str] = field(default_factory=list)
    pending_commands: list[dict[str, Any]] = field(default_factory=list)
    capabilities_seen: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> DeviceRecord:
        """Load a record from storage."""
        desired = dict(DEFAULT_DESIRED)
        desired.update(data.get("desired", {}))
        return cls(
            device_id=format_device_id(str(data["device_id"])),
            secret=str(data["secret"]),
            desired=desired,
            desired_revision=max(1, int(data.get("desired_revision", 1))),
            reported=dict(data.get("reported", {})),
            reported_revision=max(0, int(data.get("reported_revision", 0))),
            applied_revision=max(0, int(data.get("applied_revision", 0))),
            nonces=[str(item) for item in data.get("nonces", [])],
            pending_commands=[dict(item) for item in data.get("pending_commands", [])],
            capabilities_seen=[str(item) for item in data.get("capabilities_seen", [])],
        )

    def as_dict(self) -> dict[str, Any]:
        """Serialize a record for private Home Assistant storage."""
        return {
            "device_id": self.device_id,
            "secret": self.secret,
            "desired": self.desired,
            "desired_revision": self.desired_revision,
            "reported": self.reported,
            "reported_revision": self.reported_revision,
            "applied_revision": self.applied_revision,
            "nonces": self.nonces,
            "pending_commands": self.pending_commands,
            "capabilities_seen": self.capabilities_seen,
        }

    @property
    def configuration_pending(self) -> bool:
        """Return whether desired config has not been reported as applied."""
        return self.applied_revision < self.desired_revision

    def update_desired(self, changes: Mapping[str, Any]) -> bool:
        """Update desired values and increment revision only on a real change."""
        updated = self.desired | dict(changes)
        if updated == self.desired:
            return False
        self.desired = updated
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
class PairingSession:
    """Short-lived pairing transaction."""

    code_digest: str
    expires_at: datetime
    friendly_name: str
    candidate: dict[str, Any] | None = None
    claim_token_digest: str | None = None
    claim_expires_at: datetime | None = None
    secret: str | None = None
    confirmed: bool = False


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
    number = optional_number(raw_state)
    valid = raw_state not in ("unknown", "unavailable", None) and number is not None
    native_unit = attributes.get("unit_of_measurement")
    return {
        "valid": valid,
        "value": round(number, decimals) if valid and number is not None else None,
        "type": value_type,
        "label": label or attributes.get("friendly_name"),
        "unit": unit if unit is not None else native_unit,
    }


def normalize_content(hass: Any, content: Mapping[str, Any]) -> dict[str, Any]:
    """Resolve selected HA entities without exposing entity IDs to the display."""
    result: dict[str, Any] = {}
    for slot in VALUE_SLOTS:
        selection = content.get(slot, {})
        if not isinstance(selection, Mapping) or not selection.get("entity_id"):
            result[slot] = {"valid": False, "value": None}
            continue
        state = hass.states.get(selection["entity_id"])
        result[slot] = normalize_state(
            state,
            configured_type=str(selection.get("type", "auto")),
            label=selection.get("label"),
            decimals=max(0, min(3, int(selection.get("decimals", 1)))),
            unit=selection.get("unit"),
        )

    weather_id = content.get("weather")
    weather_state = hass.states.get(weather_id) if isinstance(weather_id, str) else None
    weather_attributes = (
        getattr(weather_state, "attributes", {}) if weather_state else {}
    )
    condition = getattr(weather_state, "state", None) if weather_state else None
    weather_valid = condition not in (None, "unknown", "unavailable")
    result["weather"] = {
        "valid": weather_valid,
        "condition": condition if weather_valid else None,
        "temperature": optional_number(weather_attributes.get("temperature")),
        "humidity": optional_number(weather_attributes.get("humidity")),
    }

    humidity_id = content.get("extra_humidity")
    humidity_state = (
        hass.states.get(humidity_id) if isinstance(humidity_id, str) else None
    )
    result["extra_humidity"] = normalize_state(
        humidity_state, configured_type="humidity"
    )
    return result
