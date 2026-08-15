"""Telemetry sensors for paired displays."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    PERCENTAGE,
    SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
    UnitOfElectricPotential,
    UnitOfTemperature,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import HubConfigEntry
from .const import CONF_DEVICE_ID
from .coordinator import telemetry_value
from .entity import EpaperDisplayEntity


@dataclass(frozen=True, kw_only=True)
class EpaperSensorDescription(SensorEntityDescription):
    """Describe one telemetry field."""

    telemetry_key: str


SENSORS = (
    EpaperSensorDescription(
        key="battery",
        telemetry_key="battery_percent",
        device_class=SensorDeviceClass.BATTERY,
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    EpaperSensorDescription(
        key="battery_voltage",
        telemetry_key="battery_voltage",
        device_class=SensorDeviceClass.VOLTAGE,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    EpaperSensorDescription(
        key="last_contact",
        telemetry_key="last_contact",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    EpaperSensorDescription(
        key="last_refresh",
        telemetry_key="last_refresh",
        device_class=SensorDeviceClass.TIMESTAMP,
    ),
    EpaperSensorDescription(
        key="next_wake_interval",
        telemetry_key="next_wake_interval_minutes",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.MINUTES,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    EpaperSensorDescription(
        key="active_runtime",
        telemetry_key="active_runtime_ms",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.MILLISECONDS,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    EpaperSensorDescription(
        key="rssi",
        telemetry_key="rssi",
        device_class=SensorDeviceClass.SIGNAL_STRENGTH,
        native_unit_of_measurement=SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    EpaperSensorDescription(
        key="firmware_version",
        telemetry_key="firmware_version",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
)

OPTIONAL_SENSORS = {
    "board_temperature": EpaperSensorDescription(
        key="board_temperature",
        telemetry_key="board_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    "board_humidity": EpaperSensorDescription(
        key="board_humidity",
        telemetry_key="board_humidity",
        device_class=SensorDeviceClass.HUMIDITY,
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
}


class EpaperSensor(EpaperDisplayEntity, SensorEntity):
    """Represent the most recent telemetry from one wake cycle."""

    entity_description: EpaperSensorDescription

    def __init__(
        self,
        entry: HubConfigEntry,
        subentry_id: str,
        description: EpaperSensorDescription,
    ) -> None:
        super().__init__(entry, subentry_id, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> Any:
        """Return fresh in-memory telemetry; Recorder keeps history."""
        value = telemetry_value(
            self.coordinator.device_data(self.device_id),
            self.entity_description.telemetry_key,
        )
        if self.entity_description.device_class == SensorDeviceClass.TIMESTAMP:
            if isinstance(value, datetime):
                return value
            if isinstance(value, int | float):
                return datetime.fromtimestamp(value, UTC)
            if isinstance(value, str):
                try:
                    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
                    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
                except ValueError:
                    return None
        return value


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up static and report-driven optional sensors."""
    typed_entry: HubConfigEntry = entry
    added_optional: dict[str, set[str]] = {}
    for subentry_id, subentry in entry.subentries.items():
        device_id = str(subentry.data[CONF_DEVICE_ID])
        record = typed_entry.runtime_data.store.devices.get(device_id)
        optional = set(record.capabilities_seen) if record else set()
        added_optional[device_id] = set(optional)
        entities = [EpaperSensor(typed_entry, subentry_id, item) for item in SENSORS]
        entities.extend(
            EpaperSensor(typed_entry, subentry_id, OPTIONAL_SENSORS[key])
            for key in optional
            if key in OPTIONAL_SENSORS
        )
        async_add_entities(entities, config_subentry_id=subentry_id)

    @callback
    def _add_new_optional() -> None:
        for subentry_id, subentry in entry.subentries.items():
            device_id = str(subentry.data[CONF_DEVICE_ID])
            record = typed_entry.runtime_data.store.devices.get(device_id)
            if record is None:
                continue
            new_keys = set(record.capabilities_seen) - added_optional.setdefault(
                device_id, set()
            )
            entities = [
                EpaperSensor(typed_entry, subentry_id, OPTIONAL_SENSORS[key])
                for key in new_keys
                if key in OPTIONAL_SENSORS
            ]
            if entities:
                added_optional[device_id].update(new_keys)
                async_add_entities(entities, config_subentry_id=subentry_id)

    entry.async_on_unload(
        typed_entry.runtime_data.coordinator.async_add_listener(_add_new_optional)
    )
