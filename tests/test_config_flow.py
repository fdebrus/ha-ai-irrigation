"""Config-flow tests: the hub flow and the driver-specific zone subentry flow."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.config_entries import SOURCE_USER
from homeassistant.const import CONF_NAME
from homeassistant.data_entry_flow import FlowResultType

from custom_components.irrigation_scheduler.const import (
    CONF_DRIVER,
    CONF_START_BUTTON,
    CONF_STOP_BUTTON,
    CONF_VALVE_ENTITY,
    DOMAIN,
    SUBENTRY_TYPE_ZONE,
)
from custom_components.irrigation_scheduler.models import DriverType

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

_COMMON = {
    "order": 1,
    "description": "",
    "hose_length_m": 10,
    "emitter_min_lph_per_m": 2,
    "emitter_max_lph_per_m": 4,
    "default_duration": 15,
    "min_duration": 5,
    "max_duration": 30,
    "default_schedule": "daily",
    "seasonal": False,
    "adopt_manual_runs": False,
}


async def _make_hub(hass: HomeAssistant) -> ConfigEntry:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            "morning_base": "05:30:00",
            "evening_base": "19:00:00",
            "plan_at": "22:30:00",
            "margin_minutes": 5,
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    return result["result"]


async def test_hub_flow_creates_and_loads(hass: HomeAssistant) -> None:
    """The hub flow creates a single entry that then loads."""
    entry = await _make_hub(hass)
    assert entry.state.recoverable is False or entry.state.value == "loaded"
    # A second hub is rejected (single instance).
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.ABORT


async def test_zone_subentry_valve_flow(hass: HomeAssistant) -> None:
    """Adding a valve zone routes to the valve step and stores its entity."""
    entry = await _make_hub(hass)
    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_TYPE_ZONE), context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {CONF_NAME: "Parking", CONF_DRIVER: DriverType.VALVE.value, **_COMMON},
    )
    assert result["step_id"] == "valve"
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {CONF_VALVE_ENTITY: "valve.parking"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    subentry = next(iter(entry.subentries.values()))
    assert subentry.data[CONF_VALVE_ENTITY] == "valve.parking"


async def test_zone_subentry_button_flow(hass: HomeAssistant) -> None:
    """Adding a button zone routes to the buttons step."""
    entry = await _make_hub(hass)
    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_TYPE_ZONE), context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {CONF_NAME: "Gazon", CONF_DRIVER: DriverType.BUTTON.value, **_COMMON},
    )
    assert result["step_id"] == "buttons"
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {CONF_START_BUTTON: "button.start", CONF_STOP_BUTTON: "button.stop"},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
