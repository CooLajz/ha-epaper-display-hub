# E-paper Display Hub protocol v1

This document is the firmware-facing contract. Protocol version 1 is intentionally
small: after pairing, a display normally performs one `POST` request per wake,
receives everything needed for rendering, refreshes, and returns to deep sleep.

The reference implementation is maintained separately in
[ESP32_LaskaKit_4.2](https://github.com/CooLajz/ESP32_LaskaKit_4.2). This repository is
the source of truth for the Hub API and interoperability contract; firmware changes,
builds, USB flashing, and physical display validation belong in that firmware project.

Recommended firmware implementation order:

1. Keep the signed response as an atomic input: validate its HMAC and complete schema
   before applying configuration or changing the e-ink image.
2. Persist the applied desired revision and report it on the next check-in.
3. Render only normalized `content`; never restore direct entity access or a Home
   Assistant Long-Lived Access Token.
4. Persist command acknowledgements until a later successful check-in confirms that
   the Hub accepted them.
5. Treat `ota_check` as an idempotent durable command. Persist its ID before starting
   the check and acknowledge it only after the outcome rules below are satisfied.
6. After feature parity is physically verified, remove the previous token client, local
   configuration web, local wake scheduler, and alternate non-Hub runtime paths from
   the firmware repository.

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

After Improv Serial provisions Wi-Fi, an unpaired display temporarily listens on
`http://<device-ip>:80` and shows its explicit local IPv4 address and a fresh
eight-digit PIN. Pairing is initiated by Home Assistant:

1. The config flow asks for a friendly name, the displayed IPv4 address, and PIN.
   Hostnames, redirects, IPv6 addresses, and non-local addresses are rejected.
2. Home Assistant performs
   `GET /api/coolajz_epaper_display_hub/v1/device-info` with short connect and total
   timeouts and an 8 KiB response limit. The display returns:

   ```json
   {
     "status": "pairing",
     "protocol_version": 1,
     "device_id": "AA:BB:CC:DD:EE:FF",
     "model": "LaskaKit ESPink 4.2",
     "hardware_variant": "ESP32-S3",
     "firmware_version": "1.0.0"
   }
   ```

3. Home Assistant validates pairing state, protocol, normalized factory MAC, and
   non-empty model, hardware, and firmware metadata. A MAC already present in the
   private key store or another Display subentry is rejected.
4. The first form shows Home Assistant's configured internal URL. The URL is obtained
   by the integration; the user does not enter or choose its scheme. For HTTPS the
   same form offers an explicit checkbox to allow an invalid certificate.
5. Submitting that form makes Home Assistant generate a per-device 256-bit key and a
   URL-safe transaction identifier, then send
   `POST /api/coolajz_epaper_display_hub/v1/pair` to the display:

   ```json
   {
     "protocol_version": 1,
     "pairing_pin": "12345678",
     "hub_url": "https://homeassistant.example.cz",
     "transport_security": "https_verified",
     "allow_invalid_certificate": false,
     "friendly_name": "Living room",
     "device_key": "64 lowercase hex characters",
     "transaction_id": "AAAAAAAAAAAAAAAAAAAAAA"
   }
   ```

6. The display validates the PIN and stores the transaction idempotently. It returns:

   ```json
   {
     "status": "paired",
     "protocol_version": 1,
     "device_id": "AA:BB:CC:DD:EE:FF",
     "transaction_id": "AAAAAAAAAAAAAAAAAAAAAA",
     "allow_invalid_certificate": false,
     "proof": "lowercase HMAC-SHA256 hex"
   }
   ```

The proof is `hex(HMAC-SHA256(device_key_bytes, canonical_ack))`, where canonical_ack
has no trailing LF:

```text
EPD-HUB-PAIRING-ACK-V1
1
AA:BB:CC:DD:EE:FF
AAAAAAAAAAAAAAAAAAAAAA
https://homeassistant.example.cz
0
```

The proof confirms receipt of the key and binds the acknowledgement to the device,
transaction, normalized hub URL, and certificate policy. It does not encrypt the
request or authenticate fields absent from the canonical acknowledgement. Home
Assistant persists the key and creates the Config Subentry only after the proof, MAC,
transaction, and certificate policy all match.
If the POST response is lost or invalid, retrying in the same flow reuses exactly the
same key and transaction identifier. Firmware must therefore return the same
successful acknowledgement for an already committed transaction rather than
generate or overwrite credentials.

Pairing interoperability vector:

```text
key_hex = 000102030405060708090a0b0c0d0e0f101112131415161718191a1b1c1d1e1f
device_id = AA:BB:CC:DD:EE:FF
transaction_id = CCCCCCCCCCCCCCCCCCCCCC
hub_url = https://homeassistant.example.cz
allow_invalid_certificate = true
proof = 9780d216f8001eb7b7a2ba4a1202189d43ad70a092b240ea8df1870cd96b2ff8
```

The display pairing listener is plain HTTP and transfers the new key. It is safe only
on a physically controlled, trusted or isolated local network; the acknowledgement
HMAC verifies receipt of the issued key but cannot prevent passive key capture or
protect fields outside its canonical input. The listener must rate-limit attempts per
device and stop after pairing or a bounded pairing window.

### Home Assistant callback transport

`transport_security` tells firmware how to connect to `hub_url` after pairing:

- `http`: derived only when Home Assistant's internal URL starts with `http://`;
- `https_verified`: the default for an internal `https://` URL; firmware verifies
  the certificate chain, validity, and hostname;
- `https_insecure`: an explicit opt-in available only for an internal `https://` URL;
  TLS encrypts traffic but firmware does not verify the server identity.

The integration never changes HTTPS to HTTP and never falls back from
`https_verified` to `https_insecure` after a validation failure. Selecting insecure
HTTPS is selected directly in the first pairing form. The chosen or derived value is
stored with the Display Config Subentry and is not treated as a diagnostic fault when
the user deliberately selected it. The request also carries the equivalent boolean
`allow_invalid_certificate` required by firmware; it is bound into the HMAC proof.

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
- The temporary device pairing listener rate-limits attempts to reduce online PIN
  guessing.

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
      "show_battery_voltage": true,
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
    "last_data_status": "ok",
    "last_ota_check": "2026-08-15T03:00:02+02:00",
    "last_ota_status": "current",
    "available_firmware_version": "1.0.0"
  },
  "command_acknowledgements": ["durable-url-safe-command-id"]
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
    "values": {
      "show_battery_voltage": true,
      "partial_refreshes_between_full": 10
    }
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
  "commands": [
    {"id": "durable-url-safe-command-id", "type": "ota_check"}
  ]
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
- The Hub records a revision as delivered when it has prepared the successful signed
  check-in response containing that revision. The **Configuration pending** entity
  represents this transfer state and clears in the same wake cycle.
