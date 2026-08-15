"""Push coordinator and check-in orchestration."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from .const import (
    AVAILABILITY_TOLERANCE,
    PROTOCOL_VERSION,
)
from .models import DeviceRecord, normalize_content, optional_number
from .scheduling import next_wake
from .security import generate_nonce
from .store import HubStore

_LOGGER = logging.getLogger(__name__)

_PERSISTED_NUMERIC_TELEMETRY = {
    "battery_percent",
    "battery_voltage",
    "rssi",
    "active_runtime_ms",
    "board_temperature",
    "board_humidity",
}
_PERSISTED_TEXT_TELEMETRY = {
    "last_ota_status",
    "available_firmware_version",
}


class HubCoordinator(DataUpdateCoordinator[dict[str, dict[str, Any]]]):
    """Receive device push updates without polling sleeping displays."""

    def __init__(
        self, hass: HomeAssistant, store: HubStore, config_entry: ConfigEntry
    ) -> None:
        super().__init__(
            hass,
            logger=_LOGGER,
            name="E-paper Display Hub",
            config_entry=config_entry,
        )
        self.store = store
        self.data = {
            record.device_id: self._persisted_device_data(record)
            for record in store.devices.values()
        }

    @staticmethod
    def _parse_timestamp(value: str | None) -> datetime | None:
        """Parse one persisted aware ISO timestamp."""
        if not value:
            return None
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            return None
        return parsed if parsed.tzinfo is not None else None

    @classmethod
    def _persisted_device_data(cls, record: DeviceRecord) -> dict[str, Any]:
        """Restore the latest known entity values and planning diagnostics."""
        return {
            **record.last_entity_data,
            "last_contact": cls._parse_timestamp(record.last_contact_at),
            "next_wake_at": cls._parse_timestamp(record.next_wake_at),
            "last_planned_interval_seconds": record.last_planned_interval_seconds,
        }

    @staticmethod
    def _entity_data_for_storage(
        telemetry: Mapping[str, Any], payload: Mapping[str, Any]
    ) -> dict[str, Any]:
        """Keep only bounded primitive values exposed by Hub entities."""
        persisted: dict[str, Any] = {}
        for key in _PERSISTED_NUMERIC_TELEMETRY:
            raw_value = telemetry.get(key)
            value = optional_number(raw_value)
            if value is not None:
                persisted[key] = (
                    raw_value
                    if isinstance(raw_value, int | float)
                    and not isinstance(raw_value, bool)
                    else value
                )
        last_refresh = telemetry.get("last_refresh")
        if isinstance(last_refresh, str | int | float) and not isinstance(
            last_refresh, bool
        ):
            persisted["last_refresh"] = last_refresh
        last_ota_check = telemetry.get("last_ota_check")
        if isinstance(last_ota_check, str):
            persisted["last_ota_check"] = last_ota_check
        for key in _PERSISTED_TEXT_TELEMETRY:
            value = telemetry.get(key)
            if isinstance(value, str):
                persisted[key] = value
        firmware_version = payload.get("firmware_version")
        if isinstance(firmware_version, str):
            persisted["firmware_version"] = firmware_version
        persisted["last_transfer_success"] = True
        return persisted

    async def async_schedule_automatic_ota(self, now: datetime | None = None) -> bool:
        """Create due daily OTA commands in the Home Assistant timezone."""
        timezone = dt_util.get_time_zone(self.hass.config.time_zone) or UTC
        local_now = (now or datetime.now(UTC)).astimezone(timezone)
        changed = False
        for record in self.store.devices.values():
            changed |= record.schedule_automatic_ota(local_now, generate_nonce())
        if changed:
            await self.store.async_save()
            self.async_update_listeners()
        return changed

    @callback
    def device_data(self, device_id: str) -> dict[str, Any]:
        """Return latest in-memory telemetry for an entity."""
        return self.data.get(device_id, {})

    @callback
    def is_device_available(self, device_id: str) -> bool:
        """Calculate availability from last contact and expected interval."""
        record = self.store.devices.get(device_id)
        if record is None:
            return False
        next_wake_at = self.device_data(device_id).get("next_wake_at")
        if not isinstance(next_wake_at, datetime):
            next_wake_at = self._parse_timestamp(record.next_wake_at)
        if not isinstance(next_wake_at, datetime):
            return False
        return (
            datetime.now(UTC) <= next_wake_at.astimezone(UTC) + AVAILABILITY_TOLERANCE
        )

    async def async_process_checkin(
        self,
        record: DeviceRecord,
        payload: Mapping[str, Any],
        content_config: Mapping[str, Any],
        *,
        response_time: datetime | None = None,
    ) -> dict[str, Any]:
        """Store telemetry and build the one-shot display response."""
        telemetry = dict(payload.get("telemetry", {}))
        record.apply_reported(payload)
        record.remember_capabilities(telemetry)
        now = (
            (response_time or datetime.now(UTC)).astimezone(UTC).replace(microsecond=0)
        )
        timezone = dt_util.get_time_zone(self.hass.config.time_zone) or UTC
        planned_wake, sleep_seconds = next_wake(
            now,
            timezone,
            record.wake_schedule,
        )
        record.last_contact_at = now.isoformat()
        record.next_wake_at = planned_wake.isoformat()
        record.last_planned_interval_seconds = sleep_seconds
        record.last_entity_data = self._entity_data_for_storage(telemetry, payload)
        acknowledgements = {
            str(item) for item in payload.get("command_acknowledgements", [])
        }
        record.acknowledge_commands(acknowledgements)
        record.schedule_automatic_ota(
            now.astimezone(timezone),
            generate_nonce(),
        )
        device_data = {
            **telemetry,
            "last_contact": now,
            "firmware_version": payload.get("firmware_version"),
            "model": payload.get("model"),
            "hardware_variant": payload.get("hardware_variant"),
            "protocol_version": PROTOCOL_VERSION,
            "last_transfer_success": True,
            "next_wake_at": planned_wake,
            "last_planned_interval_seconds": sleep_seconds,
        }
        updated = dict(self.data)
        updated[record.device_id] = device_data
        self.async_set_updated_data(updated)

        commands = record.commands_for_delivery()
        await self.store.async_save()
        return {
            "protocol_version": PROTOCOL_VERSION,
            "server_time": now.astimezone(timezone).isoformat(),
            "next_wake_at": planned_wake.isoformat(),
            "sleep_seconds": sleep_seconds,
            "revision": record.desired_revision,
            "desired_config": {
                "revision": record.desired_revision,
                "values": dict(record.desired),
                "pending": record.configuration_application_pending,
            },
            "content": normalize_content(
                self.hass,
                content_config,
                show_weather=record.show_weather,
            ),
            "commands": commands,
        }


def telemetry_value(data: Mapping[str, Any], key: str) -> Any:
    """Return normalized telemetry values for entities."""
    if key in {
        "battery_percent",
        "battery_voltage",
        "rssi",
        "active_runtime_ms",
        "board_temperature",
        "board_humidity",
        "last_planned_interval_seconds",
    }:
        return optional_number(data.get(key))
    return data.get(key)
