"""Tests for the nightly AI plan.

The model's output is untrusted, so the point of these tests is the degrade
path: a missing, partial, mistyped, or overlapping plan must never widen the
setpoints past a zone's bounds, never touch a running zone, and never leave the
scheduler in a worse state than yesterday's plan (invariant 7).
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING

from custom_components.irrigation_scheduler import ai as ai_module
from custom_components.irrigation_scheduler.ai import IrrigationAI, build_structure
from custom_components.irrigation_scheduler.models import (
    DriverType,
    HubState,
    SchedulePreset,
    ZoneSpec,
    ZoneState,
)

if TYPE_CHECKING:
    import pytest
    from homeassistant.core import HomeAssistant, ServiceCall


def _zone(  # noqa: PLR0913 - a test factory mirroring ZoneSpec's own field count
    sid: str,
    name: str,
    order: int,
    *,
    driver: DriverType = DriverType.VALVE,
    seasonal: bool = False,
    duration: int = 15,
) -> ZoneState:
    spec = ZoneSpec(
        sid,
        name,
        order,
        driver,
        hose_length_m=10,
        min_duration=5,
        max_duration=30,
        seasonal=seasonal,
    )
    return ZoneState(spec=spec, duration_minutes=duration)


def _garden() -> dict[str, ZoneState]:
    return {
        "z1": _zone("z1", "Jardin", 1),
        "z2": _zone("z2", "Gazon", 2, driver=DriverType.BUTTON, seasonal=True),
    }


def _make_ai(
    hass: HomeAssistant, zones: dict[str, ZoneState]
) -> tuple[IrrigationAI, HubState]:
    hub = HubState()
    scheduler = SimpleNamespace(recompute_start_times=lambda: None)
    entry = SimpleNamespace(
        runtime_data=SimpleNamespace(hub=hub, zones=zones, scheduler=scheduler)
    )
    ai = IrrigationAI(hass, entry, "ai_task.garden", None)
    return ai, hub


# --- build_structure -------------------------------------------------------
def test_build_structure_offers_enabled_only_for_seasonal_zones():
    fields = build_structure(_garden())
    assert "jardin_duration" in fields
    assert "jardin_enabled" not in fields  # not seasonal
    assert "gazon_enabled" in fields  # seasonal
    assert fields["jardin_duration"]["selector"]["number"]["min"] == 5
    assert fields["jardin_duration"]["selector"]["number"]["max"] == 30
    assert "rain_threshold" in fields
    assert "narrative" in fields


# --- _apply_plan: the happy path -------------------------------------------
async def test_apply_plan_applies_a_clean_plan(hass: HomeAssistant):
    zones = _garden()
    ai, hub = _make_ai(hass, zones)
    rejections = ai._apply_plan(
        {
            "jardin_duration": 22,
            "jardin_schedule": "weekends",
            "jardin_second_run": True,
            "gazon_duration": 25,
            "gazon_enabled": False,
            "rain_threshold": 70,
            "narrative": "Chaud et sec.",
        }
    )
    await hass.async_block_till_done()
    assert rejections == []
    assert zones["z1"].duration_minutes == 22
    assert zones["z1"].schedule is SchedulePreset.WEEKENDS
    assert zones["z1"].second_run is True
    assert zones["z2"].duration_minutes == 25
    assert zones["z2"].enabled is False
    assert hub.rain_threshold == 70
    assert hub.last_plan_narrative == "Chaud et sec."
    assert hub.last_plan_failed is False
    assert hub.last_plan_date is not None


# --- _apply_plan: malformed responses degrade to "change nothing" ----------
async def test_apply_plan_missing_keys_change_nothing(hass: HomeAssistant):
    zones = _garden()
    ai, hub = _make_ai(hass, zones)
    ai._apply_plan({})  # empty response
    await hass.async_block_till_done()
    assert zones["z1"].duration_minutes == 15
    assert zones["z1"].schedule is SchedulePreset.DAILY
    assert zones["z1"].second_run is False
    assert hub.rain_threshold == 65
    assert hub.last_plan_failed is False  # empty but valid is not a failure


async def test_apply_plan_rejects_wrong_types(hass: HomeAssistant):
    zones = _garden()
    ai, _ = _make_ai(hass, zones)
    rejections = ai._apply_plan(
        {
            "jardin_duration": "beaucoup",
            "jardin_second_run": "oui",
            "rain_threshold": "haut",
        }
    )
    await hass.async_block_till_done()
    assert zones["z1"].duration_minutes == 15  # unchanged
    assert zones["z1"].second_run is False
    assert any("not a number" in r for r in rejections)
    assert any("not a boolean" in r for r in rejections)


async def test_apply_plan_clamps_out_of_range_duration(hass: HomeAssistant):
    zones = _garden()
    ai, _ = _make_ai(hass, zones)
    rejections = ai._apply_plan({"jardin_duration": 999})
    await hass.async_block_till_done()
    assert zones["z1"].duration_minutes == 30  # max_duration
    assert any("clamped" in r for r in rejections)


async def test_apply_plan_keeps_current_schedule_on_unknown_preset(hass: HomeAssistant):
    zones = _garden()
    zones["z1"].schedule = SchedulePreset.MON_THU
    ai, _ = _make_ai(hass, zones)
    rejections = ai._apply_plan({"jardin_schedule": "fortnightly"})
    await hass.async_block_till_done()
    assert zones["z1"].schedule is SchedulePreset.MON_THU
    assert any("unknown schedule" in r for r in rejections)


async def test_apply_plan_refuses_to_toggle_a_non_seasonal_zone(hass: HomeAssistant):
    zones = _garden()
    ai, _ = _make_ai(hass, zones)
    rejections = ai._apply_plan({"jardin_enabled": False})
    await hass.async_block_till_done()
    assert zones["z1"].enabled is True  # untouched
    assert any("may not enable/disable" in r for r in rejections)


async def test_apply_plan_defers_a_running_zone(hass: HomeAssistant):
    zones = _garden()
    zones["z1"].running_until = ai_module.dt_util.now()
    ai, _ = _make_ai(hass, zones)
    rejections = ai._apply_plan({"jardin_duration": 25})
    await hass.async_block_till_done()
    assert zones["z1"].duration_minutes == 15  # not touched while running
    assert any("running, setpoints deferred" in r for r in rejections)


# --- _apply_plan: an overlap is a bug -> roll back + repair issue -----------
async def test_apply_plan_rolls_back_on_overlap(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
):
    zones = _garden()
    ai, hub = _make_ai(hass, zones)
    monkeypatch.setattr(ai_module, "find_overlaps", lambda _zones: ["Jardin vs Gazon"])
    ai._apply_plan({"jardin_duration": 22, "rain_threshold": 80})
    await hass.async_block_till_done()
    assert zones["z1"].duration_minutes == 15  # rolled back
    assert hub.rain_threshold == 65  # rolled back
    assert hub.last_plan_failed is True
    assert hub.last_plan_date is None  # plan not accepted


# --- the failure path: ai_task raising keeps yesterday's plan --------------
async def test_generate_plan_survives_a_failing_ai_task(hass: HomeAssistant):
    zones = _garden()
    ai, hub = _make_ai(hass, zones)

    async def _boom(_call: ServiceCall):
        msg = "model unavailable"
        raise RuntimeError(msg)

    hass.services.async_register(
        "ai_task", "generate_data", _boom, supports_response="only"
    )
    await ai.async_generate_plan(force=True)
    await hass.async_block_till_done()
    assert zones["z1"].duration_minutes == 15  # setpoints untouched
    assert hub.rain_threshold == 65
    assert hub.last_plan_failed is True
