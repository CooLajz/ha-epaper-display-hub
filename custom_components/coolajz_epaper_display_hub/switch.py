"""Desired configuration switches."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from homeassistant.components.switch import SwitchEntity, SwitchEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import HubConfigEntry
from .const import (
    CONF_CONTENT,
    CONF_DEVICE_ID,
    DESIRED_SHOW_BATTERY_VOLTAGE,
    DOMAIN,
    OTA_COMMAND_SOURCE_MANUAL,
    SLOT_WEATHER,
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
    EpaperSwitchDescription(
        key="wifi_full_scan",
        mode="wifi_full_scan",
        entity_category=EntityCategory.CONFIG,
    ),
)

SHOW_WEATHER_DESCRIPTION = EpaperSwitchDescription(
    key="show_weather",
    mode="show_weather",
    entity_category=EntityCategory.CONFIG,
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
        if self.entity_description.mode == "wifi_full_scan":
            return record.wifi_full_scan_requested
        if self.entity_description.mode == "show_weather":
            return record.show_weather
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
            elif not value and record.manual_ota_requested:
                self.async_write_ha_state()
                raise ServiceValidationError(
                    translation_domain=DOMAIN,
                    translation_key="command_waiting_for_device",
                )
        elif self.entity_description.mode == "wifi_full_scan":
            if value and not record.wifi_full_scan_requested:
                changed = record.enqueue_wifi_full_scan_command(generate_nonce())
            elif not value and record.wifi_full_scan_requested:
                self.async_write_ha_state()
                raise ServiceValidationError(
                    translation_domain=DOMAIN,
                    translation_key="command_waiting_for_device",
                )
        elif self.entity_description.mode == "show_weather":
            changed = record.update_show_weather(value)
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
    typed_entry: HubConfigEntry = entry
    registry = er.async_get(hass)
    weather_switches: set[str] = set()

    def _weather_configured(subentry: object) -> bool:
        data = getattr(subentry, "data", {})
        content = data.get(CONF_CONTENT, {}) if isinstance(data, Mapping) else {}
        return isinstance(content, Mapping) and bool(content.get(SLOT_WEATHER))

    def _remove_weather_switch(device_id: str) -> None:
        entity_id = registry.async_get_entity_id(
            "switch",
            DOMAIN,
            f"{device_id}-{SHOW_WEATHER_DESCRIPTION.key}",
        )
        if entity_id is not None:
            registry.async_remove(entity_id)

    for subentry_id, subentry in entry.subentries.items():
        descriptions = list(DESCRIPTIONS)
        device_id = str(subentry.data[CONF_DEVICE_ID])
        if _weather_configured(subentry):
            descriptions.append(SHOW_WEATHER_DESCRIPTION)
            weather_switches.add(device_id)
        else:
            _remove_weather_switch(device_id)
        async_add_entities(
            [
                EpaperConfigSwitch(typed_entry, subentry_id, item)
                for item in descriptions
            ],
            config_subentry_id=subentry_id,
        )

    @callback
    def _sync_weather_switches() -> None:
        for subentry_id, subentry in entry.subentries.items():
            device_id = str(subentry.data[CONF_DEVICE_ID])
            configured = _weather_configured(subentry)
            if configured and device_id not in weather_switches:
                weather_switches.add(device_id)
                async_add_entities(
                    [
                        EpaperConfigSwitch(
                            typed_entry,
                            subentry_id,
                            SHOW_WEATHER_DESCRIPTION,
                        )
                    ],
                    config_subentry_id=subentry_id,
                )
            elif not configured and device_id in weather_switches:
                weather_switches.remove(device_id)
                _remove_weather_switch(device_id)

    entry.async_on_unload(
        typed_entry.runtime_data.coordinator.async_add_listener(
            _sync_weather_switches
        )
    )
