"""Tests for pairing codes, confirmation, key isolation, and expiry."""

from datetime import UTC, datetime, timedelta

import pytest

from custom_components.coolajz_epaper_display_hub.models import ProtocolError
from custom_components.coolajz_epaper_display_hub.pairing import PairingManager
from custom_components.coolajz_epaper_display_hub.security import generate_secret


class FakeStore:
    """Minimal private-store substitute."""

    def __init__(self) -> None:
        self.pairing_salt = generate_secret()
        self.devices = {}
        self.save_count = 0

    async def async_save(self) -> None:
        self.save_count += 1


def registration(code: str, device_id: str = "AA:BB:CC:DD:EE:FF") -> dict[str, object]:
    return {
        "protocol_version": 1,
        "pairing_code": code,
        "device_id": device_id,
        "friendly_name": "Test display",
        "model": "ESPink 4.2",
        "hardware_variant": "ESP32-S3",
        "firmware_version": "1.0.0",
    }


@pytest.mark.asyncio
async def test_valid_pairing_confirmation_and_one_time_claim() -> None:
    store = FakeStore()
    manager = PairingManager(store)  # type: ignore[arg-type]
    session_id, code = manager.create("Test display")
    registered = manager.register(registration(code))
    assert manager.candidate(session_id)["device_id"] == "AA:BB:CC:DD:EE:FF"
    await manager.async_confirm(session_id)
    response = manager.claim(
        {
            "pairing_session": registered["pairing_session"],
            "claim_token": registered["claim_token"],
        }
    )
    assert response["status"] == "paired"
    assert len(response["device_key"]) == 64
    with pytest.raises(ProtocolError, match="invalid or expired"):
        manager.claim(
            {
                "pairing_session": registered["pairing_session"],
                "claim_token": registered["claim_token"],
            }
        )


def test_invalid_and_expired_pairing_code() -> None:
    store = FakeStore()
    manager = PairingManager(store)  # type: ignore[arg-type]
    session_id, code = manager.create("Test display")
    with pytest.raises(ProtocolError, match="invalid or expired"):
        manager.register(registration("00000000"))
    manager._sessions[session_id].expires_at = datetime.now(UTC) - timedelta(seconds=1)
    with pytest.raises(ProtocolError, match="invalid or expired"):
        manager.register(registration(code))


@pytest.mark.asyncio
async def test_duplicate_mac_is_rejected() -> None:
    store = FakeStore()
    manager = PairingManager(store)  # type: ignore[arg-type]
    first_session, first_code = manager.create("First")
    manager.register(registration(first_code))
    await manager.async_confirm(first_session)
    _, second_code = manager.create("Second")
    with pytest.raises(ProtocolError, match="already paired"):
        manager.register(registration(second_code))
