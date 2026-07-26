"""Tests for the scheduler's dangerous paths.

Restart recovery, adoption (including that button zones are never adopted),
self-driven suppression, one-pump enforcement, and the tick. The scheduler is
driven directly; time is controlled with freezegun.
"""

from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace
from typing import TYPE_CHECKING

from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.helpers import issue_registry as ir
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.irrigation_scheduler.const import (
    DOMAIN,
    STORAGE_KEY,
    STORAGE_VERSION,
)
from custom_components.irrigation_scheduler.drivers import ButtonDriver, ValveDriver
from custom_components.irrigation_scheduler.models import (
    DriverType,
    HubState,
    SchedulePreset,
    ZoneSpec,
    ZoneState,
)
from custom_components.irrigation_scheduler.scheduler import IrrigationScheduler

if TYPE_CHECKING:
    from datetime import time

    from homeassistant.core import HomeAssistant, ServiceCall


def _valve_zone(sid: str, *, order: int = 1, adopt: bool = False) -> ZoneState:
    spec = ZoneSpec(sid, sid.upper(), order, DriverType.VALVE, adopt_manual_runs=adopt)
    return ZoneState(spec=spec, duration_minutes=15)


def _button_zone(sid: str, *, order: int = 1, adopt: bool = False) -> ZoneState:
    spec = ZoneSpec(
        sid,
        sid.upper(),
        order,
        DriverType.BUTTON,
        settle_minutes=1,
        adopt_manual_runs=adopt,
    )
    return ZoneState(spec=spec, duration_minutes=15)


def _wire(hass: HomeAssistant) -> list[tuple[str, str]]:
    """Register valve + button services following state; record actions."""
    calls: list[tuple[str, str]] = []

    async def _open(call: ServiceCall) -> None:
        eid = call.data[ATTR_ENTITY_ID]
        calls.append(("open", eid))
        hass.states.async_set(eid, "open")

    async def _close(call: ServiceCall) -> None:
        eid = call.data[ATTR_ENTITY_ID]
        calls.append(("close", eid))
        hass.states.async_set(eid, "closed")

    async def _press(call: ServiceCall) -> None:
        calls.append(("press", call.data[ATTR_ENTITY_ID]))

    hass.services.async_register("valve", "open_valve", _open)
    hass.services.async_register("valve", "close_valve", _close)
    hass.services.async_register("button", "press", _press)
    return calls


def _make(
    hass: HomeAssistant,
    zones: dict[str, ZoneState],
    hub: HubState | None = None,
    *,
    pump_sensor_id: str | None = None,
) -> IrrigationScheduler:
    sched = IrrigationScheduler(
        hass, hub or HubState(), zones, None, pump_sensor_id=pump_sensor_id
    )
    drivers = {}
    for zid, zone in zones.items():
        if zone.spec.driver is DriverType.BUTTON:
            drivers[zid] = ButtonDriver(
                hass, f"button.{zid}_start", f"button.{zid}_stop"
            )
        else:
            drivers[zid] = ValveDriver(hass, f"valve.{zid}", sched.mark_self_driven)
    sched.set_drivers(drivers)
    return sched


def _store_run(hass_storage: dict, sid: str, ends_at, source: str = "schedule") -> None:
    hass_storage[STORAGE_KEY] = {
        "version": STORAGE_VERSION,
        "minor_version": 1,
        "key": STORAGE_KEY,
        "data": {"runs": {sid: {"ends_at": ends_at.isoformat(), "source": source}}},
    }


# --- restart recovery (invariant 3) ----------------------------------------
async def test_restart_recovery_closes_an_expired_run(
    hass: HomeAssistant, hass_storage: dict, freezer
) -> None:
    """A stored run past its end closes the valve on startup."""
    freezer.move_to("2026-07-27 06:10:00+00:00")
    zones = {"z1": _valve_zone("z1")}
    calls = _wire(hass)
    hass.states.async_set("valve.z1", "open")
    _store_run(hass_storage, "z1", dt_util.utcnow() - timedelta(minutes=5))

    sched = _make(hass, zones)
    await sched.async_start()
    await hass.async_block_till_done()

    assert ("close", "valve.z1") in calls
    assert zones["z1"].running_until is None
    assert "z1" not in sched._unsub_stop
    await sched.async_shutdown()


async def test_restart_recovery_rearms_a_future_run(
    hass: HomeAssistant, hass_storage: dict, freezer
) -> None:
    """A stored future run re-arms its stop timer and closes when it arrives."""
    freezer.move_to("2026-07-27 06:00:00+00:00")
    zones = {"z1": _valve_zone("z1")}
    calls = _wire(hass)
    hass.states.async_set("valve.z1", "open")
    ends_at = dt_util.utcnow() + timedelta(minutes=10)
    _store_run(hass_storage, "z1", ends_at)

    sched = _make(hass, zones)
    await sched.async_start()
    await hass.async_block_till_done()

    assert "z1" in sched._unsub_stop
    assert zones["z1"].running_until == ends_at
    assert calls == []

    freezer.move_to(ends_at + timedelta(seconds=1))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert ("close", "valve.z1") in calls
    assert zones["z1"].running_until is None
    await sched.async_shutdown()


