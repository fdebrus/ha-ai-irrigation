"""
Sequencing and AI-plan validation. Pure functions only.

Two responsibilities, both safety-critical and both testable without HA:

1. `plan_start_times` derives every zone's start time from the sequence, so
   that zones sharing one pump can never be scheduled to run together. Start
   times are DERIVED, never stored and never user-edited.
2. `clamp_zone_plan` / `clamp_rain_threshold` are the only doors through which
   AI output reaches a setpoint. Anything the model returns that is out of
   range, unknown, or missing is replaced by the current value and reported.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import time
from itertools import pairwise
from typing import TYPE_CHECKING, Any

from .models import SchedulePreset

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from .models import HubState, ZoneState


# ---------------------------------------------------------------------------
# Sequencing
# ---------------------------------------------------------------------------
def _to_minutes(value: time) -> int:
    return value.hour * 60 + value.minute


def _to_time(minutes: int) -> time:
    minutes %= 24 * 60
    return time(minutes // 60, minutes % 60)


def plan_start_times(
    zones: Sequence[ZoneState],
    base: time,
    margin_minutes: int,
    *,
    reserve_disabled_slots: bool = True,
) -> dict[str, time]:
    """
    Lay zones out back-to-back from ``base``, in `spec.order`.

    Every zone draws from the same pump, so slots must not overlap. A zone's
    slot is its occupancy plus a margin. Disabled zones keep their slot by
    default so that re-enabling one does not shift everything after it.
    """
    cursor = _to_minutes(base)
    starts: dict[str, time] = {}
    for zone in sorted(zones, key=lambda z: z.spec.order):
        if not zone.enabled and not reserve_disabled_slots:
            continue
        starts[zone.spec.subentry_id] = _to_time(cursor)
        cursor += zone.spec.occupancy_minutes(zone.duration_minutes)
        cursor += margin_minutes
    return starts


def apply_start_times(zones: Sequence[ZoneState], hub: HubState) -> None:
    """
    Recompute and write both slots onto every zone.

    Call this after ANY change to a duration, to the enabled set, or to the
    base times -- otherwise the derived starts go stale and zones can collide.
    """
    morning = plan_start_times(
        zones,
        hub.morning_base,
        hub.sequence_margin_minutes,
        reserve_disabled_slots=hub.reserve_disabled_slots,
    )
    evening = plan_start_times(
        zones,
        hub.evening_base,
        hub.sequence_margin_minutes,
        reserve_disabled_slots=hub.reserve_disabled_slots,
    )
    for zone in zones:
        zone.morning_start = morning.get(zone.spec.subentry_id)
        zone.evening_start = evening.get(zone.spec.subentry_id)


def find_overlaps(zones: Sequence[ZoneState]) -> list[str]:
    """
    Return human-readable descriptions of any colliding slots.

    With derived start times this should always be empty; it is the last net,
    equivalent to `binary_sensor.irrigation_schedule_overlap` in the YAML. A
    non-empty result means a bug, not a misconfiguration.
    """
    intervals: list[tuple[int, int, str]] = []
    for zone in zones:
        if not zone.enabled:
            continue
        occupancy = zone.spec.occupancy_minutes(zone.duration_minutes)
        for slot, start in (
            ("morning", zone.morning_start),
            ("evening", zone.evening_start if zone.second_run else None),
        ):
            if start is None:
                continue
            begin = _to_minutes(start)
            intervals.append((begin, begin + occupancy, f"{zone.spec.name} ({slot})"))

    conflicts: list[str] = []
    intervals.sort()
    for (_a_start, a_end, a_name), (b_start, _b_end, b_name) in pairwise(intervals):
        if b_start < a_end:
            conflicts.append(f"{a_name} overlaps {b_name}")
    return conflicts


# ---------------------------------------------------------------------------
# AI plan validation
# ---------------------------------------------------------------------------
@dataclass
class ZonePlan:
    """A validated, clamped plan for one zone."""

    enabled: bool
    duration_minutes: int
    schedule: SchedulePreset
    second_run: bool


def clamp_zone_plan(  # noqa: PLR0912 - per-field validation is the design; see invariant 7
    raw: Mapping[str, Any] | None,
    zone: ZoneState,
    *,
    allow_enable_change: bool = False,
) -> tuple[ZonePlan, list[str]]:
    """
    Validate one zone's slice of an AI plan against its spec.

    Every field falls back to the zone's CURRENT value rather than to a global
    default, so a partial or malformed response degrades to "change nothing"
    instead of to "reset everything". Returns the plan plus a list of rejection
    messages for the log and the narrative.
    """
    spec = zone.spec
    rejects: list[str] = []
    raw = raw or {}

    # Duration -----------------------------------------------------------
    duration = zone.duration_minutes
    if (value := raw.get("duration_minutes")) is not None:
        try:
            candidate = int(value)
        except (TypeError, ValueError):
            rejects.append(f"{spec.name}: duration {value!r} is not a number")
        else:
            clamped = max(spec.min_duration, min(spec.max_duration, candidate))
            if clamped != candidate:
                rejects.append(
                    f"{spec.name}: duration {candidate} clamped to {clamped} "
                    f"({spec.min_duration}-{spec.max_duration})"
                )
            duration = clamped

    # Schedule -----------------------------------------------------------
    schedule = zone.schedule
    if (value := raw.get("schedule")) is not None:
        try:
            schedule = SchedulePreset(str(value).strip())
        except ValueError:
            rejects.append(f"{spec.name}: unknown schedule {value!r}, kept current")

    # Second run ---------------------------------------------------------
    second_run = zone.second_run
    if (value := raw.get("second_run")) is not None:
        if isinstance(value, bool):
            second_run = value
        else:
            rejects.append(f"{spec.name}: second_run {value!r} is not a boolean")

    # Enabled ------------------------------------------------------------
    # Only zones flagged as seasonal (Gazon) may be switched on or off by the
    # AI. For every other zone this is a user decision.
    enabled = zone.enabled
    if (value := raw.get("enabled")) is not None:
        if not allow_enable_change:
            rejects.append(f"{spec.name}: AI may not enable/disable this zone")
        elif isinstance(value, bool):
            enabled = value
        else:
            rejects.append(f"{spec.name}: enabled {value!r} is not a boolean")

    return ZonePlan(enabled, duration, schedule, second_run), rejects


def clamp_rain_threshold(
    raw: Any, current: int, *, low: int = 50, high: int = 90
) -> tuple[int, list[str]]:
    """Clamp the AI's rain threshold into the safe band."""
    if raw is None:
        return current, []
    try:
        candidate = int(raw)
    except (TypeError, ValueError):
        return current, [f"rain_threshold {raw!r} is not a number"]
    clamped = max(low, min(high, candidate))
    if clamped != candidate:
        return clamped, [f"rain_threshold {candidate} clamped to {clamped}"]
    return clamped, []


def zone_briefing(zone: ZoneState) -> dict[str, Any]:
    """
    Return the facts about one zone that get handed to the model.

    Computed, not prose: litres are derived from hose length and emitter rate
    so the prompt cannot drift out of sync with the physical setup the way the
    hand-written DÉBITS RÉELS block did.
    """
    spec = zone.spec
    low, high = spec.litres_delivered(zone.duration_minutes)
    return {
        "name": spec.name,
        "description": spec.description,
        "driver": spec.driver.value,
        "enabled": zone.enabled,
        "schedule": zone.schedule.value,
        "duration_minutes": zone.duration_minutes,
        "second_run": zone.second_run,
        "outlets": spec.outlets,
        "hose_length_m": spec.hose_length_m,
        "litres_per_run": f"{low}-{high}" + (" per outlet" if spec.outlets > 1 else ""),
        "duration_bounds": [spec.min_duration, spec.max_duration],
        "last_run": zone.last_run.isoformat() if zone.last_run else None,
    }
