"""Home Assistant initiated pairing with a temporarily listening display."""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from aiohttp import ClientError, ClientResponse, ClientSession, ClientTimeout

from .const import (
    PROTOCOL_VERSION,
    TRANSPORT_HTTP,
    TRANSPORT_SECURITY_OPTIONS,
)
from .models import ProtocolError, format_device_id
from .security import canonical_json, generate_nonce, generate_secret

DEVICE_INFO_PATH = "/api/coolajz_epaper_display_hub/v1/device-info"
DEVICE_PAIR_PATH = "/api/coolajz_epaper_display_hub/v1/pair"
MAX_DEVICE_RESPONSE_SIZE = 8 * 1024
MAX_PAIRING_REQUEST_SIZE = 1024
PAIRING_TIMEOUT = ClientTimeout(
    total=6,
    connect=3,
    sock_connect=3,
    sock_read=3,
)
PIN_RE = re.compile(r"^[0-9]{8}$")
TRANSACTION_RE = re.compile(r"^[A-Za-z0-9_-]{22,64}$")
PROOF_RE = re.compile(r"^[0-9a-f]{64}$")
LOCAL_IPV4_NETWORKS = tuple(
    ipaddress.ip_network(network)
    for network in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")
)


@dataclass(frozen=True, slots=True)
class DeviceIdentity:
    """Validated public identity read before key delivery."""

    device_id: str
    model: str
    hardware_variant: str
    firmware_version: str


def validate_local_ipv4(value: str) -> str:
    """Accept only an explicit non-routable IPv4 address."""
    try:
        address = ipaddress.ip_address(value.strip())
    except ValueError as err:
        raise ProtocolError("invalid_ip", "A valid IPv4 address is required") from err
    if not isinstance(address, ipaddress.IPv4Address):
        raise ProtocolError("invalid_ip", "Only IPv4 addresses are supported")
    if not any(address in network for network in LOCAL_IPV4_NETWORKS):
        raise ProtocolError("invalid_ip", "The IPv4 address must be local")
    return str(address)


def validate_pairing_pin(value: str) -> str:
    """Validate the physical-access PIN displayed by the device."""
    pin = value.strip()
    if not PIN_RE.fullmatch(pin):
        raise ProtocolError("invalid_pin", "Pairing PIN must contain eight digits")
    return pin


def validate_friendly_name(value: str) -> str:
    """Validate the bounded name stored on the display."""
    name = value.strip()
    if not 1 <= len(name) <= 63:
        raise ProtocolError("invalid_name", "Friendly name must be 1 to 63 characters")
    return name


def normalize_hub_url(value: str, transport_security: str) -> str:
    """Normalize a callback URL without silently changing its transport."""
    normalized = value.strip().rstrip("/")
    if len(normalized) >= 192 or any(character in normalized for character in "@#?"):
        raise ProtocolError("hub_url_invalid", "Home Assistant URL is not supported")
    parsed = urlsplit(normalized)
    if transport_security not in TRANSPORT_SECURITY_OPTIONS:
        raise ProtocolError("invalid_transport", "Transport security is unsupported")
    expected_scheme = "http" if transport_security == TRANSPORT_HTTP else "https"
    if (
        parsed.scheme != expected_scheme
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ProtocolError(
            "hub_url_invalid", "Home Assistant URL does not match the transport"
        )
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "", ""))


def canonical_pairing_ack(device_id: str, transaction_id: str, hub_url: str) -> bytes:
    """Build the firmware-facing acknowledgement proof context."""
    return (
        f"EPD-HUB-PAIRING-ACK-V1\n{PROTOCOL_VERSION}\n{device_id}\n"
        f"{transaction_id}\n{hub_url}"
    ).encode()


def pairing_proof(
    device_key: str, device_id: str, transaction_id: str, hub_url: str
) -> str:
    """Calculate the HMAC proof expected from a paired display."""
    return hmac.new(
        bytes.fromhex(device_key),
        canonical_pairing_ack(device_id, transaction_id, hub_url),
        hashlib.sha256,
    ).hexdigest()


async def _read_limited_json(response: ClientResponse) -> dict[str, Any]:
    """Read one bounded JSON object without trusting Content-Length."""
    body = bytearray()
    while chunk := await response.content.read(
        min(4096, MAX_DEVICE_RESPONSE_SIZE + 1 - len(body))
    ):
        body.extend(chunk)
        if len(body) > MAX_DEVICE_RESPONSE_SIZE:
            raise ProtocolError("response_too_large", "Device response is too large")
    try:
        payload = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError) as err:
        raise ProtocolError("invalid_json", "Device returned invalid JSON") from err
    if not isinstance(payload, dict):
        raise ProtocolError("invalid_json", "Device response must be an object")
    return payload


