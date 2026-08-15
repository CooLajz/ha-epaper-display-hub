"""Home Assistant runtime tests for config entries and display subentries."""

from __future__ import annotations

from typing import Any

import pytest

homeassistant = pytest.importorskip("homeassistant")
pytest.importorskip("pytest_homeassistant_custom_component")

from homeassistant import config_entries, data_entry_flow  # noqa: E402
from homeassistant.core import HomeAssistant  # noqa: E402
from pytest_homeassistant_custom_component.common import MockConfigEntry  # noqa: E402

from custom_components.coolajz_epaper_display_hub.const import (  # noqa: E402
    DOMAIN,
    SUBENTRY_TYPE_DISPLAY,
)


def _registration(code: str) -> dict[str, Any]:
    return {
        "protocol_version": 1,
        "pairing_code": code,
        "device_id": "AA:BB:CC:DD:EE:FF",
        "friendly_name": "Test display",
        "model": "ESPink 4.2",
        "hardware_variant": "ESP32-S3",
        "firmware_version": "1.0.0",
    }


async def test_create_main_config_entry(hass: HomeAssistant) -> None:
    """The integration creates exactly one UI config entry."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is data_entry_flow.FlowResultType.FORM
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] is data_entry_flow.FlowResultType.CREATE_ENTRY
    assert result["result"].unique_id == DOMAIN


async def test_pair_remove_unload_and_reload(hass: HomeAssistant) -> None:
    """A confirmed display becomes one subentry and removal revokes its key."""
    entry = MockConfigEntry(domain=DOMAIN, unique_id=DOMAIN, data={}, title="Hub")
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)

    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_TYPE_DISPLAY),
        context={"source": config_entries.SOURCE_USER},
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"friendly_name": "Test display"}
    )
    code = result["description_placeholders"]["pairing_code"]
    entry.runtime_data.pairing.register(_registration(code))
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"refresh": True}
    )
    assert result["step_id"] == "confirm"
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"confirm": True}
    )
    assert result["type"] is data_entry_flow.FlowResultType.CREATE_ENTRY
    assert len(entry.subentries) == 1
    assert "AA:BB:CC:DD:EE:FF" in entry.runtime_data.store.devices

    subentry_id = next(iter(entry.subentries))
    hass.config_entries.async_remove_subentry(entry, subentry_id)
    await hass.async_block_till_done()
    assert not entry.subentries
    assert "AA:BB:CC:DD:EE:FF" not in entry.runtime_data.store.devices

    assert await hass.config_entries.async_unload(entry.entry_id)
    assert await hass.config_entries.async_setup(entry.entry_id)
