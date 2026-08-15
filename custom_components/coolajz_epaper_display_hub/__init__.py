"""E-paper Display Hub integration."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING, Any

from .const import CONF_CONTENT, CONF_DEVICE_ID, DOMAIN, PLATFORMS

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

    from .coordinator import HubCoordinator
    from .pairing import PairingManager
    from .store import HubStore

    HubConfigEntry = ConfigEntry["HubRuntime"]
else:
    HubConfigEntry = Any


@dataclass(slots=True)
class HubRuntime:
    """Runtime objects shared by protocol handlers and entity platforms."""

    entry: HubConfigEntry
    store: HubStore
    pairing: PairingManager
    coordinator: HubCoordinator

    def content_for(self, device_id: str) -> Mapping[str, Any]:
        """Return content selection for a paired device."""
        for subentry in self.entry.subentries.values():
            if subentry.data.get(CONF_DEVICE_ID) == device_id:
                value = subentry.data.get(CONF_CONTENT, {})
                return value if isinstance(value, Mapping) else {}
        return {}


async def async_setup_entry(hass: HomeAssistant, entry: HubConfigEntry) -> bool:
    """Set up the single hub config entry."""
    from homeassistant.helpers.event import async_track_time_interval

    from .coordinator import HubCoordinator
    from .pairing import PairingManager
    from .store import HubStore
    from .webhook import async_register_views

    views_key = f"{DOMAIN}_views_registered"
    if not hass.data.get(views_key):
        async_register_views(hass)
        hass.data[views_key] = True

    store = HubStore(hass)
    await store.async_load()
    configured_ids = {
        str(subentry.data[CONF_DEVICE_ID])
        for subentry in entry.subentries.values()
        if CONF_DEVICE_ID in subentry.data
    }
    removed_ids = set(store.devices) - configured_ids
    for device_id in removed_ids:
        # Removing a subentry is key revocation; no orphan key remains usable.
        store.devices.pop(device_id, None)
    if removed_ids:
        await store.async_save()

    coordinator = HubCoordinator(hass, store, entry)
    runtime = HubRuntime(entry, store, PairingManager(store), coordinator)
    entry.runtime_data = runtime
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = runtime
    entry.async_on_unload(entry.add_update_listener(_async_reload_entry))
    entry.async_on_unload(
        async_track_time_interval(
            hass,
            lambda now: coordinator.async_update_listeners(),
            timedelta(minutes=1),
        )
    )
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: HubConfigEntry) -> bool:
    """Unload the integration without deleting durable device state."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
    return unloaded


async def _async_reload_entry(hass: HomeAssistant, entry: HubConfigEntry) -> None:
    """Reload after a display is added, reconfigured, or removed."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Migrate future config-entry schemas without silently discarding data."""
    if entry.version > 1:
        return False
    if entry.version < 1:
        hass.config_entries.async_update_entry(entry, version=1, minor_version=1)
    return True
