"""UI configuration and per-display Config Subentry flows."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, override
from urllib.parse import urlsplit

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlowResult,
    ConfigSubentryFlow,
    SubentryFlowResult,
)
from homeassistant.core import callback
from homeassistant.helpers import network
from homeassistant.helpers.aiohttp_client import async_get_clientsession
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
    TimeSelector,
)

from .const import (
    CONF_ALLOW_INSECURE_TLS,
    CONF_CONTENT,
    CONF_DEVICE_ID,
    CONF_DEVICE_IP,
    CONF_FIRMWARE_VERSION,
    CONF_FRIENDLY_NAME,
    CONF_HARDWARE_VARIANT,
    CONF_HUB_URL,
    CONF_MODEL,
    CONF_PAIRING_PIN,
    CONF_PROTOCOL_VERSION,
    CONF_TRANSPORT_SECURITY,
    DEFAULT_DESIRED,
    DEFAULT_OTA_CHECK_TIME,
    DEFAULT_WAKE_SCHEDULE,
    DESIRED_PARTIAL_REFRESHES,
    DOMAIN,
    OTA_CHECK_TIME,
    PROTOCOL_VERSION,
    SLOT_BOTTOM_LEFT,
    SLOT_BOTTOM_RIGHT,
    SLOT_EXTRA_HUMIDITY,
    SLOT_MAIN,
    SLOT_WEATHER,
    SUBENTRY_TYPE_DISPLAY,
    TRANSPORT_HTTP,
    TRANSPORT_HTTPS_INSECURE,
    TRANSPORT_HTTPS_VERIFIED,
    WAKE_INTERVAL_OPTIONS,
    WAKE_SCHEDULE_FIELD_PREFIX,
)
from .models import DeviceRecord, ProtocolError
from .pairing import (
    DeviceIdentity,
    DevicePairingClient,
    new_pairing_credentials,
    normalize_hub_url,
    validate_friendly_name,
    validate_local_ipv4,
    validate_pairing_pin,
)

VALUE_TYPES = [
    "auto",
    "temperature",
    "humidity",
    "carbon_dioxide",
    "volatile_organic_compounds",
    "pressure",
    "number",
]


def _internal_hub_url(hass: Any) -> tuple[str, str]:
    """Return the configured internal HA URL and its derived transport mode."""
    try:
        raw_url = network.get_url(
            hass,
            allow_external=False,
            allow_cloud=False,
        )
    except network.NoURLAvailableError as err:
        raise ProtocolError(
            "hub_url_unavailable", "Home Assistant has no internal URL configured"
        ) from err
    scheme = urlsplit(raw_url).scheme.lower()
    if scheme == "http":
        transport_security = TRANSPORT_HTTP
    elif scheme == "https":
        transport_security = TRANSPORT_HTTPS_VERIFIED
    else:
        raise ProtocolError(
            "hub_url_invalid", "Home Assistant internal URL must use HTTP or HTTPS"
        )
    return normalize_hub_url(raw_url, transport_security), transport_security


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


def _display_schema(automatic_ota_enabled: bool) -> vol.Schema:
    """Build the form, showing OTA time only while automatic OTA is enabled."""
    ota_time_field = (
        {
            vol.Required(
                OTA_CHECK_TIME, default=DEFAULT_OTA_CHECK_TIME
            ): TimeSelector()
        }
        if automatic_ota_enabled
        else {}
    )
    return vol.Schema(
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
            **ota_time_field,
            vol.Required(
                DESIRED_PARTIAL_REFRESHES,
                default=DEFAULT_DESIRED[DESIRED_PARTIAL_REFRESHES],
            ): NumberSelector(
                NumberSelectorConfig(
                    min=0, max=100, step=1, mode=NumberSelectorMode.BOX
                )
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
    ota_check_time: str,
) -> dict[str, Any]:
    values: dict[str, Any] = {
        CONF_FRIENDLY_NAME: subentry.title,
        DESIRED_PARTIAL_REFRESHES: desired.get(
            DESIRED_PARTIAL_REFRESHES,
            DEFAULT_DESIRED[DESIRED_PARTIAL_REFRESHES],
        ),
        OTA_CHECK_TIME: ota_check_time,
    }
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
    """Pair and reconfigure one physical display."""

    def __init__(self) -> None:
        self._identity: DeviceIdentity | None = None
        self._device_ip: str | None = None
        self._pairing_pin: str | None = None
        self._friendly_name: str | None = None
        self._hub_url: str | None = None
        self._transport_security: str | None = None
        self._device_key: str | None = None
        self._transaction_id: str | None = None

    @property
    def _runtime(self) -> Any:
        return self._get_entry().runtime_data

    @property
    def _pairing_client(self) -> DevicePairingClient:
        return DevicePairingClient(async_get_clientsession(self.hass))

    def _is_duplicate(self, device_id: str) -> bool:
        entry = self._get_entry()
        return device_id in self._runtime.store.devices or any(
            subentry.unique_id == device_id for subentry in entry.subentries.values()
        )

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        errors: dict[str, str] = {}
        hub_url = ""
        default_transport: str | None = None
        try:
            hub_url, default_transport = _internal_hub_url(self.hass)
        except ProtocolError as err:
            errors["base"] = err.code
        if user_input is not None:
            try:
                if default_transport is None:
                    raise ProtocolError(
                        "hub_url_unavailable",
                        "Home Assistant has no usable internal URL",
                    )
                friendly_name = validate_friendly_name(
                    str(user_input[CONF_FRIENDLY_NAME])
                )
                device_ip = validate_local_ipv4(str(user_input[CONF_DEVICE_IP]))
                pairing_pin = validate_pairing_pin(str(user_input[CONF_PAIRING_PIN]))
                transport_security = default_transport
                if default_transport == TRANSPORT_HTTPS_VERIFIED and bool(
                    user_input.get(CONF_ALLOW_INSECURE_TLS)
                ):
                    transport_security = TRANSPORT_HTTPS_INSECURE
                identity = await self._pairing_client.async_device_info(device_ip)
                if self._is_duplicate(identity.device_id):
                    return self.async_abort(reason="already_configured")
            except ProtocolError as err:
                errors["base"] = err.code
            else:
                self._friendly_name = friendly_name
                self._device_ip = device_ip
                self._pairing_pin = pairing_pin
                self._hub_url = hub_url
                self._transport_security = transport_security
                self._identity = identity
                return await self._async_complete_pairing(user_input)
        schema_fields: dict[Any, Any] = {
            vol.Required(CONF_FRIENDLY_NAME): TextSelector(),
            vol.Required(CONF_DEVICE_IP): TextSelector(),
            vol.Required(CONF_PAIRING_PIN): TextSelector(),
        }
        if default_transport == TRANSPORT_HTTPS_VERIFIED:
            schema_fields[vol.Optional(CONF_ALLOW_INSECURE_TLS, default=False)] = (
                BooleanSelector()
            )
        schema = vol.Schema(schema_fields)
        if user_input is not None:
            schema = self.add_suggested_values_to_schema(schema, user_input)
        return self.async_show_form(
            step_id="user",
            data_schema=schema,
            errors=errors,
            description_placeholders={"hub_url": hub_url or "—"},
        )

    async def _async_complete_pairing(
        self, user_input: dict[str, Any]
    ) -> SubentryFlowResult:
        """Send stable credentials, verify proof, then persist the display."""
        assert self._identity is not None
        assert self._device_ip is not None
        assert self._pairing_pin is not None
        assert self._friendly_name is not None
        assert self._hub_url is not None
        assert self._transport_security is not None
        if self._is_duplicate(self._identity.device_id):
            return self.async_abort(reason="already_configured")
        if self._device_key is None or self._transaction_id is None:
            self._device_key, self._transaction_id = new_pairing_credentials()
        try:
            await self._pairing_client.async_pair(
                device_ip=self._device_ip,
                identity=self._identity,
                pairing_pin=self._pairing_pin,
                hub_url=self._hub_url,
                transport_security=self._transport_security,
                friendly_name=self._friendly_name,
                device_key=self._device_key,
                transaction_id=self._transaction_id,
            )
        except ProtocolError as err:
            schema_fields: dict[Any, Any] = {
                vol.Required(CONF_FRIENDLY_NAME): TextSelector(),
                vol.Required(CONF_DEVICE_IP): TextSelector(),
                vol.Required(CONF_PAIRING_PIN): TextSelector(),
            }
            if self._hub_url.startswith("https://"):
                schema_fields[vol.Optional(CONF_ALLOW_INSECURE_TLS, default=False)] = (
                    BooleanSelector()
                )
            return self.async_show_form(
                step_id="user",
                data_schema=self.add_suggested_values_to_schema(
                    vol.Schema(schema_fields), user_input
                ),
                errors={"base": err.code},
                description_placeholders={"hub_url": self._hub_url},
            )
        self._runtime.store.devices[self._identity.device_id] = DeviceRecord(
            self._identity.device_id,
            self._device_key,
        )
        await self._runtime.store.async_save()
        return self.async_create_entry(
            title=self._friendly_name,
            data={
                CONF_DEVICE_ID: self._identity.device_id,
                CONF_MODEL: self._identity.model,
                CONF_HARDWARE_VARIANT: self._identity.hardware_variant,
                CONF_FIRMWARE_VERSION: self._identity.firmware_version,
                CONF_PROTOCOL_VERSION: PROTOCOL_VERSION,
                CONF_DEVICE_IP: self._device_ip,
                CONF_HUB_URL: self._hub_url,
                CONF_TRANSPORT_SECURITY: self._transport_security,
                CONF_CONTENT: {},
            },
            unique_id=self._identity.device_id,
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
            configuration_changed = record.update_configuration(desired, wake_schedule)
            ota_settings_changed = record.update_ota_settings(
                record.automatic_ota_enabled,
                user_input.get(OTA_CHECK_TIME, record.ota_check_time),
            )
            if configuration_changed or ota_settings_changed:
                await self._runtime.store.async_save()
                self._runtime.coordinator.async_update_listeners()
            if record.automatic_ota_enabled:
                await self._runtime.coordinator.async_schedule_automatic_ota()
            data = dict(subentry.data)
            data[CONF_CONTENT] = _content_from_input(user_input)
            return self.async_update_and_abort(
                entry,
                subentry,
                title=str(user_input[CONF_FRIENDLY_NAME]),
                data=data,
            )
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=self.add_suggested_values_to_schema(
                _display_schema(record.automatic_ota_enabled),
                _suggested_values(
                    subentry,
                    record.desired,
                    record.wake_schedule,
                    record.ota_check_time,
                ),
            ),
        )
