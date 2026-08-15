"""HMAC signing, canonicalization, and replay protection."""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

from .const import (
    MAX_REPLAY_WINDOW,
    MIN_REPLAY_WINDOW,
    NONCE_HISTORY_SIZE,
    PROTOCOL_VERSION,
    REPLAY_GRACE,
)
from .models import DeviceRecord, ProtocolError

NONCE_RE = re.compile(r"^[A-Za-z0-9_-]{22,128}$")


def canonical_json(payload: Mapping[str, Any]) -> bytes:
    """Encode JSON exactly as required by protocol v1."""
    return json.dumps(
        payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")


def body_digest(body: bytes) -> str:
    """Return lowercase SHA-256 body digest."""
    return hashlib.sha256(body).hexdigest()


def canonical_request(
    method: str,
    path: str,
    device_id: str,
    timestamp: int,
    nonce: str,
    body: bytes,
) -> bytes:
    """Build canonical request bytes."""
    return (
        f"EPD-HUB-REQUEST-V1\n{method.upper()}\n{path}\n{PROTOCOL_VERSION}\n"
        f"{device_id}\n{timestamp}\n{nonce}\n{body_digest(body)}"
    ).encode()


def canonical_response(
    status: int,
    path: str,
    device_id: str,
    timestamp: int,
    request_nonce: str,
    body: bytes,
) -> bytes:
    """Build a direction-bound canonical response."""
    return (
        f"EPD-HUB-RESPONSE-V1\n{status}\n{path}\n{PROTOCOL_VERSION}\n"
        f"{device_id}\n{timestamp}\n{request_nonce}\n{body_digest(body)}"
    ).encode()


def sign(secret: str, canonical: bytes) -> str:
    """Return a lowercase SHA-256 HMAC."""
    return hmac.new(bytes.fromhex(secret), canonical, hashlib.sha256).hexdigest()


def verify_signature(secret: str, canonical: bytes, supplied: str) -> bool:
    """Compare a supplied signature in constant time."""
    if len(supplied) != 64:
        return False
    return hmac.compare_digest(sign(secret, canonical), supplied.lower())


def generate_secret() -> str:
    """Generate a 256-bit per-device key represented as hex."""
    return secrets.token_hex(32)


def generate_nonce() -> str:
    """Generate a 192-bit URL-safe nonce."""
    return secrets.token_urlsafe(24)


def replay_window(expected_interval_minutes: int) -> timedelta:
    """Calculate a deep-sleep-aware but bounded timestamp window."""
    candidate = timedelta(minutes=max(1, expected_interval_minutes) * 2) + REPLAY_GRACE
    return min(MAX_REPLAY_WINDOW, max(MIN_REPLAY_WINDOW, candidate))


def validate_freshness(
    record: DeviceRecord,
    timestamp: int,
    nonce: str,
    *,
    now: datetime | None = None,
) -> None:
    """Reject stale timestamps, future requests, malformed or reused nonces."""
    if not NONCE_RE.fullmatch(nonce):
        raise ProtocolError("invalid_nonce", "Nonce format is invalid")
    if nonce in record.nonces:
        raise ProtocolError("replay", "Nonce has already been used")
    current = now or datetime.now(UTC)
    request_time = datetime.fromtimestamp(timestamp, UTC)
    interval = int(record.desired.get("refresh_interval_minutes", 30))
    window = replay_window(interval)
    if request_time < current - window or request_time > current + REPLAY_GRACE:
        raise ProtocolError("stale_request", "Timestamp is outside the accepted window")


def remember_nonce(record: DeviceRecord, nonce: str) -> None:
    """Persist a bounded replay history after successful authentication."""
    record.nonces.append(nonce)
    del record.nonces[:-NONCE_HISTORY_SIZE]
