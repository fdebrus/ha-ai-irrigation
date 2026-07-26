"""Switch platform: hub master/rain-skip/AI, per-zone enabled/second-run."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.const import STATE_ON
from homeassistant.helpers.restore_state import RestoreEntity

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
    """Set up the switches."""
    data = entry.runtime_data
    async_add_entities(
        [
            MasterSwitch(data.hub, entry.entry_id),
            RainSkipSwitch(data.hub, entry.entry_id),
            AiSwitch(data.hub, entry.entry_id),
        ]
    )
    for subentry_id, zone in data.zones.items():
        async_add_entities(
            [
                ZoneEnabledSwitch(zone, data.scheduler),
                ZoneSecondRunSwitch(zone, data.scheduler),
            ],
            config_subentry_id=subentry_id,
        )


class _RestoringSwitch(SwitchEntity, RestoreEntity):
    """Switch whose value survives restarts."""

    async def async_added_to_hass(self) -> None:
        """Restore the previous value."""
        await super().async_added_to_hass()
        if (last := await self.async_get_last_state()) is not None:
            self._apply(value=last.state == STATE_ON)

    def _apply(self, *, value: bool) -> None:
        raise NotImplementedError

    async def async_turn_on(self, **_kwargs: Any) -> None:
        """Turn on."""
        self._apply(value=True)
        self.async_write_ha_state()

    async def async_turn_off(self, **_kwargs: Any) -> None:
        """Turn off."""
        self._apply(value=False)
        self.async_write_ha_state()


class MasterSwitch(IrrigationHubEntity, _RestoringSwitch):
    """Global on/off for all scheduled runs."""

    _attr_icon = "mdi:sprinkler-fire"

    def __init__(self, hub: HubState, entry_id: str) -> None:
        """Initialise."""
        IrrigationHubEntity.__init__(self, hub, entry_id, "master")

    @property
    def is_on(self) -> bool:
        """Return the master state."""
        return self.hub.master_enabled

    def _apply(self, *, value: bool) -> None:
        self.hub.master_enabled = value


class RainSkipSwitch(IrrigationHubEntity, _RestoringSwitch):
    """Skip scheduled runs when rain is expected."""

    _attr_icon = "mdi:weather-rainy"

    def __init__(self, hub: HubState, entry_id: str) -> None:
        """Initialise."""
        IrrigationHubEntity.__init__(self, hub, entry_id, "rain_skip")

    @property
    def is_on(self) -> bool:
        """Return whether rain skip is active."""
        return self.hub.rain_skip_enabled

    def _apply(self, *, value: bool) -> None:
        self.hub.rain_skip_enabled = value


class AiSwitch(IrrigationHubEntity, _RestoringSwitch):
    """Enable the nightly AI plan."""

    _attr_icon = "mdi:robot"

    def __init__(self, hub: HubState, entry_id: str) -> None:
        """Initialise."""
        IrrigationHubEntity.__init__(self, hub, entry_id, "ai")

    @property
    def is_on(self) -> bool:
        """Return whether the AI plan is enabled."""
        return self.hub.ai_enabled

    def _apply(self, *, value: bool) -> None:
        self.hub.ai_enabled = value


class ZoneEnabledSwitch(IrrigationZoneEntity, _RestoringSwitch):
    """Enable or disable one zone's schedule."""

    _attr_icon = "mdi:sprinkler-variant"

    def __init__(self, zone: ZoneState, scheduler: IrrigationScheduler) -> None:
        """Initialise."""
        IrrigationZoneEntity.__init__(self, zone, "enabled")
        self._scheduler = scheduler

    @property
    def is_on(self) -> bool:
        """Return whether the zone is enabled."""
        return self.zone.enabled

    def _apply(self, *, value: bool) -> None:
        self.zone.enabled = value
        self._scheduler.recompute_start_times()


class ZoneSecondRunSwitch(IrrigationZoneEntity, _RestoringSwitch):
    """Enable a second, evening run for this zone."""

    _attr_icon = "mdi:repeat"

    def __init__(self, zone: ZoneState, scheduler: IrrigationScheduler) -> None:
        """Initialise."""
        IrrigationZoneEntity.__init__(self, zone, "second_run")
        self._scheduler = scheduler

    @property
    def is_on(self) -> bool:
        """Return whether the second run is enabled."""
        return self.zone.second_run

    def _apply(self, *, value: bool) -> None:
        self.zone.second_run = value
        self._scheduler.recompute_start_times()
