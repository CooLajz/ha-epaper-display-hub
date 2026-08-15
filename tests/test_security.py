"""Tests for canonical HMAC and replay protections."""

from datetime import UTC, datetime, timedelta

import pytest

from custom_components.coolajz_epaper_display_hub.const import TIME_SYNC_PATH
from custom_components.coolajz_epaper_display_hub.models import (
    DeviceRecord,
    ProtocolError,
)
from custom_components.coolajz_epaper_display_hub.security import (
    DeviceRateLimiter,
    canonical_json,
    canonical_request,
    canonical_time_request,
    canonical_time_response,
    generate_nonce,
    generate_secret,
    remember_nonce,
    sign,
    validate_freshness,
    validate_nonce,
    verify_signature,
)


def test_valid_and_invalid_hmac() -> None:
    """A correct signature passes and a changed body fails."""
    secret = generate_secret()
    nonce = generate_nonce()
    body = b'{"protocol_version":1}'
    canonical = canonical_request(
        "POST", "/api/test", "AA:BB:CC:DD:EE:FF", 123, nonce, body
    )
    signature = sign(secret, canonical)
    assert verify_signature(secret, canonical, signature)
    changed = canonical_request(
        "POST", "/api/test", "AA:BB:CC:DD:EE:FF", 123, nonce, body + b" "
    )
    assert not verify_signature(secret, changed, signature)
    assert not verify_signature(secret, canonical, "short")


def test_documented_interoperability_vector() -> None:
    """Keep the firmware-facing deterministic vector stable for protocol v1."""
    secret = "000102030405060708090a0b0c0d0e0f101112131415161718191a1b1c1d1e1f"
    body = b'{"device_id":"AA:BB:CC:DD:EE:FF","protocol_version":1}'
    canonical = canonical_request(
        "POST",
        "/api/coolajz_epaper_display_hub/v1/check-in",
        "AA:BB:CC:DD:EE:FF",
        1786780800,
        "AAAAAAAAAAAAAAAAAAAAAA",
        body,
    )
    assert sign(secret, canonical) == (
        "019acd433d947199dd9d13bdd0bba1b3026af2a7cc30c9aa50f3f2340924aec7"
    )


def test_other_device_key_is_rejected() -> None:
    """Compromising one display cannot authenticate a second one."""
    first_key = generate_secret()
    second_key = generate_secret()
    canonical = canonical_request(
        "POST", "/api/test", "AA:BB:CC:DD:EE:FF", 123, generate_nonce(), b"{}"
    )
    assert not verify_signature(second_key, canonical, sign(first_key, canonical))


def test_documented_time_sync_interoperability_vector() -> None:
    """Keep both timestamp-free time-sync signatures stable for firmware."""
    secret = "000102030405060708090a0b0c0d0e0f101112131415161718191a1b1c1d1e1f"
    device_id = "AA:BB:CC:DD:EE:FF"
    nonce = "BBBBBBBBBBBBBBBBBBBBBB"
    request_body = canonical_json(
        {"protocol_version": 1, "device_id": device_id, "nonce": nonce}
    )
    request_canonical = canonical_time_request(
        "POST", TIME_SYNC_PATH, device_id, nonce, request_body
    )
    assert sign(secret, request_canonical) == (
        "71bd4d30b99446abb492086e5fe40f4607350a469ee9fe36f15be4cb693c00a5"
    )

    response_body = canonical_json(
        {
            "protocol_version": 1,
            "device_id": device_id,
            "server_time": 1786780800,
            "server_time_iso": "2026-08-15T10:00:00+02:00",
        }
    )
    response_canonical = canonical_time_response(
        200, TIME_SYNC_PATH, device_id, nonce, response_body
    )
    signature = sign(secret, response_canonical)
    assert signature == (
        "e81fb4af949697b2cbb63b1da5409249caeaeaebe4c4e32877076ab710d616d3"
    )
    wrong_nonce = canonical_time_response(
        200, TIME_SYNC_PATH, device_id, generate_nonce(), response_body
    )
    assert not verify_signature(secret, wrong_nonce, signature)


def test_time_sync_nonce_requires_at_least_128_bits() -> None:
    """A shorter URL-safe nonce is rejected before time is returned."""
    with pytest.raises(ProtocolError, match="Nonce format"):
        validate_nonce("too-short")


def test_time_sync_rate_limiter_is_per_device() -> None:
    """One device cannot consume another display's allowance."""
    limiter = DeviceRateLimiter(2, timedelta(seconds=60))
    first = "AA:BB:CC:DD:EE:FF"
    second = "11:22:33:44:55:66"
    assert limiter.allow(first, now=100)
    assert limiter.allow(first, now=101)
    assert not limiter.allow(first, now=102)
    assert limiter.allow(second, now=102)
    assert limiter.allow(first, now=161)


def test_replay_and_timestamp_window() -> None:
    """A used nonce and stale timestamp are rejected across record round-trips."""
    now = datetime.now(UTC)
    record = DeviceRecord("AA:BB:CC:DD:EE:FF", generate_secret())
    nonce = generate_nonce()
    validate_freshness(record, int(now.timestamp()), nonce, now=now)
    remember_nonce(record, nonce)
    restored = DeviceRecord.from_dict(record.as_dict())
    with pytest.raises(ProtocolError, match="already been used"):
        validate_freshness(restored, int(now.timestamp()), nonce, now=now)
    with pytest.raises(ProtocolError, match="outside"):
        validate_freshness(
            restored,
            int((now - timedelta(days=8)).timestamp()),
            generate_nonce(),
            now=now,
        )
