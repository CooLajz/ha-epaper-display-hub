"""Tests for telemetry, content, and desired/reported state."""

from dataclasses import dataclass

import pytest

from custom_components.coolajz_epaper_display_hub.const import DEFAULT_WAKE_SCHEDULE
from custom_components.coolajz_epaper_display_hub.models import (
    DeviceRecord,
    ProtocolError,
    RevokedDeviceRecord,
    normalize_content,
    normalize_partial_refreshes,
    normalize_state,
    retain_active_runtime,
    validate_checkin_payload,
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


def test_new_device_default_wake_schedule() -> None:
    record = DeviceRecord("AA:BB:CC:DD:EE:FF", generate_secret())

    assert record.wake_schedule == DEFAULT_WAKE_SCHEDULE
    assert {record.wake_schedule[str(hour)] for hour in range(0, 5)} == {60}
    assert {record.wake_schedule[str(hour)] for hour in range(5, 8)} == {15}
    assert {record.wake_schedule[str(hour)] for hour in range(8, 21)} == {5}
    assert {record.wake_schedule[str(hour)] for hour in range(21, 23)} == {15}
    assert record.wake_schedule["23"] == 60
    assert record.wake_time_correction_seconds == 0
    assert record.suspended_refresh_interval_minutes == 60


def test_partial_refresh_count_is_limited_to_device_entity_range() -> None:
    assert normalize_partial_refreshes(0) == 0
    assert normalize_partial_refreshes(50) == 50
    with pytest.raises(ProtocolError, match="outside 0 to 50"):
        normalize_partial_refreshes(51)
    with pytest.raises(ProtocolError, match="must be whole"):
        normalize_partial_refreshes(1.5)


def test_missing_active_runtime_retains_last_timer_wake_sample() -> None:
    previous = {"active_runtime_ms": 4200, "battery_percent": 80}

    assert retain_active_runtime({"battery_percent": 79}, previous) == {
        "active_runtime_ms": 4200,
        "battery_percent": 79,
    }
    assert retain_active_runtime(
        {"active_runtime_ms": 3600}, previous
    ) == {"active_runtime_ms": 3600}


def test_checkin_ip_address_must_be_valid_ipv4() -> None:
    payload = {
        "protocol_version": 1,
        "device_id": "AA:BB:CC:DD:EE:FF",
        "model": "ESPink",
        "hardware_variant": "ESP32-S3",
        "firmware_version": "1.0.0",
        "telemetry": {"ip_address": "192.168.1.123"},
    }
    validate_checkin_payload(payload, "AA:BB:CC:DD:EE:FF")

    payload["telemetry"]["ip_address"] = "192.168.1.999"
    with pytest.raises(ProtocolError, match="IP address"):
        validate_checkin_payload(payload, "AA:BB:CC:DD:EE:FF")


def test_checkin_wifi_bssid_must_be_normalized_mac_address() -> None:
    payload = {
        "protocol_version": 1,
        "device_id": "AA:BB:CC:DD:EE:FF",
        "model": "ESPink",
        "hardware_variant": "ESP32-S3",
        "firmware_version": "1.0.0",
        "telemetry": {"wifi_bssid": "11:22:33:44:55:66"},
    }
    validate_checkin_payload(payload, "AA:BB:CC:DD:EE:FF")

    payload["telemetry"]["wifi_bssid"] = "11:22:33:44:55"
    with pytest.raises(ProtocolError, match="BSSID"):
        validate_checkin_payload(payload, "AA:BB:CC:DD:EE:FF")


def test_revoked_record_retains_only_unpair_credentials_and_command() -> None:
    record = DeviceRecord("AA:BB:CC:DD:EE:FF", generate_secret())
    record.nonces.append("AAAAAAAAAAAAAAAAAAAAAA")
    record.last_entity_data = {"battery_percent": 80}
    record.pending_commands.append({"id": "ota-1", "type": "ota_check"})

    revoked = RevokedDeviceRecord.from_device(record, "unpair-1")

    assert revoked.device_id == record.device_id
    assert revoked.secret == record.secret
    assert revoked.nonces == record.nonces
    assert revoked.wake_schedule == record.wake_schedule
    assert revoked.commands_for_delivery() == [{"id": "unpair-1", "type": "unpair"}]
    assert set(revoked.as_dict()) == {
        "device_id",
        "secret",
        "wake_schedule",
        "nonces",
        "unpair_command_id",
    }
    assert RevokedDeviceRecord.from_dict(revoked.as_dict()) == revoked


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
    assert unavailable["valid"] is False and unavailable["display_value"] is None
    assert unknown["valid"] is False and unknown["display_value"] is None
    assert missing["valid"] is False and missing["display_value"] is None
    assert manual == {
        "valid": True,
        "display_value": "1001.2",
        "type": "number",
        "label": None,
        "unit": "hPa",
    }


def test_auto_type_preserves_explicit_zero_decimal_places() -> None:
    """Automatic type detection must not replace an explicit zero precision."""
    normalized = normalize_state(
        FakeState(
            "1245.67",
            {"device_class": "power", "unit_of_measurement": "W"},
        ),
        configured_type="auto",
        decimals=0,
    )

    assert normalized == {
        "valid": True,
        "display_value": "1246",
        "type": "number",
        "label": None,
        "unit": "W",
    }


def test_display_value_preserves_configured_trailing_zeroes() -> None:
    """The Hub sends the exact text that firmware must render."""
    normalized = normalize_state(FakeState("24", {}), decimals=2)

    assert normalized["display_value"] == "24.00"


def test_hyphen_unit_override_hides_native_unit() -> None:
    """A hyphen explicitly suppresses the entity's native display unit."""
    state = FakeState("24", {"unit_of_measurement": "°C"})

    assert normalize_state(state, unit=None)["unit"] == "°C"
    assert normalize_state(state, unit="-")["unit"] == ""


def test_display_payload_respects_firmware_utf8_limits() -> None:
    """Long metadata is bounded and an oversized value invalidates only its slot."""
    normalized = normalize_state(
        FakeState(
            "1" * 81,
            {
                "friendly_name": "Příliš dlouhý název " * 4,
                "unit_of_measurement": "velmi dlouhá jednotka °C" * 2,
            },
        ),
        decimals=0,
    )

    assert normalized["valid"] is False
    assert normalized["display_value"] is None
    assert len(normalized["label"].encode("utf-8")) <= 80
    assert len(normalized["unit"].encode("utf-8")) <= 24


def test_state_and_text_values_are_sent_as_printable_utf8() -> None:
    state = normalize_state(
        FakeState("Zapnuto", {"friendly_name": "Relé"}), configured_type="state"
    )
    text = normalize_state(
        FakeState("Příliš zataženo", {}), configured_type="text"
    )
    invalid = normalize_state(
        FakeState("řádek 1\nřádek 2", {}), configured_type="text"
    )

    assert state == {
        "valid": True,
        "display_value": "Zapnuto",
        "type": "state",
        "label": None,
        "unit": None,
    }
    assert text["valid"] is True
    assert text["display_value"] == "Příliš zataženo"
    assert invalid["valid"] is False
    assert invalid["display_value"] is None


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
    assert result["main"]["display_value"] == "23.5"
    assert result["bottom_left"] == {
        "valid": False,
        "display_value": None,
        "type": "number",
        "label": None,
        "unit": None,
    }
    assert result["weather"]["valid"] is True

    hidden = normalize_content(
        hass,
        {"weather": "weather.home"},
        show_weather=False,
    )
    assert hidden["weather"] == {
        "valid": False,
        "condition": None,
    }

    invalid_weather = normalize_content(
        FakeHass({"weather.long": FakeState("x" * 33, {})}),
        {"weather": "weather.long"},
    )
    assert invalid_weather["weather"] == {"valid": False, "condition": None}


def test_desired_reported_and_durable_commands_round_trip() -> None:
    """Pending status follows applied revision and commands survive storage reload."""
    record = DeviceRecord("AA:BB:CC:DD:EE:FF", generate_secret())
    assert record.configuration_pending
    assert record.update_desired({"show_battery_voltage": False})
    assert record.update_configuration({}, {"22": 60, "23": 15})
    revision = record.desired_revision
    record.pending_commands.append({"id": "refresh-1", "type": "ota_check"})
    record.last_contact_at = "2026-08-15T22:17:03+02:00"
    record.next_wake_at = "2026-08-15T23:00:00+02:00"
    record.last_planned_interval_seconds = 2577
    record.last_entity_data = {
        "battery_percent": 81.0,
        "battery_voltage": 3.912,
    }
    assert record.update_show_weather(False)
    assert record.update_wake_time_correction(-35)
    assert record.update_suspended_refresh_interval(180)
    restored = DeviceRecord.from_dict(record.as_dict())
    assert restored.pending_commands == [{"id": "refresh-1", "type": "ota_check"}]
    assert restored.wake_schedule["22"] == 60
    assert restored.wake_schedule["23"] == 15
    assert restored.next_wake_at == "2026-08-15T23:00:00+02:00"
    assert restored.last_planned_interval_seconds == 2577
    assert restored.last_entity_data == record.last_entity_data
    assert not restored.show_weather
    assert restored.wake_time_correction_seconds == -35
    assert restored.suspended_refresh_interval_minutes == 180
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


def test_content_change_advances_revision_and_pending_state() -> None:
    """Display content uses the same delivered revision contract as config values."""
    record = DeviceRecord("AA:BB:CC:DD:EE:FF", generate_secret())
    record.mark_configuration_delivered(record.desired_revision)
    original_revision = record.desired_revision

    assert record.update_configuration({}, content_changed=True)
    assert record.desired_revision == original_revision + 1
    assert record.configuration_pending
