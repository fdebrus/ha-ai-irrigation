"""Forecast coordinator for the Irrigation Scheduler."""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import TYPE_CHECKING

from homeassistant.components.weather import (
    DOMAIN as WEATHER_DOMAIN,
)
from homeassistant.components.weather import (
    SERVICE_GET_FORECASTS,
)
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import DOMAIN, FORECAST_UPDATE_INTERVAL_MIN
from .models import RainForecast

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)


class RainCoordinator(DataUpdateCoordinator[RainForecast | None]):
    """
    Fetch today's rain outlook from a weather entity.

    ``data`` is a :class:`RainForecast` (probability in percent and/or amount in
    mm), or ``None`` when no forecast is available. ``None`` must never be
    treated as "dry" -- the scheduler skips the rain check rather than guessing.
    """

    def __init__(self, hass: HomeAssistant, weather_entity_id: str) -> None:
        """Initialise the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN} forecast",
            update_interval=timedelta(minutes=FORECAST_UPDATE_INTERVAL_MIN),
        )
        self.weather_entity_id = weather_entity_id

    async def _async_update_data(self) -> RainForecast | None:
        """Call weather.get_forecasts and pull out today's rain outlook."""
        try:
            response = await self.hass.services.async_call(
                WEATHER_DOMAIN,
                SERVICE_GET_FORECASTS,
                {"type": "daily"},
                target={"entity_id": self.weather_entity_id},
                blocking=True,
                return_response=True,
            )
        except Exception as err:
            msg = f"Forecast call failed: {err}"
            raise UpdateFailed(msg) from err

        forecasts = (response or {}).get(self.weather_entity_id, {}).get("forecast")
        if not forecasts:
            _LOGGER.debug("No daily forecast returned by %s", self.weather_entity_id)
            return None

        # Not every provider exposes precipitation_probability. Met.no and
        # AccuWeather do; some national services only give `precipitation` in mm.
        # Read both and let the scheduler pick probability first, mm as fallback.
        today = forecasts[0]
        probability = today.get("precipitation_probability")
        precipitation = today.get("precipitation")
        if probability is None and precipitation is None:
            _LOGGER.debug(
                "%s exposes neither precipitation_probability nor precipitation",
                self.weather_entity_id,
            )
            return None
        return RainForecast(
            probability=None if probability is None else float(probability),
            precipitation_mm=None if precipitation is None else float(precipitation),
        )
