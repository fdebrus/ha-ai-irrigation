"""Setup / unload of a hub entry with valve, button and distributor zones."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.config_entries import ConfigEntryState, ConfigSubentryData
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.irrigation_scheduler.const import (
    CONF_DRIVER,
    CONF_ORDER,
    CONF_OUTLET_GAP,
    CONF_OUTLETS,
    CONF_SEASONAL,
    CONF_START_BUTTON,
    CONF_STOP_BUTTON,
    CONF_VALVE_ENTITY,
    DOMAIN,
    SUBENTRY_TYPE_ZONE,
)
from custom_components.irrigation_scheduler.models import DriverType

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

ZONE_KEYS = {
    "enabled",
    "second_run",
    "duration",
    "schedule",
    "morning_start",
    "evening_start",
    "next_run",
    "finishes_at",
    "status",
    "run_now",
    "stop",
}
HUB_KEYS = {
    "master",
    "rain_skip",
    "ai",
    "rain_threshold",
    "morning_base",
    "evening_base",
    "plan_at",
    "daily_plan",
    "stop_all",
    "plan_now",
    "overlap",
    "no_flow",
}


def _zone(title: str, data: dict) -> ConfigSubentryData:
    return ConfigSubentryData(
        subentry_type=SUBENTRY_TYPE_ZONE, title=title, unique_id=None, data=data
    )


def _entry() -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        title="Irrigation Scheduler",
        data={},  # no weather entity -> no coordinator
        subentries_data=[
            _zone(
                "Parking",
                {
                    CONF_DRIVER: DriverType.VALVE.value,
                    CONF_VALVE_ENTITY: "valve.p",
                    CONF_ORDER: 3,
                },
            ),
            _zone(
                "Jardin",
                {
                    CONF_DRIVER: DriverType.DISTRIBUTOR.value,
                    CONF_VALVE_ENTITY: "valve.j",
                    CONF_OUTLETS: 3,
                    CONF_OUTLET_GAP: 10,
                    CONF_ORDER: 1,
                },
            ),
            _zone(
                "Gazon",
                {
                    CONF_DRIVER: DriverType.BUTTON.value,
                    CONF_START_BUTTON: "button.g_start",
                    CONF_STOP_BUTTON: "button.g_stop",
                    CONF_SEASONAL: True,
                    CONF_ORDER: 2,
                },
            ),
        ],
    )


async def test_setup_creates_all_entities_and_unloads(hass: HomeAssistant) -> None:
    """The entry loads, builds each device's entities, and unloads cleanly."""
    entry = _entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED

    ent_reg = er.async_get(hass)
    dev_reg = dr.async_get(hass)

    # Each zone device carries the 11 zone entities.
    for subentry_id in entry.subentries:
        device = dev_reg.async_get_device(identifiers={(DOMAIN, subentry_id)})
        assert device is not None
        keys = {
            e.unique_id.removeprefix(f"{subentry_id}_")
            for e in er.async_entries_for_device(ent_reg, device.id)
        }
        assert keys == ZONE_KEYS

    # The hub device carries its entities (no rain probability without weather).
    hub = dev_reg.async_get_device(identifiers={(DOMAIN, entry.entry_id)})
    assert hub is not None
    hub_keys = {
        e.unique_id.removeprefix(f"{entry.entry_id}_")
        for e in er.async_entries_for_device(ent_reg, hub.id)
    }
    assert hub_keys == HUB_KEYS

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.NOT_LOADED


async def test_start_times_are_derived_and_non_overlapping(
    hass: HomeAssistant,
) -> None:
    """Derived morning starts follow the watering order from the base time."""
    entry = _entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    zones = entry.runtime_data.zones
    by_name = {z.spec.name: z for z in zones.values()}
    # Jardin (order 1) starts at the base; Gazon (order 2) after it.
    assert by_name["Jardin"].morning_start is not None
    assert by_name["Gazon"].morning_start > by_name["Jardin"].morning_start

    await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
