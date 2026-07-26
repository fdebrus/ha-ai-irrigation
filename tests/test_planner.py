"""Tests for sequencing and AI-plan clamping."""

from datetime import time

import pytest

from custom_components.irrigation_scheduler.models import (
    DriverType,
    HubState,
    SchedulePreset,
    ZoneSpec,
    ZoneState,
)
from custom_components.irrigation_scheduler.planner import (
    apply_start_times,
    clamp_rain_threshold,
    clamp_zone_plan,
    find_overlaps,
    plan_start_times,
)


def garden() -> list[ZoneState]:
    """The real five-zone layout, in watering order."""
    specs = [
        ZoneSpec(
            "z1",
            "Jardin",
            1,
            DriverType.DISTRIBUTOR,
            hose_length_m=3,
            outlets=3,
            outlet_gap_seconds=10,
            settle_minutes=1,
        ),
        ZoneSpec("z5", "Gazon", 2, DriverType.BUTTON, settle_minutes=1),
        ZoneSpec("z2", "Parking", 3, DriverType.VALVE, hose_length_m=10),
        ZoneSpec("z3", "Entrée", 4, DriverType.VALVE, hose_length_m=17),
        ZoneSpec("z4", "Framboisier", 5, DriverType.VALVE, hose_length_m=5),
    ]
    durations = {"z1": 15, "z5": 20, "z2": 25, "z3": 15, "z4": 20}
    return [ZoneState(spec=s, duration_minutes=durations[s.subentry_id]) for s in specs]


# --- occupancy must reproduce the YAML package exactly ---------------------
@pytest.mark.parametrize("duration", [5, 10, 15, 20, 30])
def test_distributor_occupies_three_slots_plus_gaps(duration):
    spec = ZoneSpec(
        "z1",
        "Jardin",
        1,
        DriverType.DISTRIBUTOR,
        outlets=3,
        outlet_gap_seconds=10,
        settle_minutes=1,
    )
    assert spec.occupancy_minutes(duration) == duration * 3 + 2


def test_button_zone_gets_a_settle_minute():
    spec = ZoneSpec("z5", "Gazon", 2, DriverType.BUTTON, settle_minutes=1)
    assert spec.occupancy_minutes(20) == 21


def test_plain_valve_occupies_its_duration():
    spec = ZoneSpec("z2", "Parking", 3, DriverType.VALVE)
    assert spec.occupancy_minutes(25) == 25


# --- sequencing ------------------------------------------------------------
def test_zones_are_laid_out_in_order_from_the_base_time():
    starts = plan_start_times(garden(), time(5, 30), 5)
    assert starts["z1"] == time(5, 30)
    assert starts["z5"] == time(6, 22)  # 05:30 + (15*3+2) + 5
    assert starts["z2"] == time(6, 48)  # + 21 + 5
    assert starts["z3"] == time(7, 18)  # + 25 + 5
    assert starts["z4"] == time(7, 38)  # + 15 + 5


def test_no_two_zones_ever_share_the_pump():
    zones = garden()
    hub = HubState()
    for zone in zones:
        zone.second_run = True
    apply_start_times(zones, hub)
    assert find_overlaps(zones) == []


def test_longer_durations_push_later_zones_back_not_into_each_other():
    zones = garden()
    zones[0].duration_minutes = 30  # Jardin: 92 min of occupancy
    hub = HubState()
    apply_start_times(zones, hub)
    assert find_overlaps(zones) == []
    assert zones[1].morning_start > time(7, 0)


def test_disabled_zone_keeps_its_slot_by_default():
    zones = garden()
    zones[1].enabled = False  # Gazon dormant
    with_slot = plan_start_times(zones, time(5, 30), 5)
    without = plan_start_times(zones, time(5, 30), 5, reserve_disabled_slots=False)
    assert with_slot["z2"] == time(6, 48)
    assert without["z2"] == time(6, 22)  # compressed


# --- AI clamping -----------------------------------------------------------
def test_out_of_range_duration_is_clamped_and_reported():
    zone = garden()[0]
    plan, rejects = clamp_zone_plan({"duration_minutes": 90}, zone)
    assert plan.duration_minutes == 30
    assert any("clamped" in r for r in rejects)


def test_unknown_schedule_keeps_the_current_one():
    zone = garden()[0]
    zone.schedule = SchedulePreset.MON_WED_FRI
    plan, rejects = clamp_zone_plan({"schedule": "every other tuesday"}, zone)
    assert plan.schedule is SchedulePreset.MON_WED_FRI
    assert rejects


def test_missing_fields_change_nothing():
    zone = garden()[0]
    zone.duration_minutes, zone.second_run = 18, True
    plan, rejects = clamp_zone_plan({}, zone)
    assert (plan.duration_minutes, plan.second_run) == (18, True)
    assert rejects == []


def test_garbage_response_changes_nothing():
    zone = garden()[0]
    zone.duration_minutes = 18
    plan, _ = clamp_zone_plan(None, zone)
    assert plan.duration_minutes == 18


def test_ai_cannot_disable_a_normal_zone():
    zone = garden()[0]
    plan, rejects = clamp_zone_plan({"enabled": False}, zone)
    assert plan.enabled is True
    assert any("may not enable/disable" in r for r in rejects)


def test_ai_can_toggle_a_seasonal_zone():
    gazon = garden()[1]
    plan, rejects = clamp_zone_plan({"enabled": False}, gazon, allow_enable_change=True)
    assert plan.enabled is False
    assert rejects == []


@pytest.mark.parametrize(
    ("raw", "expected"), [(95, 90), (10, 50), (65, 65), ("abc", 65), (None, 65)]
)
def test_rain_threshold_is_clamped(raw, expected):
    assert clamp_rain_threshold(raw, 65)[0] == expected
