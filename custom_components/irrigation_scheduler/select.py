"""Select platform: per-zone schedule preset."""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from homeassistant.components.select import SelectEntity
from homeassistant.helpers.restore_state import RestoreEntity

from .entity import IrrigationZoneEntity
from .models import SchedulePreset

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

    from . import IrrigationConfigEntry
    from .models import ZoneState


async def async_setup_entry(
    _hass: HomeAssistant,
    entry: IrrigationConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the selects."""
    for subentry_id, zone in entry.runtime_data.zones.items():
        async_add_entities([ZoneScheduleSelect(zone)], config_subentry_id=subentry_id)


class ZoneScheduleSelect(IrrigationZoneEntity, SelectEntity, RestoreEntity):
    """Which days this zone runs on."""

    _attr_icon = "mdi:calendar-clock"
    _attr_options: ClassVar[list[str]] = [preset.value for preset in SchedulePreset]

    def __init__(self, zone: ZoneState) -> None:
        """Initialise."""
        IrrigationZoneEntity.__init__(self, zone, "schedule")

    async def async_added_to_hass(self) -> None:
        """Restore the previous schedule."""
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last is not None and last.state in set(self._attr_options):
            self.zone.schedule = SchedulePreset(last.state)

    @property
    def current_option(self) -> str:
        """Return the current schedule preset."""
        return self.zone.schedule.value

    async def async_select_option(self, option: str) -> None:
        """Set a new schedule preset."""
        self.zone.schedule = SchedulePreset(option)
        self.async_write_ha_state()
