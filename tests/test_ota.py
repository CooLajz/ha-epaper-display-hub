"""Tests for Hub-owned OTA scheduling and durable command delivery."""

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest

from custom_components.coolajz_epaper_display_hub.const import (
    OTA_COMMAND_SOURCE_MANUAL,
)
from custom_components.coolajz_epaper_display_hub.models import (
    DeviceRecord,
    ProtocolError,
    validate_checkin_payload,
)
from custom_components.coolajz_epaper_display_hub.security import generate_secret


def _record() -> DeviceRecord:
    return DeviceRecord("AA:BB:CC:DD:EE:FF", generate_secret())


def test_manual_ota_stays_requested_until_acknowledgement() -> None:
    record = _record()

    assert record.enqueue_ota_command("manual-1", OTA_COMMAND_SOURCE_MANUAL)
    assert record.manual_ota_requested
    assert record.commands_for_delivery() == [
        {"id": "manual-1", "type": "ota_check"}
    ]
    assert record.mark_commands_delivered({"manual-1"})
    assert record.manual_ota_requested
    assert record.commands_for_delivery() == [
        {"id": "manual-1", "type": "ota_check"}
    ]
    assert record.acknowledge_commands({"manual-1"})
    assert not record.manual_ota_requested
    assert record.commands_for_delivery() == []


def test_wifi_full_scan_and_ota_can_wait_for_acknowledgement_together() -> None:
    record = _record()

    assert record.enqueue_ota_command("manual-1", OTA_COMMAND_SOURCE_MANUAL)
    assert record.enqueue_wifi_full_scan_command("wifi-1")
    assert not record.enqueue_wifi_full_scan_command("wifi-duplicate")
    assert record.wifi_full_scan_requested
    assert record.commands_for_delivery() == [
        {"id": "manual-1", "type": "ota_check"},
        {"id": "wifi-1", "type": "wifi_full_scan"},
    ]

    assert record.mark_commands_delivered({"manual-1", "wifi-1"})
    assert record.manual_ota_requested
    assert record.wifi_full_scan_requested
    assert record.acknowledge_commands({"wifi-1"})
    assert record.manual_ota_requested
    assert not record.wifi_full_scan_requested


def test_automatic_ota_runs_at_local_time_and_is_deduplicated_per_day() -> None:
    record = _record()
    record.update_ota_settings(True, "03:30:00")
    prague = ZoneInfo("Europe/Prague")

    assert not record.schedule_automatic_ota(
        datetime(2026, 8, 15, 3, 29, tzinfo=prague), "auto-too-early"
    )
    assert record.schedule_automatic_ota(
        datetime(2026, 8, 15, 3, 30, tzinfo=prague), "auto-1"
    )
    assert not record.schedule_automatic_ota(
        datetime(2026, 8, 15, 23, 59, tzinfo=prague), "auto-duplicate"
    )
    assert record.commands_for_delivery() == [{"id": "auto-1", "type": "ota_check"}]

    record.mark_commands_delivered({"auto-1"})
    record.acknowledge_commands({"auto-1"})
    assert record.schedule_automatic_ota(
        datetime(2026, 8, 16, 3, 31, tzinfo=prague), "auto-2"
    )
    assert record.commands_for_delivery() == [{"id": "auto-2", "type": "ota_check"}]


def test_next_daily_command_waits_for_previous_automatic_ack() -> None:
    record = _record()
    record.update_ota_settings(True, "03:30:00")
    prague = ZoneInfo("Europe/Prague")
    record.schedule_automatic_ota(
        datetime(2026, 8, 15, 3, 30, tzinfo=prague), "auto-1"
    )

    assert not record.schedule_automatic_ota(
        datetime(2026, 8, 16, 3, 30, tzinfo=prague), "auto-blocked"
    )
    assert record.last_automatic_ota_date == "2026-08-15"

    record.acknowledge_commands({"auto-1"})
    assert record.schedule_automatic_ota(
        datetime(2026, 8, 16, 3, 31, tzinfo=prague), "auto-2"
    )
    assert record.commands_for_delivery() == [{"id": "auto-2", "type": "ota_check"}]


