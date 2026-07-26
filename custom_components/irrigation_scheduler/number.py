"""Number platform: hub rain threshold, per-zone duration."""

from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.const import PERCENTAGE, UnitOfTime
from homeassistant.helpers.restore_state import RestoreEntity

from .const import RAIN_THRESHOLD_MAX, RAIN_THRESHOLD_MIN
from .entity import IrrigationHubEntity, IrrigationZoneEntity

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

    from . import IrrigationConfigEntry
    from .models import HubState, ZoneState
    from .scheduler import IrrigationScheduler


async def async_setup_entry(
    _hass: HomeAssistant,
    entry: IrrigationConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the numbers."""
    data = entry.runtime_data
    async_add_entities([RainThresholdNumber(data.hub, entry.entry_id)])
    for subentry_id, zone in data.zones.items():
        async_add_entities(
            [ZoneDurationNumber(zone, data.scheduler)], config_subentry_id=subentry_id
        )


class _RestoringNumber(NumberEntity, RestoreEntity):
    """Number whose value survives restarts."""

    _attr_mode = NumberMode.BOX

    async def async_added_to_hass(self) -> None:
        """Restore the previous value."""
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last is not None and last.state not in (None, "unknown", "unavailable"):
            with contextlib.suppress(ValueError):
                self._apply(float(last.state))

    def _apply(self, value: float) -> None:
        raise NotImplementedError

    async def async_set_native_value(self, value: float) -> None:
        """Set a new value."""
        self._apply(value)
        self.async_write_ha_state()


class RainThresholdNumber(IrrigationHubEntity, _RestoringNumber):
    """Rain probability above which scheduled runs are skipped."""

    _attr_native_min_value = RAIN_THRESHOLD_MIN
    _attr_native_max_value = RAIN_THRESHOLD_MAX
    _attr_native_step = 5
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_icon = "mdi:water-percent"

    def __init__(self, hub: HubState, entry_id: str) -> None:
        """Initialise."""
        IrrigationHubEntity.__init__(self, hub, entry_id, "rain_threshold")

    @property
    def native_value(self) -> float:
        """Return the threshold."""
        return self.hub.rain_threshold

    def _apply(self, value: float) -> None:
        self.hub.rain_threshold = int(value)


class ZoneDurationNumber(IrrigationZoneEntity, _RestoringNumber):
    """How long this zone runs, in minutes (within its configured bounds)."""

    _attr_native_step = 1
    _attr_native_unit_of_measurement = UnitOfTime.MINUTES
    _attr_icon = "mdi:timer-outline"

    def __init__(self, zone: ZoneState, scheduler: IrrigationScheduler) -> None:
        """Initialise."""
        IrrigationZoneEntity.__init__(self, zone, "duration")
        self._scheduler = scheduler
        self._attr_native_min_value = zone.spec.min_duration
        self._attr_native_max_value = zone.spec.max_duration

    @property
    def native_value(self) -> float:
        """Return the configured duration."""
        return self.zone.duration_minutes

    def _apply(self, value: float) -> None:
        self.zone.duration_minutes = int(value)
        self._scheduler.recompute_start_times()
