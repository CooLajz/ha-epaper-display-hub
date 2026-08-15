"""Tests for telemetry, content, and desired/reported state."""

from dataclasses import dataclass

import pytest

from custom_components.coolajz_epaper_display_hub.models import (
    DeviceRecord,
    ProtocolError,
    normalize_content,
    normalize_partial_refreshes,
    normalize_state,
)
from custom_components.coolajz_epaper_display_hub.security import generate_secret


@dataclass
class FakeState:
    state: str
    attributes: dict[str, object]


class FakeStates:
    def __init__(self, states: dict[str, FakeState]) -> None:
        self._states = states

    def get(self, entity_id: str) -> FakeState | None:
        return self._states.get(entity_id)


class FakeHass:
    def __init__(self, states: dict[str, FakeState]) -> None:
        self.states = FakeStates(states)


def test_partial_refresh_count_is_limited_and_old_values_are_migrated() -> None:
    assert normalize_partial_refreshes(0) == 0
    assert normalize_partial_refreshes(20) == 20
    with pytest.raises(ProtocolError, match="outside 0 to 20"):
        normalize_partial_refreshes(21)
    with pytest.raises(ProtocolError, match="must be whole"):
        normalize_partial_refreshes(1.5)

    record = DeviceRecord("AA:BB:CC:DD:EE:FF", generate_secret())
    stored = record.as_dict()
    stored["desired"]["partial_refreshes_between_full"] = 100
    old_revision = stored["desired_revision"]

    restored = DeviceRecord.from_dict(stored)

    assert restored.desired["partial_refreshes_between_full"] == 20
    assert restored.desired_revision == old_revision + 1


def test_complete_and_partial_telemetry_capabilities() -> None:
    """Optional environment entities require an explicit sensor and valid values."""
    record = DeviceRecord("AA:BB:CC:DD:EE:FF", generate_secret())
    record.remember_capabilities(
        {
            "environment_sensor_present": True,
            "board_temperature": 22.5,
            "board_humidity": 48,
            "battery_percent": 90,
        }
    )
    assert record.capabilities_seen == ["board_humidity", "board_temperature"]

    other = DeviceRecord("11:22:33:44:55:66", generate_secret())
    other.remember_capabilities(
        {"environment_sensor_present": False, "board_temperature": 99}
    )
    other.remember_capabilities({"environment_sensor_present": True})
    assert other.capabilities_seen == []


def test_unknown_unavailable_missing_and_manual_type() -> None:
    """Bad states invalidate one slot and manual type wins over device class."""
    unavailable = normalize_state(
        FakeState("unavailable", {"device_class": "temperature"})
    )
    unknown = normalize_state(FakeState("unknown", {"device_class": "humidity"}))
    missing = normalize_state(None)
    manual = normalize_state(
        FakeState(
            "1001.24", {"device_class": "pressure", "unit_of_measurement": "hPa"}
        ),
        configured_type="number",
        decimals=1,
    )
    assert unavailable["valid"] is False and unavailable["value"] is None
    assert unknown["valid"] is False and unknown["value"] is None
    assert missing["valid"] is False and missing["value"] is None
    assert manual == {
        "valid": True,
        "value": 1001.2,
        "type": "number",
        "label": None,
        "unit": "hPa",
    }


def test_content_and_weather_normalization_is_fault_isolated() -> None:
    """One missing entity does not invalidate weather or another numeric slot."""
    hass = FakeHass(
        {
            "sensor.room": FakeState(
                "23.456",
                {"device_class": "temperature", "unit_of_measurement": "°C"},
            ),
            "weather.home": FakeState("sunny", {"temperature": 24, "humidity": 40}),
        }
    )
    result = normalize_content(
        hass,
        {
            "main": {"entity_id": "sensor.room", "decimals": 1},
            "bottom_left": {"entity_id": "sensor.missing"},
            "weather": "weather.home",
        },
    )
    assert result["main"]["value"] == 23.5
    assert result["bottom_left"] == {
        "valid": False,
        "value": None,
        "type": "number",
        "label": None,
        "unit": None,
    }
    assert result["weather"]["valid"] is True


def test_desired_reported_and_durable_commands_round_trip() -> None:
    """Pending status follows applied revision and commands survive storage reload."""
    record = DeviceRecord("AA:BB:CC:DD:EE:FF", generate_secret())
    assert record.configuration_pending
    assert record.update_desired({"show_battery_voltage": False})
    assert record.update_configuration({}, {"22": 60, "23": 15})
    revision = record.desired_revision
    record.pending_commands.append({"id": "refresh-1", "type": "full_refresh"})
    record.last_contact_at = "2026-08-15T22:17:03+02:00"
    record.next_wake_at = "2026-08-15T23:00:00+02:00"
    record.last_planned_interval_seconds = 2577
    record.last_entity_data = {
        "battery_percent": 81.0,
        "battery_voltage": 3.912,
        "last_transfer_success": True,
    }
    restored = DeviceRecord.from_dict(record.as_dict())
    assert restored.pending_commands == [{"id": "refresh-1", "type": "full_refresh"}]
    assert restored.wake_schedule["22"] == 60
    assert restored.wake_schedule["23"] == 15
    assert restored.next_wake_at == "2026-08-15T23:00:00+02:00"
    assert restored.last_planned_interval_seconds == 2577
    assert restored.last_entity_data == record.last_entity_data
    assert restored.configuration_pending
    assert restored.mark_configuration_delivered(revision)
    assert not restored.configuration_pending
    assert restored.configuration_application_pending
    restored.apply_reported(
        {
            "reported_config": {
                "revision": revision,
                "applied": False,
                "values": {"show_battery_voltage": False},
            }
        }
    )
    assert not restored.configuration_pending
    assert restored.configuration_application_pending
    restored.apply_reported(
        {
            "reported_config": {
                "revision": revision,
                "applied": True,
                "values": dict(restored.desired),
            }
        }
    )
    assert not restored.configuration_pending
    assert not restored.configuration_application_pending


def test_legacy_web_enabled_is_removed_from_stored_desired_config() -> None:
    """Hub-only firmware must not receive the retired local-web setting."""
    record = DeviceRecord("AA:BB:CC:DD:EE:FF", generate_secret())
    stored = record.as_dict()
    stored["desired"] = {**stored["desired"], "web_enabled": True}

    restored = DeviceRecord.from_dict(stored)

    assert "web_enabled" not in restored.desired
