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

- [ESP32_LaskaKit_4.2](https://github.com/coolajz/ESP32_LaskaKit_4.2) — planned;
  firmware support is not part of this repository and must be implemented separately.

No firmware repository was modified while preparing this integration.

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
4. Verify the MAC, model, hardware, firmware, and the internal Home Assistant URL,
   then confirm.
5. Home Assistant sends a unique key to the display and creates the subentry only
   after verifying the display's HMAC proof.

The integration derives transport from Home Assistant's internal URL. An `http://`
URL uses local HTTP. An `https://` URL defaults to full certificate and hostname
verification. HTTPS without certificate verification is available only as an
explicit opt-in followed by a separate warning; there is no automatic downgrade.

Each display is a Config Subentry and owns exactly one Device Registry device. Its
normalized MAC is the unique ID. Displays can be renamed and reconfigured
independently.

## Display content and configuration

The current layout provides one main value, two bottom values, a weather entity, and
optional additional humidity. Numeric slots use native Home Assistant entity
selectors, automatic device-class detection, manual type override, label, decimal
places, and an optional unit override. The display never receives HA `entity_id`
values and never queries HA entities itself.

Desired configuration currently includes the web interface, battery voltage display,
automatic OTA, a 24-hour wake schedule, and partial-refresh count. The desired
revision advances immediately in Home Assistant, but a sleeping device may not apply
it until a later wake. The **Configuration pending** binary sensor makes that delay
visible.

The hub calculates the nearest future schedule boundary in Home Assistant's timezone
and returns `server_time`, `next_wake_at`, and authoritative `sleep_seconds`. The
**Next wake** and **Last planned interval** sensors expose the same persisted plan;
availability remains true until two minutes after the expected wake time.

Telemetry is not restored as if it were current. The Recorder keeps history, while
after a Home Assistant restart the integration waits for a fresh device check-in.
Only security state, desired/reported revisions, content selection, optional sensor
capabilities, pending commands, and wake-planning diagnostics are persisted.

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

Remove the Display subentry in E-paper Display Hub. On integration reload its private
key is removed, so subsequent check-ins are rejected. Delete the key and hub URL from
the physical display as well. To rotate a key in protocol v1, remove and pair the
display again.

## Development validation

The repository includes pytest, Ruff, mypy, HACS validation, and hassfest workflows.
Local unit tests cover protocol canonicalization, pairing identity validation,
idempotent transaction reuse, HMAC proof binding, replay handling, telemetry
capability rules, desired/reported revisions, durable commands, content normalization,
hourly boundaries, and daylight-saving transitions.
The suite also covers signed time recovery, nonce binding, invalid signatures,
unknown devices, short nonces, replay rejection, and per-device rate limiting. A real
Home Assistant runtime is still required
to verify UI flow rendering, Config Subentry lifecycle, Device/Entity Registry
behavior, HTTP routing behind the chosen proxy, and Recorder behavior.

## License

[MIT](LICENSE)
