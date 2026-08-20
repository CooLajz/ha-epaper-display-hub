"""Device configuration number entities."""

from __future__ import annotations

from homeassistant.components.number import NumberDeviceClass, NumberEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import HubConfigEntry
from .const import (
    DESIRED_PARTIAL_REFRESHES,
    MAX_PARTIAL_REFRESHES,
    MAX_SUSPENDED_REFRESH_INTERVAL_MINUTES,
    MIN_PARTIAL_REFRESHES,
    MIN_SUSPENDED_REFRESH_INTERVAL_MINUTES,
)
from .entity import EpaperDisplayEntity
from .models import normalize_partial_refreshes
from .scheduling import normalize_suspended_refresh_interval


class EpaperPartialRefreshNumber(EpaperDisplayEntity, NumberEntity):
    """Configure partial refreshes performed between full refreshes."""

    _attr_entity_category = EntityCategory.CONFIG
    _attr_native_min_value = float(MIN_PARTIAL_REFRESHES)
    _attr_native_max_value = float(MAX_PARTIAL_REFRESHES)
    _attr_native_step = 1.0

    def __init__(self, entry: HubConfigEntry, subentry_id: str) -> None:
        super().__init__(entry, subentry_id, DESIRED_PARTIAL_REFRESHES)

    @property
    def native_value(self) -> float:
        """Return the current desired count stored by the Hub."""
        record = self.entry.runtime_data.store.devices[self.device_id]
        return float(record.desired[DESIRED_PARTIAL_REFRESHES])

    async def async_set_native_value(self, value: float) -> None:
        """Persist a whole count and advance desired configuration revision."""
        normalized = normalize_partial_refreshes(value)
        record = self.entry.runtime_data.store.devices[self.device_id]
        if record.update_desired({DESIRED_PARTIAL_REFRESHES: normalized}):
            await self.entry.runtime_data.store.async_save()
            self.entry.runtime_data.coordinator.async_update_listeners()
        self.async_write_ha_state()


class EpaperSuspendedRefreshIntervalNumber(EpaperDisplayEntity, NumberEntity):
    """Configure telemetry wake interval while display refresh is suspended."""

    _attr_entity_category = EntityCategory.CONFIG
    _attr_native_min_value = float(MIN_SUSPENDED_REFRESH_INTERVAL_MINUTES)
    _attr_native_max_value = float(MAX_SUSPENDED_REFRESH_INTERVAL_MINUTES)
    _attr_native_step = 1.0
    _attr_device_class = NumberDeviceClass.DURATION
    _attr_native_unit_of_measurement = UnitOfTime.MINUTES

    def __init__(self, entry: HubConfigEntry, subentry_id: str) -> None:
        super().__init__(entry, subentry_id, "suspended_refresh_interval_minutes")

    @property
    def native_value(self) -> float:
        """Return the Hub-owned interval in minutes."""
        record = self.entry.runtime_data.store.devices[self.device_id]
        return float(record.suspended_refresh_interval_minutes)

    async def async_set_native_value(self, value: float) -> None:
        """Persist a whole interval without changing firmware config revision."""
        normalized = normalize_suspended_refresh_interval(value)
        record = self.entry.runtime_data.store.devices[self.device_id]
        if record.update_suspended_refresh_interval(normalized):
            await self.entry.runtime_data.store.async_save()
            self.entry.runtime_data.coordinator.async_update_listeners()
        self.async_write_ha_state()


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up per-display numeric configuration controls."""
    typed_entry: HubConfigEntry = entry
    for subentry_id in entry.subentries:
        async_add_entities(
            [
                EpaperPartialRefreshNumber(typed_entry, subentry_id),
                EpaperSuspendedRefreshIntervalNumber(typed_entry, subentry_id),
            ],
            config_subentry_id=subentry_id,
        )
