"""Unit tests for the scheduling decision.

These do not need Home Assistant running -- `should_start` is pure. New
scheduling rules belong in models.py so they can be tested here.
"""

from datetime import datetime, time

import pytest

from custom_components.irrigation_scheduler.models import (
    HubRuntime,
    ZoneRuntime,
    should_start,
)

# 2026-07-27 is a Monday.
MONDAY_0600 = datetime(2026, 7, 27, 6, 0)


def make_zone(**kwargs) -> ZoneRuntime:
    """Build a zone that would start at Monday 06:00 by default."""
    defaults = {
        "subentry_id": "zone1",
        "name": "Framboisier",
        "valve_entity_id": "valve.framboisier",
        "start_time": time(6, 0),
        "weekdays": ["mon", "wed", "fri"],
    }
    return ZoneRuntime(**{**defaults, **kwargs})


def test_starts_on_a_scheduled_day_at_the_scheduled_minute():
    start, reason = should_start(make_zone(), HubRuntime(), MONDAY_0600, None)
    assert start is True
    assert reason is None


def test_does_not_start_a_minute_early():
    start, _ = should_start(
        make_zone(), HubRuntime(), MONDAY_0600.replace(minute=59, hour=5), None
    )
    assert start is False


def test_skips_unscheduled_weekday():
    zone = make_zone(weekdays=["tue", "thu"])
    start, reason = should_start(zone, HubRuntime(), MONDAY_0600, None)
    assert (start, reason) == (False, "not_scheduled_today")


@pytest.mark.parametrize(
    ("probability", "expected"), [(80.0, False), (40.0, True), (None, True)]
)
def test_rain_skip(probability, expected):
    """A missing forecast must not be treated as 0% rain."""
    hub = HubRuntime(rain_threshold=60)
    start, _ = should_start(make_zone(), hub, MONDAY_0600, probability)
    assert start is expected


def test_master_off_blocks_everything():
    hub = HubRuntime(master_enabled=False)
    start, reason = should_start(make_zone(), hub, MONDAY_0600, None)
    assert (start, reason) == (False, "master_off")


def test_does_not_double_fire_within_the_same_day():
    zone = make_zone(last_scheduled_date="2026-07-27")
    start, reason = should_start(zone, HubRuntime(), MONDAY_0600, None)
    assert (start, reason) == (False, "already_ran_today")


def test_next_run_skips_to_the_next_scheduled_weekday():
    zone = make_zone(weekdays=["wed"])
    nxt = zone.next_run(MONDAY_0600)
    assert nxt is not None
    assert nxt.strftime("%A %H:%M") == "Wednesday 06:00"
