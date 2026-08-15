# E-paper Display Hub protocol v1

This document is the firmware-facing contract. Protocol version 1 is intentionally
small: after pairing, a display normally performs one `POST` request per wake,
receives everything needed for rendering, refreshes, and returns to deep sleep.

## Transport and trust boundaries

- The API is intended for a trusted local network and is served by the Home
  Assistant HTTP server.
- HMAC authenticates both peers and protects integrity. It does **not** hide MAC
  addresses, telemetry, selected entity values, weather, configuration, or commands.
- Use HTTPS with certificate validation whenever confidentiality or resistance to
  passive observation is required. A reverse proxy must preserve the request path.
- A different random 256-bit key is generated for every display. The key grants no
  Home Assistant user or administrator privileges and cannot authenticate another
  display.
- Home Assistant keeps keys in its private `.storage` area. Home Assistant backups
  therefore contain them and must be protected. The integration never places keys in
  Config Subentries, entity attributes, logs, or ordinary diagnostics.

All endpoints are under `/api/coolajz_epaper_display_hub/v1` and consume JSON. The
maximum request body is 64 KiB.

## Device identifier

`device_id` is the device's factory MAC address in uppercase colon form, for example
`AA:BB:CC:DD:EE:FF`. Separators and case are normalized on input. A friendly name,
hostname, IP address, or user-configurable MAC is not a stable identifier.

## Pairing

Pairing is the only multi-request operation:

1. In Home Assistant, add a Display subentry and choose a friendly name. The hub
   shows an eight-digit code valid for ten minutes.
2. The display sends `POST /pair/register`:

   ```json
   {
     "protocol_version": 1,
     "pairing_code": "12345678",
     "device_id": "AA:BB:CC:DD:EE:FF",
     "friendly_name": "Living room",
     "model": "LaskaKit ESPink 4.2",
     "hardware_variant": "ESP32-S3",
     "firmware_version": "1.0.0"
   }
   ```

3. A successful registration returns HTTP 202 with an opaque `pairing_session` and
   `claim_token`. These values authorize only this short-lived pairing transaction.
4. Home Assistant shows the MAC, model, and firmware. The user explicitly confirms.
   Only then does the hub create the Config Subentry and per-device key.
5. The display polls `POST /pair/claim` with `pairing_session` and `claim_token`.
   Before confirmation it receives HTTP 202. After confirmation it receives the key
   once over HTTP 200:

   ```json
   {
     "status": "paired",
     "protocol_version": 1,
     "device_id": "AA:BB:CC:DD:EE:FF",
     "device_key": "64 lowercase hex characters"
   }
   ```

The claim transaction is invalidated immediately after delivery. Pairing without
HTTPS exposes the new key to a passive local-network observer; HMAC cannot protect a
key that has not yet been shared. Pair on an isolated/trusted network or use HTTPS.

## Authenticated check-in

Endpoint: `POST /check-in`.

Required headers:

| Header | Value |
|---|---|
| `X-EPD-Protocol-Version` | `1` |
| `X-EPD-Device-ID` | normalized MAC |
| `X-EPD-Timestamp` | Unix seconds in UTC |
| `X-EPD-Nonce` | new URL-safe random nonce, at least 128 bits |
| `X-EPD-Signature` | lowercase hex HMAC-SHA256 |

The request body is UTF-8 JSON. The exact transmitted bytes are hashed; a firmware
must sign the same bytes it sends. Canonical request data is UTF-8 with LF separators
and no trailing LF:

```text
EPD-HUB-REQUEST-V1
POST
/api/coolajz_epaper_display_hub/v1/check-in
1
AA:BB:CC:DD:EE:FF
1786780800
random_url_safe_nonce
lowercase_sha256_of_exact_body_bytes
```

`signature = hex(HMAC-SHA256(device_key_bytes, canonical_request_bytes))`. The key
bytes are decoded from the issued hex string. The server compares signatures in
constant time and verifies that the signed device ID matches the body.

Interoperability vector:

```text
key_hex = 000102030405060708090a0b0c0d0e0f101112131415161718191a1b1c1d1e1f
body = {"device_id":"AA:BB:CC:DD:EE:FF","protocol_version":1}
timestamp = 1786780800
nonce = AAAAAAAAAAAAAAAAAAAAAA
body_sha256 = ca8a7fdc4ee6bd2568ab6ea49fb16e952a109d9940452a3d75a34de977afe0bf
signature = 019acd433d947199dd9d13bdd0bba1b3026af2a7cc30c9aa50f3f2340924aec7
```

Replay protection combines two controls:

