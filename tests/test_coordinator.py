"""Tests for the forecast coordinator.

The forecast is a best-effort, optional signal: any failure must degrade to
``None`` (the scheduler then waters without the rain skip, invariant 4) rather
than raising and logging an error on every cycle.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from custom_components.irrigation_scheduler.coordinator import RainCoordinator

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant, ServiceCall


async def test_missing_weather_entity_yields_none(hass: HomeAssistant) -> None:
    """A weather entity that is not loaded yet gives None, not an error."""
    coordinator = RainCoordinator(hass, "weather.does_not_exist")
    assert await coordinator._async_update_data() is None


async def test_forecast_service_failure_yields_none(hass: HomeAssistant) -> None:
    """A service that raises (e.g. no daily forecast) degrades to None."""

    async def _boom(_call: ServiceCall) -> dict:
        msg = "Service call requested response data but did not match any entities"
        raise RuntimeError(msg)

    hass.states.async_set("weather.home", "sunny")
    hass.services.async_register(
        "weather", "get_forecasts", _boom, supports_response="only"
    )
    coordinator = RainCoordinator(hass, "weather.home")
    assert await coordinator._async_update_data() is None


async def test_probability_is_read_from_the_first_daily_forecast(
    hass: HomeAssistant,
) -> None:
    """A well-formed forecast returns the first day's probability."""

    async def _forecast(_call: ServiceCall) -> dict:
        return {
            "weather.home": {
                "forecast": [
                    {"datetime": "2026-07-27", "precipitation_probability": 70},
                    {"datetime": "2026-07-28", "precipitation_probability": 10},
                ]
            }
        }

    hass.states.async_set("weather.home", "rainy")
    hass.services.async_register(
        "weather", "get_forecasts", _forecast, supports_response="only"
    )
    coordinator = RainCoordinator(hass, "weather.home")
    assert await coordinator._async_update_data() == 70.0


async def test_missing_probability_field_yields_none(hass: HomeAssistant) -> None:
    """A forecast without a probability is None, never 0 (invariant 4)."""

    async def _forecast(_call: ServiceCall) -> dict:
        return {"weather.home": {"forecast": [{"datetime": "2026-07-27"}]}}

    hass.states.async_set("weather.home", "cloudy")
    hass.services.async_register(
        "weather", "get_forecasts", _forecast, supports_response="only"
    )
    coordinator = RainCoordinator(hass, "weather.home")
    assert await coordinator._async_update_data() is None
