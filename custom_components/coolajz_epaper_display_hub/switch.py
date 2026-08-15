"""Desired configuration switches."""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.components.switch import SwitchEntity, SwitchEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import HubConfigEntry
from .const import (
    DESIRED_AUTO_OTA,
    DESIRED_SHOW_BATTERY_VOLTAGE,
    DESIRED_WEB_ENABLED,
)
from .entity import EpaperDisplayEntity


@dataclass(frozen=True, kw_only=True)
class EpaperSwitchDescription(SwitchEntityDescription):
    """Describe a desired configuration switch."""

    desired_key: str


DESCRIPTIONS = (
    EpaperSwitchDescription(
        key="web_enabled",
        desired_key=DESIRED_WEB_ENABLED,
        entity_category=EntityCategory.CONFIG,
    ),
    EpaperSwitchDescription(
        key="show_battery_voltage",
        desired_key=DESIRED_SHOW_BATTERY_VOLTAGE,
        entity_category=EntityCategory.CONFIG,
    ),
    EpaperSwitchDescription(
        key="auto_ota",
        desired_key=DESIRED_AUTO_OTA,
        entity_category=EntityCategory.CONFIG,
    ),
)


class EpaperConfigSwitch(EpaperDisplayEntity, SwitchEntity):
    """Change desired state; sleeping devices apply it on a future wake."""

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
        """Return desired, not necessarily applied, state."""
        record = self.entry.runtime_data.store.devices.get(self.device_id)
        return bool(record and record.desired.get(self.entity_description.desired_key))

    async def _async_set(self, value: bool) -> None:
        record = self.entry.runtime_data.store.devices[self.device_id]
        if record.update_desired({self.entity_description.desired_key: value}):
            await self.entry.runtime_data.store.async_save()
            self.entry.runtime_data.coordinator.async_update_listeners()
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
