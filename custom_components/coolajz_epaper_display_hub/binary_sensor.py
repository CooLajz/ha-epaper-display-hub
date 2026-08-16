"""Diagnostic status entities."""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import HubConfigEntry
from .const import CONF_DEVICE_ID, DOMAIN
from .entity import EpaperDisplayEntity


@dataclass(frozen=True, kw_only=True)
class EpaperBinaryDescription(BinarySensorEntityDescription):
    """Describe a diagnostic boolean."""

    mode: str


DESCRIPTIONS = (
    EpaperBinaryDescription(
        key="available",
        mode="available",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    EpaperBinaryDescription(
        key="configuration_pending",
        mode="pending",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
)


class EpaperBinarySensor(EpaperDisplayEntity, BinarySensorEntity):
    """Represent calculated display status."""

    entity_description: EpaperBinaryDescription

    def __init__(
        self,
        entry: HubConfigEntry,
        subentry_id: str,
        description: EpaperBinaryDescription,
    ) -> None:
        super().__init__(entry, subentry_id, description.key)
        self.entity_description = description

    @property
    def is_on(self) -> bool:
        """Return the requested diagnostic state."""
        if self.entity_description.mode == "available":
            return self.coordinator.is_device_available(self.device_id)
        record = self.entry.runtime_data.store.devices.get(self.device_id)
        return bool(record and record.configuration_pending)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up one diagnostic set per subentry."""
    registry = er.async_get(hass)
    for subentry_id, subentry in entry.subentries.items():
        obsolete_entity_id = registry.async_get_entity_id(
            "binary_sensor",
            DOMAIN,
            f"{subentry.data[CONF_DEVICE_ID]}-last_transfer_success",
        )
        if obsolete_entity_id is not None:
            registry.async_remove(obsolete_entity_id)
        async_add_entities(
            [EpaperBinarySensor(entry, subentry_id, item) for item in DESCRIPTIONS],
            config_subentry_id=subentry_id,
        )
