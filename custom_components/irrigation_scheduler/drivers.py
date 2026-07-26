"""
Driver implementations: the only code that calls services on the hardware.

Each zone has one driver. The scheduler decides *when* a zone runs; the driver
knows *how* to make water flow for that zone. Three physical arrangements, one
interface (``async_start`` / ``async_stop`` / ``is_open``):

- ``ValveDriver`` — a single valve or switch entity.
- ``DistributorDriver`` — one valve feeding a GARDENA multi-outlet distributor.
  The distributor is mechanical and advances one outlet per open/close cycle,
  so a run opens and closes the valve once per outlet (reproduced from the
  reference ``irrigation_run_jardin`` script).
- ``ButtonDriver`` — a start button and a stop button, no state entity. Its
  ``is_open`` is ``None``: the run timer is the only truth.

Every valve command is announced through ``mark_self_driven`` so the scheduler's
adoption listener does not mistake our own command for a manual one.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import TYPE_CHECKING

from homeassistant.const import (
    ATTR_ENTITY_ID,
    SERVICE_TURN_OFF,
    SERVICE_TURN_ON,
    STATE_ON,
    STATE_OPEN,
    STATE_OPENING,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

VALVE_OPEN_STATES: frozenset[str] = frozenset({STATE_ON, STATE_OPEN, STATE_OPENING})

# Domains whose entities are opened/closed with turn_on / turn_off.
_SWITCH_LIKE: frozenset[str] = frozenset({"switch", "input_boolean"})


def _noop(_entity_id: str) -> None:
    """Default self-driven hook: do nothing."""


class Driver:
    """Interface every zone driver implements."""

    async def async_start(self) -> None:
        """Make water flow for this zone."""
        raise NotImplementedError

    async def async_stop(self) -> None:
        """Stop water for this zone."""
        raise NotImplementedError

    @property
    def is_open(self) -> bool | None:
        """Real open state, or None when the driver has no feedback."""
        raise NotImplementedError

    @property
    def watched_entity(self) -> str | None:
        """Entity the adoption listener should watch, or None (button zones)."""
        return None

    @property
    def required_entities(self) -> list[str]:
        """The hardware entities this driver cannot run without."""
        return []


async def _set_valve(
    hass: HomeAssistant,
    entity_id: str,
    *,
    open_valve: bool,
    mark_self_driven: Callable[[str], None],
) -> None:
    """Open or close a valve/switch entity, whatever domain it lives in."""
    domain = entity_id.split(".", 1)[0]
    if domain == "valve":
        service = "open_valve" if open_valve else "close_valve"
    else:
        domain = domain if domain in _SWITCH_LIKE else "homeassistant"
        service = SERVICE_TURN_ON if open_valve else SERVICE_TURN_OFF
    mark_self_driven(entity_id)
    await hass.services.async_call(
        domain, service, {ATTR_ENTITY_ID: entity_id}, blocking=True
    )


def _read_open(hass: HomeAssistant, entity_id: str) -> bool | None:
    """Return whether a valve/switch entity reads open, or None if unknown."""
    state = hass.states.get(entity_id)
    if state is None:
        return None
    return state.state in VALVE_OPEN_STATES


class ValveDriver(Driver):
    """A single valve (or switch) entity."""

    def __init__(
        self,
        hass: HomeAssistant,
        entity_id: str,
        mark_self_driven: Callable[[str], None] = _noop,
    ) -> None:
        """Initialise for ``entity_id``."""
        self._hass = hass
        self._entity_id = entity_id
        self._mark = mark_self_driven

    async def async_start(self) -> None:
        """Open the valve."""
        await _set_valve(
            self._hass, self._entity_id, open_valve=True, mark_self_driven=self._mark
        )

    async def async_stop(self) -> None:
        """Close the valve."""
        await _set_valve(
            self._hass, self._entity_id, open_valve=False, mark_self_driven=self._mark
        )

    @property
    def is_open(self) -> bool | None:
        """Return the valve's real open state."""
        return _read_open(self._hass, self._entity_id)

    @property
    def watched_entity(self) -> str | None:
        """Return the valve entity to watch for adoption."""
        return self._entity_id

    @property
    def required_entities(self) -> list[str]:
        """Return the single valve/switch entity."""
        return [self._entity_id]


