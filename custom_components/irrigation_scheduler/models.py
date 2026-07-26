"""
Runtime models for the Irrigation Scheduler.

The dataclasses here are the single source of truth at runtime. Subentry data
supplies the *initial* values only; once entities exist, the entities own the
mutable values (enabled / duration / start time) and restore them across
restarts. Keep it that way -- two sources of truth for "how long is zone 3"
is how the old YAML package got confusing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, time, timedelta

from .const import (
    DEFAULT_DURATION_MIN,
    DEFAULT_RAIN_MM_THRESHOLD,
    DEFAULT_RAIN_THRESHOLD,
    WEEKDAY_KEYS,
)


@dataclass
class RainForecast:
    """
    Today's rain outlook, as read from the weather forecast.

    ``probability`` is a percentage; ``precipitation_mm`` is the forecast amount
    in millimetres. Either may be ``None`` when the provider does not expose it.
    Both ``None`` means "no usable forecast" and must never be read as "dry".
    """

    probability: float | None = None
    precipitation_mm: float | None = None


@dataclass
class ZoneRuntime:
    """Mutable runtime state for a single irrigation zone."""

    subentry_id: str
    name: str
    valve_entity_id: str

    # Owned by entities, seeded from subentry data.
    enabled: bool = True
    duration_minutes: int = DEFAULT_DURATION_MIN
    start_time: time = field(default_factory=lambda: time(6, 0))
    weekdays: list[str] = field(default_factory=lambda: list(WEEKDAY_KEYS))

    # Behaviour flags from subentry config.
    adopt_manual_runs: bool = False

    # Live run state.
    running_until: datetime | None = None
    running_source: str | None = None
    last_run: datetime | None = None
    last_skipped_reason: str | None = None
    last_scheduled_date: str | None = None
    # Waiting behind another zone in sequential mode.
    queued: bool = False

    @property
    def is_running(self) -> bool:
        """Return True if this zone currently has a tracked run."""
        return self.running_until is not None

    def next_run(self, now: datetime) -> datetime | None:
        """
        Return the next scheduled start, or None if the zone never runs.

        Purely calendar-based: it ignores the master switch and the rain skip,
        because those are evaluated at fire time, not now.
        """
        if not self.enabled or not self.weekdays:
            return None
        candidate = now.replace(
            hour=self.start_time.hour,
            minute=self.start_time.minute,
            second=0,
            microsecond=0,
        )
        if candidate <= now:
            candidate += timedelta(days=1)
        for _ in range(8):
            if WEEKDAY_KEYS[candidate.weekday()] in self.weekdays:
                return candidate
            candidate += timedelta(days=1)
        return None


@dataclass
class HubRuntime:
    """Mutable runtime state shared by every zone."""

    master_enabled: bool = True
    rain_skip_enabled: bool = True
    rain_threshold: int = DEFAULT_RAIN_THRESHOLD
    # Fallback skip when only precipitation (mm) is forecast, no probability.
    rain_mm_threshold: float = DEFAULT_RAIN_MM_THRESHOLD
    # Run overlapping zones one at a time instead of together.
    sequential: bool = False


def should_start(  # noqa: PLR0911 - guard clauses are the design; see CLAUDE.md invariant 6
    zone: ZoneRuntime,
    hub: HubRuntime,
    now: datetime,
    rain_probability: float | None,
    rain_mm: float | None = None,
) -> tuple[bool, str | None]:
    """
    Decide whether ``zone`` should start right now.

    Returns ``(start, skip_reason)``. This function is deliberately pure so it
    can be unit tested without spinning up Home Assistant -- put new scheduling
    rules here rather than inline in the scheduler tick.

    Rain skip uses probability when the provider gives one; when it does not, it
    falls back to the forecast amount in mm. A missing probability is never read
    as 0% -- it just defers to the mm figure, and if that is missing too the
    zone waters.
    """
    if not hub.master_enabled:
        return False, "master_off"
    if not zone.enabled:
        return False, "zone_disabled"
    if zone.is_running:
        return False, "already_running"
    if WEEKDAY_KEYS[now.weekday()] not in zone.weekdays:
        return False, "not_scheduled_today"
    if (now.hour, now.minute) != (zone.start_time.hour, zone.start_time.minute):
        return False, None
    if zone.last_scheduled_date == now.date().isoformat():
        return False, "already_ran_today"
    if hub.rain_skip_enabled and _rain_expected(hub, rain_probability, rain_mm):
        return False, "rain_expected"
    return True, None


def _rain_expected(
    hub: HubRuntime, probability: float | None, precipitation_mm: float | None
) -> bool:
    """Return True if the forecast crosses the configured rain skip threshold."""
    if probability is not None:
        return probability >= hub.rain_threshold
    if precipitation_mm is not None:
        return precipitation_mm >= hub.rain_mm_threshold
    return False
