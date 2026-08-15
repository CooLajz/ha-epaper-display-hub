"""UI configuration and per-display Config Subentry flows."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, override

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlowResult,
    ConfigSubentryFlow,
    SubentryFlowResult,
)
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    BooleanSelector,
    EntitySelector,
    EntitySelectorConfig,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectSelector,
    SelectSelectorConfig,
    TextSelector,
)

from .const import (
    CONF_CONTENT,
    CONF_DEVICE_ID,
    CONF_FIRMWARE_VERSION,
    CONF_FRIENDLY_NAME,
    CONF_HARDWARE_VARIANT,
    CONF_MODEL,
    CONF_PROTOCOL_VERSION,
    DEFAULT_DESIRED,
    DEFAULT_WAKE_SCHEDULE,
    DESIRED_AUTO_OTA,
    DESIRED_PARTIAL_REFRESHES,
    DESIRED_SHOW_BATTERY_VOLTAGE,
    DESIRED_WEB_ENABLED,
    DOMAIN,
    SLOT_BOTTOM_LEFT,
    SLOT_BOTTOM_RIGHT,
    SLOT_EXTRA_HUMIDITY,
    SLOT_MAIN,
    SLOT_WEATHER,
    SUBENTRY_TYPE_DISPLAY,
    WAKE_INTERVAL_OPTIONS,
    WAKE_SCHEDULE_FIELD_PREFIX,
)
from .models import ProtocolError

VALUE_TYPES = [
    "auto",
    "temperature",
    "humidity",
    "carbon_dioxide",
    "volatile_organic_compounds",
    "pressure",
    "number",
]


def _value_slot_schema(prefix: str) -> dict[Any, Any]:
    """Build flat selector fields for a display value slot."""
    return {
        vol.Optional(f"{prefix}_entity"): EntitySelector(
            EntitySelectorConfig(domain=["sensor", "number", "input_number"])
        ),
        vol.Optional(f"{prefix}_type", default="auto"): SelectSelector(
            SelectSelectorConfig(options=VALUE_TYPES)
        ),
        vol.Optional(f"{prefix}_label"): TextSelector(),
        vol.Optional(f"{prefix}_decimals", default=1): NumberSelector(
            NumberSelectorConfig(min=0, max=3, step=1, mode=NumberSelectorMode.BOX)
        ),
        vol.Optional(f"{prefix}_unit"): TextSelector(),
    }


def _wake_schedule_schema() -> dict[Any, Any]:
    """Build one constrained interval selector for every local hour."""
    options = [str(value) for value in WAKE_INTERVAL_OPTIONS]
    return {
        vol.Required(
            f"{WAKE_SCHEDULE_FIELD_PREFIX}{hour:02d}",
            default=str(DEFAULT_WAKE_SCHEDULE[str(hour)]),
        ): SelectSelector(SelectSelectorConfig(options=options))
        for hour in range(24)
    }


DISPLAY_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_FRIENDLY_NAME): TextSelector(),
        **_value_slot_schema(SLOT_MAIN),
        **_value_slot_schema(SLOT_BOTTOM_LEFT),
        **_value_slot_schema(SLOT_BOTTOM_RIGHT),
        vol.Optional(SLOT_WEATHER): EntitySelector(
            EntitySelectorConfig(domain="weather")
        ),
        vol.Optional(SLOT_EXTRA_HUMIDITY): EntitySelector(
            EntitySelectorConfig(domain="sensor")
        ),
        vol.Required(
            DESIRED_WEB_ENABLED, default=DEFAULT_DESIRED[DESIRED_WEB_ENABLED]
        ): BooleanSelector(),
        vol.Required(
            DESIRED_SHOW_BATTERY_VOLTAGE,
            default=DEFAULT_DESIRED[DESIRED_SHOW_BATTERY_VOLTAGE],
        ): BooleanSelector(),
        vol.Required(
            DESIRED_AUTO_OTA, default=DEFAULT_DESIRED[DESIRED_AUTO_OTA]
        ): BooleanSelector(),
        vol.Required(
            DESIRED_PARTIAL_REFRESHES,
            default=DEFAULT_DESIRED[DESIRED_PARTIAL_REFRESHES],
        ): NumberSelector(
            NumberSelectorConfig(min=0, max=100, step=1, mode=NumberSelectorMode.BOX)
        ),
        **_wake_schedule_schema(),
    }
)


def _content_from_input(user_input: Mapping[str, Any]) -> dict[str, Any]:
    content: dict[str, Any] = {}
    for slot in (SLOT_MAIN, SLOT_BOTTOM_LEFT, SLOT_BOTTOM_RIGHT):
        entity_id = user_input.get(f"{slot}_entity")
        if entity_id:
            content[slot] = {
                "entity_id": entity_id,
                "type": user_input.get(f"{slot}_type", "auto"),
                "label": user_input.get(f"{slot}_label"),
                "decimals": int(user_input.get(f"{slot}_decimals", 1)),
                "unit": user_input.get(f"{slot}_unit"),
            }
    for slot in (SLOT_WEATHER, SLOT_EXTRA_HUMIDITY):
        if user_input.get(slot):
            content[slot] = user_input[slot]
    return content


def _suggested_values(
    subentry: Any,
    desired: Mapping[str, Any],
    wake_schedule: Mapping[str, Any],
) -> dict[str, Any]:
    values: dict[str, Any] = {CONF_FRIENDLY_NAME: subentry.title, **desired}
    for hour in range(24):
        values[f"{WAKE_SCHEDULE_FIELD_PREFIX}{hour:02d}"] = str(
            wake_schedule.get(str(hour), DEFAULT_WAKE_SCHEDULE[str(hour)])
        )
    content = subentry.data.get(CONF_CONTENT, {})
    for slot in (SLOT_MAIN, SLOT_BOTTOM_LEFT, SLOT_BOTTOM_RIGHT):
        selection = content.get(slot, {})
        for field in ("entity_id", "type", "label", "decimals", "unit"):
            if field in selection and selection[field] is not None:
                suffix = "entity" if field == "entity_id" else field
                values[f"{slot}_{suffix}"] = selection[field]
    for slot in (SLOT_WEATHER, SLOT_EXTRA_HUMIDITY):
        if slot in content:
            values[slot] = content[slot]
    return values


class EpaperHubConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Create exactly one hub and expose display subentry flows."""

    VERSION = 1
    MINOR_VERSION = 1

    @classmethod
    @callback
    @override
    def async_get_supported_subentry_types(
        cls, config_entry: ConfigEntry
    ) -> dict[str, type[ConfigSubentryFlow]]:
        return {SUBENTRY_TYPE_DISPLAY: DisplaySubentryFlow}

    @override
    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured()
        if user_input is not None:
            return self.async_create_entry(title="E-paper Display Hub", data={})
        return self.async_show_form(step_id="user", data_schema=vol.Schema({}))