- Every accepted nonce is persisted before the request is processed. The last 64
  nonces are retained per display, including across Home Assistant restarts.
- The timestamp window is `max(24 h, 2 × longest scheduled interval + 10 min)`,
  capped at seven days. Up to ten minutes of future clock skew is accepted. This
  allows deep sleep without accepting indefinitely old captures.
- Pairing registration is limited per client address to reduce online guessing of
  the short-lived eight-digit code.

The device must keep a usable UTC clock through deep sleep. After a full power loss
with no trusted clock source it uses the authenticated time-recovery exchange below
before check-in. The `/check-in` freshness rules are never weakened.

## Trusted time recovery after power loss

Endpoint: `POST /time-sync`.

This endpoint is used only when the device has lost trustworthy UTC time. It does not
replace the normal one-request `/check-in` wake cycle and never returns configuration,
content, weather, commands, or wake planning. An unauthenticated network time value
must not be treated as trusted device time.

The request body contains exactly these fields and deliberately has no timestamp:

```json
{
  "protocol_version": 1,
  "device_id": "AA:BB:CC:DD:EE:FF",
  "nonce": "BBBBBBBBBBBBBBBBBBBBBB"
}
```

`nonce` is a new URL-safe random value carrying at least 128 bits of entropy. The
request has `X-EPD-Signature`; other identity values come from the signed body. The
exact transmitted body bytes are hashed into this separate canonical context:

```text
EPD-HUB-TIME-REQUEST-V1
POST
/api/coolajz_epaper_display_hub/v1/time-sync
1
AA:BB:CC:DD:EE:FF
BBBBBBBBBBBBBBBBBBBBBB
lowercase_sha256_of_exact_body_bytes
```

The server validates the protocol, normalized device ID, nonce format and replay
history, device existence, and HMAC before consuming the per-device rate limit. A
valid nonce is persisted before the response is returned. The rate limit is six
authenticated requests per device per rolling minute; invalid signatures cannot
consume another device's allowance.

A successful response contains only trusted time metadata:

```json
{
  "device_id": "AA:BB:CC:DD:EE:FF",
  "protocol_version": 1,
  "server_time": 1786780800,
  "server_time_iso": "2026-08-15T10:00:00+02:00"
}
```

`server_time` is Unix seconds in UTC. `server_time_iso` is the same instant rendered
in the Home Assistant timezone. The response includes `X-EPD-Signature`,
`X-EPD-Device-ID`, `X-EPD-Protocol-Version`, and the original request nonce in
`X-EPD-Nonce`. Its signature uses a direction-specific context bound to that nonce:

```text
EPD-HUB-TIME-RESPONSE-V1
200
/api/coolajz_epaper_display_hub/v1/time-sync
1
AA:BB:CC:DD:EE:FF
BBBBBBBBBBBBBBBBBBBBBB
lowercase_sha256_of_exact_response_body_bytes
```

Firmware verifies the response body, device ID, protocol, original nonce, and HMAC
before setting its UTC clock. A response captured for a different request nonce is
invalid, so replaying an old signed time response cannot satisfy a fresh request.

### Time-sync interoperability vector

```text
key_hex = 000102030405060708090a0b0c0d0e0f101112131415161718191a1b1c1d1e1f
device_id = AA:BB:CC:DD:EE:FF
nonce = BBBBBBBBBBBBBBBBBBBBBB
request_body = {"device_id":"AA:BB:CC:DD:EE:FF","nonce":"BBBBBBBBBBBBBBBBBBBBBB","protocol_version":1}
request_signature = 71bd4d30b99446abb492086e5fe40f4607350a469ee9fe36f15be4cb693c00a5
response_body = {"device_id":"AA:BB:CC:DD:EE:FF","protocol_version":1,"server_time":1786780800,"server_time_iso":"2026-08-15T10:00:00+02:00"}
response_signature = e81fb4af949697b2cbb63b1da5409249caeaeaebe4c4e32877076ab710d616d3
```

### Request body

```json
{
  "protocol_version": 1,
  "device_id": "AA:BB:CC:DD:EE:FF",
  "model": "LaskaKit ESPink 4.2",
  "hardware_variant": "ESP32-S3",
  "firmware_version": "1.0.0",
  "reported_config": {
    "revision": 4,
    "applied": true,
    "values": {
      "web_enabled": false,
      "show_battery_voltage": true,
      "auto_ota": false,
      "partial_refreshes_between_full": 10
    }
  },
  "telemetry": {
    "battery_percent": 81,
    "battery_voltage": 3.91,
    "rssi": -63,
    "active_runtime_ms": 2140,
    "last_refresh": "2026-08-15T08:00:02Z",
    "wake_reason": "timer",
    "refresh_type": "partial",
    "environment_sensor_present": true,
    "board_temperature": 24.3,
    "board_humidity": 46.1,
    "last_data_status": "ok"
  },
  "command_acknowledgements": ["full-refresh-42"]
}
```

