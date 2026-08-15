# E-paper Display Hub

E-paper Display Hub is a personal community integration for Home Assistant. It is
not an official part of Home Assistant, Home Assistant Community Store, or LaskaKit,
and none of those projects provides support for it.

The integration is primarily intended for our internal local-network e-paper
displays. It replaces Home Assistant Long-Lived Access Tokens in firmware with a
small versioned protocol and a separate HMAC key for every display. It does not use
MQTT. A display sends telemetry once per wake and receives configuration, selected
Home Assistant values, weather, and commands in one response.

## Compatible firmware

Compatible firmware must explicitly implement [protocol v1](docs/PROTOCOL.md).
Installing this integration alone cannot connect an arbitrary display.

- [ESP32_LaskaKit_4.2](https://github.com/coolajz/ESP32_LaskaKit_4.2) — planned;
  firmware support is not part of this repository and must be implemented separately.

No firmware repository was modified while preparing this integration.

## Requirements and installation

- Home Assistant 2026.8.0 or newer.
- HACS with support for custom integration repositories.
- A direct local URL reachable from the display. HTTPS is strongly recommended for
  pairing and whenever payload confidentiality matters.

In HACS, open Custom repositories, add
`https://github.com/coolajz/ha-epaper-display-hub` as an Integration, install it, and
restart Home Assistant. Then open Settings → Devices & services → Add integration and
choose **E-paper Display Hub**.

This repository has not been published yet, so the HACS URL above is the intended
future location rather than a claim that installation is currently available.

## Pair a display

1. Open the E-paper Display Hub integration and choose **Add entry → Display**.
2. Enter a friendly name. Home Assistant shows a temporary eight-digit code.
3. In the display web UI enter the Home Assistant local URL, the code, and friendly
   name. The firmware sends its MAC, model, hardware variant, firmware, and protocol.
4. Verify the device details shown in Home Assistant and confirm them.
5. The display claims its own random key. The pairing code and claim transaction then
   expire.

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
automatic OTA, default refresh interval, and partial-refresh count. The desired
revision advances immediately in Home Assistant, but a sleeping device may not apply
it until a later wake. The **Configuration pending** binary sensor makes that delay
visible.

Telemetry is not restored as if it were current. The Recorder keeps history, while
after a Home Assistant restart the integration waits for a fresh device check-in.
Only security state, desired/reported revisions, content selection, optional sensor
capabilities, and pending commands are persisted.

## Security limitations

- HMAC provides authentication and integrity, not encryption. Local HTTP is visible
  to passive observers. Use HTTPS with certificate validation for confidentiality.
- Initial key delivery is safe from modification by the short-lived transaction and
  user confirmation, but only HTTPS or an isolated trusted network prevents passive
  key capture.
- Keys are stored in Home Assistant `.storage` and backups. Protect both.
- A device needs trustworthy UTC time for replay protection after full power loss.
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
Local unit tests cover protocol canonicalization, key isolation, replay handling,
pairing expiry, telemetry capability rules, desired/reported revisions, durable
commands, and content normalization. A real Home Assistant runtime is still required
to verify UI flow rendering, Config Subentry lifecycle, Device/Entity Registry
behavior, HTTP routing behind the chosen proxy, and Recorder behavior.

## License

[MIT](LICENSE)
