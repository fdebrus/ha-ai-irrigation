"""Tests for the scheduler's dangerous paths.

These are the failure modes that make this integration worth more than the YAML
package it replaces: a valve left open across a restart, and manual runs being
silently adopted (or, worse, our own valve commands being adopted back). The
scheduler is driven directly here -- no config entry -- so each behaviour is
isolated. Time is controlled with freezegun; timers are fired with
``async_fire_time_changed`` rather than real waits.
"""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.irrigation_scheduler.const import (
    SOURCE_ADOPTED,
    SOURCE_MANUAL,
    STORAGE_KEY,
    STORAGE_VERSION,
)
from custom_components.irrigation_scheduler.models import HubRuntime, ZoneRuntime
from custom_components.irrigation_scheduler.scheduler import IrrigationScheduler

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant, ServiceCall

VALVE = "valve.test"


def _zone(**kwargs) -> ZoneRuntime:
    """Build a zone backed by ``valve.test``."""
    defaults = {
        "subentry_id": "zone1",
        "name": "Test",
        "valve_entity_id": VALVE,
    }
    return ZoneRuntime(**{**defaults, **kwargs})


def _wire_valve(hass: HomeAssistant) -> list[tuple[str, str]]:
    """Register a fake valve whose state follows open/close commands.

    Returns the list that records ``("open"|"close", entity_id)`` for each call,
    so a test can assert what the scheduler actually sent to the valve.
    """
    calls: list[tuple[str, str]] = []

    async def _open(call: ServiceCall) -> None:
        entity_id = call.data[ATTR_ENTITY_ID]
        calls.append(("open", entity_id))
        hass.states.async_set(entity_id, "open")

    async def _close(call: ServiceCall) -> None:
        entity_id = call.data[ATTR_ENTITY_ID]
        calls.append(("close", entity_id))
        hass.states.async_set(entity_id, "closed")

    hass.services.async_register("valve", "open_valve", _open)
    hass.services.async_register("valve", "close_valve", _close)
    return calls


def _store_run(hass_storage: dict, ends_at, source: str = "schedule") -> None:
    """Seed the run store as if a run was live when HA shut down."""
    hass_storage[STORAGE_KEY] = {
        "version": STORAGE_VERSION,
        "minor_version": 1,
        "key": STORAGE_KEY,
        "data": {"runs": {"zone1": {"ends_at": ends_at.isoformat(), "source": source}}},
    }


# ---------------------------------------------------------------------------
# Restart recovery -- invariant 2
# ---------------------------------------------------------------------------
async def test_restart_recovery_closes_a_run_that_expired_while_down(
    hass: HomeAssistant, hass_storage: dict, freezer
) -> None:
    """A stored run whose end time already passed must close the valve."""
    freezer.move_to("2026-07-27 06:10:00+00:00")
    zone = _zone()
    calls = _wire_valve(hass)
    hass.states.async_set(VALVE, "open")  # still open from before the restart
    _store_run(hass_storage, dt_util.utcnow() - timedelta(minutes=5))

    scheduler = IrrigationScheduler(hass, HubRuntime(), {"zone1": zone}, None)
    await scheduler.async_start()
    await hass.async_block_till_done()

    assert ("close", VALVE) in calls
    assert zone.running_until is None
    assert "zone1" not in scheduler._unsub_stop

    await scheduler.async_shutdown()


async def test_restart_recovery_rearms_a_run_still_in_the_future(
    hass: HomeAssistant, hass_storage: dict, freezer
) -> None:
    """A stored run still in the future must re-arm its stop timer, not reopen."""
    freezer.move_to("2026-07-27 06:00:00+00:00")
    zone = _zone()
    calls = _wire_valve(hass)
    hass.states.async_set(VALVE, "open")
    ends_at = dt_util.utcnow() + timedelta(minutes=10)
    _store_run(hass_storage, ends_at)

    scheduler = IrrigationScheduler(hass, HubRuntime(), {"zone1": zone}, None)
    await scheduler.async_start()
    await hass.async_block_till_done()

    # Re-armed: tracked, no valve command sent for a run that is already live.
    assert "zone1" in scheduler._unsub_stop
    assert zone.running_until == ends_at
    assert calls == []

    # When the end time arrives the re-armed timer closes the valve.
    freezer.move_to(ends_at + timedelta(seconds=1))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()

    assert ("close", VALVE) in calls
    assert zone.running_until is None

    await scheduler.async_shutdown()


