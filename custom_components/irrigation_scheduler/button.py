"""Button platform: run/stop per zone, stop-all and plan-now on the hub."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import voluptuous as vol
from homeassistant.components.button import ButtonEntity
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import entity_platform

from .const import DURATION_HARD_MAX, DURATION_HARD_MIN, SOURCE_MANUAL
from .entity import IrrigationHubEntity, IrrigationZoneEntity

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

    from . import IrrigationConfigEntry
    from .models import HubState, ZoneState
    from .scheduler import IrrigationScheduler

_LOGGER = logging.getLogger(__name__)

SERVICE_RUN_ZONE = "run_zone"
SERVICE_STOP_ZONE = "stop_zone"


# All entities read shared runtime_data and push via the dispatcher;
# there is no per-entity I/O to serialise.
PARALLEL_UPDATES = 0


async def async_setup_entry(
    _hass: HomeAssistant,
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
                cv.positive_int,
                vol.Range(min=DURATION_HARD_MIN, max=DURATION_HARD_MAX),
            )
        },
        "async_run_zone",
    )
    platform.async_register_entity_service(SERVICE_STOP_ZONE, {}, "async_stop_zone")

    async_add_entities(
        [
            StopAllButton(data.hub, entry.entry_id, data.scheduler),
            PlanNowButton(data.hub, entry.entry_id, entry),
        ]
    )
    for subentry_id, zone in data.zones.items():
        async_add_entities(
            [
                ZoneRunNowButton(zone, data.scheduler),
                ZoneStopButton(zone, data.scheduler),
            ],
            config_subentry_id=subentry_id,
        )


class ZoneRunNowButton(IrrigationZoneEntity, ButtonEntity):
    """Start this zone now for its configured duration."""

    _attr_icon = "mdi:play-circle-outline"

    def __init__(self, zone: ZoneState, scheduler: IrrigationScheduler) -> None:
        """Initialise."""
        IrrigationZoneEntity.__init__(self, zone, "run_now")
        self._scheduler = scheduler

    async def async_press(self) -> None:
        """Start the zone."""
        await self.async_run_zone()

    async def async_run_zone(self, duration: int | None = None) -> None:
        """Entity-service target for irrigation_scheduler.run_zone."""
        await self._scheduler.async_start_zone(
            self.zone.spec.subentry_id, duration_minutes=duration, source=SOURCE_MANUAL
        )

    async def async_stop_zone(self) -> None:
        """Entity-service target for irrigation_scheduler.stop_zone."""
        await self._scheduler.async_stop_zone(self.zone.spec.subentry_id)


class ZoneStopButton(IrrigationZoneEntity, ButtonEntity):
    """Stop this zone now."""

    _attr_icon = "mdi:stop-circle-outline"

    def __init__(self, zone: ZoneState, scheduler: IrrigationScheduler) -> None:
        """Initialise."""
        IrrigationZoneEntity.__init__(self, zone, "stop")
        self._scheduler = scheduler

    async def async_press(self) -> None:
        """Stop the zone."""
        await self._scheduler.async_stop_zone(self.zone.spec.subentry_id)


class StopAllButton(IrrigationHubEntity, ButtonEntity):
    """Stop every zone. The panic button."""

    _attr_icon = "mdi:stop-circle"

    def __init__(
        self, hub: HubState, entry_id: str, scheduler: IrrigationScheduler
    ) -> None:
        """Initialise."""
        IrrigationHubEntity.__init__(self, hub, entry_id, "stop_all")
        self._scheduler = scheduler

    async def async_press(self) -> None:
        """Stop everything."""
        await self._scheduler.async_stop_all()


class PlanNowButton(IrrigationHubEntity, ButtonEntity):
    """Run the AI plan now, outside its nightly schedule."""

    _attr_icon = "mdi:robot-outline"

    def __init__(
        self, hub: HubState, entry_id: str, entry: IrrigationConfigEntry
    ) -> None:
        """Initialise."""
        IrrigationHubEntity.__init__(self, hub, entry_id, "plan_now")
        self._entry = entry

    async def async_press(self) -> None:
        """Trigger the AI plan if the AI layer is configured."""
        ai = self._entry.runtime_data.ai
        if ai is None:
            _LOGGER.warning("Plan Now pressed but no AI task is configured")
            return
        await ai.async_generate_plan(force=True)
