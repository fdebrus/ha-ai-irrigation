"""
Constants for the Irrigation Scheduler integration.

Keys and defaults only, no logic. The garden's real numbers (bases, durations,
occupancy) come from the reference YAML package and are reproduced by the pure
core in models.py / planner.py.
"""

from __future__ import annotations

from typing import Final

from homeassistant.const import Platform

DOMAIN: Final = "irrigation_scheduler"

PLATFORMS: Final[list[Platform]] = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SWITCH,
    Platform.TIME,
]

SUBENTRY_TYPE_ZONE: Final = "zone"

# --- Hub config keys -------------------------------------------------------
CONF_WEATHER_ENTITY: Final = "weather_entity"
CONF_PUMP_SENSOR: Final = "pump_sensor"
CONF_AI_TASK_ENTITY: Final = "ai_task_entity"
CONF_MORNING_BASE: Final = "morning_base"
CONF_EVENING_BASE: Final = "evening_base"
CONF_PLAN_AT: Final = "plan_at"
CONF_MARGIN_MINUTES: Final = "margin_minutes"

# --- Zone subentry config keys --------------------------------------------
CONF_DRIVER: Final = "driver"
CONF_ORDER: Final = "order"
CONF_DESCRIPTION: Final = "description"
CONF_VALVE_ENTITY: Final = "valve_entity"
CONF_START_BUTTON: Final = "start_button"
CONF_STOP_BUTTON: Final = "stop_button"
CONF_OUTLETS: Final = "outlets"
CONF_OUTLET_GAP: Final = "outlet_gap_seconds"
CONF_SETTLE_MINUTES: Final = "settle_minutes"
CONF_HOSE_LENGTH: Final = "hose_length_m"
CONF_EMITTER_MIN: Final = "emitter_min_lph_per_m"
CONF_EMITTER_MAX: Final = "emitter_max_lph_per_m"
CONF_MIN_DURATION: Final = "min_duration"
CONF_MAX_DURATION: Final = "max_duration"
CONF_SEASONAL: Final = "seasonal"
CONF_ADOPT_MANUAL_RUNS: Final = "adopt_manual_runs"
CONF_DEFAULT_DURATION: Final = "default_duration"
CONF_DEFAULT_SCHEDULE: Final = "default_schedule"

# --- Defaults --------------------------------------------------------------
DEFAULT_MORNING_BASE: Final = "05:30:00"
DEFAULT_EVENING_BASE: Final = "19:00:00"
DEFAULT_PLAN_AT: Final = "22:30:00"
DEFAULT_MARGIN_MINUTES: Final = 5

DEFAULT_RAIN_THRESHOLD: Final = 65
RAIN_THRESHOLD_MIN: Final = 10  # user-facing number entity range
RAIN_THRESHOLD_MAX: Final = 100
AI_RAIN_MIN: Final = 50  # band the AI is clamped to
AI_RAIN_MAX: Final = 90

DEFAULT_DURATION: Final = 15
DEFAULT_MIN_DURATION: Final = 5
DEFAULT_MAX_DURATION: Final = 30
DURATION_HARD_MIN: Final = 1  # widest the duration number entity ever allows
DURATION_HARD_MAX: Final = 60

DEFAULT_OUTLETS: Final = 1
DEFAULT_OUTLET_GAP: Final = 10
DEFAULT_SETTLE_MINUTES: Final = 0
DEFAULT_EMITTER_MIN: Final = 2.0
DEFAULT_EMITTER_MAX: Final = 4.0

# --- Storage ---------------------------------------------------------------
STORAGE_VERSION: Final = 1
STORAGE_KEY: Final = f"{DOMAIN}.state"
STORAGE_KEY_PLAN: Final = f"{DOMAIN}.plan"

# --- Dispatcher ------------------------------------------------------------
SIGNAL_ZONE_UPDATED: Final = f"{DOMAIN}_zone_updated"

# --- Forecast --------------------------------------------------------------
FORECAST_UPDATE_INTERVAL_MIN: Final = 30

# --- Run sources -----------------------------------------------------------
SOURCE_SCHEDULE: Final = "schedule"
SOURCE_MANUAL: Final = "manual"
SOURCE_ADOPTED: Final = "adopted"

# --- Watchdog --------------------------------------------------------------
NO_FLOW_GRACE_MINUTES: Final = 3
