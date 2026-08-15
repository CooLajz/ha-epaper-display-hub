"""Private persistent state for E-paper Display Hub."""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import STORAGE_KEY, STORAGE_VERSION
from .models import DeviceRecord


class HubStore:
    """Own the private per-device keys and durable protocol state."""

    def __init__(self, hass: HomeAssistant) -> None:
        self._store = Store[dict[str, Any]](
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
