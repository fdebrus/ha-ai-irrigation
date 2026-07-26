"""Binary sensor platform: schedule overlap and pump no-flow (hub)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)

from .entity import IrrigationHubEntity
from .planner import find_overlaps

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

    from . import IrrigationConfigEntry
    from .models import HubState, ZoneState
    from .scheduler import IrrigationScheduler


# All entities read shared runtime_data and push via the dispatcher;
# there is no per-entity I/O to serialise.
PARALLEL_UPDATES = 0


async def async_setup_entry(
    _hass: HomeAssistant,
    entry: IrrigationConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the hub binary sensors."""
    data = entry.runtime_data
    async_add_entities(
        [
            OverlapBinarySensor(data.hub, entry.entry_id, data.zones),
            NoFlowBinarySensor(data.hub, entry.entry_id, data.scheduler),
        ]
    )


class OverlapBinarySensor(IrrigationHubEntity, BinarySensorEntity):
    """On when two derived runs would collide -- a bug, not a misconfig."""

    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_icon = "mdi:alert"

    def __init__(
        self, hub: HubState, entry_id: str, zones: dict[str, ZoneState]
    ) -> None:
        """Initialise."""
        IrrigationHubEntity.__init__(self, hub, entry_id, "overlap")
        self._zones = zones

    @property
    def is_on(self) -> bool:
        """Return whether any two runs overlap."""
        return bool(find_overlaps(list(self._zones.values())))

    @property
    def extra_state_attributes(self) -> dict[str, list[str]]:
        """List the colliding runs."""
        return {"conflicts": find_overlaps(list(self._zones.values()))}


class NoFlowBinarySensor(IrrigationHubEntity, BinarySensorEntity):
    """On when a zone is running but the pump reports no flow."""

    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_icon = "mdi:water-pump-off"

    def __init__(
        self, hub: HubState, entry_id: str, scheduler: IrrigationScheduler
    ) -> None:
        """Initialise."""
        IrrigationHubEntity.__init__(self, hub, entry_id, "no_flow")
        self._scheduler = scheduler

    @property
    def is_on(self) -> bool:
        """Return whether the pump watchdog has flagged no flow."""
        return self._scheduler.no_flow