# ---------------------------------------------------------------------------
# Manual-run adoption -- invariant 4
# ---------------------------------------------------------------------------
async def test_manual_open_is_adopted_when_enabled(
    hass: HomeAssistant, freezer
) -> None:
    """With adoption on, an external open starts a tracked, closable run."""
    freezer.move_to("2026-07-27 06:00:00+00:00")
    zone = _zone(adopt_manual_runs=True)
    calls = _wire_valve(hass)
    hass.states.async_set(VALVE, "closed")

    scheduler = IrrigationScheduler(hass, HubRuntime(), {"zone1": zone}, None)
    await scheduler.async_start()

    hass.states.async_set(VALVE, "open")  # e.g. HomeKit / Google Home
    await hass.async_block_till_done()

    assert zone.is_running
    assert zone.running_source == SOURCE_ADOPTED
    assert "zone1" in scheduler._unsub_stop
    # Adoption tracks the existing run; it must not command the valve open.
    assert ("open", VALVE) not in calls

    await scheduler.async_shutdown()


async def test_manual_open_is_ignored_when_disabled(
    hass: HomeAssistant, freezer
) -> None:
    """With adoption off, an external open is left untouched."""
    freezer.move_to("2026-07-27 06:00:00+00:00")
    zone = _zone(adopt_manual_runs=False)
    _wire_valve(hass)
    hass.states.async_set(VALVE, "closed")

    scheduler = IrrigationScheduler(hass, HubRuntime(), {"zone1": zone}, None)
    await scheduler.async_start()

    hass.states.async_set(VALVE, "open")
    await hass.async_block_till_done()

    assert not zone.is_running
    assert "zone1" not in scheduler._unsub_stop

    await scheduler.async_shutdown()


async def test_our_own_valve_open_is_not_adopted(hass: HomeAssistant, freezer) -> None:
    """A valve opened by the scheduler itself must not be adopted back.

    ``adopt_manual_runs`` is on and the zone is idle, so without the
    ``_self_driven`` guard the state change would be adopted. The guard must
    suppress it.
    """
    freezer.move_to("2026-07-27 06:00:00+00:00")
    zone = _zone(adopt_manual_runs=True)
    _wire_valve(hass)
    hass.states.async_set(VALVE, "closed")

    scheduler = IrrigationScheduler(hass, HubRuntime(), {"zone1": zone}, None)
    await scheduler.async_start()

    # Drive the valve ourselves without setting up a run first.
    await scheduler._async_set_valve(zone, open_valve=True)
    await hass.async_block_till_done()

    assert not zone.is_running  # not adopted despite adopt=on and valve now open
    assert VALVE in scheduler._self_driven

    await scheduler.async_shutdown()


# ---------------------------------------------------------------------------
# Stop cancels the pending close
# ---------------------------------------------------------------------------
async def test_stopping_a_zone_cancels_its_pending_stop_timer(
    hass: HomeAssistant, freezer
) -> None:
    """Stopping a zone cancels the armed close so it cannot fire later."""
    freezer.move_to("2026-07-27 06:00:00+00:00")
    zone = _zone()
    calls = _wire_valve(hass)
    hass.states.async_set(VALVE, "closed")

    scheduler = IrrigationScheduler(hass, HubRuntime(), {"zone1": zone}, None)
    await scheduler.async_start()

    await scheduler.async_start_zone("zone1", source=SOURCE_MANUAL)
    assert "zone1" in scheduler._unsub_stop
    ends_at = zone.running_until

    await scheduler.async_stop_zone("zone1")
    assert "zone1" not in scheduler._unsub_stop
    assert zone.running_until is None
    assert ("close", VALVE) in calls

    # The cancelled timer must not fire a second close after the old end time.
    closes = calls.count(("close", VALVE))
    freezer.move_to(ends_at + timedelta(seconds=1))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert calls.count(("close", VALVE)) == closes

    await scheduler.async_shutdown()


