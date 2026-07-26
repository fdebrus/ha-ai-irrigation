"""Time platform for the Irrigation Scheduler."""

from __future__ import annotations

from datetime import time as dt_time

from homeassistant.components.time import TimeEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.util import dt as dt_util

from . import IrrigationConfigEntry
from .entity import IrrigationZoneEntity
from .models import ZoneRuntime


async def async_setup_entry(
    hass: HomeAssistant,
    entry: IrrigationConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the start-time entities."""
    for subentry_id, zone in entry.runtime_data.zones.items():
        async_add_entities(
            [ZoneStartTime(zone)], config_subentry_id=subentry_id
        )


class ZoneStartTime(IrrigationZoneEntity, TimeEntity, RestoreEntity):
    """The local time this zone starts on its scheduled days."""

    _attr_icon = "mdi:clock-start"

    def __init__(self, zone: ZoneRuntime) -> None:
        """Initialise."""
        IrrigationZoneEntity.__init__(self, zone, "start_time")

    async def async_added_to_hass(self) -> None:
        """Restore the previous start time."""
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last is not None and (restored := dt_util.parse_time(last.state)):
            self.zone.start_time = restored

    @property
    def native_value(self) -> dt_time:
        """Return the start time."""
        return self.zone.start_time

    async def async_set_value(self, value: dt_time) -> None:
        """Set a new start time."""
        self.zone.start_time = value
        self.async_write_ha_state()
