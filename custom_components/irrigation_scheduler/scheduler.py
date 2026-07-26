"""Scheduling engine for the Irrigation Scheduler."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from homeassistant.const import (
    ATTR_ENTITY_ID,
    SERVICE_TURN_OFF,
    SERVICE_TURN_ON,
    STATE_OFF,
    STATE_ON,
    STATE_OPEN,
    STATE_OPENING,
)
from homeassistant.core import (
    CALLBACK_TYPE,
    Event,
    EventStateChangedData,
    HomeAssistant,
    callback,
)
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import (
    async_track_point_in_utc_time,
    async_track_state_change_event,
    async_track_time_change,
)
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import (
    SIGNAL_ZONE_UPDATED,
    SOURCE_ADOPTED,
    SOURCE_MANUAL,
    SOURCE_SCHEDULE,
    STORAGE_KEY,
    STORAGE_VERSION,
)
from .models import HubRuntime, ZoneRuntime, should_start

if TYPE_CHECKING:
    from .coordinator import RainCoordinator

_LOGGER = logging.getLogger(__name__)

VALVE_OPEN_STATES = {STATE_ON, STATE_OPEN, STATE_OPENING}


class IrrigationScheduler:
    """Owns the tick, the valve calls and the persisted run state."""

    def __init__(
        self,
        hass: HomeAssistant,
        hub: HubRuntime,
        zones: dict[str, ZoneRuntime],
        coordinator: RainCoordinator | None,
    ) -> None:
        """Initialise the scheduler."""
        self.hass = hass
        self.hub = hub
        self.zones = zones
        self.coordinator = coordinator
        self._store: Store = Store(hass, STORAGE_VERSION, STORAGE_KEY)
        self._unsub_tick: CALLBACK_TYPE | None = None
        self._unsub_watch: CALLBACK_TYPE | None = None
        self._unsub_stop: dict[str, CALLBACK_TYPE] = {}
        # Valve entity IDs we are mid-command on, so our own service calls do
        # not come back through the state listener and get adopted.
        self._self_driven: set[str] = set()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    async def async_start(self) -> None:
        """Restore any in-flight runs and arm the tick."""
        await self._async_restore_runs()

        self._unsub_tick = async_track_time_change(
            self.hass, self._async_tick, second=0
        )
        watched = [z.valve_entity_id for z in self.zones.values() if z.valve_entity_id]
        if watched:
            self._unsub_watch = async_track_state_change_event(
                self.hass, watched, self._async_valve_changed
            )

    async def async_shutdown(self) -> None:
        """Tear down listeners. Does *not* close valves."""
        for unsub in (self._unsub_tick, self._unsub_watch):
            if unsub is not None:
                unsub()
        self._unsub_tick = None
        self._unsub_watch = None
        for unsub in self._unsub_stop.values():
            unsub()
        self._unsub_stop.clear()

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------
    async def _async_restore_runs(self) -> None:
        """
        Re-arm or close out runs that were live when HA went down.

        This is the whole reason the integration exists rather than the YAML
        package: without it, a restart mid-run leaves a valve open forever.
        """
        stored = await self._store.async_load() or {}
        now = dt_util.utcnow()
        for zone_id, payload in (stored.get("runs") or {}).items():
            zone = self.zones.get(zone_id)
            if zone is None:
                continue
            ends_at = dt_util.parse_datetime(payload.get("ends_at", ""))
            if ends_at is None:
                continue
            zone.running_source = payload.get("source")
            if ends_at <= now:
                _LOGGER.info(
                    "Zone %s run expired while HA was down; closing valve", zone.name
                )
                zone.running_until = ends_at
                await self.async_stop_zone(zone_id)
            else:
                _LOGGER.info("Resuming zone %s, %s remaining", zone.name, ends_at - now)
                zone.running_until = ends_at
                self._arm_stop(zone_id, ends_at)
        self._notify()

    async def _async_save(self) -> None:
        """Write live runs to disk."""
        runs = {
            zone_id: {
                "ends_at": zone.running_until.isoformat(),
                "source": zone.running_source,
            }
            for zone_id, zone in self.zones.items()
            if zone.running_until is not None
        }
        await self._store.async_save({"runs": runs})

    # ------------------------------------------------------------------
    # Tick
    # ------------------------------------------------------------------
    @callback
    def _async_tick(self, now: datetime) -> None:
        """Fire once a minute at :00 and evaluate every zone."""
        local_now = dt_util.as_local(now)
        rain = self.coordinator.data if self.coordinator else None
        for zone_id, zone in self.zones.items():
            start, reason = should_start(zone, self.hub, local_now, rain)
            if reason is not None:
                zone.last_skipped_reason = reason
            if not start:
                continue
            zone.last_scheduled_date = local_now.date().isoformat()
            self.hass.async_create_task(
                self.async_start_zone(zone_id, source=SOURCE_SCHEDULE)
            )

    # ------------------------------------------------------------------
    # Run control
    # ------------------------------------------------------------------
    async def async_start_zone(
        self,
        zone_id: str,
        duration_minutes: int | None = None,
        source: str = SOURCE_MANUAL,
    ) -> None:
        """Open a zone's valve and arm its stop timer."""
        zone = self.zones[zone_id]
        minutes = duration_minutes or zone.duration_minutes
        ends_at = dt_util.utcnow() + timedelta(minutes=minutes)

        if source != SOURCE_ADOPTED:
            await self._async_set_valve(zone, open_valve=True)

        zone.running_until = ends_at
        zone.running_source = source
        zone.last_run = dt_util.utcnow()
        zone.last_skipped_reason = None
        self._arm_stop(zone_id, ends_at)
        await self._async_save()
        self._notify()
        _LOGGER.debug("Zone %s started (%s) for %s min", zone.name, source, minutes)

    async def async_stop_zone(self, zone_id: str) -> None:
        """Close a zone's valve and clear its run state."""
        zone = self.zones[zone_id]
        if (unsub := self._unsub_stop.pop(zone_id, None)) is not None:
            unsub()
        await self._async_set_valve(zone, open_valve=False)
        zone.running_until = None
        zone.running_source = None
        await self._async_save()
        self._notify()
        _LOGGER.debug("Zone %s stopped", zone.name)

    async def async_stop_all(self) -> None:
        """Close every zone. Wire this to your emergency-stop button."""
        for zone_id in list(self.zones):
            await self.async_stop_zone(zone_id)

    def _arm_stop(self, zone_id: str, ends_at: datetime) -> None:
        """Schedule the close for a running zone."""
        if (unsub := self._unsub_stop.pop(zone_id, None)) is not None:
            unsub()

        async def _fire(_now: datetime) -> None:
            self._unsub_stop.pop(zone_id, None)
            await self.async_stop_zone(zone_id)

        self._unsub_stop[zone_id] = async_track_point_in_utc_time(
            self.hass, _fire, ends_at
        )

    # ------------------------------------------------------------------
    # Valve I/O
    # ------------------------------------------------------------------
    async def _async_set_valve(self, zone: ZoneRuntime, *, open_valve: bool) -> None:
        """Open or close the zone's valve, whatever domain it lives in."""
        entity_id = zone.valve_entity_id
        if not entity_id:
            return
        domain = entity_id.split(".", 1)[0]
        if domain == "valve":
            service = "open_valve" if open_valve else "close_valve"
        else:
            # switch, input_boolean, light, ... all take turn_on/turn_off.
            domain = (
                domain if domain in ("switch", "input_boolean") else "homeassistant"
            )
            service = SERVICE_TURN_ON if open_valve else SERVICE_TURN_OFF

        self._self_driven.add(entity_id)
        try:
            await self.hass.services.async_call(
                domain, service, {ATTR_ENTITY_ID: entity_id}, blocking=True
            )
        finally:
            self.hass.loop.call_later(5, self._self_driven.discard, entity_id)

    @callback
    def _async_valve_changed(self, event: Event[EventStateChangedData]) -> None:
        """
        Adopt (or ignore) a valve that was opened outside the integration.

        The old YAML package did this implicitly for every zone, which is what
        made the HomeKit runs look like they were being closed by the device.
        Here it is opt-in per zone via `adopt_manual_runs`.
        """
        entity_id = event.data["entity_id"]
        if entity_id in self._self_driven:
            return
        new_state = event.data["new_state"]
        if new_state is None:
            return

        zone_id = next(
            (zid for zid, z in self.zones.items() if z.valve_entity_id == entity_id),
            None,
        )
        if zone_id is None:
            return
        zone = self.zones[zone_id]

        if new_state.state in VALVE_OPEN_STATES:
            if zone.adopt_manual_runs and not zone.is_running:
                self.hass.async_create_task(
                    self.async_start_zone(zone_id, source=SOURCE_ADOPTED)
                )
        elif new_state.state in (STATE_OFF, "closed") and zone.is_running:
            # Someone closed it under us -- drop our timer so we do not send a
            # redundant close later.
            if (unsub := self._unsub_stop.pop(zone_id, None)) is not None:
                unsub()
            zone.running_until = None
            zone.running_source = None
            self.hass.async_create_task(self._async_save())
            self._notify()

    # ------------------------------------------------------------------
    @callback
    def _notify(self) -> None:
        """Ask every entity to re-read runtime state."""
        async_dispatcher_send(self.hass, SIGNAL_ZONE_UPDATED)
