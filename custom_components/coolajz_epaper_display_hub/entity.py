"""Shared entity implementation."""

from __future__ import annotations

from typing import Any

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import HubConfigEntry
from .const import (
    CONF_DEVICE_ID,
    CONF_FIRMWARE_VERSION,
    CONF_HARDWARE_VARIANT,
    CONF_MODEL,
    DOMAIN,
    MANUFACTURER,
)
from .coordinator import HubCoordinator


class EpaperDisplayEntity(CoordinatorEntity[HubCoordinator]):
    """Base entity attached to exactly one display Config Subentry."""

    _attr_has_entity_name = True

    def __init__(
        self,
        entry: HubConfigEntry,
        subentry_id: str,
        key: str,
    ) -> None:
        super().__init__(entry.runtime_data.coordinator)
        self.entry = entry
        self.subentry_id = subentry_id
        self.subentry = entry.subentries[subentry_id]
        self.device_id = str(self.subentry.data[CONF_DEVICE_ID])
        self._attr_unique_id = f"{self.device_id}-{key}"
        self._attr_translation_key = key

    @property
    def device_info(self) -> DeviceInfo:
        """Return registry metadata; platform supplies the owning subentry id."""
        return DeviceInfo(
            identifiers={(DOMAIN, self.device_id)},
            manufacturer=MANUFACTURER,
            name=self.subentry.title,
            model=str(self.subentry.data.get(CONF_MODEL, "E-paper display")),
            hw_version=str(self.subentry.data.get(CONF_HARDWARE_VARIANT, "")),
            sw_version=str(
                self.coordinator.device_data(self.device_id).get("firmware_version")
                or self.subentry.data.get(CONF_FIRMWARE_VERSION, "")
            ),
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Expose safe metadata only; never expose keys or request signatures."""
        return {"device_id": self.device_id}
