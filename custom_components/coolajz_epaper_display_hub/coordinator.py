"""Push coordinator and check-in orchestration."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .const import DEFAULT_AVAILABILITY_GRACE, PROTOCOL_VERSION
from .models import DeviceRecord, normalize_content, optional_number
from .store import HubStore

_LOGGER = logging.getLogger(__name__)


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
        self.data = {}

    @callback
    def device_data(self, device_id: str) -> dict[str, Any]:
        """Return latest in-memory telemetry for an entity."""
        return self.data.get(device_id, {})

    @callback
    def is_device_available(self, device_id: str) -> bool:
        """Calculate availability from last contact and expected interval."""
        data = self.device_data(device_id)
        last_contact = data.get("last_contact")
        if not isinstance(last_contact, datetime):
            return False
        record = self.store.devices.get(device_id)
        if record is None:
            return False
        minutes = max(1, int(record.desired.get("refresh_interval_minutes", 30)))
        elapsed = (datetime.now(UTC) - last_contact).total_seconds()
        return elapsed <= minutes * 60 * DEFAULT_AVAILABILITY_GRACE

    async def async_process_checkin(
        self,
        record: DeviceRecord,
        payload: Mapping[str, Any],
        content_config: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Store telemetry and build the one-shot display response."""
        telemetry = dict(payload.get("telemetry", {}))
        record.apply_reported(payload)
        record.remember_capabilities(telemetry)
        now = datetime.now(UTC)
        device_data = {
            **telemetry,
            "last_contact": now,
            "firmware_version": payload.get("firmware_version"),
            "model": payload.get("model"),
            "hardware_variant": payload.get("hardware_variant"),
            "protocol_version": PROTOCOL_VERSION,
            "last_transfer_success": True,
        }
        updated = dict(self.data)
        updated[record.device_id] = device_data
        self.async_set_updated_data(updated)

        acknowledgements = {
            str(item) for item in payload.get("command_acknowledgements", [])
        }
        if acknowledgements:
            record.pending_commands = [
                item
                for item in record.pending_commands
                if str(item.get("id", "")) not in acknowledgements
            ]
        commands = [dict(item) for item in record.pending_commands]
        await self.store.async_save()
        return {
            "protocol_version": PROTOCOL_VERSION,
            "server_time": int(now.timestamp()),
            "desired_config": {
                "revision": record.desired_revision,
                "values": dict(record.desired),
                "pending": record.configuration_pending,
            },
            "content": normalize_content(self.hass, content_config),
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
        "next_wake_interval_minutes",
    }:
        return optional_number(data.get(key))
    return data.get(key)
