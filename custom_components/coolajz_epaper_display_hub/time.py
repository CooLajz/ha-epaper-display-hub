"""Hub-owned daily OTA check time."""

from __future__ import annotations

from datetime import time

from homeassistant.components.time import TimeEntity, TimeEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import HubConfigEntry
from .entity import EpaperDisplayEntity

OTA_CHECK_TIME_DESCRIPTION = TimeEntityDescription(
    key="ota_check_time",
    translation_key="ota_check_time",
    entity_category=EntityCategory.CONFIG,
)


class EpaperOtaCheckTime(EpaperDisplayEntity, TimeEntity):
    """Configure the local Home Assistant time for automatic OTA checks."""

    entity_description = OTA_CHECK_TIME_DESCRIPTION

    def __init__(self, entry: HubConfigEntry, subentry_id: str) -> None:
        super().__init__(entry, subentry_id, OTA_CHECK_TIME_DESCRIPTION.key)

    @property
    def native_value(self) -> time:
        """Return the configured local wall-clock time."""
        record = self.entry.runtime_data.store.devices[self.device_id]
        return time.fromisoformat(record.ota_check_time)

    async def async_set_value(self, value: time) -> None:
        """Persist the Hub-owned schedule and immediately evaluate it."""
        record = self.entry.runtime_data.store.devices[self.device_id]
        if record.update_ota_settings(record.automatic_ota_enabled, value):
            await self.entry.runtime_data.store.async_save()
            self.entry.runtime_data.coordinator.async_update_listeners()
        await self.entry.runtime_data.coordinator.async_schedule_automatic_ota()
        self.async_write_ha_state()


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up one OTA schedule time per display subentry."""
    typed_entry: HubConfigEntry = entry
    for subentry_id in entry.subentries:
        async_add_entities(
            [EpaperOtaCheckTime(typed_entry, subentry_id)],
            config_subentry_id=subentry_id,
        )
