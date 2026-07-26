"""Tests for the three drivers.

The driver is the only code that touches the hardware, so these assert exactly
which services each one calls and what ``is_open`` reports -- especially that a
button zone reports ``None`` and never pretends to know its state.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.const import ATTR_ENTITY_ID

from custom_components.irrigation_scheduler.drivers import (
    ButtonDriver,
    DistributorDriver,
    ValveDriver,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant, ServiceCall


def _wire(hass: HomeAssistant) -> list[tuple[str, str, str]]:
    """Register valve/switch/button services; record (domain, service, entity)."""
    calls: list[tuple[str, str, str]] = []

    def _record(domain: str, service: str, *, opens: bool | None = None):
        async def _handler(call: ServiceCall) -> None:
            entity_id = call.data[ATTR_ENTITY_ID]
            calls.append((domain, service, entity_id))
            if opens is not None:
                hass.states.async_set(entity_id, "open" if opens else "closed")

        hass.services.async_register(domain, service, _handler)

    _record("valve", "open_valve", opens=True)
    _record("valve", "close_valve", opens=False)
    _record("switch", "turn_on")
    _record("switch", "turn_off")
    _record("button", "press")
    return calls


async def test_valve_driver_opens_closes_and_reads_state(hass: HomeAssistant) -> None:
    """ValveDriver calls valve services and reads the entity's state."""
    calls = _wire(hass)
    driver = ValveDriver(hass, "valve.test")

    hass.states.async_set("valve.test", "closed")
    assert driver.is_open is False
    await driver.async_start()
    assert ("valve", "open_valve", "valve.test") in calls
    assert driver.is_open is True
    await driver.async_stop()
    assert ("valve", "close_valve", "valve.test") in calls
    assert driver.is_open is False


async def test_valve_driver_uses_turn_on_off_for_a_switch(hass: HomeAssistant) -> None:
    """A switch-domain entity is driven with turn_on / turn_off."""
    calls = _wire(hass)
    driver = ValveDriver(hass, "switch.pump_line")
    await driver.async_start()
    await driver.async_stop()
    assert ("switch", "turn_on", "switch.pump_line") in calls
    assert ("switch", "turn_off", "switch.pump_line") in calls


async def test_button_driver_presses_and_has_no_state(hass: HomeAssistant) -> None:
    """ButtonDriver presses start/stop and reports is_open None."""
    calls = _wire(hass)
    driver = ButtonDriver(hass, "button.start", "button.stop")

    assert driver.is_open is None
    assert driver.watched_entity is None
    await driver.async_start()
    assert ("button", "press", "button.start") in calls
    await driver.async_stop()
    assert ("button", "press", "button.stop") in calls


async def test_distributor_opens_and_closes_once_per_outlet(
    hass: HomeAssistant,
) -> None:
    """A distributor run cycles the valve once per outlet (3 opens, 3 closes)."""
    calls = _wire(hass)
    # Zero soak and gap so the whole sequence completes instantly under test.
    driver = DistributorDriver(hass, "valve.jardin", 3, 0, lambda: 0)

    hass.states.async_set("valve.jardin", "closed")
    assert driver.watched_entity == "valve.jardin"
    await driver.async_start()
    await hass.async_block_till_done()

    assert calls.count(("valve", "open_valve", "valve.jardin")) == 3
    assert calls.count(("valve", "close_valve", "valve.jardin")) == 3


async def test_distributor_stop_cancels_an_in_flight_sequence(
    hass: HomeAssistant,
) -> None:
    """Stopping mid-run cancels the outlet sequence and closes the valve."""
    calls = _wire(hass)
    driver = DistributorDriver(hass, "valve.jardin", 3, 10, lambda: 15)  # long soak

    hass.states.async_set("valve.jardin", "closed")
    await driver.async_start()
    await driver.async_stop()  # cancel before the sequence can finish
    await hass.async_block_till_done()

    assert ("valve", "close_valve", "valve.jardin") in calls
