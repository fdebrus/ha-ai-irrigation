"""Constants for the Irrigation Scheduler integration."""

from __future__ import annotations

from typing import Final

from homeassistant.const import Platform

DOMAIN: Final = "irrigation_scheduler"

PLATFORMS: Final[list[Platform]] = [
    Platform.BUTTON,
    Platform.NUMBER,
    Platform.SENSOR,
    Platform.SWITCH,
    Platform.TIME,
]

SUBENTRY_TYPE_ZONE: Final = "zone"

# --- Hub config keys -------------------------------------------------------
CONF_WEATHER_ENTITY: Final = "weather_entity"

# --- Zone subentry config keys --------------------------------------------
CONF_VALVE_ENTITY: Final = "valve_entity"
CONF_WEEKDAYS: Final = "weekdays"
CONF_DEFAULT_DURATION: Final = "default_duration"
CONF_DEFAULT_START: Final = "default_start"
CONF_ADOPT_MANUAL_RUNS: Final = "adopt_manual_runs"

# --- Defaults --------------------------------------------------------------
DEFAULT_DURATION_MIN: Final = 15
DEFAULT_START_TIME: Final = "06:00:00"
DEFAULT_RAIN_THRESHOLD: Final = 60
DEFAULT_RAIN_MM_THRESHOLD: Final = 2.0

MIN_RAIN_MM_THRESHOLD: Final = 0.0
MAX_RAIN_MM_THRESHOLD: Final = 25.0
DEFAULT_WEEKDAYS: Final = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]

MIN_DURATION_MIN: Final = 1
MAX_DURATION_MIN: Final = 120

WEEKDAY_KEYS: Final = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]

# --- Storage ---------------------------------------------------------------
STORAGE_VERSION: Final = 1
STORAGE_KEY: Final = f"{DOMAIN}.runs"

# --- Dispatcher ------------------------------------------------------------
SIGNAL_ZONE_UPDATED: Final = f"{DOMAIN}_zone_updated"

# --- Forecast --------------------------------------------------------------
FORECAST_UPDATE_INTERVAL_MIN: Final = 30

# --- Run sources -----------------------------------------------------------
SOURCE_SCHEDULE: Final = "schedule"
SOURCE_MANUAL: Final = "manual"
SOURCE_ADOPTED: Final = "adopted"
