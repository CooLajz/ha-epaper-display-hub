# E-paper Display Hub

E-paper Display Hub is a personal community integration for Home Assistant. It is
not an official part of Home Assistant, Home Assistant Community Store, or LaskaKit,
and none of those projects provides support for it.

The integration is primarily intended for our internal local-network e-paper
displays. It replaces Home Assistant Long-Lived Access Tokens in firmware with a
small versioned protocol and a separate HMAC key for every display. It does not use
MQTT. A display sends telemetry once per wake and receives configuration, selected
Home Assistant values, weather, commands, and an authoritative wake time in one
response.

## Compatible firmware

Compatible firmware must explicitly implement [protocol v1](docs/PROTOCOL.md).
Installing this integration alone cannot connect an arbitrary display.

- [ESP32_LaskaKit_4.2](https://github.com/CooLajz/ESP32_LaskaKit_4.2) — reference
  firmware. Its implementation, hardware validation, builds, and releases are handled
  separately in the firmware repository.

## Requirements and installation

- Home Assistant 2026.8.0 or newer.
- HACS with support for custom integration repositories.
- A configured Home Assistant internal URL reachable from the display. HTTPS with
  certificate verification is strongly recommended for normal operation.

In HACS, open Custom repositories, add
`https://github.com/coolajz/ha-epaper-display-hub` as an Integration, install it, and
restart Home Assistant. Then open Settings → Devices & services → Add integration and
choose **E-paper Display Hub**.

[![Open this repository in HACS](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=CooLajz&repository=ha-epaper-display-hub&category=integration)

## Pair a display

1. Open the E-paper Display Hub integration and choose **Add entry → Display**.
2. Provision the display's Wi-Fi with Improv Serial. The unpaired display temporarily
   shows its local IPv4 address and an eight-digit PIN.
3. Enter a friendly name, the displayed IPv4 address, and PIN in Home Assistant.
4. If the displayed internal HA URL uses HTTPS and its certificate cannot be
   verified by the display, enable the explicit insecure-certificate checkbox.
5. Submit the single form. Home Assistant validates the device identity, sends a
   unique key to the display, and creates the subentry only
   after verifying the display's HMAC proof.

The integration derives transport from Home Assistant's internal URL. An `http://`
URL uses local HTTP. An `https://` URL defaults to full certificate and hostname
verification. HTTPS without certificate verification is available as an explicit
checkbox in the same pairing form; there is no automatic downgrade.

Each display is a Config Subentry and owns exactly one Device Registry device. Its
normalized MAC is the unique ID. Displays can be renamed and reconfigured
independently.

## Display content and configuration

The current layout provides one main value, two bottom values, a weather entity, and
optional additional humidity. Numeric slots use native Home Assistant entity
selectors, automatic device-class detection, numeric or text/state type override, label, decimal
places, and an optional unit override. The display never receives HA `entity_id`
values and never queries HA entities itself.

When a weather entity is configured, the display exposes a per-device
**Show weather** switch. Turning it off keeps the selected entity configured but
suppresses weather content in subsequent check-ins; turning it on restores delivery.

Desired firmware configuration currently includes battery voltage display, the
partial-refresh count, and per-display suspension of e-ink refresh. While refresh is
suspended, a separate 5–300 minute number entity controls telemetry-only wake cycles;
the firmware still checks in and reports sensors but preserves the existing image.
The 24-hour wake schedule is owned and evaluated by the Hub.
The desired revision advances immediately in Home Assistant, but a sleeping device
may not apply it until a later wake. The **Configuration pending** binary sensor
remains on only until the Hub includes the latest revision in a signed check-in
response. The applied revision remains tracked separately for protocol diagnostics.

The hub calculates the nearest future schedule boundary in Home Assistant's timezone
and applies the display's optional −60 to +60 second wake correction without shifting
the underlying schedule. It then returns `server_time`, `next_wake_at`, and
authoritative `sleep_seconds`. The
**Next wake** and **Last planned interval** sensors expose the same persisted plan;
availability remains true until two minutes after the expected wake time.
Turning refresh suspension off takes effect at the next telemetry wake and restores
the standard 24-hour schedule and display refresh immediately in that cycle.

OTA is also orchestrated exclusively by the Hub. Each display exposes an
**Automatic OTA** switch and an independent **OTA on next wake** switch. When
automatic OTA is enabled, the display configuration form also shows its daily OTA
check time. The time is evaluated in Home Assistant's timezone.
Enabling automatic OTA skips the current local day, so the first automatic command
is eligible only on the next day at the configured time.
Both automatic and manual requests create the same durable `ota_check` command, which
is repeated until firmware acknowledges its ID. Once enabled, the manual switch cannot
be cancelled and turns off only after that acknowledgement. Firmware
must verify the complete response HMAC before applying commands, persist processed IDs
for idempotence, and follow the completion/acknowledgement rules in
[protocol v1](docs/PROTOCOL.md#hub-owned-ota-orchestration).

The Hub exposes the last OTA check time, its `current` / `updated` / `failed` status,
and the available firmware version when the display can determine it. The installed
version remains available separately as **Firmware version**.

Each display also exposes **Find strongest Wi-Fi AP once**. The switch queues a durable
`wifi_full_scan` command. On receipt, firmware persists its ID, restarts, performs an
all-channel scan for the configured SSID, connects to the strongest AP, and acknowledges
completion. Like the one-time OTA switch, it cannot be cancelled while pending and
turns off only after the firmware acknowledgement.

The device also exposes **Partial refreshes between full refreshes** as a number
entity with a whole-number range of 0 to 50. Like the battery-voltage switch, this is
device configuration and is not duplicated in the display configuration form.

The latest known entity values are persisted and restored after a Home Assistant
restart, so sleeping displays do not temporarily become `unknown`. The persisted
last-contact timestamp makes the age of those values explicit; new check-ins replace
them atomically. Security state, desired/delivered/reported revisions, content
selection, optional sensor capabilities, pending commands, and wake-planning
diagnostics are persisted as well.

## Security limitations

- HMAC provides authentication and integrity, not encryption. Local HTTP is visible
  to passive observers. Use HTTPS with certificate validation for confidentiality.
- The temporary device pairing endpoint itself uses local HTTP. The nonce-like
  transaction and HMAC proof detect mismatches, but only a trusted or isolated local
  network prevents passive capture of the initial key.
- Keys are stored in Home Assistant `.storage` and backups. Protect both.
- A device needs trustworthy UTC time for replay protection after full power loss.
- Protocol v1 restores that time through the nonce-bound, per-device HMAC-signed
  `/time-sync` endpoint before the first normal `/check-in`.
- Do not expose the protocol endpoint directly to the internet. It is designed for a
  controlled local network and does not replace firewalling or network segmentation.

The complete canonical signing format, replay window, response verification, and
payload schema are in [docs/PROTOCOL.md](docs/PROTOCOL.md).

## Remove a display and revoke its key

Remove the Display subentry in E-paper Display Hub. Its entities disappear immediately,
but the Hub retains a restricted revocation record containing only the device key,
replay state, and one durable `unpair` command. At the display's next authenticated
check-in, the Hub returns a signed response containing only that command. Compatible
firmware verifies the complete response, deletes its Hub credentials, and enters local
pairing mode. The Hub repeats the command until the display stops using the old key.

The restricted record cannot receive content, desired configuration, OTA commands, or
ordinary telemetry updates. Pairing the same MAC again replaces it with new credentials.
Removing the complete Hub config entry deletes active and restricted keys immediately;
displays which were still paired must then be reset or reflashed manually.

## Development validation

The repository includes pytest, Ruff, mypy, HACS validation, and hassfest workflows.
Local unit tests cover protocol canonicalization, pairing identity validation,
idempotent transaction reuse, HMAC proof binding, replay handling, telemetry
capability rules, desired/reported revisions, durable commands, content normalization,
hourly boundaries, daylight-saving transitions, manual OTA cancellation, durable OTA
redelivery, daily schedule deduplication, Hub-timezone OTA triggering, and restricted
signed unpair responses after display removal.
The suite also covers signed time recovery, nonce binding, invalid signatures,
unknown devices, short nonces, replay rejection, and per-device rate limiting. A real
Home Assistant runtime is still required
to verify UI flow rendering, Config Subentry lifecycle, Device/Entity Registry
behavior, HTTP routing behind the chosen proxy, and Recorder behavior.

## License

[MIT](LICENSE)
