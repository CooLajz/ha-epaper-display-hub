"""Private persistent state for E-paper Display Hub."""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import STORAGE_KEY, STORAGE_VERSION
from .migration import migrate_storage_data
from .models import DeviceRecord


class HubStateStore(Store[dict[str, Any]]):
    """Private Store with an explicit future migration hook."""

    async def _async_migrate_func(
        self,
        old_major_version: int,
        old_minor_version: int,
        old_data: dict[str, Any],
    ) -> dict[str, Any]:
        return migrate_storage_data(old_major_version, old_minor_version, old_data)


class HubStore:
    """Own the private per-device keys and durable protocol state."""

    def __init__(self, hass: HomeAssistant) -> None:
        self._store = HubStateStore(
            hass, STORAGE_VERSION, STORAGE_KEY, private=True, atomic_writes=True
        )
        self.devices: dict[str, DeviceRecord] = {}
        self.pairing_salt = ""

    async def async_load(self) -> None:
        """Load state without blocking the event loop."""
        from .security import generate_secret

        stored = await self._store.async_load() or {}
        self.pairing_salt = str(stored.get("pairing_salt") or generate_secret())
        self.devices = {
            item.device_id: item
            for raw in stored.get("devices", [])
            if isinstance(raw, dict)
            for item in [DeviceRecord.from_dict(raw)]
        }
        if not stored.get("pairing_salt"):
            await self.async_save()

    async def async_save(self) -> None:
        """Save secrets and replay state immediately."""
        await self._store.async_save(
            {
                "pairing_salt": self.pairing_salt,
                "devices": [record.as_dict() for record in self.devices.values()],
            }
        )
