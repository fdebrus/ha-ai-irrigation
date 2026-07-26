"""The Irrigation Scheduler integration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from homeassistant.config_entries import ConfigEntry
from homeassistant.util import dt as dt_util

from .const import (
    CONF_ADOPT_MANUAL_RUNS,
    CONF_DEFAULT_DURATION,
    CONF_DEFAULT_START,
    CONF_VALVE_ENTITY,
    CONF_WEATHER_ENTITY,
    CONF_WEEKDAYS,
    DEFAULT_DURATION_MIN,
    DEFAULT_START_TIME,
    DEFAULT_WEEKDAYS,
    PLATFORMS,
    SUBENTRY_TYPE_ZONE,
)
from .coordinator import RainCoordinator
from .models import HubRuntime, ZoneRuntime
from .scheduler import IrrigationScheduler

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant


@dataclass
class IrrigationRuntimeData:
    """Everything the platforms need, hung off the config entry."""

    hub: HubRuntime
    zones: dict[str, ZoneRuntime]
    scheduler: IrrigationScheduler
    coordinator: RainCoordinator | None


type IrrigationConfigEntry = ConfigEntry[IrrigationRuntimeData]


async def async_setup_entry(hass: HomeAssistant, entry: IrrigationConfigEntry) -> bool:
    """Set up Irrigation Scheduler from a config entry."""
    hub = HubRuntime()

    zones: dict[str, ZoneRuntime] = {}
    for subentry_id, subentry in entry.subentries.items():
        if subentry.subentry_type != SUBENTRY_TYPE_ZONE:
            continue
        data = subentry.data
        start = dt_util.parse_time(data.get(CONF_DEFAULT_START, DEFAULT_START_TIME))
        zones[subentry_id] = ZoneRuntime(
            subentry_id=subentry_id,
            name=subentry.title,
            valve_entity_id=data[CONF_VALVE_ENTITY],
            duration_minutes=data.get(CONF_DEFAULT_DURATION, DEFAULT_DURATION_MIN),
            start_time=start or dt_util.parse_time(DEFAULT_START_TIME),
            weekdays=list(data.get(CONF_WEEKDAYS, DEFAULT_WEEKDAYS)),
            adopt_manual_runs=data.get(CONF_ADOPT_MANUAL_RUNS, False),
        )

    coordinator: RainCoordinator | None = None
    if weather_entity := entry.data.get(CONF_WEATHER_ENTITY):
        coordinator = RainCoordinator(hass, weather_entity)
        # Do not fail setup on a missing forecast -- irrigation should still
        # run, it just will not skip for rain.
        await coordinator.async_config_entry_first_refresh()

    scheduler = IrrigationScheduler(hass, hub, zones, coordinator)

    entry.runtime_data = IrrigationRuntimeData(
        hub=hub, zones=zones, scheduler=scheduler, coordinator=coordinator
    )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    # Started after the platforms so restored entity values (durations, start
    # times) are already in place when the first tick fires.
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
