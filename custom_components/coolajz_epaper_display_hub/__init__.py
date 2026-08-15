"""E-paper Display Hub integration."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING, Any

from .const import (
    CONF_CONTENT,
    CONF_DEVICE_ID,
    DOMAIN,
    PLATFORMS,
    TIME_SYNC_RATE_LIMIT,
    TIME_SYNC_RATE_WINDOW,
)

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.device_registry import DeviceEntry

    from .coordinator import HubCoordinator
    from .security import DeviceRateLimiter
    from .store import HubStore

    HubConfigEntry = ConfigEntry["HubRuntime"]
else:
    HubConfigEntry = Any


@dataclass(slots=True)
class HubRuntime:
    """Runtime objects shared by protocol handlers and entity platforms."""

    entry: HubConfigEntry
    store: HubStore
    coordinator: HubCoordinator
    time_sync_limiter: DeviceRateLimiter

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
    from .security import DeviceRateLimiter
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
        store.revoke_device(device_id)
    if removed_ids:
        await store.async_save()

    coordinator = HubCoordinator(hass, store, entry)
    runtime = HubRuntime(
        entry,
        store,
        coordinator,
        DeviceRateLimiter(TIME_SYNC_RATE_LIMIT, TIME_SYNC_RATE_WINDOW),
    )
    entry.runtime_data = runtime
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = runtime

    async def _async_minute_tick(now: Any) -> None:
        if not await coordinator.async_schedule_automatic_ota(now):
            coordinator.async_update_listeners()

    entry.async_on_unload(entry.add_update_listener(_async_reload_entry))
    entry.async_on_unload(
        async_track_time_interval(
            hass,
            _async_minute_tick,
            timedelta(minutes=1),
        )
    )
    await coordinator.async_schedule_automatic_ota()
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


async def async_remove_config_entry_device(
    hass: HomeAssistant,
    config_entry: HubConfigEntry,
    device_entry: DeviceEntry,
) -> bool:
    """Remove one display and retain only a signed pending unpair command."""
    device_id = next(
        (
            identifier
            for domain, identifier in device_entry.identifiers
            if domain == DOMAIN
        ),
        None,
    )
    if device_id is None:
        return False
    subentry_id = next(
        (
            subentry_id
            for subentry_id, subentry in config_entry.subentries.items()
            if subentry.data.get(CONF_DEVICE_ID) == device_id
        ),
        None,
    )
    if subentry_id is None:
        return False
    if config_entry.runtime_data.store.revoke_device(device_id):
        await config_entry.runtime_data.store.async_save()
    hass.config_entries.async_remove_subentry(config_entry, subentry_id)
    return True


async def async_remove_entry(hass: HomeAssistant, entry: HubConfigEntry) -> None:
    """Delete every retained key when the complete Hub is removed."""
    from .store import HubStore

    store = HubStore(hass)
    await store.async_load()
    await store.async_clear()
