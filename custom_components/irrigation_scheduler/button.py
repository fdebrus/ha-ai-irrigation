"""Button platform for the Irrigation Scheduler."""

from __future__ import annotations

import voluptuous as vol

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv, entity_platform
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import IrrigationConfigEntry
from .const import MAX_DURATION_MIN, MIN_DURATION_MIN, SOURCE_MANUAL
from .entity import IrrigationHubEntity, IrrigationZoneEntity
from .models import HubRuntime, ZoneRuntime
from .scheduler import IrrigationScheduler

SERVICE_RUN_ZONE = "run_zone"
SERVICE_STOP_ZONE = "stop_zone"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: IrrigationConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the buttons and their entity services."""
    data = entry.runtime_data

    platform = entity_platform.async_get_current_platform()
    platform.async_register_entity_service(
        SERVICE_RUN_ZONE,
        {
            vol.Optional("duration"): vol.All(
                cv.positive_int, vol.Range(min=MIN_DURATION_MIN, max=MAX_DURATION_MIN)
            )
        },
        "async_run_zone",
    )
    platform.async_register_entity_service(SERVICE_STOP_ZONE, {}, "async_stop_zone")

    async_add_entities([StopAllButton(data.hub, entry.entry_id, data.scheduler)])
    for subentry_id, zone in data.zones.items():
        async_add_entities(
            [
                ZoneRunNowButton(zone, data.scheduler),
                ZoneStopButton(zone, data.scheduler),
            ],
            config_subentry_id=subentry_id,
        )


class ZoneRunNowButton(IrrigationZoneEntity, ButtonEntity):
    """Start this zone immediately for its configured duration."""

    _attr_icon = "mdi:play-circle-outline"

    def __init__(self, zone: ZoneRuntime, scheduler: IrrigationScheduler) -> None:
        """Initialise."""
        IrrigationZoneEntity.__init__(self, zone, "run_now")
        self._scheduler = scheduler

    async def async_press(self) -> None:
        """Start the zone."""
        await self.async_run_zone()

    async def async_run_zone(self, duration: int | None = None) -> None:
        """Entity-service target for irrigation_scheduler.run_zone."""
        await self._scheduler.async_start_zone(
            self.zone.subentry_id, duration_minutes=duration, source=SOURCE_MANUAL
        )

    async def async_stop_zone(self) -> None:
        """Entity-service target for irrigation_scheduler.stop_zone."""
        await self._scheduler.async_stop_zone(self.zone.subentry_id)


class ZoneStopButton(IrrigationZoneEntity, ButtonEntity):
    """Close this zone's valve now."""

    _attr_icon = "mdi:stop-circle-outline"

    def __init__(self, zone: ZoneRuntime, scheduler: IrrigationScheduler) -> None:
        """Initialise."""
        IrrigationZoneEntity.__init__(self, zone, "stop")
        self._scheduler = scheduler

    async def async_press(self) -> None:
        """Stop the zone."""
        await self._scheduler.async_stop_zone(self.zone.subentry_id)


class StopAllButton(IrrigationHubEntity, ButtonEntity):
    """Close every valve. The panic button."""

    _attr_icon = "mdi:stop-circle"

    def __init__(
        self, hub: HubRuntime, entry_id: str, scheduler: IrrigationScheduler
    ) -> None:
        """Initialise."""
        IrrigationHubEntity.__init__(self, hub, entry_id, "stop_all")
        self._scheduler = scheduler

    async def async_press(self) -> None:
        """Stop everything."""
        await self._scheduler.async_stop_all()
