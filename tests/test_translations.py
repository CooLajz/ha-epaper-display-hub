"""Translation coverage for every pairing config-flow surface."""

from __future__ import annotations

import json
from pathlib import Path

PAIRING_STEPS = {"user"}
PAIRING_FIELDS = {
    "friendly_name",
    "device_ip",
    "pairing_pin",
    "allow_insecure_tls",
}


def test_pairing_translations_exist_in_czech_and_english() -> None:
    translations = (
        Path(__file__).parents[1]
        / "custom_components"
        / "coolajz_epaper_display_hub"
        / "translations"
    )
    for language in ("cs", "en"):
        payload = json.loads((translations / f"{language}.json").read_text())
        display = payload["config_subentries"]["display"]
        assert PAIRING_STEPS <= display["step"].keys()
        translated_fields = {
            key
            for step in PAIRING_STEPS
            for key in display["step"][step]["data"].keys()
        }
        assert PAIRING_FIELDS <= translated_fields
        assert "invalid_proof" in display["error"]
        assert "rate_limited" in display["error"]
        assert "confirm" not in display["step"]
        assert "insecure_warning" not in display["step"]
        assert "command_waiting_for_device" in payload["exceptions"]
        assert "wifi_full_scan" in payload["entity"]["switch"]
