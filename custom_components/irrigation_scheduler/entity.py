"""Base entities: dispatcher-driven, one device per zone plus a hub device."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import Entity

from .const import DOMAIN, SIGNAL_ZONE_UPDATED

if TYPE_CHECKING:
    from .models import HubState, ZoneState


class IrrigationBaseEntity(Entity):
    """Common plumbing: no polling, dispatcher-driven updates."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    async def async_added_to_hass(self) -> None:
        """Subscribe to scheduler updates."""
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, SIGNAL_ZONE_UPDATED, self.async_write_ha_state
            )
        )


class IrrigationZoneEntity(IrrigationBaseEntity):
    """An entity belonging to one zone device."""

    def __init__(self, zone: ZoneState, key: str) -> None:
        """Initialise the zone entity."""
        self.zone = zone
        self._attr_unique_id = f"{zone.spec.subentry_id}_{key}"
        self._attr_translation_key = key
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, zone.spec.subentry_id)},
            name=zone.spec.name,
            manufacturer="Irrigation Scheduler",
            model=zone.spec.driver.value,
        )


class IrrigationHubEntity(IrrigationBaseEntity):
    """An entity belonging to the hub device."""

    def __init__(self, hub: HubState, entry_id: str, key: str) -> None:
        """Initialise the hub entity."""
        self.hub = hub
        self._attr_unique_id = f"{entry_id}_{key}"
        self._attr_translation_key = key
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry_id)},
            name="Irrigation",
            manufacturer="Irrigation Scheduler",
            model="Controller",
        )