class DisplaySubentryFlow(ConfigSubentryFlow):
    """Pair, confirm, and reconfigure one physical display."""

    def __init__(self) -> None:
        self._pairing_session: str | None = None
        self._pairing_code: str | None = None

    @property
    def _runtime(self) -> Any:
        return self._get_entry().runtime_data

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        if user_input is not None:
            self._pairing_session, self._pairing_code = self._runtime.pairing.create(
                str(user_input[CONF_FRIENDLY_NAME])
            )
            return await self.async_step_pair()
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({vol.Required(CONF_FRIENDLY_NAME): TextSelector()}),
        )

    async def async_step_pair(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        if self._pairing_session is None or self._pairing_code is None:
            return self.async_abort(reason="pairing_expired")
        candidate = self._runtime.pairing.candidate(self._pairing_session)
        if user_input is not None and candidate is not None:
            return await self.async_step_confirm()
        errors = {"base": "device_not_registered"} if user_input is not None else {}
        return self.async_show_form(
            step_id="pair",
            data_schema=vol.Schema(
                {vol.Required("refresh", default=True): BooleanSelector()}
            ),
            errors=errors,
            description_placeholders={
                "pairing_code": self._pairing_code,
                "register_path": "/api/coolajz_epaper_display_hub/v1/pair/register",
            },
        )

    async def async_step_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        if self._pairing_session is None:
            return self.async_abort(reason="pairing_expired")
        candidate = self._runtime.pairing.candidate(self._pairing_session)
        if candidate is None:
            return self.async_abort(reason="pairing_expired")
        if user_input is not None:
            entry = self._get_entry()
            if any(
                subentry.unique_id == candidate[CONF_DEVICE_ID]
                for subentry in entry.subentries.values()
            ):
                self._runtime.pairing.cancel(self._pairing_session)
                return self.async_abort(reason="already_configured")
            try:
                candidate = await self._runtime.pairing.async_confirm(
                    self._pairing_session
                )
            except ProtocolError as err:
                return self.async_abort(reason=err.code)
            data = {
                CONF_DEVICE_ID: candidate[CONF_DEVICE_ID],
                CONF_MODEL: candidate[CONF_MODEL],
                CONF_HARDWARE_VARIANT: candidate[CONF_HARDWARE_VARIANT],
                CONF_FIRMWARE_VERSION: candidate[CONF_FIRMWARE_VERSION],
                CONF_PROTOCOL_VERSION: candidate[CONF_PROTOCOL_VERSION],
                CONF_CONTENT: {},
            }
            return self.async_create_entry(
                title=candidate[CONF_FRIENDLY_NAME],
                data=data,
                unique_id=candidate[CONF_DEVICE_ID],
            )
        return self.async_show_form(
            step_id="confirm",
            data_schema=vol.Schema(
                {vol.Required("confirm", default=True): BooleanSelector()}
            ),
            description_placeholders={
                "device_id": candidate[CONF_DEVICE_ID],
                "model": candidate[CONF_MODEL],
                "firmware_version": candidate[CONF_FIRMWARE_VERSION],
            },
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        entry = self._get_entry()
        subentry = self._get_reconfigure_subentry()
        record = self._runtime.store.devices.get(subentry.data[CONF_DEVICE_ID])
        if record is None:
            return self.async_abort(reason="unknown_device")
        if user_input is not None:
            desired = {
                key: user_input[key] for key in DEFAULT_DESIRED if key in user_input
            }
            desired[DESIRED_PARTIAL_REFRESHES] = int(desired[DESIRED_PARTIAL_REFRESHES])
            wake_schedule = {
                str(hour): int(user_input[f"{WAKE_SCHEDULE_FIELD_PREFIX}{hour:02d}"])
                for hour in range(24)
            }
            if record.update_configuration(desired, wake_schedule):
                await self._runtime.store.async_save()
            data = dict(subentry.data)
            data[CONF_CONTENT] = _content_from_input(user_input)
            return self.async_update_reload_and_abort(
                entry,
                subentry,
                title=str(user_input[CONF_FRIENDLY_NAME]),
                data=data,
            )
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=self.add_suggested_values_to_schema(
                DISPLAY_SCHEMA,
                _suggested_values(subentry, record.desired, record.wake_schedule),
            ),
        )
