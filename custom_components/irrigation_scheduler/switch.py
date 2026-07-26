"""Switch platform for the Irrigation Scheduler."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.const import STATE_ON
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from . import IrrigationConfigEntry
from .entity import IrrigationHubEntity, IrrigationZoneEntity
from .models import HubRuntime, ZoneRuntime


async def async_setup_entry(
    hass: HomeAssistant,
    entry: IrrigationConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the switches."""
    data = entry.runtime_data
    async_add_entities(
        [
            MasterSwitch(data.hub, entry.entry_id),
            RainSkipSwitch(data.hub, entry.entry_id),
        ]
    )
    for subentry_id, zone in data.zones.items():
        async_add_entities(
            [ZoneEnabledSwitch(zone)], config_subentry_id=subentry_id
        )


class _RestoringSwitch(SwitchEntity, RestoreEntity):
    """Switch whose value survives restarts."""

    _attr_entity_category = None

    async def async_added_to_hass(self) -> None:
        """Restore the previous value."""
        await super().async_added_to_hass()
        if (last := await self.async_get_last_state()) is not None:
            self._apply(last.state == STATE_ON)

    def _apply(self, value: bool) -> None:
        raise NotImplementedError

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the switch on."""
        self._apply(True)
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the switch off."""
        self._apply(False)
        self.async_write_ha_state()


class MasterSwitch(IrrigationHubEntity, _RestoringSwitch):
    """Global on/off for all scheduled runs."""

    _attr_icon = "mdi:sprinkler-fire"

    def __init__(self, hub: HubRuntime, entry_id: str) -> None:
        """Initialise."""
        IrrigationHubEntity.__init__(self, hub, entry_id, "master")

    @property
    def is_on(self) -> bool:
        """Return the master state."""
        return self.hub.master_enabled

    def _apply(self, value: bool) -> None:
        self.hub.master_enabled = value


class RainSkipSwitch(IrrigationHubEntity, _RestoringSwitch):
    """Enable skipping runs when rain is forecast."""

    _attr_icon = "mdi:weather-rainy"

    def __init__(self, hub: HubRuntime, entry_id: str) -> None:
        """Initialise."""
        IrrigationHubEntity.__init__(self, hub, entry_id, "rain_skip")

    @property
    def is_on(self) -> bool:
        """Return whether rain skip is active."""
        return self.hub.rain_skip_enabled

    def _apply(self, value: bool) -> None:
        self.hub.rain_skip_enabled = value


class ZoneEnabledSwitch(IrrigationZoneEntity, _RestoringSwitch):
    """Enable or disable one zone's schedule."""

    _attr_icon = "mdi:sprinkler-variant"

    def __init__(self, zone: ZoneRuntime) -> None:
        """Initialise."""
        IrrigationZoneEntity.__init__(self, zone, "enabled")

    @property
    def is_on(self) -> bool:
        """Return whether the zone is enabled."""
        return self.zone.enabled

    def _apply(self, value: bool) -> None:
        self.zone.enabled = value
