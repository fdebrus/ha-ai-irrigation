"""Tests for the nightly AI plan.

The model's output is untrusted, so the point of these tests is the degrade
path: a missing, partial, mistyped, or overlapping plan must never widen the
setpoints past a zone's bounds, never touch a running zone, and never leave the
scheduler in a worse state than yesterday's plan (invariant 7).
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING

from homeassistant.helpers.dispatcher import async_dispatcher_connect

from custom_components.irrigation_scheduler import ai as ai_module
from custom_components.irrigation_scheduler.ai import (
    IrrigationAI,
    build_response_format,
    parse_plan,
)
from custom_components.irrigation_scheduler.const import SIGNAL_ZONE_UPDATED
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


# --- build_response_format -------------------------------------------------
def test_response_format_offers_enabled_only_for_seasonal_zones():
    text = build_response_format(_garden())
    assert "jardin_duration" in text
    assert "jardin_enabled" not in text  # not seasonal
    assert "gazon_enabled" in text  # seasonal
    assert "jardin_duration: 5-30" in text  # bounds are spelled out
    assert "rain_threshold" in text
    assert "narrative" in text
    assert "daily" in text  # the schedule presets are listed


# --- the instructions carry the duration-adjustment rules -------------------
async def test_instructions_carry_current_settings_and_rules(hass: HomeAssistant):
    zones = _garden()
    zones["z1"].second_run = True
    zones["z2"].enabled = False
    ai, hub = _make_ai(hass, zones)
    text = ai._build_instructions(
        forecast=[
            # Met.no-style day: an amount in mm but no probability.
            {"datetime": "2026-07-28", "temperature": 28.4, "precipitation": 0.2}
        ]
    )
    # The forecast line shows the rain amount even when probability is absent.
    assert "rain ?%, 0.2 mm" in text
    # Current settings per zone, so "change as little as possible" has a basis.
    assert "evening on" in text  # Jardin's 2nd run state
    assert "CURRENTLY DISABLED" in text  # Gazon is off
    assert f"Current rain-skip threshold: {hub.rain_threshold}%" in text
    # The regime rules that drive per-zone duration changes.
    assert "Adjust DURATIONS per zone" in text
    assert "Heat wave" in text
    assert "upper bound" in text
    assert "factor of 5" in text
    # The format block closes the prompt.
    assert "RESPONSE FORMAT" in text


async def test_instructions_carry_no_hardcoded_locale(hass: HomeAssistant):
    """The prompt is location- and language-neutral (worldwide integration)."""
    ai, _ = _make_ai(hass, _garden())
    text = ai._build_instructions(forecast=[])
    for locale_specific in ("belge", "Belgium", "Waterloo", "citerne"):
        assert locale_specific not in text
    # The narrative language follows the HA configuration.
    assert f'Write "narrative" in this language: {hass.config.language}' in text


# --- parse_plan: lenient like the YAML package was --------------------------
def test_parse_plan_accepts_raw_and_fenced_json():
    assert parse_plan('{"a": 1}') == {"a": 1}
    assert parse_plan('```json\n{"a": 1}\n```') == {"a": 1}
    assert parse_plan({"a": 1}) == {"a": 1}  # already structured


def test_parse_plan_rejects_non_objects():
    for bad in (None, "not json at all", "[1, 2]", 42):
        try:
            parse_plan(bad)
        except (TypeError, ValueError):
            continue
        msg = f"parse_plan accepted {bad!r}"
        raise AssertionError(msg)


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


# --- end to end: a fenced text reply is parsed and applied ------------------
async def test_generate_plan_applies_a_fenced_text_reply(hass: HomeAssistant):
    zones = _garden()
    ai, hub = _make_ai(hass, zones)

    async def _reply(_call: ServiceCall) -> dict:
        return {
            "data": '```json\n{"jardin_duration": 22, "rain_threshold": 70,'
            ' "narrative": "Sec."}\n```'
        }

    hass.services.async_register(
        "ai_task", "generate_data", _reply, supports_response="only"
    )
    await ai.async_generate_plan(force=True)
    await hass.async_block_till_done()
    assert zones["z1"].duration_minutes == 22
    assert hub.rain_threshold == 70
    assert hub.last_plan_failed is False


# --- scheduling: manual plans must not suppress the nightly run -------------
async def test_scheduled_plan_fires_even_after_a_manual_plan_today(
    hass: HomeAssistant,
):
    zones = _garden()
    ai, hub = _make_ai(hass, zones)
    hub.last_plan_date = ai_module.dt_util.now().date()  # manual plan earlier today
    calls: list = []

    async def _reply(_call) -> dict:
        calls.append(1)
        return {"data": '{"narrative": "soir"}'}

    hass.services.async_register(
        "ai_task", "generate_data", _reply, supports_response="only"
    )
    at = ai_module.dt_util.now().replace(
        hour=hub.plan_at.hour, minute=hub.plan_at.minute
    )
    ai._async_minute(at)
    await hass.async_block_till_done()
    assert calls, "the 22:30 run was suppressed by an earlier manual plan"


async def test_startup_restore_notifies_entities(hass: HomeAssistant):
    """Restored plan state is pushed to entities, not left on unknown."""
    zones = _garden()
    ai, hub = _make_ai(hass, zones)
    await ai._store.async_save(
        {"date": "2026-07-26", "narrative": "hier", "failed": False, "rejections": []}
    )
    notified: list = []
    unsub = async_dispatcher_connect(
        hass, SIGNAL_ZONE_UPDATED, lambda: notified.append(1)
    )
    await ai.async_start()
    await hass.async_block_till_done()
    assert hub.last_plan_narrative == "hier"
    assert str(hub.last_plan_date) == "2026-07-26"
    assert notified, "entities were not told about the restored plan"
    unsub()
    ai.async_shutdown()


async def test_startup_catch_up_skipped_when_plan_exists_today(hass: HomeAssistant):
    """Catch-up (unlike the scheduled run) still respects one-per-day."""
    zones = _garden()
    ai, hub = _make_ai(hass, zones)
    now = ai_module.dt_util.now()
    hub.last_plan_date = now.date()
    hub.plan_at = now.replace(hour=0, minute=0).time()  # plan time already passed
    calls: list = []

    async def _reply(_call) -> dict:
        calls.append(1)
        return {"data": '{"narrative": "x"}'}

    hass.services.async_register(
        "ai_task", "generate_data", _reply, supports_response="only"
    )
    await ai.async_start()
    await hass.async_block_till_done()
    assert not calls  # today's plan already exists; no catch-up
    ai.async_shutdown()


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
