"""Tests for the rain forecast coordinator.

Focus on the precipitation fallback (gap 5): the coordinator must surface a
probability when the provider gives one and fall back to the mm amount when it
does not, without ever inventing a "dry" reading.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.core import SupportsResponse

from custom_components.irrigation_scheduler.coordinator import RainCoordinator

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant, ServiceCall

WEATHER = "weather.home"


def _register_forecast(hass: HomeAssistant, forecast: list[dict]) -> None:
    """Register a fake weather.get_forecasts returning ``forecast``."""

    async def _get(_call: ServiceCall) -> dict:
        return {WEATHER: {"forecast": forecast}}

    hass.services.async_register(
        "weather", "get_forecasts", _get, supports_response=SupportsResponse.ONLY
    )


async def _refresh(hass: HomeAssistant, forecast: list[dict]) -> RainCoordinator:
    """Build a coordinator against ``forecast`` and refresh it once."""
    _register_forecast(hass, forecast)
    coordinator = RainCoordinator(hass, WEATHER)
    await coordinator.async_refresh()
    assert coordinator.last_update_success
    return coordinator


async def test_reads_probability_when_present(hass: HomeAssistant) -> None:
    """A probability figure is surfaced as-is; mm stays None."""
    coordinator = await _refresh(hass, [{"precipitation_probability": 70}])
    assert coordinator.data.probability == 70.0
    assert coordinator.data.precipitation_mm is None


async def test_falls_back_to_precipitation_mm(hass: HomeAssistant) -> None:
    """When only precipitation (mm) is given it is surfaced, probability None."""
    coordinator = await _refresh(hass, [{"precipitation": 4.2}])
    assert coordinator.data.probability is None
    assert coordinator.data.precipitation_mm == 4.2


async def test_no_usable_fields_is_none_not_dry(hass: HomeAssistant) -> None:
    """A forecast exposing neither field yields None, never a 0 reading."""
    coordinator = await _refresh(hass, [{"temperature": 21}])
    assert coordinator.data is None


async def test_empty_forecast_is_none(hass: HomeAssistant) -> None:
    """An empty forecast list yields None."""
    coordinator = await _refresh(hass, [])
    assert coordinator.data is None