class ButtonDriver(Driver):
    """Start/stop buttons with no state feedback (Aiper IrriSense)."""

    def __init__(
        self, hass: HomeAssistant, start_button: str, stop_button: str
    ) -> None:
        """Initialise for the start and stop button entities."""
        self._hass = hass
        self._start_button = start_button
        self._stop_button = stop_button

    async def _press(self, entity_id: str) -> None:
        await self._hass.services.async_call(
            "button", "press", {ATTR_ENTITY_ID: entity_id}, blocking=True
        )

    async def async_start(self) -> None:
        """Press the start button."""
        await self._press(self._start_button)

    async def async_stop(self) -> None:
        """Press the stop button (harmless if it already stopped itself)."""
        await self._press(self._stop_button)

    @property
    def is_open(self) -> bool | None:
        """Return None: a button zone has no readable state."""
        return None

    @property
    def required_entities(self) -> list[str]:
        """Return the start and stop button entities."""
        return [self._start_button, self._stop_button]


class DistributorDriver(Driver):
    """
    One valve feeding a mechanical multi-outlet distributor.

    A run opens and closes the valve once per outlet; each close advances the
    dial to the next outlet, and the last close returns it to the start. The
    outlet sequence runs as a cancellable background task so ``async_start``
    returns immediately; the scheduler's stop timer is the authoritative end.
    """

    def __init__(  # noqa: PLR0913 - the distributor genuinely needs these
        self,
        hass: HomeAssistant,
        valve_entity: str,
        outlets: int,
        gap_seconds: int,
        get_duration_minutes: Callable[[], int],
        mark_self_driven: Callable[[str], None] = _noop,
    ) -> None:
        """Initialise for the shared valve and its outlet parameters."""
        self._hass = hass
        self._valve_entity = valve_entity
        self._outlets = max(1, outlets)
        self._gap_seconds = gap_seconds
        self._get_duration_minutes = get_duration_minutes
        self._mark = mark_self_driven
        self._task: asyncio.Task[None] | None = None

    async def _valve(self, *, open_valve: bool) -> None:
        await _set_valve(
            self._hass,
            self._valve_entity,
            open_valve=open_valve,
            mark_self_driven=self._mark,
        )

    async def _run_sequence(self) -> None:
        try:
            soak = self._get_duration_minutes() * 60
            for outlet in range(self._outlets):
                await self._valve(open_valve=True)
                await asyncio.sleep(soak)
                await self._valve(open_valve=False)
                if outlet < self._outlets - 1:
                    await asyncio.sleep(self._gap_seconds)
        except asyncio.CancelledError:
            with contextlib.suppress(Exception):
                await self._valve(open_valve=False)
            raise

    async def _cancel_task(self) -> None:
        if self._task is not None and not self._task.done():
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        self._task = None

    async def async_start(self) -> None:
        """Launch the outlet sequence (cancelling any prior one)."""
        await self._cancel_task()
        self._task = self._hass.async_create_task(self._run_sequence())

    async def async_stop(self) -> None:
        """Cancel the sequence and close the valve."""
        await self._cancel_task()
        await self._valve(open_valve=False)

    @property
    def is_open(self) -> bool | None:
        """Return the shared valve's real open state."""
        return _read_open(self._hass, self._valve_entity)

    @property
    def watched_entity(self) -> str | None:
        """Return the shared valve entity to watch for adoption."""
        return self._valve_entity

    @property
    def required_entities(self) -> list[str]:
        """Return the shared valve entity."""
        return [self._valve_entity]
