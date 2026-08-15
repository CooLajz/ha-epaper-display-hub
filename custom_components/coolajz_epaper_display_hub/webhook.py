"""Unauthenticated HTTP surface authenticated by pairing or device HMAC."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any

from aiohttp import web
from homeassistant.components.http.view import HomeAssistantView
from homeassistant.core import HomeAssistant

from .const import (
    CHECKIN_PATH,
    DEVICE_HEADER,
    DOMAIN,
    NONCE_HEADER,
    PAIR_CLAIM_PATH,
    PAIR_REGISTER_PATH,
    PROTOCOL_HEADER,
    PROTOCOL_VERSION,
    SIGNATURE_HEADER,
    TIMESTAMP_HEADER,
)
from .models import ProtocolError, format_device_id, validate_checkin_payload
from .security import (
    canonical_request,
    canonical_response,
    remember_nonce,
    sign,
    validate_freshness,
    verify_signature,
)

_LOGGER = logging.getLogger(__name__)
MAX_BODY_SIZE = 64 * 1024


def _json_response(payload: dict[str, Any], status: int = 200) -> web.Response:
    return web.json_response(payload, status=status)


async def _read_json(request: web.Request) -> tuple[bytes, dict[str, Any]]:
    body = await request.read()
    if len(body) > MAX_BODY_SIZE:
        raise ProtocolError("payload_too_large", "Request body is too large")
    try:
        payload = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError) as err:
        raise ProtocolError("invalid_json", "Body must be a JSON object") from err
    if not isinstance(payload, dict):
        raise ProtocolError("invalid_json", "Body must be a JSON object")
    return body, payload


class PairRegisterView(HomeAssistantView):
    """Receive a pairing candidate using a short-lived user code."""

    url = PAIR_REGISTER_PATH
    name = f"api:{DOMAIN}:pair_register"
    requires_auth = False

    async def post(self, request: web.Request) -> web.Response:
        hass: HomeAssistant = request.app["hass"]
        runtime = next(iter(hass.data.get(DOMAIN, {}).values()), None)
        if runtime is None:
            return _json_response({"error": "hub_not_ready"}, 503)
        if not runtime.pairing.allow_attempt(request.remote or "unknown"):
            return _json_response({"error": "rate_limited"}, 429)
        try:
            _, payload = await _read_json(request)
            return _json_response(runtime.pairing.register(payload), 202)
        except ProtocolError as err:
            return _json_response({"error": err.code, "message": str(err)}, 400)


class PairClaimView(HomeAssistantView):
    """Deliver a per-device secret once after explicit HA confirmation."""

    url = PAIR_CLAIM_PATH
    name = f"api:{DOMAIN}:pair_claim"
    requires_auth = False

    async def post(self, request: web.Request) -> web.Response:
        hass: HomeAssistant = request.app["hass"]
        runtime = next(iter(hass.data.get(DOMAIN, {}).values()), None)
        if runtime is None:
            return _json_response({"error": "hub_not_ready"}, 503)
        try:
            _, payload = await _read_json(request)
            response = runtime.pairing.claim(payload)
            return _json_response(
                response, 200 if response["status"] == "paired" else 202
            )
        except ProtocolError as err:
            return _json_response({"error": err.code, "message": str(err)}, 400)


class CheckinView(HomeAssistantView):
    """Authenticate a sleeping display and return all data in one response."""

    url = CHECKIN_PATH
    name = f"api:{DOMAIN}:checkin"
    requires_auth = False

    async def post(self, request: web.Request) -> web.Response:
        hass: HomeAssistant = request.app["hass"]
        runtime = next(iter(hass.data.get(DOMAIN, {}).values()), None)
        if runtime is None:
            return _json_response({"error": "hub_not_ready"}, 503)
        try:
            body, payload = await _read_json(request)
            device_id = format_device_id(request.headers.get(DEVICE_HEADER, ""))
            if int(request.headers.get(PROTOCOL_HEADER, "0")) != PROTOCOL_VERSION:
                raise ProtocolError(
                    "unsupported_protocol", "Unsupported protocol version"
                )
            timestamp = int(request.headers.get(TIMESTAMP_HEADER, "0"))
            nonce = request.headers.get(NONCE_HEADER, "")
            supplied = request.headers.get(SIGNATURE_HEADER, "")
            record = runtime.store.devices.get(device_id)
            if record is None:
                raise ProtocolError("unknown_device", "Device is not paired")
            validate_freshness(record, timestamp, nonce)
            canonical = canonical_request(
                request.method, request.path, device_id, timestamp, nonce, body
            )
            if not verify_signature(record.secret, canonical, supplied):
                raise ProtocolError(
                    "invalid_signature", "Signature verification failed"
                )
            validate_checkin_payload(payload, device_id)
            remember_nonce(record, nonce)
            await runtime.store.async_save()
            content = runtime.content_for(device_id)
            response_payload = await runtime.coordinator.async_process_checkin(
                record, payload, content
            )
            response_body = json.dumps(
                response_payload,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
            response_time = int(datetime.now(UTC).timestamp())
            signature = sign(
                record.secret,
                canonical_response(
                    200, request.path, device_id, response_time, nonce, response_body
                ),
            )
            return web.Response(
                body=response_body,
                status=200,
                content_type="application/json",
                headers={
                    SIGNATURE_HEADER: signature,
                    TIMESTAMP_HEADER: str(response_time),
                    DEVICE_HEADER: device_id,
                    PROTOCOL_HEADER: str(PROTOCOL_VERSION),
                    NONCE_HEADER: nonce,
                },
            )
        except (ProtocolError, ValueError) as err:
            code = err.code if isinstance(err, ProtocolError) else "invalid_headers"
            # Deliberately do not log headers, bodies, codes, tokens, or keys.
            _LOGGER.debug("Rejected display check-in: %s", code)
            return _json_response({"error": code}, 401)


def async_register_views(hass: HomeAssistant) -> None:
    """Register the protocol surface once per Home Assistant process."""
    hass.http.register_view(PairRegisterView)
    hass.http.register_view(PairClaimView)
    hass.http.register_view(CheckinView)
