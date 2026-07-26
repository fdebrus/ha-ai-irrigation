"""
The Irrigation Scheduler integration.

Scheduling logic is pure and lives in models.py / planner.py. I/O is split into
drivers.py (valve/distributor/button), scheduler.py (the tick, run/stop, Store),
coordinator.py (forecast) and ai.py (the nightly plan). This module wires them
onto the config entry.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from homeassistant.util import dt as dt_util

from .const import (
    CONF_ADOPT_MANUAL_RUNS,
    CONF_DEFAULT_DURATION,
    CONF_DEFAULT_SCHEDULE,
    CONF_DESCRIPTION,
    CONF_DRIVER,
    CONF_EMITTER_MAX,
    CONF_EMITTER_MIN,
    CONF_EVENING_BASE,
    CONF_HOSE_LENGTH,
    CONF_MARGIN_MINUTES,
    CONF_MAX_DURATION,
    CONF_MIN_DURATION,
    CONF_MORNING_BASE,
    CONF_ORDER,
    CONF_OUTLET_GAP,
    CONF_OUTLETS,
    CONF_PLAN_AT,
    CONF_SEASONAL,
    CONF_SETTLE_MINUTES,
    CONF_START_BUTTON,
    CONF_STOP_BUTTON,
    CONF_VALVE_ENTITY,
    CONF_WEATHER_ENTITY,
    DEFAULT_DURATION,
    DEFAULT_EMITTER_MAX,
    DEFAULT_EMITTER_MIN,
    DEFAULT_EVENING_BASE,
    DEFAULT_MARGIN_MINUTES,
    DEFAULT_MAX_DURATION,
    DEFAULT_MIN_DURATION,
    DEFAULT_MORNING_BASE,
    DEFAULT_OUTLET_GAP,
    DEFAULT_OUTLETS,
    DEFAULT_PLAN_AT,
    DEFAULT_SETTLE_MINUTES,
    PLATFORMS,
    SUBENTRY_TYPE_ZONE,
)
from .coordinator import RainCoordinator
from .drivers import ButtonDriver, DistributorDriver, Driver, ValveDriver
from .models import DriverType, HubState, SchedulePreset, ZoneSpec, ZoneState
from .planner import apply_start_times
from .scheduler import IrrigationScheduler

if TYPE_CHECKING:
    from collections.abc import Mapping
    from typing import Any

    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant


@dataclass
class IrrigationRuntimeData:
    """Everything the platforms need, hung off the config entry."""

    hub: HubState
    zones: dict[str, ZoneState]
    drivers: dict[str, Driver]
    scheduler: IrrigationScheduler
    coordinator: RainCoordinator | None
    ai: object | None = None  # IrrigationAI, attached in async_setup_entry (Phase 6)


type IrrigationConfigEntry = ConfigEntry[IrrigationRuntimeData]


def _parse_time_or(value: str | None, default: str):  # noqa: ANN202 - datetime.time
    return dt_util.parse_time(value or default) or dt_util.parse_time(default)


def _build_zone(subentry_id: str, title: str, data: Mapping[str, Any]) -> ZoneState:
    """Build a zone's spec and initial state from its subentry data."""
    driver = DriverType(data[CONF_DRIVER])
    spec = ZoneSpec(
        subentry_id=subentry_id,
        name=title,
        order=int(data.get(CONF_ORDER, 1)),
        driver=driver,
        description=data.get(CONF_DESCRIPTION, ""),
        hose_length_m=float(data.get(CONF_HOSE_LENGTH, 0.0)),
        emitter_min_lph_per_m=float(data.get(CONF_EMITTER_MIN, DEFAULT_EMITTER_MIN)),
        emitter_max_lph_per_m=float(data.get(CONF_EMITTER_MAX, DEFAULT_EMITTER_MAX)),
        outlets=int(data.get(CONF_OUTLETS, DEFAULT_OUTLETS)),
        outlet_gap_seconds=int(data.get(CONF_OUTLET_GAP, DEFAULT_OUTLET_GAP)),
        settle_minutes=int(data.get(CONF_SETTLE_MINUTES, DEFAULT_SETTLE_MINUTES)),
        min_duration=int(data.get(CONF_MIN_DURATION, DEFAULT_MIN_DURATION)),
        max_duration=int(data.get(CONF_MAX_DURATION, DEFAULT_MAX_DURATION)),
        seasonal=bool(data.get(CONF_SEASONAL, False)),
        adopt_manual_runs=bool(data.get(CONF_ADOPT_MANUAL_RUNS, False)),
    )
    schedule = SchedulePreset(data.get(CONF_DEFAULT_SCHEDULE, SchedulePreset.DAILY))
    return ZoneState(
        spec=spec,
        duration_minutes=int(data.get(CONF_DEFAULT_DURATION, DEFAULT_DURATION)),
        schedule=schedule,
    )


