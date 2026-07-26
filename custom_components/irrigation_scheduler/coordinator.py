"""Forecast coordinator: today's rain probability from a weather entity."""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import TYPE_CHECKING

from homeassistant.components.weather import DOMAIN as WEATHER_DOMAIN
from homeassistant.components.weather import SERVICE_GET_FORECASTS
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

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

    async def _async_update_data(self) -> float | None:
        """Call weather.get_forecasts and pull today's rain probability."""
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
            _LOGGER.debug("No daily forecast from %s", self.weather_entity_id)
            return None
        probability = forecasts[0].get("precipitation_probability")
        return None if probability is None else float(probability)
