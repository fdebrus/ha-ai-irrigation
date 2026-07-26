"""
Config flow: the hub, and a driver-specific zone subentry flow.

The hub carries the shared settings (weather, pump sensor, AI task, base times,
margin). Each zone is a subentry whose second step depends on its driver: a
valve entity; a valve plus outlet count and gap; or a start and a stop button.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigFlow,
    ConfigSubentryFlow,
    OptionsFlow,
)
from homeassistant.const import CONF_NAME
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import (
    CONF_ADOPT_MANUAL_RUNS,
    CONF_AI_TASK_ENTITY,
    CONF_DEFAULT_DURATION,
    CONF_DEFAULT_SCHEDULE,
    CONF_DESCRIPTION,
    CONF_DRIVER,
    CONF_EMITTER_MAX,
    CONF_EMITTER_MIN,
    CONF_EVENING_BASE,
    CONF_HOSE_LENGTH,
    CONF_MARGIN_MINUTES,
    CONF_MAX_DURATION,
    CONF_MIN_DURATION,
    CONF_MORNING_BASE,
    CONF_ORDER,
    CONF_OUTLET_GAP,
    CONF_OUTLETS,
    CONF_PLAN_AT,
    CONF_PUMP_SENSOR,
    CONF_SEASONAL,
    CONF_START_BUTTON,
    CONF_STOP_BUTTON,
    CONF_VALVE_ENTITY,
    CONF_WEATHER_ENTITY,
    DEFAULT_DURATION,
    DEFAULT_EMITTER_MAX,
    DEFAULT_EMITTER_MIN,
    DEFAULT_EVENING_BASE,
    DEFAULT_MARGIN_MINUTES,
    DEFAULT_MAX_DURATION,
    DEFAULT_MIN_DURATION,
    DEFAULT_MORNING_BASE,
    DEFAULT_OUTLET_GAP,
    DEFAULT_OUTLETS,
    DEFAULT_PLAN_AT,
    DOMAIN,
    DURATION_HARD_MAX,
    DURATION_HARD_MIN,
    SUBENTRY_TYPE_ZONE,
)
from .models import DriverType, SchedulePreset

if TYPE_CHECKING:
    from homeassistant.config_entries import (
        ConfigEntry,
        ConfigFlowResult,
        SubentryFlowResult,
    )

_INT_FIELDS = (
    CONF_ORDER,
    CONF_OUTLETS,
    CONF_OUTLET_GAP,
    CONF_DEFAULT_DURATION,
    CONF_MIN_DURATION,
    CONF_MAX_DURATION,
)


def _number(low: float, high: float, step: float, unit: str | None = None):  # noqa: ANN202
    return selector.NumberSelector(
        selector.NumberSelectorConfig(
            min=low,
            max=high,
            step=step,
            mode=selector.NumberSelectorMode.BOX,
            unit_of_measurement=unit,
        )
    )


HUB_SCHEMA = vol.Schema(
    {
        vol.Optional(CONF_WEATHER_ENTITY): selector.EntitySelector(
            selector.EntitySelectorConfig(domain="weather")
        ),
        vol.Optional(CONF_PUMP_SENSOR): selector.EntitySelector(
            selector.EntitySelectorConfig(domain="binary_sensor")
        ),
        vol.Optional(CONF_AI_TASK_ENTITY): selector.EntitySelector(
            selector.EntitySelectorConfig(domain="ai_task")
        ),
        vol.Required(CONF_MORNING_BASE, default=DEFAULT_MORNING_BASE): (
            selector.TimeSelector()
        ),
        vol.Required(CONF_EVENING_BASE, default=DEFAULT_EVENING_BASE): (
            selector.TimeSelector()
        ),
        vol.Required(CONF_PLAN_AT, default=DEFAULT_PLAN_AT): selector.TimeSelector(),
        vol.Required(CONF_MARGIN_MINUTES, default=DEFAULT_MARGIN_MINUTES): _number(
            0, 30, 1, "min"
        ),
    }
)


def _zone_common_schema(defaults: dict[str, Any]) -> vol.Schema:
    """Build the driver-independent first step of the zone form."""
    return vol.Schema(
        {
            vol.Required(CONF_NAME, default=defaults.get(CONF_NAME, "")): str,
            vol.Required(
                CONF_DRIVER, default=defaults.get(CONF_DRIVER, DriverType.VALVE.value)
            ): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=[d.value for d in DriverType],
                    translation_key="driver",
                )
            ),
            vol.Required(CONF_ORDER, default=defaults.get(CONF_ORDER, 1)): _number(
                1, 20, 1
            ),
            vol.Optional(
                CONF_DESCRIPTION, default=defaults.get(CONF_DESCRIPTION, "")
            ): selector.TextSelector(selector.TextSelectorConfig(multiline=True)),
            vol.Required(
                CONF_HOSE_LENGTH, default=defaults.get(CONF_HOSE_LENGTH, 0.0)
            ): _number(0, 100, 0.5, "m"),
            vol.Required(
                CONF_EMITTER_MIN,
                default=defaults.get(CONF_EMITTER_MIN, DEFAULT_EMITTER_MIN),
            ): _number(0, 20, 0.5, "L/h/m"),
            vol.Required(
                CONF_EMITTER_MAX,
                default=defaults.get(CONF_EMITTER_MAX, DEFAULT_EMITTER_MAX),
            ): _number(0, 20, 0.5, "L/h/m"),
            vol.Required(
                CONF_DEFAULT_DURATION,
                default=defaults.get(CONF_DEFAULT_DURATION, DEFAULT_DURATION),
            ): _number(DURATION_HARD_MIN, DURATION_HARD_MAX, 1, "min"),
            vol.Required(
                CONF_MIN_DURATION,
                default=defaults.get(CONF_MIN_DURATION, DEFAULT_MIN_DURATION),
            ): _number(DURATION_HARD_MIN, DURATION_HARD_MAX, 1, "min"),
            vol.Required(
                CONF_MAX_DURATION,
                default=defaults.get(CONF_MAX_DURATION, DEFAULT_MAX_DURATION),
            ): _number(DURATION_HARD_MIN, DURATION_HARD_MAX, 1, "min"),
            vol.Required(
                CONF_DEFAULT_SCHEDULE,
                default=defaults.get(CONF_DEFAULT_SCHEDULE, SchedulePreset.DAILY.value),
            ): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=[s.value for s in SchedulePreset],
                    translation_key="schedule",
                )
            ),
            vol.Required(
                CONF_SEASONAL, default=defaults.get(CONF_SEASONAL, False)
            ): selector.BooleanSelector(),
            vol.Required(
                CONF_ADOPT_MANUAL_RUNS,
                default=defaults.get(CONF_ADOPT_MANUAL_RUNS, False),
            ): selector.BooleanSelector(),
        }
    )


def _valve_schema(defaults: dict[str, Any]) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(
                CONF_VALVE_ENTITY, default=defaults.get(CONF_VALVE_ENTITY)
            ): selector.EntitySelector(
                selector.EntitySelectorConfig(
                    domain=["valve", "switch", "input_boolean"]
                )
            ),
        }
    )


def _distributor_schema(defaults: dict[str, Any]) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(
                CONF_VALVE_ENTITY, default=defaults.get(CONF_VALVE_ENTITY)
            ): selector.EntitySelector(
                selector.EntitySelectorConfig(domain=["valve", "switch"])
            ),
            vol.Required(
                CONF_OUTLETS, default=defaults.get(CONF_OUTLETS, DEFAULT_OUTLETS)
            ): _number(1, 6, 1),
            vol.Required(
                CONF_OUTLET_GAP,
                default=defaults.get(CONF_OUTLET_GAP, DEFAULT_OUTLET_GAP),
            ): _number(0, 60, 1, "s"),
        }
    )


def _buttons_schema(defaults: dict[str, Any]) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(
                CONF_START_BUTTON, default=defaults.get(CONF_START_BUTTON)
            ): selector.EntitySelector(selector.EntitySelectorConfig(domain="button")),
            vol.Required(
                CONF_STOP_BUTTON, default=defaults.get(CONF_STOP_BUTTON)
            ): selector.EntitySelector(selector.EntitySelectorConfig(domain="button")),
        }
    )


def _clean(data: dict[str, Any]) -> dict[str, Any]:
    """Coerce integer-valued selector output before storing."""
    out = dict(data)
    for key in _INT_FIELDS:
        if key in out and out[key] is not None:
            out[key] = int(out[key])
    return out


class IrrigationConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the hub config flow."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Create the single hub entry."""
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
        """Allow editing hub settings later."""
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
    """Add or reconfigure a zone, with a driver-specific second step."""

    _common: dict[str, Any]
    _reconfigure: bool = False

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """First step: the driver-independent fields."""
        self._reconfigure = False
        if user_input is not None:
            self._common = user_input
            return await self._async_driver_step()
        return self.async_show_form(step_id="user", data_schema=_zone_common_schema({}))

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """First step when editing an existing zone."""
        self._reconfigure = True
        subentry = self._get_reconfigure_subentry()
        if user_input is not None:
            self._common = user_input
            return await self._async_driver_step()
        defaults = {CONF_NAME: subentry.title, **subentry.data}
        return self.async_show_form(
            step_id="reconfigure", data_schema=_zone_common_schema(defaults)
        )

    async def _async_driver_step(self) -> SubentryFlowResult:
        driver = self._common[CONF_DRIVER]
        if driver == DriverType.DISTRIBUTOR.value:
            return await self.async_step_distributor()
        if driver == DriverType.BUTTON.value:
            return await self.async_step_buttons()
        return await self.async_step_valve()

    def _defaults(self) -> dict[str, Any]:
        if not self._reconfigure:
            return {}
        return dict(self._get_reconfigure_subentry().data)

    async def async_step_valve(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Second step for a plain valve zone."""
        if user_input is not None:
            return self._finish(user_input)
        return self.async_show_form(
            step_id="valve", data_schema=_valve_schema(self._defaults())
        )

    async def async_step_distributor(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Second step for a distributor zone."""
        if user_input is not None:
            return self._finish(user_input)
        return self.async_show_form(
            step_id="distributor", data_schema=_distributor_schema(self._defaults())
        )

    async def async_step_buttons(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Second step for a button zone."""
        if user_input is not None:
            return self._finish(user_input)
        return self.async_show_form(
            step_id="buttons", data_schema=_buttons_schema(self._defaults())
        )

    def _finish(self, driver_input: dict[str, Any]) -> SubentryFlowResult:
        data = _clean({**self._common, **driver_input})
        title = data.pop(CONF_NAME)
        if self._reconfigure:
            return self.async_update_and_abort(
                self._get_entry(),
                self._get_reconfigure_subentry(),
                title=title,
                data=data,
            )
        return self.async_create_entry(title=title, data=data)
