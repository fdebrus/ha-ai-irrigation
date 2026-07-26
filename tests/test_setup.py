"""Setup / teardown tests for the config entry and its zone subentries.

These boot Home Assistant (via pytest-homeassistant-custom-component) and check
that a hub entry with a single zone subentry loads, creates the expected
entities on the expected devices, and unloads cleanly.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.config_entries import ConfigEntryState, ConfigSubentryData
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.irrigation_scheduler.const import (
    CONF_ADOPT_MANUAL_RUNS,
    CONF_DEFAULT_DURATION,
    CONF_DEFAULT_START,
    CONF_VALVE_ENTITY,
    CONF_WEEKDAYS,
    DOMAIN,
    SUBENTRY_TYPE_ZONE,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

# The entities a single zone device owns, by unique-id suffix.
ZONE_ENTITY_KEYS = {
    "enabled",  # switch
    "duration",  # number
    "start_time",  # time
    "next_run",  # sensor
    "finishes_at",  # sensor
    "status",  # sensor
    "run_now",  # button
    "stop",  # button
    # seven weekday switches
    "weekday_mon",
    "weekday_tue",
    "weekday_wed",
    "weekday_thu",
    "weekday_fri",
    "weekday_sat",
    "weekday_sun",
}
# The entities the hub device owns when no weather entity is configured.
HUB_ENTITY_KEYS = {
    "master",  # switch
    "rain_skip",  # switch
    "sequential",  # switch
    "rain_threshold",  # number
    "stop_all",  # button
}


def _make_entry() -> MockConfigEntry:
    """Build a hub entry with one zone subentry and no weather entity."""
    return MockConfigEntry(
        domain=DOMAIN,
        title="Irrigation Scheduler",
        data={},
        subentries_data=[
            ConfigSubentryData(
                subentry_type=SUBENTRY_TYPE_ZONE,
                title="Framboisier",
                unique_id=None,
                data={
                    CONF_VALVE_ENTITY: "valve.framboisier",
                    CONF_DEFAULT_START: "06:00:00",
                    CONF_DEFAULT_DURATION: 15,
                    CONF_WEEKDAYS: ["mon", "wed", "fri"],
                    CONF_ADOPT_MANUAL_RUNS: False,
                },
            )
        ],
    )


async def _setup(hass: HomeAssistant) -> MockConfigEntry:
    """Add and set up the entry, returning it once loaded."""
    entry = _make_entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_setup_creates_entities_on_the_right_devices(
    hass: HomeAssistant,
) -> None:
    """One zone subentry loads and lands its entities on the zone device."""
    entry = await _setup(hass)
    assert entry.state is ConfigEntryState.LOADED

    subentry_id = next(iter(entry.subentries))
    ent_reg = er.async_get(hass)
    dev_reg = dr.async_get(hass)

    # The zone device carries exactly the zone entities.
    zone_device = dev_reg.async_get_device(identifiers={(DOMAIN, subentry_id)})
    assert zone_device is not None
    assert zone_device.name == "Framboisier"
    zone_entities = er.async_entries_for_device(ent_reg, zone_device.id)
    zone_keys = {e.unique_id.removeprefix(f"{subentry_id}_") for e in zone_entities}
    assert zone_keys == ZONE_ENTITY_KEYS

    # The hub device carries the hub entities (no rain sensor without weather).
    hub_device = dev_reg.async_get_device(identifiers={(DOMAIN, entry.entry_id)})
    assert hub_device is not None
    hub_entities = er.async_entries_for_device(ent_reg, hub_device.id)
    hub_keys = {e.unique_id.removeprefix(f"{entry.entry_id}_") for e in hub_entities}
    assert hub_keys == HUB_ENTITY_KEYS

    # Every entity produced a state.
    for entity in (*zone_entities, *hub_entities):
        assert hass.states.get(entity.entity_id) is not None


async def test_weekday_switch_owns_the_zone_weekdays(
    hass: HomeAssistant,
) -> None:
    """Toggling a weekday switch updates the zone's weekdays list."""
    entry = await _setup(hass)
    subentry_id = next(iter(entry.subentries))
    zone = entry.runtime_data.zones[subentry_id]
    assert "wed" in zone.weekdays  # seeded from the subentry

    ent_reg = er.async_get(hass)
    wed = ent_reg.async_get_entity_id("switch", DOMAIN, f"{subentry_id}_weekday_wed")
    assert wed is not None

    await hass.services.async_call(
        "switch", "turn_off", {"entity_id": wed}, blocking=True
    )
    assert "wed" not in zone.weekdays

    await hass.services.async_call(
        "switch", "turn_on", {"entity_id": wed}, blocking=True
    )
    assert "wed" in zone.weekdays


async def test_unload_is_clean(hass: HomeAssistant) -> None:
    """Unloading tears the entry down without leaving it loaded."""
    entry = await _setup(hass)
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.NOT_LOADED