# --- adoption (invariant 5) ------------------------------------------------
async def test_manual_open_is_adopted_when_enabled(
    hass: HomeAssistant, freezer
) -> None:
    """A valve opened by hand becomes a tracked run when adoption is on."""
    freezer.move_to("2026-07-27 06:00:00+00:00")
    zones = {"z1": _valve_zone("z1", adopt=True)}
    calls = _wire(hass)
    hass.states.async_set("valve.z1", "closed")
    sched = _make(hass, zones)
    await sched.async_start()

    hass.states.async_set("valve.z1", "open")
    await hass.async_block_till_done()

    assert zones["z1"].is_running
    assert zones["z1"].running_source == "adopted"
    assert ("open", "valve.z1") not in calls  # adoption does not re-open
    await sched.async_shutdown()


async def test_manual_open_is_ignored_when_disabled(
    hass: HomeAssistant, freezer
) -> None:
    """With adoption off, a hand-opened valve is left untracked."""
    freezer.move_to("2026-07-27 06:00:00+00:00")
    zones = {"z1": _valve_zone("z1", adopt=False)}
    _wire(hass)
    hass.states.async_set("valve.z1", "closed")
    sched = _make(hass, zones)
    await sched.async_start()

    hass.states.async_set("valve.z1", "open")
    await hass.async_block_till_done()

    assert not zones["z1"].is_running
    await sched.async_shutdown()


async def test_button_zone_is_never_watched_for_adoption(
    hass: HomeAssistant, freezer
) -> None:
    """A button zone has no state, so the adoption listener skips it entirely."""
    freezer.move_to("2026-07-27 06:00:00+00:00")
    zones = {"z1": _button_zone("z1", adopt=True)}
    _wire(hass)
    sched = _make(hass, zones)
    await sched.async_start()

    assert sched._unsub_watch is None  # nothing to watch
    await sched.async_shutdown()


async def test_our_own_valve_open_is_not_adopted(hass: HomeAssistant, freezer) -> None:
    """A valve the scheduler opened itself is not adopted back."""
    freezer.move_to("2026-07-27 06:00:00+00:00")
    zones = {"z1": _valve_zone("z1", adopt=True)}
    _wire(hass)
    hass.states.async_set("valve.z1", "closed")
    sched = _make(hass, zones)
    await sched.async_start()

    await sched.drivers["z1"].async_start()  # marks self-driven, opens the valve
    await hass.async_block_till_done()

    assert not zones["z1"].is_running
    assert "valve.z1" in sched._self_driven
    await sched.async_shutdown()


# --- run control -----------------------------------------------------------
async def test_stop_cancels_the_pending_stop_timer(
    hass: HomeAssistant, freezer
) -> None:
    """Stopping a zone cancels its armed close so it cannot fire later."""
    freezer.move_to("2026-07-27 06:00:00+00:00")
    zones = {"z1": _valve_zone("z1")}
    calls = _wire(hass)
    hass.states.async_set("valve.z1", "closed")
    sched = _make(hass, zones)
    await sched.async_start()

    await sched.async_start_zone("z1", source="manual")
    assert "z1" in sched._unsub_stop
    ends_at = zones["z1"].running_until

    await sched.async_stop_zone("z1")
    assert "z1" not in sched._unsub_stop
    assert zones["z1"].running_until is None

    closes = calls.count(("close", "valve.z1"))
    freezer.move_to(ends_at + timedelta(seconds=1))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert calls.count(("close", "valve.z1")) == closes
    await sched.async_shutdown()


async def test_starting_a_zone_stops_any_other_running_zone(
    hass: HomeAssistant, freezer
) -> None:
    """One pump: starting a second zone stops the first (invariant 2)."""
    freezer.move_to("2026-07-27 06:00:00+00:00")
    zones = {"z1": _valve_zone("z1", order=1), "z2": _valve_zone("z2", order=2)}
    calls = _wire(hass)
    hass.states.async_set("valve.z1", "closed")
    hass.states.async_set("valve.z2", "closed")
    sched = _make(hass, zones)
    await sched.async_start()

    await sched.async_start_zone("z1", source="manual")
    await sched.async_start_zone("z2", source="manual")
    await hass.async_block_till_done()

    assert not zones["z1"].is_running
    assert zones["z2"].is_running
    assert ("close", "valve.z1") in calls
    await sched.async_shutdown()


# --- tick ------------------------------------------------------------------
async def _fire_tick(hass: HomeAssistant) -> None:
    async_fire_time_changed(hass)
    await hass.async_block_till_done()


def _now_time(hass: HomeAssistant) -> time:
    return dt_util.as_local(dt_util.utcnow()).time().replace(second=0, microsecond=0)


async def test_tick_starts_a_zone_whose_slot_is_due(
    hass: HomeAssistant, freezer
) -> None:
    """At a zone's morning start, the tick runs it."""
    freezer.move_to("2026-07-27 06:00:00+00:00")
    zones = {"z1": _valve_zone("z1")}
    zones["z1"].schedule = SchedulePreset.DAILY
    zones["z1"].morning_start = _now_time(hass)
    _wire(hass)
    hass.states.async_set("valve.z1", "closed")
    sched = _make(hass, zones)
    await sched.async_start()

    await _fire_tick(hass)
    assert zones["z1"].is_running
    await sched.async_shutdown()


