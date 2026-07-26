"""Forecast coordinator for the Irrigation Scheduler."""

from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.components.weather import (
    DOMAIN as WEATHER_DOMAIN,
    SERVICE_GET_FORECASTS,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import DOMAIN, FORECAST_UPDATE_INTERVAL_MIN

_LOGGER = logging.getLogger(__name__)


class RainCoordinator(DataUpdateCoordinator[float | None]):
    """Fetch today's precipitation probability from a weather entity.

    ``data`` is the probability in percent, or ``None`` when the forecast is
    unavailable. ``None`` must never be treated as "0% chance of rain" -- the
    scheduler skips the rain check entirely rather than guessing.
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

    async def _async_update_data(self) -> float | None:
        """Call weather.get_forecasts and pull out today's rain probability."""
        try:
            response = await self.hass.services.async_call(
                WEATHER_DOMAIN,
                SERVICE_GET_FORECASTS,
                {"type": "daily"},
                target={"entity_id": self.weather_entity_id},
                blocking=True,
                return_response=True,
            )
        except Exception as err:  # noqa: BLE001 - surfaced as UpdateFailed
            raise UpdateFailed(f"Forecast call failed: {err}") from err

        forecasts = (response or {}).get(self.weather_entity_id, {}).get("forecast")
        if not forecasts:
            _LOGGER.debug("No daily forecast returned by %s", self.weather_entity_id)
            return None

        # NOTE: not every integration exposes precipitation_probability. Met.no
        # and AccuWeather do; some national services only give `precipitation`
        # in mm. If you switch provider, extend this rather than assuming.
        probability = forecasts[0].get("precipitation_probability")
        if probability is None:
            _LOGGER.debug(
                "%s does not expose precipitation_probability", self.weather_entity_id
            )
            return None
        return float(probability)
