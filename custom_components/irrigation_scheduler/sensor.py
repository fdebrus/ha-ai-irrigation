"""Sensor platform: hub rain probability and daily plan, per-zone status."""

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

from .entity import IrrigationHubEntity, IrrigationZoneEntity

if TYPE_CHECKING:
    from datetime import datetime

    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

    from . import IrrigationConfigEntry
    from .coordinator import RainCoordinator
    from .models import HubState, ZoneState


# All entities read shared runtime_data and push via the dispatcher;
# there is no per-entity I/O to serialise.
PARALLEL_UPDATES = 0


async def async_setup_entry(
    _hass: HomeAssistant,
    entry: IrrigationConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the sensors."""
    data = entry.runtime_data
    hub_entities: list = [DailyPlanSensor(data.hub, entry.entry_id)]
    if data.coordinator is not None:
        hub_entities.append(
            RainProbabilitySensor(data.hub, entry.entry_id, data.coordinator)
        )
    async_add_entities(hub_entities)
    for subentry_id, zone in data.zones.items():
        async_add_entities(
            [
                ZoneMorningStartSensor(zone),
                ZoneEveningStartSensor(zone),
                ZoneNextRunSensor(zone),
                ZoneFinishesAtSensor(zone),
                ZoneStatusSensor(zone),
            ],
            config_subentry_id=subentry_id,
        )


class ZoneMorningStartSensor(IrrigationZoneEntity, SensorEntity):
    """The derived morning start time, HH:MM (read-only)."""

    _attr_icon = "mdi:weather-sunset-up"

    def __init__(self, zone: ZoneState) -> None:
        """Initialise."""
        IrrigationZoneEntity.__init__(self, zone, "morning_start")

    @property
    def native_value(self) -> str | None:
        """Return the morning start as HH:MM."""
        start = self.zone.morning_start
        return start.strftime("%H:%M") if start else None


class ZoneEveningStartSensor(IrrigationZoneEntity, SensorEntity):
    """The derived evening start time, HH:MM (read-only)."""

    _attr_icon = "mdi:weather-sunset-down"

    def __init__(self, zone: ZoneState) -> None:
        """Initialise."""
        IrrigationZoneEntity.__init__(self, zone, "evening_start")

    @property
    def native_value(self) -> str | None:
        """Return the evening start as HH:MM."""
        start = self.zone.evening_start
        return start.strftime("%H:%M") if start else None


class ZoneNextRunSensor(IrrigationZoneEntity, SensorEntity):
    """When this zone is next scheduled to start."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_icon = "mdi:calendar-clock"

    def __init__(self, zone: ZoneState) -> None:
        """Initialise."""
        IrrigationZoneEntity.__init__(self, zone, "next_run")

    @property
    def native_value(self) -> datetime | None:
        """Return the next scheduled start."""
        return self.zone.next_run(dt_util.now())


class ZoneFinishesAtSensor(IrrigationZoneEntity, SensorEntity):
    """When the current run ends. Unknown when idle."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_icon = "mdi:timer-sand"

    def __init__(self, zone: ZoneState) -> None:
        """Initialise."""
        IrrigationZoneEntity.__init__(self, zone, "finishes_at")

    @property
    def native_value(self) -> datetime | None:
        """Return the end of the current run."""
        return self.zone.running_until


class ZoneStatusSensor(IrrigationZoneEntity, SensorEntity):
    """Human-readable zone status."""

    _attr_icon = "mdi:information-outline"

    def __init__(self, zone: ZoneState) -> None:
        """Initialise."""
        IrrigationZoneEntity.__init__(self, zone, "status")

    @property
    def native_value(self) -> str:
        """Return running / idle, or the last skip reason."""
        if self.zone.is_running:
            return f"running ({self.zone.running_source})"
        if self.zone.last_skipped_reason:
            return self.zone.last_skipped_reason
        return "idle"

    @property
    def extra_state_attributes(self) -> dict[str, str | None]:
        """Expose raw fields for templating."""
        return {
            "driver": self.zone.spec.driver.value,
            "last_run": (
                self.zone.last_run.isoformat() if self.zone.last_run else None
            ),
        }


class RainProbabilitySensor(
    CoordinatorEntity["RainCoordinator"], IrrigationHubEntity, SensorEntity
):
    """Today's forecast rain probability, as used by the rain skip."""

    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:weather-pouring"

    def __init__(
        self, hub: HubState, entry_id: str, coordinator: RainCoordinator
    ) -> None:
        """Initialise."""
        CoordinatorEntity.__init__(self, coordinator)
        IrrigationHubEntity.__init__(self, hub, entry_id, "rain_probability")

    @property
    def native_value(self) -> float | None:
        """Return the probability, or None when no forecast is available."""
        return self.coordinator.data

    @property
    def extra_state_attributes(self) -> dict[str, str | None]:
        """Say where the probability came from (daily/hourly forecast)."""
        return {"source": self.coordinator.source}


class DailyPlanSensor(IrrigationHubEntity, SensorEntity):
    """The date of the last AI plan; the narrative lives in an attribute."""

    _attr_icon = "mdi:clipboard-text-clock"

    def __init__(self, hub: HubState, entry_id: str) -> None:
        """Initialise."""
        IrrigationHubEntity.__init__(self, hub, entry_id, "daily_plan")

    @property
    def native_value(self) -> str | None:
        """Return the plan date, or None before the first run."""
        return self.hub.last_plan_date.isoformat() if self.hub.last_plan_date else None

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        """Full narrative and rejections -- no 255-char truncation."""
        return {
            "narrative": self.hub.last_plan_narrative,
            "rejections": self.hub.last_plan_rejections,
            "generated_at": (
                self.hub.last_plan_generated_at.isoformat()
                if self.hub.last_plan_generated_at
                else None
            ),
            "stale": self.hub.last_plan_failed,
        }
