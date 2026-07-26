"""
Time platform: hub morning/evening base times and the AI plan time.

Only the *bases* are editable. Per-zone start times are derived and surfaced as
sensors (invariant 2), never as time entities.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.components.time import TimeEntity
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.util import dt as dt_util

from .entity import IrrigationHubEntity

if TYPE_CHECKING:
    from datetime import time as dt_time

    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

    from . import IrrigationConfigEntry
    from .models import HubState
    from .scheduler import IrrigationScheduler


async def async_setup_entry(
    _hass: HomeAssistant,
    entry: IrrigationConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the hub time entities."""
    data = entry.runtime_data
    async_add_entities(
        [
            MorningBaseTime(data.hub, entry.entry_id, data.scheduler),
            EveningBaseTime(data.hub, entry.entry_id, data.scheduler),
            PlanAtTime(data.hub, entry.entry_id),
        ]
    )


class _RestoringBaseTime(IrrigationHubEntity, TimeEntity, RestoreEntity):
    """A hub base time that recomputes the sequence when changed."""

    def __init__(
        self, hub: HubState, entry_id: str, key: str, scheduler: IrrigationScheduler
    ) -> None:
        """Initialise."""
        IrrigationHubEntity.__init__(self, hub, entry_id, key)
        self._scheduler = scheduler

    async def async_added_to_hass(self) -> None:
        """Restore the previous base time."""
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last is not None and (restored := dt_util.parse_time(last.state)):
            self._set(restored)
            self._scheduler.recompute_start_times()

    def _get(self) -> dt_time:
        raise NotImplementedError

    def _set(self, value: dt_time) -> None:
        raise NotImplementedError

    @property
    def native_value(self) -> dt_time:
        """Return the base time."""
        return self._get()

    async def async_set_value(self, value: dt_time) -> None:
        """Set a new base time and re-derive the sequence."""
        self._set(value)
        self._scheduler.recompute_start_times()
        self.async_write_ha_state()


class MorningBaseTime(_RestoringBaseTime):
    """When the morning sequence starts."""

    _attr_icon = "mdi:weather-sunset-up"

    def __init__(
        self, hub: HubState, entry_id: str, scheduler: IrrigationScheduler
    ) -> None:
        """Initialise."""
        super().__init__(hub, entry_id, "morning_base", scheduler)

    def _get(self) -> dt_time:
        return self.hub.morning_base

    def _set(self, value: dt_time) -> None:
        self.hub.morning_base = value


class EveningBaseTime(_RestoringBaseTime):
    """When the evening sequence starts."""

    _attr_icon = "mdi:weather-sunset-down"

    def __init__(
        self, hub: HubState, entry_id: str, scheduler: IrrigationScheduler
    ) -> None:
        """Initialise."""
        super().__init__(hub, entry_id, "evening_base", scheduler)

    def _get(self) -> dt_time:
        return self.hub.evening_base

    def _set(self, value: dt_time) -> None:
        self.hub.evening_base = value


class PlanAtTime(IrrigationHubEntity, TimeEntity, RestoreEntity):
    """When the nightly AI plan runs."""

    _attr_icon = "mdi:clock-outline"

    def __init__(self, hub: HubState, entry_id: str) -> None:
        """Initialise."""
        IrrigationHubEntity.__init__(self, hub, entry_id, "plan_at")

    async def async_added_to_hass(self) -> None:
        """Restore the previous plan time."""
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last is not None and (restored := dt_util.parse_time(last.state)):
            self.hub.plan_at = restored

    @property
    def native_value(self) -> dt_time:
        """Return the plan time."""
        return self.hub.plan_at

    async def async_set_value(self, value: dt_time) -> None:
        """Set a new plan time."""
        self.hub.plan_at = value
        self.async_write_ha_state()
