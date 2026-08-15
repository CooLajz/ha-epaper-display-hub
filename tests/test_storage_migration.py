"""Tests for durable schema migration boundaries."""

import pytest

from custom_components.coolajz_epaper_display_hub.migration import migrate_storage_data


def test_storage_migration_preserves_fields_and_adds_devices() -> None:
    payload = {"pairing_salt": "abc", "future_safe": {"value": 1}}
    migrated = migrate_storage_data(1, 0, payload)
    assert migrated["devices"] == []
    assert migrated["future_safe"] == {"value": 1}


def test_storage_migration_rejects_newer_major() -> None:
    with pytest.raises(ValueError, match="newer"):
        migrate_storage_data(99, 0, {})