# ---------------------------------------------------------------------------
# Sequential mode -- gap 2
# ---------------------------------------------------------------------------
def _two_zones() -> dict[str, ZoneRuntime]:
    """Two zones on separate valves."""
    return {
        "zone1": _zone(subentry_id="zone1", valve_entity_id="valve.z1"),
        "zone2": _zone(subentry_id="zone2", valve_entity_id="valve.z2"),
    }


async def test_sequential_mode_queues_an_overlapping_start(
    hass: HomeAssistant, freezer
) -> None:
    """With sequential on, a second start queues and runs when the first stops."""
    freezer.move_to("2026-07-27 06:00:00+00:00")
    zones = _two_zones()
    calls = _wire_valve(hass)
    hass.states.async_set("valve.z1", "closed")
    hass.states.async_set("valve.z2", "closed")

    scheduler = IrrigationScheduler(hass, HubRuntime(sequential=True), zones, None)
    await scheduler.async_start()

    await scheduler.async_start_zone("zone1", source=SOURCE_MANUAL)
    assert zones["zone1"].is_running

    await scheduler.async_start_zone("zone2", source=SOURCE_MANUAL)
    await hass.async_block_till_done()
    # zone2 waits its turn; its valve is not opened yet.
    assert not zones["zone2"].is_running
    assert zones["zone2"].queued
    assert ("open", "valve.z2") not in calls

    # zone1 finishing releases zone2.
    await scheduler.async_stop_zone("zone1")
    await hass.async_block_till_done()
    assert zones["zone2"].is_running
    assert not zones["zone2"].queued
    assert ("open", "valve.z2") in calls

    await scheduler.async_shutdown()


async def test_non_sequential_mode_allows_zones_to_overlap(
    hass: HomeAssistant, freezer
) -> None:
    """With sequential off, overlapping starts both run."""
    freezer.move_to("2026-07-27 06:00:00+00:00")
    zones = _two_zones()
    _wire_valve(hass)
    hass.states.async_set("valve.z1", "closed")
    hass.states.async_set("valve.z2", "closed")

    scheduler = IrrigationScheduler(hass, HubRuntime(sequential=False), zones, None)
    await scheduler.async_start()

    await scheduler.async_start_zone("zone1", source=SOURCE_MANUAL)
    await scheduler.async_start_zone("zone2", source=SOURCE_MANUAL)
    await hass.async_block_till_done()

    assert zones["zone1"].is_running
    assert zones["zone2"].is_running
    assert not zones["zone2"].queued

    await scheduler.async_shutdown()


async def test_adopted_run_bypasses_the_sequential_queue(
    hass: HomeAssistant, freezer
) -> None:
    """An externally opened valve is adopted at once, never queued.

    The valve is already physically open, so deferring it would leave it
    running untracked -- adoption must take effect immediately even mid-run.
    """
    freezer.move_to("2026-07-27 06:00:00+00:00")
    zones = _two_zones()
    zones["zone2"].adopt_manual_runs = True
    _wire_valve(hass)
    hass.states.async_set("valve.z1", "closed")
    hass.states.async_set("valve.z2", "closed")

    scheduler = IrrigationScheduler(hass, HubRuntime(sequential=True), zones, None)
    await scheduler.async_start()

    await scheduler.async_start_zone("zone1", source=SOURCE_MANUAL)
    hass.states.async_set("valve.z2", "open")  # opened by hand, mid zone1 run
    await hass.async_block_till_done()

    assert zones["zone2"].is_running
    assert zones["zone2"].running_source == SOURCE_ADOPTED
    assert not zones["zone2"].queued

    await scheduler.async_shutdown()
