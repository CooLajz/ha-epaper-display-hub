"""Tests for canonical HMAC and replay protections."""

from datetime import UTC, datetime, timedelta

import pytest

from custom_components.coolajz_epaper_display_hub.models import (
    DeviceRecord,
    ProtocolError,
)
from custom_components.coolajz_epaper_display_hub.security import (
    canonical_request,
    generate_nonce,
    generate_secret,
    remember_nonce,
    sign,
    validate_freshness,
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
