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
    """No probability in the daily NOR hourly forecast: None, never 0."""

    async def _forecast(_call: ServiceCall) -> dict:
        return {"weather.home": {"forecast": [{"datetime": "2026-07-27"}]}}

    hass.states.async_set("weather.home", "cloudy")
    hass.services.async_register(
        "weather", "get_forecasts", _forecast, supports_response="only"
    )
    coordinator = RainCoordinator(hass, "weather.home")
    assert await coordinator._async_update_data() is None
    assert coordinator.source is None


async def test_falls_back_to_hourly_max_when_daily_has_no_probability(
    hass: HomeAssistant,
) -> None:
    """Met.no-style provider: daily lacks probability, hourly carries it."""

    async def _forecast(call: ServiceCall) -> dict:
        if call.data.get("type") == "daily":
            return {
                "weather.home": {
                    "forecast": [{"datetime": "2026-07-27", "precipitation": 4.2}]
                }
            }
        return {
            "weather.home": {
                "forecast": [
                    {"datetime": "2026-07-27T06:00", "precipitation_probability": 20},
                    {"datetime": "2026-07-27T07:00", "precipitation_probability": 75},
                    {"datetime": "2026-07-27T08:00", "precipitation_probability": 40},
                ]
            }
        }

    hass.states.async_set("weather.home", "rainy")
    hass.services.async_register(
        "weather", "get_forecasts", _forecast, supports_response="only"
    )
    coordinator = RainCoordinator(hass, "weather.home")
    assert await coordinator._async_update_data() == 75.0  # max over the hours
    assert coordinator.source == "hourly"


async def test_daily_probability_wins_over_hourly(hass: HomeAssistant) -> None:
    """When the daily forecast has a probability, hourly is not consulted."""
    calls: list[str] = []

    async def _forecast(call: ServiceCall) -> dict:
        calls.append(call.data.get("type"))
        return {
            "weather.home": {
                "forecast": [
                    {"datetime": "2026-07-27", "precipitation_probability": 55}
                ]
            }
        }

    hass.states.async_set("weather.home", "cloudy")
    hass.services.async_register(
        "weather", "get_forecasts", _forecast, supports_response="only"
    )
    coordinator = RainCoordinator(hass, "weather.home")
    assert await coordinator._async_update_data() == 55.0
    assert coordinator.source == "daily"
    assert calls == ["daily"]  # no hourly call needed
