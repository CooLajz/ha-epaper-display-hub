"""Home Assistant runtime tests for config entries and display subentries."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

import pytest

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")

homeassistant = pytest.importorskip("homeassistant")
pytest.importorskip("pytest_homeassistant_custom_component")

from homeassistant import config_entries, data_entry_flow  # noqa: E402
from homeassistant.core import HomeAssistant  # noqa: E402
from pytest_homeassistant_custom_component.common import MockConfigEntry  # noqa: E402

from custom_components.coolajz_epaper_display_hub.const import (  # noqa: E402
    DOMAIN,
    NONCE_HEADER,
    SIGNATURE_HEADER,
    SUBENTRY_TYPE_DISPLAY,
    TIME_SYNC_PATH,
)
from custom_components.coolajz_epaper_display_hub.models import (  # noqa: E402
    DeviceRecord,
)
from custom_components.coolajz_epaper_display_hub.security import (  # noqa: E402
    canonical_json,
    canonical_time_request,
    canonical_time_response,
    generate_nonce,
    generate_secret,
    sign,
    verify_signature,
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

    record = entry.runtime_data.store.devices["AA:BB:CC:DD:EE:FF"]
    response = await entry.runtime_data.coordinator.async_process_checkin(
        record,
        {"telemetry": {}, "firmware_version": "1.0.0"},
        {},
    )
    assert response["revision"] == record.desired_revision
    assert response["sleep_seconds"] > 0
    assert datetime.fromisoformat(response["server_time"]).tzinfo is not None
    assert datetime.fromisoformat(response["next_wake_at"]).tzinfo is not None
    assert entry.runtime_data.coordinator.is_device_available(record.device_id)

    subentry_id = next(iter(entry.subentries))
    hass.config_entries.async_remove_subentry(entry, subentry_id)
    await hass.async_block_till_done()
    assert not entry.subentries
    assert "AA:BB:CC:DD:EE:FF" not in entry.runtime_data.store.devices

    assert await hass.config_entries.async_unload(entry.entry_id)
    assert await hass.config_entries.async_setup(entry.entry_id)


async def test_time_sync_security_and_rate_limit(
    hass: HomeAssistant, hass_client: Any
) -> None:
    """Time recovery authenticates both directions and limits each device."""
    entry = MockConfigEntry(domain=DOMAIN, unique_id=DOMAIN, data={}, title="Hub")
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    device_id = "AA:BB:CC:DD:EE:FF"
    secret = generate_secret()
    entry.runtime_data.store.devices[device_id] = DeviceRecord(device_id, secret)
    client = await hass_client()

    async def request(
        request_device_id: str,
        request_secret: str,
        nonce: str,
        *,
        signature: str | None = None,
    ) -> Any:
        body = canonical_json(
            {
                "protocol_version": 1,
                "device_id": request_device_id,
                "nonce": nonce,
            }
        )
        supplied = signature or sign(
            request_secret,
            canonical_time_request(
                "POST", TIME_SYNC_PATH, request_device_id, nonce, body
            ),
        )
        return await client.post(
            TIME_SYNC_PATH,
            data=body,
            headers={SIGNATURE_HEADER: supplied, "Content-Type": "application/json"},
        )

    valid_nonce = generate_nonce()
    response = await request(device_id, secret, valid_nonce)
    assert response.status == 200
    response_body = await response.read()
    response_payload = json.loads(response_body)
    assert set(response_payload) == {
        "protocol_version",
        "device_id",
        "server_time",
        "server_time_iso",
    }
    assert response_payload["device_id"] == device_id
    assert isinstance(response_payload["server_time"], int)
    assert (
        datetime.fromisoformat(response_payload["server_time_iso"]).tzinfo is not None
    )
    assert response.headers[NONCE_HEADER] == valid_nonce
    assert verify_signature(
        secret,
        canonical_time_response(
            200, TIME_SYNC_PATH, device_id, valid_nonce, response_body
        ),
        response.headers[SIGNATURE_HEADER],
    )

    replay = await request(device_id, secret, valid_nonce)
    assert replay.status == 401
    assert (await replay.json())["error"] == "replay"

    invalid = await request(device_id, secret, generate_nonce(), signature="0" * 64)
    assert invalid.status == 401
    assert (await invalid.json())["error"] == "invalid_signature"

    unknown = await request("11:22:33:44:55:66", generate_secret(), generate_nonce())
    assert unknown.status == 401
    assert (await unknown.json())["error"] == "unknown_device"

    short = await request(device_id, secret, "too-short")
    assert short.status == 401
    assert (await short.json())["error"] == "invalid_nonce"

    for _ in range(5):
        accepted = await request(device_id, secret, generate_nonce())
        assert accepted.status == 200
    limited = await request(device_id, secret, generate_nonce())
    assert limited.status == 429
    assert (await limited.json())["error"] == "rate_limited"
