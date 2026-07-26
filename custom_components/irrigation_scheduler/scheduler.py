"""
Scheduling engine: the tick, run/stop, persistence and adoption.

The decisions are pure (models.slot_due / should_skip_for_rain, planner
sequencing); this module is the thin I/O shell that fires them, drives the
hardware through drivers, and survives a restart. One pump feeds every zone, so
only one run is ever live at a time.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import TYPE_CHECKING

from homeassistant.const import STATE_CLOSED, STATE_OFF, STATE_ON
from homeassistant.core import callback
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import (
    async_track_point_in_utc_time,
    async_track_state_change_event,
    async_track_time_change,
)
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import (
    DOMAIN,
    NO_FLOW_GRACE_MINUTES,
    SIGNAL_ZONE_UPDATED,
    SOURCE_ADOPTED,
    SOURCE_MANUAL,
    SOURCE_SCHEDULE,
    STORAGE_KEY,
    STORAGE_VERSION,
)
from .drivers import VALVE_OPEN_STATES
from .models import (
    DriverType,
    should_skip_for_rain,
    slot_due,
)
from .planner import apply_start_times

if TYPE_CHECKING:
    from asyncio import TimerHandle
    from datetime import datetime

    from homeassistant.core import (
        CALLBACK_TYPE,
        Event,
        EventStateChangedData,
        HomeAssistant,
    )

    from .coordinator import RainCoordinator
    from .drivers import Driver
    from .models import HubState, ZoneState

_LOGGER = logging.getLogger(__name__)

# States that mean "not open" for the adoption close-detection branch.
_CLOSED_STATES: frozenset[str] = frozenset({STATE_OFF, STATE_CLOSED})
# Weather conditions that count as raining right now.
_RAINING_STATES: frozenset[str] = frozenset({"rainy", "pouring"})
# Seconds to keep ignoring our own valve command echoing back as a state event.
_SELF_DRIVEN_TTL = 5
# Repair issue id for the pump running dry under an open zone.
_NO_FLOW_ISSUE = "pump_no_flow"


class IrrigationScheduler:
    """Owns the minute tick, the run/stop path, the Store and adoption."""

    def __init__(  # noqa: PLR0913 - the scheduler collects the hub's collaborators
        self,
        hass: HomeAssistant,
        hub: HubState,
        zones: dict[str, ZoneState],
        coordinator: RainCoordinator | None,
        *,
        weather_entity_id: str | None = None,
        pump_sensor_id: str | None = None,
    ) -> None:
        """Initialise; drivers are attached with :meth:`set_drivers`."""
        self.hass = hass
        self.hub = hub
        self.zones = zones
        self.coordinator = coordinator
        self._weather_entity_id = weather_entity_id
        self._pump_sensor_id = pump_sensor_id
        self.drivers: dict[str, Driver] = {}
        self._store: Store = Store(hass, STORAGE_VERSION, STORAGE_KEY)
        self._unsub_tick: CALLBACK_TYPE | None = None
        self._unsub_watch: CALLBACK_TYPE | None = None
        self._unsub_stop: dict[str, CALLBACK_TYPE] = {}
        self._self_driven: set[str] = set()
        self._self_driven_timers: dict[str, TimerHandle | None] = {}
        # Set by the pump watchdog; read by the no-flow binary sensor.
        self.no_flow = False

    def set_drivers(self, drivers: dict[str, Driver]) -> None:
        """Attach the per-zone drivers."""
        self.drivers = drivers

    @callback
    def recompute_start_times(self) -> None:
        """
        Re-derive every zone's start times and refresh entities.

        Call after any change to a duration, an enabled flag or a base time
        (invariant 2) so the sequence never lets two zones share the pump.
        """
        apply_start_times(list(self.zones.values()), self.hub)
        self._notify()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    async def async_start(self) -> None:
        """Restore in-flight runs and arm the tick and adoption listener."""
        await self._async_restore_runs()
        self._unsub_tick = async_track_time_change(
            self.hass, self._async_tick, second=0
        )
        # Only valve/distributor zones have a valve to watch; button zones have
        # no state, so the adoption listener skips them entirely.
        watched = [
            entity_id
            for zone_id, zone in self.zones.items()
            if (entity_id := self._watched_entity(zone_id))
            and zone.spec.driver is not DriverType.BUTTON
        ]
        if watched:
            self._unsub_watch = async_track_state_change_event(
                self.hass, watched, self._async_valve_changed
            )

    async def async_shutdown(self) -> None:
        """Tear down listeners and timers. Does not close valves."""
        for unsub in (self._unsub_tick, self._unsub_watch):
            if unsub is not None:
                unsub()
        self._unsub_tick = self._unsub_watch = None
        for unsub in self._unsub_stop.values():
            unsub()
        self._unsub_stop.clear()
        for timer in self._self_driven_timers.values():
            if timer is not None:
                timer.cancel()
        self._self_driven_timers.clear()
        self._self_driven.clear()

    def _watched_entity(self, zone_id: str) -> str | None:
        """Entity id whose state should be watched for this zone, if any."""
        driver = self.drivers.get(zone_id)
        return driver.watched_entity if driver is not None else None

    # ------------------------------------------------------------------
    # Persistence + restart recovery (invariant 3)
    # ------------------------------------------------------------------
    async def _async_restore_runs(self) -> None:
        """Re-arm or close out runs that were live when HA went down."""
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
            zone.running_until = ends_at
            if ends_at <= now:
                _LOGGER.info("Zone %s run expired while down; stopping", zone.spec.name)
                await self.async_stop_zone(zone_id)
            else:
                _LOGGER.info("Resuming zone %s until %s", zone.spec.name, ends_at)
                self._arm_stop(zone_id, ends_at)
        self._notify()

    async def _async_save(self) -> None:
        """Persist live runs to disk."""
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
        """Fire once a minute and start any zone whose slot is due."""
        local_now = dt_util.as_local(now)
        rain = self.coordinator.data if self.coordinator else None
        raining = self._raining_now()
        self._evaluate_no_flow()
        for zone_id, zone in self.zones.items():
            if not self.hub.master_enabled:
                zone.last_skipped_reason = "master_off"
                continue
            slot = slot_due(zone, local_now)
            if slot is None or zone.is_running:
                continue
            slot_key = f"{local_now.date().isoformat()}/{slot}"
            if zone.last_scheduled_slot == slot_key:
                continue
            zone.last_scheduled_slot = slot_key
            if should_skip_for_rain(self.hub, rain, raining_now=raining):
                zone.last_skipped_reason = "rain_expected"
                self._notify()
                continue
            self.hass.async_create_task(
                self.async_start_zone(zone_id, source=SOURCE_SCHEDULE)
            )

    def _raining_now(self) -> bool:
        """Whether the weather entity currently reports rain."""
        if not self._weather_entity_id:
            return False
        state = self.hass.states.get(self._weather_entity_id)
        return state is not None and state.state in _RAINING_STATES

    # ------------------------------------------------------------------
    # Pump watchdog
    # ------------------------------------------------------------------
    @callback
    def _evaluate_no_flow(self) -> None:
        """
        Flag a dry pump: a zone open past the grace window with the pump off.

        Membership is by the tracked run, so a button zone (Gazon), which has no
        valve to watch, still counts as "running" while its timer is live. With
        no pump sensor configured the watchdog is inert.
        """
        if not self._pump_sensor_id:
            return
        running = [zone for zone in self.zones.values() if zone.is_running]
        if not running:
            self._set_no_flow(state=False)
            return
        pump = self.hass.states.get(self._pump_sensor_id)
        if pump is not None and pump.state == STATE_ON:
            self._set_no_flow(state=False)
            return
        now = dt_util.utcnow()
        grace = timedelta(minutes=NO_FLOW_GRACE_MINUTES)
        stalled = any(
            zone.last_run is not None and now - zone.last_run >= grace
            for zone in running
        )
        self._set_no_flow(state=stalled)

    @callback
    def _set_no_flow(self, *, state: bool) -> None:
        """Latch the no-flow flag and raise/clear the matching repair issue."""
        if state == self.no_flow:
            return
        self.no_flow = state
        if state:
            _LOGGER.warning(
                "Pump reports no flow while a zone is running (sensor %s)",
                self._pump_sensor_id,
            )
            ir.async_create_issue(
                self.hass,
                DOMAIN,
                _NO_FLOW_ISSUE,
                is_fixable=False,
                severity=ir.IssueSeverity.WARNING,
                translation_key=_NO_FLOW_ISSUE,
                translation_placeholders={"sensor": self._pump_sensor_id or ""},
            )
        else:
            ir.async_delete_issue(self.hass, DOMAIN, _NO_FLOW_ISSUE)
        self._notify()

    # ------------------------------------------------------------------
    # Run control -- one pump, one zone at a time (invariant 2)
    # ------------------------------------------------------------------
    async def async_start_zone(
        self,
        zone_id: str,
        duration_minutes: int | None = None,
        source: str = SOURCE_MANUAL,
    ) -> None:
        """Start a zone, stopping any other running zone first."""
        zone = self.zones[zone_id]

        # Enforce the single pump: never let two runs overlap.
        for other_id, other in self.zones.items():
            if other_id != zone_id and other.is_running:
                await self.async_stop_zone(other_id)

        minutes = duration_minutes or zone.duration_minutes
        run_minutes = zone.spec.occupancy_minutes(minutes) - zone.spec.settle_minutes
        ends_at = dt_util.utcnow() + timedelta(minutes=run_minutes)

        # Mark the run live before the awaited driver call so the adoption
        # listener sees it running and never re-adopts our own command.
        zone.running_until = ends_at
        zone.running_source = source
        zone.last_run = dt_util.utcnow()
        zone.last_skipped_reason = None

        if source != SOURCE_ADOPTED and (driver := self.drivers.get(zone_id)):
            await driver.async_start()

        self._arm_stop(zone_id, ends_at)
        await self._async_save()
        self._notify()
        _LOGGER.debug(
            "Zone %s started (%s) for %s min", zone.spec.name, source, run_minutes
        )

    async def async_stop_zone(self, zone_id: str) -> None:
        """Stop a zone and clear its run state."""
        zone = self.zones[zone_id]
        if (unsub := self._unsub_stop.pop(zone_id, None)) is not None:
            unsub()
        if driver := self.drivers.get(zone_id):
            await driver.async_stop()
        zone.running_until = None
        zone.running_source = None
        await self._async_save()
        self._evaluate_no_flow()
        self._notify()
        _LOGGER.debug("Zone %s stopped", zone.spec.name)

    async def async_stop_all(self) -> None:
        """Stop every zone. The panic button."""
        for zone_id in list(self.zones):
            await self.async_stop_zone(zone_id)

    def _arm_stop(self, zone_id: str, ends_at: datetime) -> None:
        """Schedule the stop for a running zone."""
        if (unsub := self._unsub_stop.pop(zone_id, None)) is not None:
            unsub()

        async def _fire(_now: datetime) -> None:
            self._unsub_stop.pop(zone_id, None)
            await self.async_stop_zone(zone_id)

        self._unsub_stop[zone_id] = async_track_point_in_utc_time(
            self.hass, _fire, ends_at
        )

    # ------------------------------------------------------------------
    # Adoption + self-driven suppression (invariants 1, 5)
    # ------------------------------------------------------------------
    @callback
    def mark_self_driven(self, entity_id: str) -> None:
        """Announce that we are about to command ``entity_id`` ourselves."""
        self._self_driven.add(entity_id)
        if (timer := self._self_driven_timers.pop(entity_id, None)) is not None:
            timer.cancel()
        self._self_driven_timers[entity_id] = self.hass.loop.call_later(
            _SELF_DRIVEN_TTL, self._clear_self_driven, entity_id
        )

    @callback
    def _clear_self_driven(self, entity_id: str) -> None:
        self._self_driven.discard(entity_id)
        self._self_driven_timers.pop(entity_id, None)

    @callback
    def _async_valve_changed(self, event: Event[EventStateChangedData]) -> None:
        """Adopt (or release) a valve changed outside the integration."""
        entity_id = event.data["entity_id"]
        if entity_id in self._self_driven:
            return
        new_state = event.data["new_state"]
        if new_state is None:
            return
        zone_id = next(
            (zid for zid in self.zones if self._watched_entity(zid) == entity_id),
            None,
        )
        if zone_id is None:
            return
        zone = self.zones[zone_id]
        # Button zones never reach here (not watched), but guard anyway.
        if zone.spec.driver is DriverType.BUTTON:
            return

        if new_state.state in VALVE_OPEN_STATES:
            if zone.spec.adopt_manual_runs and not zone.is_running:
                self.hass.async_create_task(
                    self.async_start_zone(zone_id, source=SOURCE_ADOPTED)
                )
        elif new_state.state in _CLOSED_STATES and zone.is_running:
            # Closed under us: drop the run so we do not send a redundant stop.
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