def _build_driver(
    hass: HomeAssistant,
    zone: ZoneState,
    data: Mapping[str, Any],
    mark_self_driven,  # noqa: ANN001 - Callable[[str], None]
) -> Driver:
    """Build the right driver for a zone from its subentry data."""
    if zone.spec.driver is DriverType.BUTTON:
        return ButtonDriver(hass, data[CONF_START_BUTTON], data[CONF_STOP_BUTTON])
    if zone.spec.driver is DriverType.DISTRIBUTOR:
        return DistributorDriver(
            hass,
            data[CONF_VALVE_ENTITY],
            zone.spec.outlets,
            zone.spec.outlet_gap_seconds,
            lambda z=zone: z.duration_minutes,
            mark_self_driven,
        )
    return ValveDriver(hass, data[CONF_VALVE_ENTITY], mark_self_driven)


async def async_setup_entry(hass: HomeAssistant, entry: IrrigationConfigEntry) -> bool:
    """Set up Irrigation Scheduler from a config entry."""
    hub = HubState(
        morning_base=_parse_time_or(
            entry.data.get(CONF_MORNING_BASE), DEFAULT_MORNING_BASE
        ),
        evening_base=_parse_time_or(
            entry.data.get(CONF_EVENING_BASE), DEFAULT_EVENING_BASE
        ),
        plan_at=_parse_time_or(entry.data.get(CONF_PLAN_AT), DEFAULT_PLAN_AT),
        sequence_margin_minutes=int(
            entry.data.get(CONF_MARGIN_MINUTES, DEFAULT_MARGIN_MINUTES)
        ),
    )

    zones: dict[str, ZoneState] = {}
    zone_data: dict[str, Mapping[str, Any]] = {}
    for subentry_id, subentry in entry.subentries.items():
        if subentry.subentry_type != SUBENTRY_TYPE_ZONE:
            continue
        zones[subentry_id] = _build_zone(subentry_id, subentry.title, subentry.data)
        zone_data[subentry_id] = subentry.data

    coordinator: RainCoordinator | None = None
    weather_entity: str | None = entry.data.get(CONF_WEATHER_ENTITY)
    if weather_entity:
        coordinator = RainCoordinator(hass, weather_entity)

    scheduler = IrrigationScheduler(
        hass, hub, zones, coordinator, weather_entity_id=weather_entity
    )
    drivers = {
        zone_id: _build_driver(
            hass, zone, zone_data[zone_id], scheduler.mark_self_driven
        )
        for zone_id, zone in zones.items()
    }
    scheduler.set_drivers(drivers)

    entry.runtime_data = IrrigationRuntimeData(
        hub=hub,
        zones=zones,
        drivers=drivers,
        scheduler=scheduler,
        coordinator=coordinator,
    )

    if coordinator is not None:
        # Do not fail setup on a missing forecast -- irrigation still runs.
        await coordinator.async_refresh()

    apply_start_times(list(zones.values()), hub)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    # Started after platforms so restored entity values are already in place.
    await scheduler.async_start()

    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: IrrigationConfigEntry) -> bool:
    """Unload a config entry."""
    await entry.runtime_data.scheduler.async_shutdown()
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def _async_update_listener(
    hass: HomeAssistant, entry: IrrigationConfigEntry
) -> None:
    """Reload when the hub config or a zone subentry changes."""
    await hass.config_entries.async_reload(entry.entry_id)
