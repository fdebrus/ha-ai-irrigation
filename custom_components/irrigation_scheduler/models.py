"""Runtime models for the Irrigation Scheduler.

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
    DEFAULT_RAIN_THRESHOLD,
    WEEKDAY_KEYS,
)


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

    @property
    def is_running(self) -> bool:
        """Return True if this zone currently has a tracked run."""
        return self.running_until is not None

    def next_run(self, now: datetime) -> datetime | None:
        """Return the next scheduled start, or None if the zone never runs.

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


def should_start(
    zone: ZoneRuntime,
    hub: HubRuntime,
    now: datetime,
    rain_probability: float | None,
) -> tuple[bool, str | None]:
    """Decide whether ``zone`` should start right now.

    Returns ``(start, skip_reason)``. This function is deliberately pure so it
    can be unit tested without spinning up Home Assistant -- put new scheduling
    rules here rather than inline in the scheduler tick.
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
    if (
        hub.rain_skip_enabled
        and rain_probability is not None
        and rain_probability >= hub.rain_threshold
    ):
        return False, "rain_expected"
    return True, None
