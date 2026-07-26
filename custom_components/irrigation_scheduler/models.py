"""
Domain model for the Irrigation Scheduler.

Everything here is pure: no `hass`, no I/O, no entity access. That is what makes
the sequencing and the AI clamping testable without booting Home Assistant, and
it is the main structural rule of this codebase.

Driver *implementations* (which actually call services) live in drivers.py.
This module only knows what kind of driver a zone has, because that determines
how long the zone occupies the shared water source.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from enum import StrEnum

WEEKDAY_KEYS: tuple[str, ...] = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


class DriverType(StrEnum):
    """How a zone is physically started and stopped."""

    VALVE = "valve"
    """A single valve or switch entity. Parking, Entrée, Framboisier."""

    DISTRIBUTOR = "distributor"
    """One valve feeding N sequential outlets. Jardin (GARDENA, 3 outlets).

    The valve opens once; the distributor advances between outlets on a pulse,
    so the zone occupies the water source for N x duration plus the gaps.
    """

    BUTTON = "button"
    """Start/stop buttons with no state feedback. Gazon (Aiper IrriSense).

    There is no entity that reports open/closed, so the run timer is the only
    source of truth. Code must never assume it can read this zone's state.
    """


class SchedulePreset(StrEnum):
    """
    The only day patterns a zone may use.

    Deliberately a closed set: the AI picks from these by key, so a hallucinated
    pattern fails validation instead of silently becoming a new schedule.
    """

    DAILY = "daily"
    WEEKDAYS = "weekdays"
    WEEKENDS = "weekends"
    MON_WED_FRI = "mon_wed_fri"
    TUE_THU_SAT = "tue_thu_sat"
    MON_THU = "mon_thu"


PRESET_WEEKDAYS: dict[SchedulePreset, frozenset[int]] = {
    SchedulePreset.DAILY: frozenset(range(7)),
    SchedulePreset.WEEKDAYS: frozenset({0, 1, 2, 3, 4}),
    SchedulePreset.WEEKENDS: frozenset({5, 6}),
    SchedulePreset.MON_WED_FRI: frozenset({0, 2, 4}),
    SchedulePreset.TUE_THU_SAT: frozenset({1, 3, 5}),
    SchedulePreset.MON_THU: frozenset({0, 3}),
}


@dataclass(frozen=True)
class ZoneSpec:
    """
    Static, user-configured description of a zone.

    Lives in the config subentry. The AI never writes to any of this -- it is
    the ground truth the AI is *told about*, not something it may change.
    """

    subentry_id: str
    name: str
    order: int
    """Position in the watering sequence. Zones share one pump, so this is the
    order in which they are given the water source. Lower runs first."""

    driver: DriverType

    # --- Free-text profile handed to the AI ------------------------------
    description: str = ""
    """What is planted here and its condition, in plain language. This replaces
    the hardcoded ZONES ET PROFILS block from the old YAML prompt."""

    # --- Physical characteristics, used to compute delivered water -------
    hose_length_m: float = 0.0
    emitter_min_lph_per_m: float = 2.0
    emitter_max_lph_per_m: float = 4.0

    # --- Driver parameters ------------------------------------------------
    outlets: int = 1
    outlet_gap_seconds: int = 0
    settle_minutes: int = 0
    """Slack added after the zone releases the water source. 1 for drivers with
    no state feedback, where we cannot confirm the run actually ended."""

    # --- Bounds the AI is clamped to -------------------------------------
    min_duration: int = 5
    max_duration: int = 30

    # --- Flags -----------------------------------------------------------
    seasonal: bool = False
    """Whether the AI may enable/disable this zone (Gazon). For every other zone
    the enabled flag is a user decision -- see invariant 7."""

    adopt_manual_runs: bool = False
    """Adopt a valve opened outside the integration as a tracked run. Default
    off (invariant 5). Never applies to button drivers, which have no state to
    watch."""

    def occupancy_minutes(self, duration_minutes: int) -> int:
        """
        Minutes this zone holds the shared water source for one run.

        One formula covers all three driver types -- verified to reproduce the
        old YAML's separate jardin / gazon / plain expressions exactly.
        """
        gaps = math.ceil((self.outlets - 1) * self.outlet_gap_seconds / 60)
        return self.outlets * duration_minutes + gaps + self.settle_minutes

    def litres_delivered(self, duration_minutes: int) -> tuple[float, float]:
        """
        Low and high estimate of litres delivered by one run.

        Per outlet for a distributor, total for everything else. The AI is given
        these numbers rather than being asked to derive them from hose lengths,
        which is where the old prompt was most likely to drift out of date.
        """
        low = self.hose_length_m * self.emitter_min_lph_per_m * duration_minutes / 60
        high = self.hose_length_m * self.emitter_max_lph_per_m * duration_minutes / 60
        return round(low, 1), round(high, 1)

    @property
    def has_state_feedback(self) -> bool:
        """Whether this zone's real open/closed state can be read."""
        return self.driver is not DriverType.BUTTON