def test_home_assistant_timezone_conversion_controls_automatic_ota() -> None:
    record = _record()
    record.update_ota_settings(True, "03:00:00")
    prague = ZoneInfo("Europe/Prague")
    utc_now = datetime(2026, 8, 15, 1, 0, tzinfo=UTC)

    assert record.schedule_automatic_ota(utc_now.astimezone(prague), "auto-local")


def test_manual_ota_is_independent_of_automatic_ota() -> None:
    record = _record()
    record.update_ota_settings(True, "03:00:00")

    assert record.enqueue_ota_command("manual-1", OTA_COMMAND_SOURCE_MANUAL)
    record.update_ota_settings(False, "03:00:00")
    assert not record.automatic_ota_enabled
    assert record.manual_ota_requested
    assert record.commands_for_delivery() == [
        {"id": "manual-1", "type": "ota_check"}
    ]


def test_only_one_ota_command_can_wait_for_acknowledgement() -> None:
    record = _record()
    prague = ZoneInfo("Europe/Prague")

    assert record.enqueue_ota_command("manual-1", OTA_COMMAND_SOURCE_MANUAL)
    assert not record.enqueue_ota_command("manual-2", OTA_COMMAND_SOURCE_MANUAL)
    record.update_ota_settings(True, "03:00:00")
    assert not record.schedule_automatic_ota(
        datetime(2026, 8, 15, 3, 0, tzinfo=prague), "auto-blocked"
    )
    assert record.last_automatic_ota_date is None

    record.mark_commands_delivered({"manual-1"})
    assert not record.enqueue_ota_command("manual-3", OTA_COMMAND_SOURCE_MANUAL)
    record.acknowledge_commands({"manual-1"})
    assert record.schedule_automatic_ota(
        datetime(2026, 8, 15, 3, 1, tzinfo=prague), "auto-1"
    )
    assert record.commands_for_delivery() == [{"id": "auto-1", "type": "ota_check"}]


def test_command_ids_match_firmware_contract() -> None:
    record = _record()

    with pytest.raises(ValueError, match="command ID"):
        record.enqueue_ota_command("contains space", OTA_COMMAND_SOURCE_MANUAL)
    with pytest.raises(ValueError, match="command ID"):
        record.enqueue_ota_command("x" * 129, OTA_COMMAND_SOURCE_MANUAL)


def test_ota_settings_and_delivered_queue_survive_storage_round_trip() -> None:
    record = _record()
    record.update_ota_settings(True, "04:15:00")
    record.enqueue_ota_command("manual-1", OTA_COMMAND_SOURCE_MANUAL)
    record.mark_commands_delivered({"manual-1"})

    restored = DeviceRecord.from_dict(record.as_dict())

    assert restored.automatic_ota_enabled
    assert restored.ota_check_time == "04:15:00"
    assert restored.manual_ota_requested
    assert restored.commands_for_delivery() == [
        {"id": "manual-1", "type": "ota_check"}
    ]
def test_ota_diagnostics_and_acknowledgements_are_validated() -> None:
    payload = {
        "protocol_version": 1,
        "device_id": "AA:BB:CC:DD:EE:FF",
        "model": "ESPink",
        "hardware_variant": "ESP32-S3",
        "firmware_version": "1.0.0",
        "telemetry": {
            "last_ota_check": "2026-08-15T03:00:02+02:00",
            "last_ota_status": "current",
            "available_firmware_version": "1.0.0",
        },
        "command_acknowledgements": ["ota-1"],
    }
    validate_checkin_payload(payload, "AA:BB:CC:DD:EE:FF")

    payload["telemetry"]["last_ota_status"] = "broken"
    with pytest.raises(ProtocolError, match="OTA status"):
        validate_checkin_payload(payload, "AA:BB:CC:DD:EE:FF")
