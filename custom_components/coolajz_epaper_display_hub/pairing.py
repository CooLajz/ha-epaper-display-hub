"""User-confirmed local pairing transactions."""

from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from .const import CLAIM_TTL, PAIRING_CODE_LENGTH, PAIRING_TTL, PROTOCOL_VERSION
from .models import DeviceRecord, PairingSession, ProtocolError, format_device_id
from .security import generate_nonce, generate_secret

if TYPE_CHECKING:
    from .store import HubStore


class PairingManager:
    """Manage short-lived codes; raw codes are never persisted or logged."""

    def __init__(self, store: HubStore) -> None:
        self._store = store
        self._sessions: dict[str, PairingSession] = {}
        self._attempts: dict[str, list[float]] = {}

    def allow_attempt(self, client: str) -> bool:
        """Apply a small per-client limit to online pairing-code guesses."""
        now = time.monotonic()
        recent = [item for item in self._attempts.get(client, []) if item > now - 60]
        if len(recent) >= 10:
            self._attempts[client] = recent
            return False
        recent.append(now)
        self._attempts[client] = recent
        return True

    def _digest(self, value: str) -> str:
        return hmac.new(
            bytes.fromhex(self._store.pairing_salt),
            value.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    def create(self, friendly_name: str) -> tuple[str, str]:
        """Create a UI flow-owned pairing session and return id plus raw code."""
        session_id = generate_nonce()
        code = "".join(secrets.choice("0123456789") for _ in range(PAIRING_CODE_LENGTH))
        self._sessions[session_id] = PairingSession(
            code_digest=self._digest(code),
            expires_at=datetime.now(UTC) + PAIRING_TTL,
            friendly_name=friendly_name.strip(),
        )
        return session_id, code

    def _active_by_code(self, code: str) -> tuple[str, PairingSession]:
        digest = self._digest(code)
        now = datetime.now(UTC)
        for session_id, session in tuple(self._sessions.items()):
            if session.expires_at < now:
                self._sessions.pop(session_id, None)
                continue
            if hmac.compare_digest(session.code_digest, digest):
                return session_id, session
        raise ProtocolError(
            "invalid_pairing_code", "Pairing code is invalid or expired"
        )

    def register(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        """Accept device metadata but wait for explicit user confirmation."""
        if int(payload.get("protocol_version", 0)) != PROTOCOL_VERSION:
            raise ProtocolError("unsupported_protocol", "Unsupported protocol version")
        code = str(payload.get("pairing_code", ""))
        session_id, session = self._active_by_code(code)
        device_id = format_device_id(str(payload.get("device_id", "")))
        if device_id in self._store.devices:
            raise ProtocolError("already_paired", "This device is already paired")
        for key in ("model", "hardware_variant", "firmware_version"):
            if not isinstance(payload.get(key), str) or not payload[key].strip():
                raise ProtocolError("invalid_payload", f"Missing {key}")
        claim_token = generate_nonce()
        session.candidate = {
            "device_id": device_id,
            "model": payload["model"].strip(),
            "hardware_variant": payload["hardware_variant"].strip(),
            "firmware_version": payload["firmware_version"].strip(),
            "protocol_version": PROTOCOL_VERSION,
            "friendly_name": str(
                payload.get("friendly_name") or session.friendly_name
            ).strip(),
        }
        session.claim_token_digest = self._digest(claim_token)
        session.claim_expires_at = datetime.now(UTC) + CLAIM_TTL
        return {
            "status": "pending_confirmation",
            "pairing_session": session_id,
            "claim_token": claim_token,
            "retry_after_seconds": 5,
        }

    def candidate(self, session_id: str) -> dict[str, Any] | None:
        """Return public candidate metadata to the owning HA flow."""
        session = self._sessions.get(session_id)
        if session is None or session.expires_at < datetime.now(UTC):
            return None
        return dict(session.candidate) if session.candidate else None

    async def async_confirm(self, session_id: str) -> dict[str, Any]:
        """Create durable key material after user confirmation."""
        session = self._sessions.get(session_id)
        if session is None or session.expires_at < datetime.now(UTC):
            raise ProtocolError("pairing_expired", "Pairing session expired")
        if session.candidate is None:
            raise ProtocolError("device_not_registered", "No device has registered yet")
        device_id = session.candidate["device_id"]
        if device_id in self._store.devices:
            raise ProtocolError("already_paired", "This device is already paired")
        session.secret = generate_secret()
        session.confirmed = True
        self._store.devices[device_id] = DeviceRecord(
            device_id=device_id, secret=session.secret
        )
        await self._store.async_save()
        return dict(session.candidate)

    def claim(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        """Deliver a key exactly once to the confirmed device transaction."""
        session_id = str(payload.get("pairing_session", ""))
        token = str(payload.get("claim_token", ""))
        session = self._sessions.get(session_id)
        now = datetime.now(UTC)
        if (
            session is None
            or session.claim_expires_at is None
            or session.claim_expires_at < now
            or session.claim_token_digest is None
            or not hmac.compare_digest(session.claim_token_digest, self._digest(token))
        ):
            raise ProtocolError("invalid_claim", "Claim is invalid or expired")
        if not session.confirmed or session.secret is None or session.candidate is None:
            return {"status": "pending_confirmation", "retry_after_seconds": 5}
        response = {
            "status": "paired",
            "protocol_version": PROTOCOL_VERSION,
            "device_id": session.candidate["device_id"],
            "device_key": session.secret,
        }
        self._sessions.pop(session_id, None)
        return response

    def cancel(self, session_id: str) -> None:
        """Invalidate a pairing session."""
        self._sessions.pop(session_id, None)
