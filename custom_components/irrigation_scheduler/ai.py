"""
The nightly AI plan.

Ported from ``irrigation_ai.yaml`` with the fragile parts replaced. The model
proposes setpoints (durations, schedules, second runs, the rain threshold, and
enabled only for seasonal zones); it never proposes actions. Everything it
returns passes through ``planner.clamp_zone_plan`` / ``clamp_rain_threshold``,
which fall back to the current value, so any failure degrades to yesterday's
plan rather than to a stopped scheduler (invariant 7).

The response shape is requested as raw JSON via a format block built
dynamically from the configured zones (see ``build_response_format`` for why
``ai_task``'s ``structure`` parameter is deliberately not used) -- no
hand-maintained prompt block.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

from homeassistant.core import callback
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.event import async_track_time_change
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util
from homeassistant.util import slugify

from .const import (
    AI_RAIN_MAX,
    AI_RAIN_MIN,
    DOMAIN,
    STORAGE_KEY_PLAN,
    STORAGE_VERSION,
)
from .models import SchedulePreset
from .planner import (
    apply_start_times,
    clamp_rain_threshold,
    clamp_zone_plan,
    find_overlaps,
    zone_briefing,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from homeassistant.core import CALLBACK_TYPE, HomeAssistant

    from . import IrrigationConfigEntry
    from .models import HubState, ZoneState

_LOGGER = logging.getLogger(__name__)

_OVERLAP_ISSUE = "ai_plan_overlap"
_FORECAST_DAYS = 5


def build_response_format(zones: Mapping[str, ZoneState], language: str = "en") -> str:
    """
    Describe the exact JSON reply expected, built from the configured zones.

    The plan is requested as raw JSON in the instructions rather than through
    ``ai_task``'s ``structure`` parameter: the Anthropic structured-output
    endpoint rejected the generated schema twice in production (first
    ``minimum``/``maximum`` on numbers, then "Schema is too complex" from the
    five 6-value schedule enums). The reference YAML package always used a
    response-format block and it worked for a season -- and the real safety
    is ``clamp_zone_plan`` / ``clamp_rain_threshold``, not the schema.

    ``enabled`` is offered only for seasonal zones. The example carries each
    zone's *current* values, which doubles as "change as little as possible".
    The narrative is requested in ``language`` -- the Home Assistant configured
    language -- so the dashboard reads naturally wherever the garden is.
    """
    presets = ", ".join(preset.value for preset in SchedulePreset)
    example: dict[str, Any] = {}
    bounds: list[str] = []
    for zone in zones.values():
        slug = slugify(zone.spec.name)
        example[f"{slug}_duration"] = zone.duration_minutes
        example[f"{slug}_schedule"] = zone.schedule.value
        example[f"{slug}_second_run"] = zone.second_run
        if zone.spec.seasonal:
            example[f"{slug}_enabled"] = zone.enabled
        bounds.append(
            f"{slug}_duration: {zone.spec.min_duration}-{zone.spec.max_duration}"
        )
    example["rain_threshold"] = 65
    example["narrative"] = "short reasoning for the dashboard"
    return (
        "RESPONSE FORMAT: reply ONLY with a raw single-line JSON object, no "
        "markdown fences, with exactly these keys:\n"
        f"{json.dumps(example, ensure_ascii=False)}\n"
        f"Allowed *_schedule values: {presets}. "
        f"Durations: whole minutes ({'; '.join(bounds)}). "
        f"rain_threshold: integer {AI_RAIN_MIN}-{AI_RAIN_MAX}. "
        f'Write "narrative" in this language: {language}.'
    )


def parse_plan(data: Any) -> dict[str, Any]:
    """
    Parse the model's reply into a dict, tolerating markdown fences.

    Accepts an already-structured dict or raw JSON text (the model sometimes
    wraps it in ```json fences despite the instructions -- the YAML package
    stripped them too). Raises on anything else; the caller degrades to
    yesterday's plan.
    """
    if isinstance(data, str):
        text = data.replace("```json", "").replace("```", "").strip()
        data = json.loads(text)
    if not isinstance(data, dict):
        msg = f"AI plan is not a JSON object: {data!r}"
        raise TypeError(msg)
    return data


class IrrigationAI:
    """Requests, validates and applies the nightly plan."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: IrrigationConfigEntry,
        ai_task_entity: str | None,
        weather_entity: str | None,
    ) -> None:
        """Initialise for the configured AI task and weather entities."""
        self._hass = hass
        self._entry = entry
        self._ai_task_entity = ai_task_entity
        self._weather_entity = weather_entity
        self._store: Store = Store(hass, STORAGE_VERSION, STORAGE_KEY_PLAN)
        self._unsub: CALLBACK_TYPE | None = None

    @property
    def _hub(self) -> HubState:
        return self._entry.runtime_data.hub

    @property
    def _zones(self) -> dict[str, ZoneState]:
        return self._entry.runtime_data.zones

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    async def async_start(self) -> None:
        """Restore plan bookkeeping and arm the daily check."""
        stored = await self._store.async_load() or {}
        hub = self._hub
        if stored.get("date"):
            hub.last_plan_date = dt_util.parse_date(stored["date"])
        hub.last_plan_narrative = stored.get("narrative")
        hub.last_plan_failed = bool(stored.get("failed", False))
        hub.last_plan_rejections = list(stored.get("rejections", []))

        self._unsub = async_track_time_change(self._hass, self._async_minute, second=0)

        # Catch up if HA was down at the planning time (invariant: never block).
        now = dt_util.now()
        if (
            hub.ai_enabled
            and hub.last_plan_date != now.date()
            and (now.hour, now.minute) >= (hub.plan_at.hour, hub.plan_at.minute)
        ):
            self._hass.async_create_task(self.async_generate_plan())

    def async_shutdown(self) -> None:
        """Cancel the daily check."""
        if self._unsub is not None:
            self._unsub()
            self._unsub = None

    @callback
    def _async_minute(self, now: Any) -> None:
        local = dt_util.as_local(now)
        hub = self._hub
        if not hub.ai_enabled:
            return
        if (local.hour, local.minute) != (hub.plan_at.hour, hub.plan_at.minute):
            return
        if hub.last_plan_date == local.date():
            return
        self._hass.async_create_task(self.async_generate_plan())

    # ------------------------------------------------------------------
    # Generate + apply
    # ------------------------------------------------------------------
    async def async_generate_plan(self, *, force: bool = False) -> None:
        """Fetch a plan and apply it; on any failure keep yesterday's."""
        hub = self._hub
        if not force and not hub.ai_enabled:
            return
        if not self._ai_task_entity:
            _LOGGER.warning("AI plan requested but no ai_task entity is configured")
            return
        try:
            raw = await self._async_request_plan()
            self._apply_plan(raw)
        except Exception:
            _LOGGER.exception("AI plan failed; keeping yesterday's plan")
            hub.last_plan_failed = True
            await self._async_save()
            self._entry.runtime_data.scheduler.recompute_start_times()

    async def _async_request_plan(self) -> dict[str, Any]:
        # No `structure` parameter: see build_response_format. The format is
        # part of the instructions and the reply is parsed leniently.
        forecast = await self._async_forecast()
        instructions = self._build_instructions(forecast)
        response = await self._hass.services.async_call(
            "ai_task",
            "generate_data",
            {
                "task_name": "irrigation_daily_check",
                "entity_id": self._ai_task_entity,
                "instructions": instructions,
            },
            blocking=True,
            return_response=True,
        )
        return parse_plan((response or {}).get("data"))

    def _apply_plan(self, raw: Mapping[str, Any]) -> list[str]:
        """
        Clamp and apply a raw plan; return the rejection messages.

        A running zone is never touched -- its setpoints are deferred. If the
        applied plan would overlap (a bug, not a misconfig), the setpoints are
        rolled back and a repair issue is raised.
        """
        hub = self._hub
        zones = list(self._zones.values())
        snapshot = _snapshot(zones, hub)
        rejections: list[str] = []

        for zone in zones:
            if zone.is_running:
                rejections.append(f"{zone.spec.name}: running, setpoints deferred")
                continue
            slug = slugify(zone.spec.name)
            per_zone = {
                "duration_minutes": raw.get(f"{slug}_duration"),
                "schedule": raw.get(f"{slug}_schedule"),
                "second_run": raw.get(f"{slug}_second_run"),
                "enabled": raw.get(f"{slug}_enabled"),
            }
            plan, rej = clamp_zone_plan(
                per_zone, zone, allow_enable_change=zone.spec.seasonal
            )
            rejections.extend(rej)
            zone.duration_minutes = plan.duration_minutes
            zone.schedule = plan.schedule
            zone.second_run = plan.second_run
            zone.enabled = plan.enabled

        threshold, rej = clamp_rain_threshold(
            raw.get("rain_threshold"),
            hub.rain_threshold,
            low=AI_RAIN_MIN,
            high=AI_RAIN_MAX,
        )
        rejections.extend(rej)
        hub.rain_threshold = threshold

        apply_start_times(zones, hub)
        if overlaps := find_overlaps(zones):
            _LOGGER.error("AI plan overlaps, rolling back: %s", overlaps)
            _restore(zones, hub, snapshot)
            apply_start_times(zones, hub)
            self._raise_overlap_issue(overlaps)
            hub.last_plan_failed = True
        else:
            self._clear_overlap_issue()
            hub.last_plan_date = dt_util.now().date()
            hub.last_plan_narrative = str(raw.get("narrative") or "")
            hub.last_plan_rejections = rejections
            hub.last_plan_generated_at = dt_util.now()
            hub.last_plan_failed = False

        self._hass.async_create_task(self._async_save())
        self._entry.runtime_data.scheduler.recompute_start_times()
        return rejections

    # ------------------------------------------------------------------
    # Prompt building
    # ------------------------------------------------------------------
    async def _async_forecast(self) -> list[dict[str, Any]]:
        if not self._weather_entity:
            return []
        try:
            response = await self._hass.services.async_call(
                "weather",
                "get_forecasts",
                {"type": "daily"},
                target={"entity_id": self._weather_entity},
                blocking=True,
                return_response=True,
            )
        except Exception:  # noqa: BLE001 - forecast is best-effort
            _LOGGER.debug("Forecast fetch failed for the plan prompt")
            return []
        forecasts = (response or {}).get(self._weather_entity, {}).get("forecast", [])
        return list(forecasts[:_FORECAST_DAYS])

    def _build_instructions(self, forecast: list[dict[str, Any]]) -> str:
        location = self._hass.config.location_name or "the garden"
        language = self._hass.config.language or "en"
        today = dt_util.now().strftime("%Y-%m-%d")
        lines = [
            f"You are the irrigation manager for a garden ({location}).",
            f"Today is {today}. Propose the plan for TOMORROW.",
            "Start times are computed by the system -- do not set them. All "
            "zones share one pump and never run at the same time.",
            "",
            "Forecast:",
        ]
        # Show whatever rain signal the provider has: some (Met.no daily among
        # them) publish no probability but do publish an amount in mm.
        lines.extend(
            f"- {str(day.get('datetime', ''))[:10]}: max "
            f"{day.get('temperature')}C, rain "
            f"{day.get('precipitation_probability', '?')}%, "
            f"{day.get('precipitation', '?')} mm"
            for day in forecast
        )
        lines.append("")
        lines.append("Zones (CURRENT settings):")
        for zone in self._zones.values():
            brief = zone_briefing(zone)
            seasonal = " [seasonal: you decide enabled]" if zone.spec.seasonal else ""
            state = "" if brief["enabled"] else " CURRENTLY DISABLED,"
            lines.append(
                f"- {brief['name']}{seasonal}:{state} "
                f"{brief['duration_minutes']} min, {brief['schedule']}, "
                f"evening {'on' if brief['second_run'] else 'off'}, "
                f"~{brief['litres_per_run']} L/run, "
                f"bounds {brief['duration_bounds'][0]}-{brief['duration_bounds'][1]} "
                f"min | {brief['description']}"
            )
        lines.append(
            f"Current rain-skip threshold: {self._hub.rain_threshold}% (a run "
            "is skipped at start time when the rain probability exceeds it -- "
            "a real-time mechanism, do not handle skips yourself)."
        )
        lines.extend(
            [
                "",
                "RULES:",
                "- Adjust DURATIONS per zone, not only the days. To give a "
                "zone more water: raise its duration toward its upper bound "
                "and/or enable its evening run. To give less: lower it toward "
                "the bottom bound.",
                "- Heat wave (2+ consecutive days >= 28C without significant "
                "rain): raised beds and young plantings go daily with "
                "durations adapted to their flow, useful evening runs on; "
                "robust zones are lengthened rather than moved to daily.",
                "- Normal weather for the region (<26C or regular rain): base "
                "regimes, all evening runs off.",
                "- Heavy rain expected (>70% probability, or substantial mm, "
                "over several days): reduced durations, the automatic rain "
                "skip does the rest.",
                "- rain_threshold: dry heat wave with sheltered beds 80-85 "
                "(avoid false skips); changeable weather 60-65; very rainy "
                "spell 50-55 (save water).",
                "- Reason in litres delivered; per-minute flow rates differ "
                "by a factor of 5 between zones, never adjust all zones "
                "uniformly.",
                "- Change as little as possible: if the current plan fits, "
                "return it unchanged.",
            ]
        )
        lines.append("")
        lines.append(build_response_format(self._zones, language))
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Persistence + repair issue
    # ------------------------------------------------------------------
    async def _async_save(self) -> None:
        hub = self._hub
        await self._store.async_save(
            {
                "date": hub.last_plan_date.isoformat() if hub.last_plan_date else None,
                "narrative": hub.last_plan_narrative,
                "failed": hub.last_plan_failed,
                "rejections": hub.last_plan_rejections,
            }
        )

    def _raise_overlap_issue(self, overlaps: list[str]) -> None:
        ir.async_create_issue(
            self._hass,
            DOMAIN,
            _OVERLAP_ISSUE,
            is_fixable=False,
            severity=ir.IssueSeverity.ERROR,
            translation_key="ai_plan_overlap",
            translation_placeholders={"conflicts": ", ".join(overlaps)},
        )

    def _clear_overlap_issue(self) -> None:
        ir.async_delete_issue(self._hass, DOMAIN, _OVERLAP_ISSUE)


def _snapshot(zones: list[ZoneState], hub: HubState) -> dict[str, Any]:
    return {
        "zones": {
            z.spec.subentry_id: (
                z.duration_minutes,
                z.schedule,
                z.second_run,
                z.enabled,
            )
            for z in zones
        },
        "rain_threshold": hub.rain_threshold,
    }


def _restore(zones: list[ZoneState], hub: HubState, snap: dict[str, Any]) -> None:
    for zone in zones:
        duration, schedule, second_run, enabled = snap["zones"][zone.spec.subentry_id]
        zone.duration_minutes = duration
        zone.schedule = schedule
        zone.second_run = second_run
        zone.enabled = enabled
    hub.rain_threshold = snap["rain_threshold"]
