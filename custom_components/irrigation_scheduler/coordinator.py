"""Forecast coordinator: today's rain probability from a weather entity."""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import TYPE_CHECKING

from homeassistant.components.weather import DOMAIN as WEATHER_DOMAIN
from homeassistant.components.weather import SERVICE_GET_FORECASTS
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .const import DOMAIN, FORECAST_UPDATE_INTERVAL_MIN

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)


class RainCoordinator(DataUpdateCoordinator[float | None]):
    """
    Fetch today's precipitation probability in percent.

    ``data`` is the probability, or ``None`` when no forecast is available.
    ``None`` must never be read as "0% chance of rain" (invariant 4) -- the
    scheduler bypasses the probability check rather than guessing.
    """

    def __init__(self, hass: HomeAssistant, weather_entity_id: str) -> None:
        """Initialise for ``weather_entity_id``."""
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN} forecast",
            update_interval=timedelta(minutes=FORECAST_UPDATE_INTERVAL_MIN),
        )
        self.weather_entity_id = weather_entity_id
        self._warned = False
        # Where the probability came from: "daily", "hourly" or None. Shown as
        # a sensor attribute so an Unknown probability is diagnosable.
        self.source: str | None = None

    async def _async_update_data(self) -> float | None:
        """
        Call weather.get_forecasts and pull today's rain probability.

        The daily forecast is tried first; if the provider publishes no
        ``precipitation_probability`` there (Met.no's daily forecast, for one),
        fall back to the hourly forecast and take the maximum over the next 24
        hours.

        A forecast is best-effort. Any failure -- the weather entity not loaded
        yet, a wrong id, or a provider with no probability at all -- yields
        ``None`` so the scheduler waters without the rain skip (invariant 4).
        It never raises ``UpdateFailed``: an optional signal must not log an
        error on every cycle, and a dropped forecast is the safe direction
        (water, don't skip).
        """
        if self.hass.states.get(self.weather_entity_id) is None:
            # Common at startup: the weather integration has not loaded yet.
            _LOGGER.debug("Weather entity %s not available yet", self.weather_entity_id)
            self.source = None
            return None

        daily = await self._async_get_forecast("daily")
        if daily is None:
            self.source = None
            return None
        self._warned = False
        probability = daily[0].get("precipitation_probability") if daily else None
        if probability is not None:
            self.source = "daily"
            return float(probability)

        # Daily forecast without a probability field: try hourly, max over 24 h.
        hourly = await self._async_get_forecast("hourly") or []
        probabilities = [
            hour["precipitation_probability"]
            for hour in hourly[:24]
            if hour.get("precipitation_probability") is not None
        ]
        if probabilities:
            self.source = "hourly"
            return float(max(probabilities))

        _LOGGER.debug(
            "No precipitation probability from %s (daily or hourly)",
            self.weather_entity_id,
        )
        self.source = None
        return None

    async def _async_get_forecast(self, kind: str) -> list[dict] | None:
        """Fetch one forecast type; None on failure (logged once per streak)."""
        try:
            response = await self.hass.services.async_call(
                WEATHER_DOMAIN,
                SERVICE_GET_FORECASTS,
                {"type": kind},
                target={"entity_id": self.weather_entity_id},
                blocking=True,
                return_response=True,
            )
        except Exception as err:  # noqa: BLE001 - forecast is best-effort
            if not self._warned:
                _LOGGER.warning(
                    "%s forecast from %s unavailable (%s); watering without "
                    "the rain skip until it recovers. Check that this entity "
                    "exists and offers a forecast.",
                    kind.capitalize(),
                    self.weather_entity_id,
                    err,
                )
                self._warned = True
            return None
        return (response or {}).get(self.weather_entity_id, {}).get("forecast")
