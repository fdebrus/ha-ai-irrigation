"""Config flow for the Irrigation Scheduler."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    ConfigSubentryFlow,
    OptionsFlow,
    SubentryFlowResult,
)
from homeassistant.const import CONF_NAME
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import (
    CONF_ADOPT_MANUAL_RUNS,
    CONF_DEFAULT_DURATION,
    CONF_DEFAULT_START,
    CONF_VALVE_ENTITY,
    CONF_WEATHER_ENTITY,
    CONF_WEEKDAYS,
    DEFAULT_DURATION_MIN,
    DEFAULT_START_TIME,
    DEFAULT_WEEKDAYS,
    DOMAIN,
    MAX_DURATION_MIN,
    MIN_DURATION_MIN,
    SUBENTRY_TYPE_ZONE,
    WEEKDAY_KEYS,
)

HUB_SCHEMA = vol.Schema(
    {
        vol.Optional(CONF_WEATHER_ENTITY): selector.EntitySelector(
            selector.EntitySelectorConfig(domain="weather")
        ),
    }
)


def zone_schema(defaults: dict[str, Any] | None = None) -> vol.Schema:
    """Build the zone form, pre-filled when reconfiguring."""
    defaults = defaults or {}
    return vol.Schema(
        {
            vol.Required(CONF_NAME, default=defaults.get(CONF_NAME, "")): str,
            vol.Required(
                CONF_VALVE_ENTITY, default=defaults.get(CONF_VALVE_ENTITY)
            ): selector.EntitySelector(
                selector.EntitySelectorConfig(domain=["valve", "switch"])
            ),
            vol.Required(
                CONF_DEFAULT_START,
                default=defaults.get(CONF_DEFAULT_START, DEFAULT_START_TIME),
            ): selector.TimeSelector(),
            vol.Required(
                CONF_DEFAULT_DURATION,
                default=defaults.get(CONF_DEFAULT_DURATION, DEFAULT_DURATION_MIN),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=MIN_DURATION_MIN,
                    max=MAX_DURATION_MIN,
                    step=1,
                    mode=selector.NumberSelectorMode.BOX,
                    unit_of_measurement="min",
                )
            ),
            vol.Required(
                CONF_WEEKDAYS, default=defaults.get(CONF_WEEKDAYS, DEFAULT_WEEKDAYS)
            ): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=WEEKDAY_KEYS,
                    multiple=True,
                    translation_key="weekdays",
                    mode=selector.SelectSelectorMode.LIST,
                )
            ),
            vol.Required(
                CONF_ADOPT_MANUAL_RUNS,
                default=defaults.get(CONF_ADOPT_MANUAL_RUNS, False),
            ): selector.BooleanSelector(),
        }
    )


class IrrigationConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the hub config flow."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Create the single hub entry. Zones are added as subentries."""
        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured()

        if user_input is not None:
            return self.async_create_entry(
                title="Irrigation Scheduler", data=user_input
            )
        return self.async_show_form(step_id="user", data_schema=HUB_SCHEMA)

    @classmethod
    @callback
    def async_get_supported_subentry_types(
        cls, _config_entry: ConfigEntry
    ) -> dict[str, type[ConfigSubentryFlow]]:
        """Zones are subentries of the hub."""
        return {SUBENTRY_TYPE_ZONE: ZoneSubentryFlowHandler}

    @staticmethod
    @callback
    def async_get_options_flow(_config_entry: ConfigEntry) -> OptionsFlow:
        """Allow changing the weather entity later."""
        return IrrigationOptionsFlow()


class IrrigationOptionsFlow(OptionsFlow):
    """Edit hub-level settings."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the options form."""
        if user_input is not None:
            self.hass.config_entries.async_update_entry(
                self.config_entry, data={**self.config_entry.data, **user_input}
            )
            return self.async_create_entry(title="", data={})
        return self.async_show_form(
            step_id="init",
            data_schema=self.add_suggested_values_to_schema(
                HUB_SCHEMA, self.config_entry.data
            ),
        )


class ZoneSubentryFlowHandler(ConfigSubentryFlow):
    """Add or reconfigure a single irrigation zone."""

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Add a zone."""
        if user_input is not None:
            return self.async_create_entry(
                title=user_input[CONF_NAME], data=_clean(user_input)
            )
        return self.async_show_form(step_id="user", data_schema=zone_schema())

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Edit an existing zone."""
        subentry = self._get_reconfigure_subentry()
        if user_input is not None:
            return self.async_update_and_abort(
                self._get_entry(),
                subentry,
                title=user_input[CONF_NAME],
                data=_clean(user_input),
            )
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=zone_schema({CONF_NAME: subentry.title, **subentry.data}),
        )


def _clean(user_input: dict[str, Any]) -> dict[str, Any]:
    """Normalise form output before storing it."""
    data = dict(user_input)
    data[CONF_DEFAULT_DURATION] = int(data[CONF_DEFAULT_DURATION])
    return data
