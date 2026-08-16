"""Home Assistant runtime tests for config entries and display subentries."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")

homeassistant = pytest.importorskip("homeassistant")
pytest.importorskip("pytest_homeassistant_custom_component")

from homeassistant import config_entries, data_entry_flow  # noqa: E402
from homeassistant.core import HomeAssistant  # noqa: E402
from pytest_homeassistant_custom_component.common import MockConfigEntry  # noqa: E402

from custom_components.coolajz_epaper_display_hub.binary_sensor import (  # noqa: E402
    DESCRIPTIONS as BINARY_SENSOR_DESCRIPTIONS,
)
from custom_components.coolajz_epaper_display_hub.config_flow import (  # noqa: E402
    _display_schema,
)
from custom_components.coolajz_epaper_display_hub.const import (  # noqa: E402
    CHECKIN_PATH,
    CONF_ALLOW_INSECURE_TLS,
    CONF_DEVICE_IP,
    CONF_FRIENDLY_NAME,
    CONF_PAIRING_PIN,
    CONF_TRANSPORT_SECURITY,
    DEFAULT_WAKE_SCHEDULE,
    DESIRED_PARTIAL_REFRESHES,
    DESIRED_SHOW_BATTERY_VOLTAGE,
    DEVICE_HEADER,
    DOMAIN,
    NONCE_HEADER,
    OTA_CHECK_TIME,
    PROTOCOL_HEADER,
    SIGNATURE_HEADER,
    SUBENTRY_TYPE_DISPLAY,
    TIME_SYNC_PATH,
    TIMESTAMP_HEADER,
    TRANSPORT_HTTPS_INSECURE,
    TRANSPORT_HTTPS_VERIFIED,
    WAKE_SCHEDULE_FIELD_PREFIX,
)
from custom_components.coolajz_epaper_display_hub.models import (  # noqa: E402
    DeviceRecord,
    ProtocolError,
)
from custom_components.coolajz_epaper_display_hub.number import (  # noqa: E402
    EpaperPartialRefreshNumber,
)
from custom_components.coolajz_epaper_display_hub.pairing import (  # noqa: E402
    DeviceIdentity,
)
from custom_components.coolajz_epaper_display_hub.security import (  # noqa: E402
    canonical_json,
    canonical_request,
    canonical_response,
    canonical_time_request,
    canonical_time_response,
    generate_nonce,
    generate_secret,
    sign,
    verify_signature,
)
from custom_components.coolajz_epaper_display_hub.sensor import (  # noqa: E402
    OPTIONAL_SENSORS,
    SENSORS,
)

IDENTITY = DeviceIdentity("AA:BB:CC:DD:EE:FF", "ESPink 4.2", "ESP32-S3", "1.0.0")


def test_measurements_use_semantic_display_precision() -> None:
    """Telemetry presentation should match each physical quantity."""
    precision = {item.key: item.suggested_display_precision for item in SENSORS}
    precision.update(
        {
            key: item.suggested_display_precision
            for key, item in OPTIONAL_SENSORS.items()
        }
    )
    assert precision["battery"] == 0
    assert precision["battery_voltage"] == 2
    assert precision["last_planned_interval"] == 0
    assert precision["active_runtime"] == 0
    assert precision["partial_refresh_count"] == 0
    assert precision["rssi"] == 0
    assert precision["board_temperature"] == 1
    assert precision["board_humidity"] == 0


def test_pending_configuration_is_not_a_problem_device_class() -> None:
    """Waiting for a sleeping display is an expected state, not a fault."""
    pending = next(
        item
        for item in BINARY_SENSOR_DESCRIPTIONS
        if item.key == "configuration_pending"
    )
    assert pending.device_class is None


def test_device_switches_are_not_in_reconfigure_form() -> None:
    """Only an enabled automatic OTA switch reveals the Hub-side schedule time."""
    disabled_keys = {str(key.schema) for key in _display_schema(False).schema}
    enabled_keys = {str(key.schema) for key in _display_schema(True).schema}

    assert DESIRED_SHOW_BATTERY_VOLTAGE not in disabled_keys
    assert DESIRED_PARTIAL_REFRESHES not in disabled_keys
    assert "auto_ota" not in disabled_keys
    assert OTA_CHECK_TIME not in disabled_keys
    assert DESIRED_SHOW_BATTERY_VOLTAGE not in enabled_keys
    assert DESIRED_PARTIAL_REFRESHES not in enabled_keys
    assert "auto_ota" not in enabled_keys
    assert OTA_CHECK_TIME in enabled_keys


def test_partial_refresh_number_has_device_range() -> None:
    assert EpaperPartialRefreshNumber._attr_native_min_value == 0
    assert EpaperPartialRefreshNumber._attr_native_max_value == 20
    assert EpaperPartialRefreshNumber._attr_native_step == 1


def _reconfigure_input(*, first_hour_interval: int = 30) -> dict[str, Any]:
    return {
        CONF_FRIENDLY_NAME: "Test display",
        **{
            f"{WAKE_SCHEDULE_FIELD_PREFIX}{hour:02d}": str(
                first_hour_interval
                if hour == 0
                else DEFAULT_WAKE_SCHEDULE[str(hour)]
            )
            for hour in range(24)
        },
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


async def test_pair_remove_unload_and_reload(
    hass: HomeAssistant, hass_client: Any
) -> None:
    """Removing a display retains only a signed unpair response."""
    entry = MockConfigEntry(domain=DOMAIN, unique_id=DOMAIN, data={}, title="Hub")
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)

    with (
        patch(
            "custom_components.coolajz_epaper_display_hub.config_flow._internal_hub_url",
            return_value=(
                "https://homeassistant.example.cz",
                TRANSPORT_HTTPS_VERIFIED,
            ),
        ),
        patch(
            "custom_components.coolajz_epaper_display_hub.pairing.DevicePairingClient.async_device_info",
            AsyncMock(return_value=IDENTITY),
        ),
        patch(
            "custom_components.coolajz_epaper_display_hub.pairing.DevicePairingClient.async_pair",
            AsyncMock(),
        ) as pair_request,
    ):
        result = await hass.config_entries.subentries.async_init(
            (entry.entry_id, SUBENTRY_TYPE_DISPLAY),
            context={"source": config_entries.SOURCE_USER},
        )
        result = await hass.config_entries.subentries.async_configure(
            result["flow_id"],
            {
                CONF_FRIENDLY_NAME: "Test display",
                CONF_DEVICE_IP: "192.168.1.42",
                CONF_PAIRING_PIN: "12345678",
                CONF_ALLOW_INSECURE_TLS: False,
            },
        )
        pair_request.assert_awaited_once()
    assert result["type"] is data_entry_flow.FlowResultType.CREATE_ENTRY
    assert len(entry.subentries) == 1
    assert "AA:BB:CC:DD:EE:FF" in entry.runtime_data.store.devices
    subentry = next(iter(entry.subentries.values()))
    assert subentry.data[CONF_TRANSPORT_SECURITY] == TRANSPORT_HTTPS_VERIFIED
    assert CONF_PAIRING_PIN not in subentry.data

    record = entry.runtime_data.store.devices["AA:BB:CC:DD:EE:FF"]
    original_revision = record.desired_revision
    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_TYPE_DISPLAY),
        context={
            "source": config_entries.SOURCE_RECONFIGURE,
            "subentry_id": subentry.subentry_id,
        },
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], _reconfigure_input()
    )
    assert result["type"] is data_entry_flow.FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert record.desired_revision == original_revision

    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_TYPE_DISPLAY),
        context={
            "source": config_entries.SOURCE_RECONFIGURE,
            "subentry_id": subentry.subentry_id,
        },
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], _reconfigure_input(first_hour_interval=15)
    )
    assert result["type"] is data_entry_flow.FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert record.desired[DESIRED_SHOW_BATTERY_VOLTAGE] is True
    assert record.desired[DESIRED_PARTIAL_REFRESHES] == 10
    assert record.wake_schedule["0"] == 15
    assert record.desired_revision == original_revision + 1

    response = await entry.runtime_data.coordinator.async_process_checkin(
        record,
        {"telemetry": {"partial_refresh_count": 4}, "firmware_version": "1.0.0"},
        {},
    )
    assert response["revision"] == record.desired_revision
    assert response["sleep_seconds"] > 0
    assert datetime.fromisoformat(response["server_time"]).tzinfo is not None
    assert datetime.fromisoformat(response["next_wake_at"]).tzinfo is not None
    assert entry.runtime_data.coordinator.is_device_available(record.device_id)
    assert record.last_entity_data["partial_refresh_count"] == 4
    assert (
        entry.runtime_data.coordinator.device_data(record.device_id)[
            "partial_refresh_count"
        ]
        == 4
    )

    subentry_id = next(iter(entry.subentries))
    hass.config_entries.async_remove_subentry(entry, subentry_id)
    await hass.async_block_till_done()
    assert not entry.subentries
    assert "AA:BB:CC:DD:EE:FF" not in entry.runtime_data.store.devices
    assert "AA:BB:CC:DD:EE:FF" in entry.runtime_data.store.revoked_devices
    unpair_command_id = entry.runtime_data.store.revoked_devices[
        "AA:BB:CC:DD:EE:FF"
    ].unpair_command_id

    client = await hass_client()
    device_id = record.device_id
    request_nonce = generate_nonce()
    request_timestamp = int(datetime.now(UTC).timestamp())
    request_body = canonical_json(
        {
            "protocol_version": 1,
            "device_id": device_id,
            "model": "ESPink 4.2",
            "hardware_variant": "ESP32-S3",
            "firmware_version": "1.0.0",
            "telemetry": {},
            "command_acknowledgements": [],
        }
    )
    response = await client.post(
        CHECKIN_PATH,
        data=request_body,
        headers={
            DEVICE_HEADER: device_id,
            PROTOCOL_HEADER: "1",
            TIMESTAMP_HEADER: str(request_timestamp),
            NONCE_HEADER: request_nonce,
            SIGNATURE_HEADER: sign(
                record.secret,
                canonical_request(
                    "POST",
                    CHECKIN_PATH,
                    device_id,
                    request_timestamp,
                    request_nonce,
                    request_body,
                ),
            ),
            "Content-Type": "application/json",
        },
    )
    assert response.status == 200
    response_body = await response.read()
    response_payload = json.loads(response_body)
    assert set(response_payload) == {"protocol_version", "server_time", "commands"}
    assert response_payload["commands"] == [
        {
            "id": unpair_command_id,
            "type": "unpair",
        }
    ]
    assert verify_signature(
        record.secret,
        canonical_response(
            200,
            CHECKIN_PATH,
            device_id,
            int(response.headers[TIMESTAMP_HEADER]),
            request_nonce,
            response_body,
        ),
        response.headers[SIGNATURE_HEADER],
    )

    assert await hass.config_entries.async_unload(entry.entry_id)
    assert await hass.config_entries.async_setup(entry.entry_id)
    assert (
        entry.runtime_data.store.revoked_devices[device_id].unpair_command_id
        == unpair_command_id
    )


async def test_https_insecure_pairs_directly_from_first_form(
    hass: HomeAssistant,
) -> None:
    """The first form explicitly selects unverified TLS and pairs immediately."""
    entry = MockConfigEntry(domain=DOMAIN, unique_id=DOMAIN, data={}, title="Hub")
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    with (
        patch(
            "custom_components.coolajz_epaper_display_hub.config_flow._internal_hub_url",
            return_value=(
                "https://homeassistant.example.cz",
                TRANSPORT_HTTPS_VERIFIED,
            ),
        ),
        patch(
            "custom_components.coolajz_epaper_display_hub.pairing.DevicePairingClient.async_device_info",
            AsyncMock(return_value=IDENTITY),
        ),
        patch(
            "custom_components.coolajz_epaper_display_hub.pairing.DevicePairingClient.async_pair",
            AsyncMock(),
        ) as pair_request,
    ):
        result = await hass.config_entries.subentries.async_init(
            (entry.entry_id, SUBENTRY_TYPE_DISPLAY),
            context={"source": config_entries.SOURCE_USER},
        )
        result = await hass.config_entries.subentries.async_configure(
            result["flow_id"],
            {
                CONF_FRIENDLY_NAME: "Test display",
                CONF_DEVICE_IP: "192.168.1.42",
                CONF_PAIRING_PIN: "12345678",
                CONF_ALLOW_INSECURE_TLS: True,
            },
        )
        assert result["type"] is data_entry_flow.FlowResultType.CREATE_ENTRY
        subentry = next(iter(entry.subentries.values()))
        assert subentry.data[CONF_TRANSPORT_SECURITY] == TRANSPORT_HTTPS_INSECURE
        assert pair_request.await_args.kwargs["transport_security"] == (
            TRANSPORT_HTTPS_INSECURE
        )


async def test_duplicate_mac_preserves_existing_record(hass: HomeAssistant) -> None:
    """A duplicate aborts before POST and does not replace the existing key."""
    entry = MockConfigEntry(domain=DOMAIN, unique_id=DOMAIN, data={}, title="Hub")
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    existing_secret = generate_secret()
    entry.runtime_data.store.devices[IDENTITY.device_id] = DeviceRecord(
        IDENTITY.device_id, existing_secret
    )
    with (
        patch(
            "custom_components.coolajz_epaper_display_hub.config_flow._internal_hub_url",
            return_value=("http://homeassistant.local:8123", "http"),
        ),
        patch(
            "custom_components.coolajz_epaper_display_hub.pairing.DevicePairingClient.async_device_info",
            AsyncMock(return_value=IDENTITY),
        ),
        patch(
            "custom_components.coolajz_epaper_display_hub.pairing.DevicePairingClient.async_pair",
            AsyncMock(),
        ) as pair_request,
    ):
        result = await hass.config_entries.subentries.async_init(
            (entry.entry_id, SUBENTRY_TYPE_DISPLAY),
            context={"source": config_entries.SOURCE_USER},
        )
        result = await hass.config_entries.subentries.async_configure(
            result["flow_id"],
            {
                CONF_FRIENDLY_NAME: "Duplicate",
                CONF_DEVICE_IP: "192.168.1.42",
                CONF_PAIRING_PIN: "12345678",
            },
        )
        assert result["type"] is data_entry_flow.FlowResultType.ABORT
        assert result["reason"] == "already_configured"
        pair_request.assert_not_awaited()
    assert (
        entry.runtime_data.store.devices[IDENTITY.device_id].secret == existing_secret
    )


async def test_pairing_retry_reuses_credentials(hass: HomeAssistant) -> None:
    """Retry within one flow reuses the generated key and transaction ID."""
    entry = MockConfigEntry(domain=DOMAIN, unique_id=DOMAIN, data={}, title="Hub")
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    pair_request = AsyncMock(side_effect=[ProtocolError("timeout", "timed out"), None])
    with (
        patch(
            "custom_components.coolajz_epaper_display_hub.config_flow._internal_hub_url",
            return_value=("http://homeassistant.local:8123", "http"),
        ),
        patch(
            "custom_components.coolajz_epaper_display_hub.pairing.DevicePairingClient.async_device_info",
            AsyncMock(return_value=IDENTITY),
        ),
        patch(
            "custom_components.coolajz_epaper_display_hub.pairing.DevicePairingClient.async_pair",
            pair_request,
        ),
    ):
        result = await hass.config_entries.subentries.async_init(
            (entry.entry_id, SUBENTRY_TYPE_DISPLAY),
            context={"source": config_entries.SOURCE_USER},
        )
        result = await hass.config_entries.subentries.async_configure(
            result["flow_id"],
            {
                CONF_FRIENDLY_NAME: "Retry display",
                CONF_DEVICE_IP: "192.168.1.42",
                CONF_PAIRING_PIN: "12345678",
            },
        )
        assert result["errors"] == {"base": "timeout"}
        assert IDENTITY.device_id not in entry.runtime_data.store.devices
        assert not entry.subentries
        result = await hass.config_entries.subentries.async_configure(
            result["flow_id"],
            {
                CONF_FRIENDLY_NAME: "Retry display",
                CONF_DEVICE_IP: "192.168.1.42",
                CONF_PAIRING_PIN: "12345678",
            },
        )
        assert result["type"] is data_entry_flow.FlowResultType.CREATE_ENTRY
    first = pair_request.await_args_list[0].kwargs
    second = pair_request.await_args_list[1].kwargs
    assert first["device_key"] == second["device_key"]
    assert first["transaction_id"] == second["transaction_id"]


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

    assert entry.runtime_data.store.revoke_device(device_id)
    await entry.runtime_data.store.async_save()
    assert device_id not in entry.runtime_data.store.devices
    assert device_id in entry.runtime_data.store.revoked_devices

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