async def test_tick_skips_for_rain(hass: HomeAssistant, freezer) -> None:
    """A rain probability over the threshold skips the run."""
    freezer.move_to("2026-07-27 06:00:00+00:00")
    zones = {"z1": _valve_zone("z1")}
    zones["z1"].morning_start = _now_time(hass)
    _wire(hass)
    hass.states.async_set("valve.z1", "closed")
    sched = _make(hass, zones, HubState(rain_threshold=65))
    sched.coordinator = SimpleNamespace(data=80.0)
    await sched.async_start()

    await _fire_tick(hass)
    assert not zones["z1"].is_running
    assert zones["z1"].last_skipped_reason == "rain_expected"
    await sched.async_shutdown()


# --- pump watchdog (item 5) ------------------------------------------------
async def test_no_flow_flags_a_dry_pump_past_the_grace_window(
    hass: HomeAssistant, freezer
) -> None:
    """A zone open past the grace window with the pump off flags no-flow."""
    freezer.move_to("2026-07-27 06:00:00+00:00")
    zones = {"z1": _valve_zone("z1")}
    _wire(hass)
    hass.states.async_set("valve.z1", "closed")
    hass.states.async_set("binary_sensor.pump", "off")
    sched = _make(hass, zones, pump_sensor_id="binary_sensor.pump")
    await sched.async_start()

    await sched.async_start_zone("z1")
    assert zones["z1"].is_running
    assert not sched.no_flow  # just started, still inside the grace window

    freezer.tick(timedelta(minutes=4))
    await _fire_tick(hass)
    assert sched.no_flow
    await sched.async_shutdown()


async def test_no_flow_clears_when_the_pump_reports_flow(
    hass: HomeAssistant, freezer
) -> None:
    """The flag clears as soon as the pump sensor turns on."""
    freezer.move_to("2026-07-27 06:00:00+00:00")
    zones = {"z1": _valve_zone("z1")}
    _wire(hass)
    hass.states.async_set("valve.z1", "closed")
    hass.states.async_set("binary_sensor.pump", "off")
    sched = _make(hass, zones, pump_sensor_id="binary_sensor.pump")
    await sched.async_start()
    await sched.async_start_zone("z1")

    freezer.tick(timedelta(minutes=4))
    await _fire_tick(hass)
    assert sched.no_flow

    hass.states.async_set("binary_sensor.pump", "on")
    freezer.tick(timedelta(minutes=1))
    await _fire_tick(hass)
    assert not sched.no_flow
    await sched.async_shutdown()


async def test_no_flow_clears_when_the_zone_stops(hass: HomeAssistant, freezer) -> None:
    """Stopping the last running zone clears the flag immediately."""
    freezer.move_to("2026-07-27 06:00:00+00:00")
    zones = {"z1": _valve_zone("z1")}
    _wire(hass)
    hass.states.async_set("valve.z1", "closed")
    hass.states.async_set("binary_sensor.pump", "off")
    sched = _make(hass, zones, pump_sensor_id="binary_sensor.pump")
    await sched.async_start()
    await sched.async_start_zone("z1")
    freezer.tick(timedelta(minutes=4))
    await _fire_tick(hass)
    assert sched.no_flow

    await sched.async_stop_zone("z1")
    assert not sched.no_flow
    await sched.async_shutdown()


async def test_no_flow_inert_without_a_pump_sensor(
    hass: HomeAssistant, freezer
) -> None:
    """With no pump sensor configured the watchdog never fires."""
    freezer.move_to("2026-07-27 06:00:00+00:00")
    zones = {"z1": _valve_zone("z1")}
    _wire(hass)
    hass.states.async_set("valve.z1", "closed")
    sched = _make(hass, zones)  # no pump_sensor_id
    await sched.async_start()
    await sched.async_start_zone("z1")
    freezer.tick(timedelta(minutes=10))
    await _fire_tick(hass)
    assert not sched.no_flow
    await sched.async_shutdown()


# --- missing hardware entities (item 6) ------------------------------------
async def test_missing_zone_entity_raises_and_clears_a_repair_issue(
    hass: HomeAssistant, freezer
) -> None:
    """A configured valve that does not exist raises a repair issue."""
    freezer.move_to("2026-07-27 06:00:00+00:00")
    zones = {"z1": _valve_zone("z1")}
    _wire(hass)
    # valve.z1 is deliberately never given a state.
    sched = _make(hass, zones)
    await sched.async_start()

    await _fire_tick(hass)
    registry = ir.async_get(hass)
    assert registry.async_get_issue(DOMAIN, "zone_entity_missing_z1") is not None

    # The entity appears -> the issue clears on the next tick.
    hass.states.async_set("valve.z1", "closed")
    freezer.tick(timedelta(minutes=1))
    await _fire_tick(hass)
    assert registry.async_get_issue(DOMAIN, "zone_entity_missing_z1") is None
    await sched.async_shutdown()