- Firmware reports the revision it has stored, plus `applied: true` only after all
  values in that revision are actually active.
- Applied state remains independent: `desired_config.pending` stays true while
  `applied_revision < desired_revision`, even after the Hub-side transfer indicator
  has cleared.
- Unknown future desired keys must not make known keys unusable. Firmware should
  report unsupported keys in a future capability/error extension instead of claiming
  that the full revision was applied.

One-time commands remain in private storage and are returned again until firmware
reports their IDs in `command_acknowledgements`. Firmware must execute each command ID
idempotently and retain its acknowledgement across deep sleep until a later successful
check-in. This avoids losing a command when a response or display refresh fails.

`partial_refreshes_between_full` is an integer from 0 through 20 and is configured by
the display's Home Assistant number entity. It is not part of the display config-flow
form. A change advances `desired_config.revision` like any other firmware-facing
desired value.

## Hub-owned OTA orchestration

OTA timing and command creation belong exclusively to the Hub. Firmware has no fixed
24-hour OTA schedule. Each display has an **Automatic OTA** switch and a local daily
check time. Home Assistant evaluates that wall-clock time in its configured timezone,
including daylight-saving transitions. When automatic OTA is enabled, the Hub creates
at most one command for each local calendar day, at the configured time or as soon as
the Hub next evaluates the schedule after that time. The first check-in at or after the
due time therefore receives the command. Disabling automatic OTA prevents future
automatic commands but does not retract a command that is already in the durable queue.
Only one automatic OTA command may be outstanding. If a failed older command is still
waiting for acknowledgement at the next daily time, the Hub continues delivering that
ID; after it is acknowledged, the current day's due command can be created without
building an unbounded queue.

