"""Number platform for the Irrigation Scheduler."""

from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.const import PERCENTAGE, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from . import IrrigationConfigEntry
from .const import MAX_DURATION_MIN, MIN_DURATION_MIN
from .entity import IrrigationHubEntity, IrrigationZoneEntity
from .models import HubRuntime, ZoneRuntime


async def async_setup_entry(
    hass: HomeAssistant,
    entry: IrrigationConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the numbers."""
    data = entry.runtime_data
    async_add_entities([RainThresholdNumber(data.hub, entry.entry_id)])
    for subentry_id, zone in data.zones.items():
        async_add_entities(
            [ZoneDurationNumber(zone)], config_subentry_id=subentry_id
        )


class _RestoringNumber(NumberEntity, RestoreEntity):
    """Number whose value survives restarts."""

    _attr_mode = NumberMode.BOX

    async def async_added_to_hass(self) -> None:
        """Restore the previous value."""
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last is not None and last.state not in (None, "unknown", "unavailable"):
            try:
                self._apply(float(last.state))
            except ValueError:
                pass

    def _apply(self, value: float) -> None:
        raise NotImplementedError

    async def async_set_native_value(self, value: float) -> None:
        """Set a new value."""
        self._apply(value)
        self.async_write_ha_state()


class ZoneDurationNumber(IrrigationZoneEntity, _RestoringNumber):
    """How long this zone runs, in minutes."""

    _attr_native_min_value = MIN_DURATION_MIN
    _attr_native_max_value = MAX_DURATION_MIN
    _attr_native_step = 1
    _attr_native_unit_of_measurement = UnitOfTime.MINUTES
    _attr_icon = "mdi:timer-outline"

    def __init__(self, zone: ZoneRuntime) -> None:
        """Initialise."""
        IrrigationZoneEntity.__init__(self, zone, "duration")

    @property
    def native_value(self) -> float:
        """Return the configured duration."""
        return self.zone.duration_minutes

    def _apply(self, value: float) -> None:
        self.zone.duration_minutes = int(value)


class RainThresholdNumber(IrrigationHubEntity, _RestoringNumber):
    """Rain probability above which scheduled runs are skipped."""

    _attr_native_min_value = 10
    _attr_native_max_value = 100
    _attr_native_step = 5
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_icon = "mdi:water-percent"

    def __init__(self, hub: HubRuntime, entry_id: str) -> None:
        """Initialise."""
        IrrigationHubEntity.__init__(self, hub, entry_id, "rain_threshold")

    @property
    def native_value(self) -> float:
        """Return the threshold."""
        return self.hub.rain_threshold

    def _apply(self, value: float) -> None:
        self.hub.rain_threshold = int(value)