@dataclass
class ZoneState:
    """Mutable per-zone state. Setpoints are AI-writable; run state is not."""

    spec: ZoneSpec

    # --- Setpoints: AI-writable, always via planner.clamp_zone_plan ------
    enabled: bool = True
    duration_minutes: int = 15
    schedule: SchedulePreset = SchedulePreset.DAILY
    second_run: bool = False

    # --- Run state: only the scheduler writes these ----------------------
    running_until: datetime | None = None
    running_source: str | None = None
    last_run: datetime | None = None
    last_skipped_reason: str | None = None
    last_scheduled_slot: str | None = None
    """Guard against double-firing: "<iso date>/morning" or "<iso date>/evening"."""

    # --- Derived by the planner, never stored -----------------------------
    morning_start: time | None = None
    evening_start: time | None = None

    @property
    def is_running(self) -> bool:
        """Whether a tracked run is in progress."""
        return self.running_until is not None

    def runs_on(self, day: date) -> bool:
        """Whether this zone is scheduled on the given day."""
        return day.weekday() in PRESET_WEEKDAYS[self.schedule]

    def next_run(self, now: datetime) -> datetime | None:
        """
        Next scheduled start, ignoring master switch and rain skip.

        Considers both the morning and the evening slot.
        """
        if not self.enabled or self.morning_start is None:
            return None
        slots = [self.morning_start]
        if self.second_run and self.evening_start is not None:
            slots.append(self.evening_start)
        best: datetime | None = None
        for offset in range(8):
            day = (now + timedelta(days=offset)).date()
            if not self.runs_on(day):
                continue
            for slot in slots:
                candidate = datetime.combine(day, slot, tzinfo=now.tzinfo)
                if candidate > now and (best is None or candidate < best):
                    best = candidate
            if best is not None:
                return best
        return None


@dataclass
class HubState:
    """Mutable state shared by every zone."""

    master_enabled: bool = True
    rain_skip_enabled: bool = True
    rain_threshold: int = 65
    ai_enabled: bool = True

    morning_base: time = field(default_factory=lambda: time(5, 30))
    evening_base: time = field(default_factory=lambda: time(19, 0))
    sequence_margin_minutes: int = 5
    reserve_disabled_slots: bool = True
    """Keep a disabled zone's slot in the sequence instead of compressing it.

    Ported deliberately from the YAML: enabling a zone then does not shift every
    later zone's start time. Costs a few idle minutes, buys stability.
    """

    # AI plan bookkeeping
    last_plan_date: date | None = None
    last_plan_narrative: str | None = None
    last_plan_failed: bool = False


# ---------------------------------------------------------------------------
# Fire-time decisions -- pure, so the scheduler tick stays a thin shell
# ---------------------------------------------------------------------------
def slot_due(zone: ZoneState, now: datetime) -> str | None:
    """
    Return which derived slot is due exactly at ``now``, else ``None``.

    Calendar/clock only: the caller still applies the master switch, the rain
    skip and the running check. Returns ``"morning"`` or ``"evening"``.
    """
    if not zone.enabled or zone.morning_start is None:
        return None
    if not zone.runs_on(now.date()):
        return None
    hm = (now.hour, now.minute)
    if hm == (zone.morning_start.hour, zone.morning_start.minute):
        return "morning"
    if (
        zone.second_run
        and zone.evening_start is not None
        and hm == (zone.evening_start.hour, zone.evening_start.minute)
    ):
        return "evening"
    return None


def should_skip_for_rain(
    hub: HubState, rain_probability: float | None, *, raining_now: bool = False
) -> bool:
    """
    Whether a run should be skipped for rain right now.

    Mirrors the YAML: skip when it is currently raining, or when today's
    probability meets the threshold. A missing probability is never read as 0%
    (invariant 4) -- it only defers to the "raining now" signal.
    """
    if not hub.rain_skip_enabled:
        return False
    if raining_now:
        return True
    if rain_probability is None:
        return False
    return rain_probability >= hub.rain_threshold
