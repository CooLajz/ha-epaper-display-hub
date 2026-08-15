"""Private persistent state for E-paper Display Hub."""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import STORAGE_KEY, STORAGE_VERSION
from .models import DeviceRecord, RevokedDeviceRecord


class HubStore:
    """Own the private per-device keys and durable protocol state."""

    def __init__(self, hass: HomeAssistant) -> None:
        self._store = Store[dict[str, Any]](
            hass, STORAGE_VERSION, STORAGE_KEY, private=True, atomic_writes=True
        )
        self.devices: dict[str, DeviceRecord] = {}
        self.revoked_devices: dict[str, RevokedDeviceRecord] = {}
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
        self.revoked_devices = {
            item.device_id: item
            for raw in stored.get("revoked_devices", [])
            if isinstance(raw, dict)
            for item in [RevokedDeviceRecord.from_dict(raw)]
        }
        if not stored.get("pairing_salt"):
            await self.async_save()

    def revoke_device(self, device_id: str) -> bool:
        """Move one active device into the restricted unpair-only collection."""
        from .security import generate_nonce

        record = self.devices.pop(device_id, None)
        if record is None:
            return False
        self.revoked_devices[device_id] = RevokedDeviceRecord.from_device(
            record, generate_nonce()
        )
        return True

    def activate_device(self, record: DeviceRecord) -> None:
        """Store freshly paired credentials and discard an older revocation."""
        self.revoked_devices.pop(record.device_id, None)
        self.devices[record.device_id] = record

    async def async_clear(self) -> None:
        """Remove all keys when the complete Hub config entry is deleted."""
        from .security import generate_secret

        self.devices.clear()
        self.revoked_devices.clear()
        self.pairing_salt = generate_secret()
        await self.async_save()

    async def async_save(self) -> None:
        """Save secrets and replay state immediately."""
        await self._store.async_save(
            {
                "pairing_salt": self.pairing_salt,
                "devices": [record.as_dict() for record in self.devices.values()],
                "revoked_devices": [
                    record.as_dict() for record in self.revoked_devices.values()
                ],
            }
        )
