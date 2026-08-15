"""Tests for telemetry, content, and desired/reported state."""

from dataclasses import dataclass

from custom_components.coolajz_epaper_display_hub.models import (
    DeviceRecord,
    normalize_content,
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
    assert record.update_desired({"web_enabled": False})
    revision = record.desired_revision
    record.pending_commands.append({"id": "refresh-1", "type": "full_refresh"})
    restored = DeviceRecord.from_dict(record.as_dict())
    assert restored.pending_commands == [{"id": "refresh-1", "type": "full_refresh"}]
    restored.apply_reported(
        {
            "reported_config": {
                "revision": revision,
                "applied": False,
                "values": {"web_enabled": False},
            }
        }
    )
    assert restored.configuration_pending
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
