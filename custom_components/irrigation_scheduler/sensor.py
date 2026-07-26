"""Sensor platform for the Irrigation Scheduler."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import PERCENTAGE
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .coordinator import RainCoordinator
from .entity import IrrigationHubEntity, IrrigationZoneEntity

if TYPE_CHECKING:
    from datetime import datetime

    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

    from . import IrrigationConfigEntry
    from .models import HubRuntime, ZoneRuntime


async def async_setup_entry(
    _hass: HomeAssistant,
    entry: IrrigationConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the sensors."""
    data = entry.runtime_data
    if data.coordinator is not None:
        async_add_entities(
            [RainProbabilitySensor(data.hub, entry.entry_id, data.coordinator)]
        )
    for subentry_id, zone in data.zones.items():
        async_add_entities(
            [
                ZoneNextRunSensor(zone),
                ZoneFinishesAtSensor(zone),
                ZoneStatusSensor(zone),
            ],
            config_subentry_id=subentry_id,
        )


class ZoneNextRunSensor(IrrigationZoneEntity, SensorEntity):
    """When this zone is next scheduled to start."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_icon = "mdi:calendar-clock"

    def __init__(self, zone: ZoneRuntime) -> None:
        """Initialise."""
        IrrigationZoneEntity.__init__(self, zone, "next_run")

    @property
    def native_value(self) -> datetime | None:
        """Return the next scheduled start."""
        return self.zone.next_run(dt_util.now())


class ZoneFinishesAtSensor(IrrigationZoneEntity, SensorEntity):
    """When the current run ends. Unknown when the zone is idle."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_icon = "mdi:timer-sand"

    def __init__(self, zone: ZoneRuntime) -> None:
        """Initialise."""
        IrrigationZoneEntity.__init__(self, zone, "finishes_at")

    @property
    def native_value(self) -> datetime | None:
        """Return the end of the current run."""
        return self.zone.running_until


class ZoneStatusSensor(IrrigationZoneEntity, SensorEntity):
    """Human-readable zone status."""

    _attr_icon = "mdi:information-outline"

    def __init__(self, zone: ZoneRuntime) -> None:
        """Initialise."""
        IrrigationZoneEntity.__init__(self, zone, "status")

    @property
    def native_value(self) -> str:
        """Return running / idle, plus why the last run was skipped."""
        if self.zone.is_running:
            return f"running ({self.zone.running_source})"
        if self.zone.queued:
            return "queued"
        if self.zone.last_skipped_reason:
            return self.zone.last_skipped_reason
        return "idle"

    @property
    def extra_state_attributes(self) -> dict[str, str | None]:
        """Expose the raw fields for templating and debugging."""
        return {
            "valve_entity_id": self.zone.valve_entity_id,
            "last_run": self.zone.last_run.isoformat() if self.zone.last_run else None,
            "weekdays": ", ".join(self.zone.weekdays),
            "adopt_manual_runs": str(self.zone.adopt_manual_runs),
        }


class RainProbabilitySensor(
    CoordinatorEntity[RainCoordinator], IrrigationHubEntity, SensorEntity
):
    """Today's forecast rain probability, as used by the rain skip."""

    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:weather-pouring"

    def __init__(
        self, hub: HubRuntime, entry_id: str, coordinator: RainCoordinator
    ) -> None:
        """Initialise."""
        CoordinatorEntity.__init__(self, coordinator)
        IrrigationHubEntity.__init__(self, hub, entry_id, "rain_probability")

    @property
    def native_value(self) -> float | None:
        """Return the probability, or None when no forecast is available."""
        return self.coordinator.data
