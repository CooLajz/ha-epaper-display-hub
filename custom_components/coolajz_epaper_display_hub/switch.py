"""Desired configuration switches."""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.components.switch import SwitchEntity, SwitchEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import HubConfigEntry
from .const import (
    DESIRED_SHOW_BATTERY_VOLTAGE,
    OTA_COMMAND_SOURCE_MANUAL,
)
from .entity import EpaperDisplayEntity
from .security import generate_nonce


@dataclass(frozen=True, kw_only=True)
class EpaperSwitchDescription(SwitchEntityDescription):
    """Describe one desired or Hub-owned switch."""

    mode: str


DESCRIPTIONS = (
    EpaperSwitchDescription(
        key="show_battery_voltage",
        mode="desired_battery_voltage",
        entity_category=EntityCategory.CONFIG,
    ),
    EpaperSwitchDescription(
        key="auto_ota",
        mode="automatic_ota",
        entity_category=EntityCategory.CONFIG,
    ),
    EpaperSwitchDescription(
        key="ota_next_wake",
        mode="manual_ota",
        entity_category=EntityCategory.CONFIG,
    ),
)


class EpaperConfigSwitch(EpaperDisplayEntity, SwitchEntity):
    """Change desired state or manage a durable Hub-side OTA request."""

    entity_description: EpaperSwitchDescription

    def __init__(
        self,
        entry: HubConfigEntry,
        subentry_id: str,
        description: EpaperSwitchDescription,
    ) -> None:
        super().__init__(entry, subentry_id, description.key)
        self.entity_description = description

    @property
    def is_on(self) -> bool:
        """Return the current desired or queued state."""
        record = self.entry.runtime_data.store.devices.get(self.device_id)
        if record is None:
            return False
        if self.entity_description.mode == "automatic_ota":
            return record.automatic_ota_enabled
        if self.entity_description.mode == "manual_ota":
            return record.manual_ota_requested
        return bool(record.desired.get(DESIRED_SHOW_BATTERY_VOLTAGE))

    async def _async_set(self, value: bool) -> None:
        record = self.entry.runtime_data.store.devices[self.device_id]
        changed = False
        if self.entity_description.mode == "automatic_ota":
            changed = record.update_ota_settings(value, record.ota_check_time)
        elif self.entity_description.mode == "manual_ota":
            if value and not record.manual_ota_requested:
                changed = record.enqueue_ota_command(
                    generate_nonce(), OTA_COMMAND_SOURCE_MANUAL
                )
            elif not value:
                changed = record.cancel_undelivered_manual_ota()
        else:
            changed = record.update_desired({DESIRED_SHOW_BATTERY_VOLTAGE: value})
        if changed:
            await self.entry.runtime_data.store.async_save()
            self.entry.runtime_data.coordinator.async_update_listeners()
        if self.entity_description.mode == "automatic_ota" and value:
            await self.entry.runtime_data.coordinator.async_schedule_automatic_ota()
        self.async_write_ha_state()

    async def async_turn_on(self, **kwargs: object) -> None:
        await self._async_set(True)

    async def async_turn_off(self, **kwargs: object) -> None:
        await self._async_set(False)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up desired-state controls per subentry."""
    for subentry_id in entry.subentries:
        async_add_entities(
            [EpaperConfigSwitch(entry, subentry_id, item) for item in DESCRIPTIONS],
            config_subentry_id=subentry_id,
        )
