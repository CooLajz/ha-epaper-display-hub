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
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import HubConfigEntry
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
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    EpaperBinaryDescription(
        key="last_transfer_success",
        mode="transfer",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
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
        if self.entity_description.mode == "pending":
            record = self.entry.runtime_data.store.devices.get(self.device_id)
            return bool(record and record.configuration_pending)
        return bool(
            self.coordinator.device_data(self.device_id).get("last_transfer_success")
        )


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up one diagnostic set per subentry."""
    for subentry_id in entry.subentries:
        async_add_entities(
            [EpaperBinarySensor(entry, subentry_id, item) for item in DESCRIPTIONS],
            config_subentry_id=subentry_id,
        )