Optional telemetry keys may be omitted. `null` is not a valid measurement. Board
temperature and humidity are accepted only when `environment_sensor_present` is
true and the corresponding value is finite; the board model never implies a sensor.

### Response body and signature

The response contains the server time, desired configuration with its monotonically
increasing revision, normalized content values, normalized weather, and durable
one-time commands. A bad source entity affects only its own item:

```json
{
  "protocol_version": 1,
  "server_time": "2026-08-15T22:17:03+02:00",
  "next_wake_at": "2026-08-15T23:00:00+02:00",
  "sleep_seconds": 2577,
  "revision": 5,
  "desired_config": {
    "revision": 5,
    "pending": true,
    "values": {"web_enabled": false}
  },
  "content": {
    "main": {
      "valid": true,
      "value": 24.1,
      "type": "temperature",
      "label": "Living room",
      "unit": "°C"
    },
    "bottom_left": {"valid": false, "value": null},
    "weather": {
      "valid": true,
      "condition": "partlycloudy",
      "temperature": 22.0,
      "humidity": 58.0
    }
  },
  "commands": []
}
```

The response repeats all request headers except the timestamp and signature values.
Its signature uses this separate context, preventing request/response reflection:

```text
EPD-HUB-RESPONSE-V1
200
/api/coolajz_epaper_display_hub/v1/check-in
1
AA:BB:CC:DD:EE:FF
1786780802
original_request_nonce
lowercase_sha256_of_exact_response_body_bytes
```

Firmware must verify the response device ID, original nonce, time, exact-body hash,
and HMAC before applying configuration, commands, or display data.

## Wake scheduling and offline fallback

Home Assistant is the only source of truth for wake scheduling. Each display has a
24-hour local schedule whose values are constrained to 5, 10, 15, 20, 30, or 60
minutes. The hub searches for the nearest strictly future valid boundary; it does not
add the interval belonging to the current hour. For example, a check-in at 22:17 with
a 60-minute interval for hour 22 and a 15-minute interval for hour 23 returns 23:00,
followed by 23:15, 23:30, and 23:45.

`server_time` and `next_wake_at` are timezone-aware ISO 8601 values in the Home
Assistant timezone. `sleep_seconds` is authoritative and is measured from response
creation. The response-level `revision` identifies the desired configuration used by
the calculation. Timezone transitions are resolved on Home Assistant's real UTC
timeline, including skipped and repeated local hours.

Firmware records a monotonic timestamp when it accepts the signed response. Just
before deep sleep it subtracts the elapsed render and cycle time from
`sleep_seconds`, never substitutes a locally calculated schedule, and uses
`server_time` for the displayed last-refresh time.

If the hub is unreachable, times out, returns an unsuccessful response, or fails
signature validation, firmware preserves the current image, performs no e-ink
refresh, applies no configuration, runs no automatic OTA, and sleeps for exactly 300
seconds before retrying. Unverified response data must never affect device state.

The hub persists `next_wake_at` and the last planned interval. A sleeping display is
available until two minutes after its planned wake time. It becomes unavailable only
after missing that deadline and returns automatically on its next successful check-in.

## Desired versus reported state

- `desired_config.revision` increments only when Home Assistant changes a desired
  value.
- A returned revision means “delivered,” not “applied.”
- Firmware reports the revision it has stored, plus `applied: true` only after all
  values in that revision are actually active.
- Home Assistant marks configuration pending while `applied_revision <
  desired_revision`.
- Unknown future desired keys must not make known keys unusable. Firmware should
  report unsupported keys in a future capability/error extension instead of claiming
  that the full revision was applied.

One-time commands remain in private storage and are returned again until firmware
reports their IDs in `command_acknowledgements`. Firmware must execute each command ID
idempotently and retain its acknowledgement across deep sleep until a later successful
check-in. This avoids losing a command when a response or display refresh fails.

A configured unit override changes only the short unit label sent to the display; it
does not convert the numeric value. It should therefore be used only when the selected
entity's native value is already expressed in that unit.

## Key rotation and revocation

Removing the display Subentry revokes and deletes its server-side key during reload;
future requests return `unknown_device`. The device must delete its local key and be
paired again. Protocol v1 performs rotation as revoke plus a new pairing. Compromise
of one key therefore affects only that MAC and cannot authorize Home Assistant API
calls or other displays.
