"""Pure durable-schema migration helpers."""

from __future__ import annotations

from typing import Any

from .const import STORAGE_VERSION


def migrate_storage_data(
    old_major_version: int, old_minor_version: int, old_data: dict[str, Any]
) -> dict[str, Any]:
    """Migrate durable state while preserving unknown future-safe fields."""
    if old_major_version > STORAGE_VERSION:
        raise ValueError("Cannot migrate storage from a newer major version")
    migrated = dict(old_data)
    migrated.setdefault("devices", [])
    return migrated
