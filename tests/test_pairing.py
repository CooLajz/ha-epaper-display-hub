"""Tests for local device discovery and authenticated pairing."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import pytest
from aiohttp import ClientConnectionError

from custom_components.coolajz_epaper_display_hub.models import ProtocolError
from custom_components.coolajz_epaper_display_hub.pairing import (
    DeviceIdentity,
    DevicePairingClient,
    canonical_pairing_ack,
    normalize_hub_url,
    pairing_proof,
    validate_local_ipv4,
)

DEVICE_ID = "AA:BB:CC:DD:EE:FF"
DEVICE_KEY = "000102030405060708090a0b0c0d0e0f101112131415161718191a1b1c1d1e1f"
TRANSACTION_ID = "AAAAAAAAAAAAAAAAAAAAAA"
HUB_URL = "https://homeassistant.example.cz"
VECTOR_TRANSACTION_ID = "CCCCCCCCCCCCCCCCCCCCCC"
VECTOR_HUB_URL = "http://homeassistant.local:8123"


class FakeContent:
    """Minimal bounded response stream."""

    def __init__(self, body: bytes) -> None:
        self._body = body

    async def read(self, _limit: int) -> bytes:
        body, self._body = self._body, b""
        return body


class FakeResponse:
    """Minimal async response context manager."""

    def __init__(self, status: int, payload: Any, *, raw: bool = False) -> None:
        body = payload if raw else json.dumps(payload).encode()
        self.status = status
        self.content = FakeContent(body)

    async def __aenter__(self) -> FakeResponse:
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None


class FakeSession:
    """Capture requests and return programmable responses."""

    def __init__(
        self,
        *,
        get_result: FakeResponse | BaseException | None = None,
        post_factory: Callable[[dict[str, Any]], FakeResponse] | None = None,
    ) -> None:
        self.get_result = get_result
        self.post_factory = post_factory
        self.post_bodies: list[dict[str, Any]] = []

    def get(self, *_args: object, **_kwargs: object) -> FakeResponse:
        if isinstance(self.get_result, BaseException):
            raise self.get_result
        assert self.get_result is not None
        return self.get_result

    def post(self, *_args: object, **kwargs: object) -> FakeResponse:
        body = json.loads(kwargs["data"])
        self.post_bodies.append(body)
        assert self.post_factory is not None
        return self.post_factory(body)


def identity_payload(**changes: Any) -> dict[str, Any]:
    payload = {
        "status": "pairing",
        "protocol_version": 1,
        "device_id": DEVICE_ID,
        "model": "ESPink 4.2",
        "hardware_variant": "ESP32-S3",
        "firmware_version": "1.0.0",
    }
    payload.update(changes)
    return payload


def successful_pair_response(body: dict[str, Any]) -> FakeResponse:
    return FakeResponse(
        200,
        {
            "status": "paired",
            "protocol_version": 1,
            "device_id": DEVICE_ID,
            "transaction_id": body["transaction_id"],
            "proof": pairing_proof(
                body["device_key"],
                DEVICE_ID,
                body["transaction_id"],
                body["hub_url"],
            ),
        },
    )


def client(session: FakeSession) -> DevicePairingClient:
    return DevicePairingClient(session)  # type: ignore[arg-type]


async def pair(pairing_client: DevicePairingClient) -> None:
    await pairing_client.async_pair(
        device_ip="192.168.1.42",
        identity=DeviceIdentity(DEVICE_ID, "ESPink 4.2", "ESP32-S3", "1.0.0"),
        pairing_pin="12345678",
        hub_url=HUB_URL,
        transport_security="https_verified",
        friendly_name="Living room",
        device_key=DEVICE_KEY,
        transaction_id=TRANSACTION_ID,
    )


@pytest.mark.asyncio
async def test_valid_device_info() -> None:
    session = FakeSession(get_result=FakeResponse(200, identity_payload()))
    identity = await client(session).async_device_info("192.168.1.42")
    assert identity == DeviceIdentity(DEVICE_ID, "ESPink 4.2", "ESP32-S3", "1.0.0")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("payload", "error"),
    [
        ({"not": "json"}, "not_pairing"),
        (identity_payload(protocol_version=2), "unsupported_protocol"),
        (identity_payload(protocol_version="wrong"), "unsupported_protocol"),
        (identity_payload(protocol_version="1"), "unsupported_protocol"),
        (identity_payload(device_id="not-a-mac"), "invalid_device_id"),
        (identity_payload(model=""), "empty_metadata"),
    ],
)
async def test_invalid_device_info(payload: dict[str, Any], error: str) -> None:
    session = FakeSession(get_result=FakeResponse(200, payload))
    with pytest.raises(ProtocolError) as caught:
        await client(session).async_device_info("192.168.1.42")
    assert caught.value.code == error


@pytest.mark.asyncio
async def test_invalid_json_unavailable_timeout_and_redirect() -> None:
    cases: list[tuple[FakeResponse | BaseException, str]] = [
        (FakeResponse(200, b"not-json", raw=True), "invalid_json"),
        (ClientConnectionError(), "device_unavailable"),
        (TimeoutError(), "timeout"),
        (FakeResponse(302, {}), "redirect_rejected"),
        (FakeResponse(200, b"x" * 8193, raw=True), "response_too_large"),
    ]
    for response, expected in cases:
        with pytest.raises(ProtocolError) as caught:
            await client(FakeSession(get_result=response)).async_device_info(
                "192.168.1.42"
            )
        assert caught.value.code == expected


@pytest.mark.parametrize(
    ("address", "valid"),
    [
        ("192.168.1.42", True),
        ("10.1.2.3", True),
        ("172.16.0.1", True),
        ("8.8.8.8", False),
        ("127.0.0.1", False),
        ("2001:db8::1", False),
        ("display.local", False),
    ],
)
def test_explicit_local_ipv4_validation(address: str, valid: bool) -> None:
    if valid:
        assert validate_local_ipv4(address) == address
    else:
        with pytest.raises(ProtocolError):
            validate_local_ipv4(address)


@pytest.mark.parametrize(
    ("url", "transport"),
    [
        ("http://homeassistant.local:8123", "https_verified"),
        ("https://homeassistant.example.cz", "http"),
        ("https://homeassistant.example.cz", "invalid"),
    ],
)
def test_transport_never_changes_url_scheme(url: str, transport: str) -> None:
    with pytest.raises(ProtocolError):
        normalize_hub_url(url, transport)


def test_pairing_hmac_interoperability_vector() -> None:
    assert canonical_pairing_ack(DEVICE_ID, VECTOR_TRANSACTION_ID, VECTOR_HUB_URL) == (
        b"EPD-HUB-PAIRING-ACK-V1\n1\nAA:BB:CC:DD:EE:FF\n"
        b"CCCCCCCCCCCCCCCCCCCCCC\nhttp://homeassistant.local:8123"
    )
    assert pairing_proof(
        DEVICE_KEY, DEVICE_ID, VECTOR_TRANSACTION_ID, VECTOR_HUB_URL
    ) == ("10509da40336ccd044ddf2450a4ca3c0142405772d1398cc49e846889ac7aa1e")


@pytest.mark.asyncio
async def test_successful_pair_and_same_transaction_retry() -> None:
    session = FakeSession(post_factory=successful_pair_response)
    pairing_client = client(session)
    await pair(pairing_client)
    await pair(pairing_client)
    assert session.post_bodies[0] == session.post_bodies[1]
    assert session.post_bodies[0]["transport_security"] == "https_verified"
    assert session.post_bodies[0]["pairing_pin"] == "12345678"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "error"),
    [(403, "invalid_pin"), (429, "rate_limited"), (500, "pairing_failed")],
)
async def test_pairing_http_failures(status: int, error: str) -> None:
    session = FakeSession(post_factory=lambda _body: FakeResponse(status, {}))
    with pytest.raises(ProtocolError) as caught:
        await pair(client(session))
    assert caught.value.code == error


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["proof", "transaction_id", "device_id"])
async def test_pairing_rejects_unbound_response(change: str) -> None:
    def response(body: dict[str, Any]) -> FakeResponse:
        payload = {
            "status": "paired",
            "protocol_version": 1,
            "device_id": DEVICE_ID,
            "transaction_id": body["transaction_id"],
            "proof": pairing_proof(
                body["device_key"], DEVICE_ID, body["transaction_id"], body["hub_url"]
            ),
        }
        payload[change] = {
            "proof": "0" * 64,
            "transaction_id": "BBBBBBBBBBBBBBBBBBBBBB",
            "device_id": "11:22:33:44:55:66",
        }[change]
        return FakeResponse(200, payload)

    with pytest.raises(ProtocolError):
        await pair(client(FakeSession(post_factory=response)))