The independent **OTA on next wake** switch creates a persistent manual request even
when automatic OTA is disabled. It can be turned off to cancel the request only before
the Hub includes it in a signed check-in response. Once included, the switch returns to
off automatically while the internal command remains queued. Turning the switch off at
that point cannot cancel delivery or acknowledgement tracking.

The only protocol-v1 OTA command is:

```json
{
  "id": "durable-url-safe-command-id",
  "type": "ota_check"
}
```

The Hub stores command source and delivery state privately, but those fields are never
part of the public `commands` item. The same public command is included in every signed
check-in response until the request contains its exact ID in
`command_acknowledgements`.

Firmware must verify the HMAC over the exact response body before reading or applying
`commands`. It must persist the ID of an accepted, in-progress, or completed OTA command
and must never start the same ID twice. Acknowledgement rules are:

- no update available: report the ID after the check completes;
- update installed successfully: persist completion before reboot and report the ID
  from the new firmware after startup;
- download or installation failure: report `failed` telemetry but do not acknowledge
  the ID, allowing the Hub to redeliver it.

The next check-in after a completed attempt reports `last_ota_check` as a timezone-aware
ISO 8601 timestamp and `last_ota_status` as `current`, `updated`, or `failed`.
`available_firmware_version` is optional and is included only when firmware can
determine it. The installed version continues to use the top-level `firmware_version`.
These diagnostic values describe the last completed attempt; they do not replace the
durable command acknowledgement.

`auto_ota` is not a firmware desired-configuration key. The Hub translates both the
manual switch and daily schedule into the same signed `ota_check` command contract.

A configured unit override changes only the short unit label sent to the display; it
does not convert the numeric value. It should therefore be used only when the selected
entity's native value is already expressed in that unit.

## Key rotation and revocation

Removing one display Subentry removes its entities and active configuration but moves
its per-device key into a restricted `revoked_pending_unpair` record. The record retains
only the key, replay-protection state, wake schedule needed for freshness validation,
and one stable command ID. It cannot access content, desired configuration, telemetry
storage, OTA scheduling, or another display.

The next valid signed `/check-in` receives this reduced response instead of the normal
response schema:

```json
{
  "protocol_version": 1,
  "server_time": "2026-08-15T22:17:03+02:00",
  "commands": [
    {"id": "durable-url-safe-command-id", "type": "unpair"}
  ]
}
```

The response uses the normal `EPD-HUB-RESPONSE-V1` canonical signature and remains
bound to the check-in nonce. It deliberately contains no `revision`, `desired_config`,
`content`, `next_wake_at`, `sleep_seconds`, or other command. Firmware must first
verify the exact-body HMAC and response binding, then recognize this restricted schema.
After accepting `unpair`, it deletes the per-device key, Hub URL and transport settings,
pending Hub commands, and any trusted Hub state, then immediately enters pairing mode,
shows a fresh PIN and local IP address, and does not refresh normal display content.

`unpair` is not acknowledged: erasing the key prevents a later signed ACK. The Hub
therefore repeats the same command ID on every authenticated check-in. If trusted UTC
was lost, the restricted key remains valid only for signed `/time-sync` followed by the
check-in. A new successful pairing of the same MAC replaces the restricted record.
Deleting the complete Hub config entry clears all active and restricted keys, so any
remaining display must be reset or reflashed manually.

Protocol v1 performs key rotation as removal, verified device-side unpairing, and a new
pairing. Compromise of one key therefore affects only that MAC and cannot authorize Home
Assistant API calls or other displays.