def _validate_identity(payload: Mapping[str, Any]) -> DeviceIdentity:
    if payload.get("status") != "pairing":
        raise ProtocolError("not_pairing", "Device is not in pairing mode")
    if (
        type(payload.get("protocol_version")) is not int
        or payload["protocol_version"] != PROTOCOL_VERSION
    ):
        raise ProtocolError("unsupported_protocol", "Unsupported protocol version")
    device_id = format_device_id(str(payload.get("device_id", "")))
    metadata: dict[str, str] = {}
    for key in ("model", "hardware_variant", "firmware_version"):
        value = payload.get(key)
        if not isinstance(value, str) or not value.strip():
            raise ProtocolError("empty_metadata", f"Device returned empty {key}")
        metadata[key] = value.strip()
    return DeviceIdentity(device_id=device_id, **metadata)


class DevicePairingClient:
    """Perform bounded, redirect-free pairing requests to one local display."""

    def __init__(self, session: ClientSession) -> None:
        self._session = session

    async def async_device_info(self, device_ip: str) -> DeviceIdentity:
        """Fetch and validate identity before generating any key material."""
        ip = validate_local_ipv4(device_ip)
        url = f"http://{ip}{DEVICE_INFO_PATH}"
        try:
            async with self._session.get(
                url,
                allow_redirects=False,
                timeout=PAIRING_TIMEOUT,
            ) as response:
                if 300 <= response.status < 400:
                    raise ProtocolError(
                        "redirect_rejected", "Redirects are not allowed"
                    )
                if response.status != 200:
                    raise ProtocolError(
                        "device_unavailable", "Device identity request failed"
                    )
                return _validate_identity(await _read_limited_json(response))
        except TimeoutError as err:
            raise ProtocolError("timeout", "Device identity request timed out") from err
        except ClientError as err:
            raise ProtocolError("device_unavailable", "Device is unavailable") from err

    async def async_pair(
        self,
        *,
        device_ip: str,
        identity: DeviceIdentity,
        pairing_pin: str,
        hub_url: str,
        transport_security: str,
        friendly_name: str,
        device_key: str,
        transaction_id: str,
    ) -> None:
        """Deliver one stable transaction and verify the device's HMAC proof."""
        ip = validate_local_ipv4(device_ip)
        pin = validate_pairing_pin(pairing_pin)
        name = validate_friendly_name(friendly_name)
        normalized_url = normalize_hub_url(hub_url, transport_security)
        if not re.fullmatch(r"[0-9a-f]{64}", device_key):
            raise ProtocolError("invalid_device_key", "Device key format is invalid")
        if not TRANSACTION_RE.fullmatch(transaction_id):
            raise ProtocolError(
                "invalid_transaction", "Transaction identifier format is invalid"
            )
        request_body = canonical_json(
            {
                "protocol_version": PROTOCOL_VERSION,
                "pairing_pin": pin,
                "hub_url": normalized_url,
                "transport_security": transport_security,
                "friendly_name": name,
                "device_key": device_key,
                "transaction_id": transaction_id,
            }
        )
        if len(request_body) > MAX_PAIRING_REQUEST_SIZE:
            raise ProtocolError("payload_too_large", "Pairing request is too large")
        url = f"http://{ip}{DEVICE_PAIR_PATH}"
        try:
            async with self._session.post(
                url,
                data=request_body,
                headers={"Content-Type": "application/json"},
                allow_redirects=False,
                timeout=PAIRING_TIMEOUT,
            ) as response:
                if 300 <= response.status < 400:
                    raise ProtocolError(
                        "redirect_rejected", "Redirects are not allowed"
                    )
                if response.status == 429:
                    raise ProtocolError("rate_limited", "Device rate limit reached")
                if response.status in (401, 403):
                    raise ProtocolError("invalid_pin", "Pairing PIN was rejected")
                if response.status != 200:
                    raise ProtocolError(
                        "pairing_failed", "Device pairing request failed"
                    )
                payload = await _read_limited_json(response)
        except TimeoutError as err:
            raise ProtocolError("timeout", "Pairing request timed out") from err
        except ClientError as err:
            raise ProtocolError("device_unavailable", "Device is unavailable") from err

        if payload.get("status") != "paired":
            raise ProtocolError("pairing_failed", "Device did not confirm pairing")
        if (
            type(payload.get("protocol_version")) is not int
            or payload["protocol_version"] != PROTOCOL_VERSION
        ):
            raise ProtocolError("unsupported_protocol", "Unsupported protocol version")
        response_device_id = format_device_id(str(payload.get("device_id", "")))
        if response_device_id != identity.device_id:
            raise ProtocolError("device_mismatch", "Pairing response device differs")
        if payload.get("transaction_id") != transaction_id:
            raise ProtocolError("transaction_mismatch", "Pairing transaction differs")
        proof = payload.get("proof")
        if not isinstance(proof, str) or not PROOF_RE.fullmatch(proof):
            raise ProtocolError("invalid_proof", "Pairing proof format is invalid")
        expected = pairing_proof(
            device_key,
            identity.device_id,
            transaction_id,
            normalized_url,
        )
        if not hmac.compare_digest(expected, proof):
            raise ProtocolError("invalid_proof", "Pairing proof verification failed")


def new_pairing_credentials() -> tuple[str, str]:
    """Generate one stable key and transaction for a config-flow attempt."""
    return generate_secret(), generate_nonce()
