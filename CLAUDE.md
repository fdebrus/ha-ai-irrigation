# CLAUDE.md

Working notes for Claude Code on this repo. Read this before changing anything.
`docs/ai-contract.md` is the companion spec for the AI layer.

## What this is

A Home Assistant custom integration that schedules five irrigation zones in a
garden in Waterloo, Belgium, and adapts them daily using an LLM. It replaces a
YAML package (`irrigation.yaml` + `irrigation_ai.yaml`) that used `input_*`
helpers, timers and automations. Both files are in `reference/` — read them
before implementing anything, they encode a year of hard-won detail.

It is a *scheduler*, not a device driver: the valves, buttons and pump already
exist in Home Assistant and this integration only calls services on them.

Target: Home Assistant **2025.6+**, Python **3.13**.

## The garden, precisely

All five zones draw from **one pump** (Grundfos RMQ, monitored via a smart plug
as `binary_sensor.pump_running`). Two zones can never run at once. This is the
central constraint and it shapes everything else.

| Order | Zone | Driver | Notes |
|---|---|---|---|
| 1 | Jardin | `distributor` | One valve → GARDENA 3-outlet distributor, 3 m of porous hose per outlet. Occupies **3 × duration** plus gaps. Japanese maple, nandinas, grasses, planted 4 months ago — fears waterlogging (phytophthora) as much as drought. |
| 2 | Gazon | `button` | Aiper IrriSense on a dedicated line. Start/stop **buttons**, no state entity — the run timer is the only truth. Rotary sprinkler, far higher flow than the porous hose. Seasonally enabled/disabled by the AI. |
| 3 | Parking | `valve` | 10 m hose. Blueberries in fruit, raised bed, full sun against a white wall. |
| 4 | Entrée | `valve` | 17 m hose — the most productive line per minute. Established spiraeas, robust. |
| 5 | Framboisier | `valve` | 5 m hose. Young raspberry canes, raised bed. |

Porous hose delivers roughly 2–4 L/h per metre at 1 bar. **Zone flow rates
differ by a factor of five**, so a uniform percentage adjustment across zones is
always wrong — this is why the model is given computed litres per run rather
than being asked to infer them.

## Non-negotiable invariants

1. **Never own the hardware.** Act on the user's existing entities through
   their own services. Never talk to a device directly.
2. **One pump, one zone at a time.** Start times are **derived** by
   `planner.plan_start_times`, never stored and never user-editable. What the
   user sets is the morning base (05:30), the evening base (19:00) and the
   margin. Recompute via `apply_start_times` after *any* change to a duration,
   an enabled flag or a base time.
3. **A run must survive a restart.** Live runs are persisted and re-armed on
   setup; a run whose end time passed while HA was down closes its valve
   immediately. A valve left open overnight is the worst failure this project
   has.
4. **`None` rain probability is not 0%.** Missing forecast bypasses the rain
   skip and the zone waters.
5. **Manual-run adoption is opt-in per zone.** The YAML started a zone timer
   whenever a valve opened from any source, which is why HomeKit runs looked
   like the device was closing them. Keep it behind a per-zone flag, default
   off.
6. **New scheduling rules go in `models.py` / `planner.py`.** They are pure —
   no `hass`, no I/O. That is what makes the sequencing testable. Do not inline
   conditions in the scheduler tick.
7. **The AI writes setpoints, never actions.** It may propose duration,
   schedule, second run, rain threshold, and enabled *only for zones flagged
   seasonal*. Every value passes through `planner.clamp_zone_plan` or
   `clamp_rain_threshold`, which fall back to the current value. The AI can
   never open, close, delay or cancel a run, can never touch a zone that is
   currently running, and its failure mode is "yesterday's plan, unchanged".

## Architecture

```
models.py       ZoneSpec / ZoneState / HubState, DriverType, SchedulePreset  <- pure
planner.py      sequencing, overlap detection, AI clamping, zone briefings   <- pure
drivers.py      ValveDriver / DistributorDriver / ButtonDriver               <- I/O
coordinator.py  RainCoordinator: weather.get_forecasts -> probability
ai.py           nightly plan: build prompt, call ai_task, clamp, apply
scheduler.py    minute tick, run/stop, Store persistence, adoption listener
config_flow.py  hub flow + ZoneSubentryFlowHandler (driver-specific forms)
entity.py       base classes, per-zone device info
switch/number/select/time/sensor/button/binary_sensor.py
```

### Drivers

The driver abstraction is why Gazon works. Interface:

```python
async def async_start(self) -> None
async def async_stop(self) -> None
@property
def is_open(self) -> bool | None    # None when the driver has no feedback
```

- `ValveDriver` — `valve.open_valve` / `close_valve`, or `switch.turn_on/off`.
- `DistributorDriver` — opens the valve once, advances outlets on a pulse.
  Occupancy is `outlets × duration + gaps`.
- `ButtonDriver` — presses a start button and a stop button. `is_open` returns
  `None`. **Never assume you can read this zone's state**; the run timer is
  authoritative, and the adoption listener must skip these zones entirely.

### Entities

Per zone: `switch.<zone>_enabled`, `switch.<zone>_second_run`,
`number.<zone>_duration`, `select.<zone>_schedule`,
`sensor.<zone>_morning_start`, `sensor.<zone>_evening_start`,
`sensor.<zone>_next_run`, `sensor.<zone>_finishes_at`, `sensor.<zone>_status`,
`button.<zone>_run_now`, `button.<zone>_stop`.

Note `sensor` for the start times, not `time` — they are derived (invariant 2).

Hub: `switch.irrigation_master`, `switch.irrigation_rain_skip`,
`switch.irrigation_ai`, `number.irrigation_rain_threshold`,
`time.irrigation_morning_base`, `time.irrigation_evening_base`,
`time.irrigation_plan_at`, `sensor.irrigation_rain_probability`,
`sensor.irrigation_daily_plan`, `binary_sensor.irrigation_overlap`,
`binary_sensor.irrigation_no_flow`, `button.irrigation_stop_all`,
`button.irrigation_plan_now`.

## Home Assistant rules that get remembered wrong

- No blocking I/O in the event loop.
- Use `entry.runtime_data`, not `hass.data[DOMAIN]`.
- `weather.get_forecasts` needs `blocking=True, return_response=True` and
  returns `{entity_id: {"forecast": [...]}}`. `get_forecast` (singular) is gone.
- `ai_task.generate_data` likewise needs `return_response=True`. Declare the
  shape with `structure` — see `docs/ai-contract.md`.
- **Sensor states cap at 255 characters.** The plan narrative goes in an
  attribute, never the state.
- Entity names come from `_attr_translation_key` + `strings.json` with
  `_attr_has_entity_name = True`. Never set `_attr_name`.
- Every UI string must exist in both `strings.json` and `translations/en.json`.
- Forward platforms before starting the scheduler, so restored entity values
  are in place when the first tick fires.

## Dev loop

```bash
pip install -r requirements_dev.txt
pytest -q
ruff check .
scripts/develop          # throwaway HA with this component mounted
```

Reload without restarting: Developer Tools → YAML → Reload custom integrations,
then reload the config entry. Full restart only after `manifest.json` changes.

Verify before a PR: `pytest`, `ruff`, then the `hassfest` and `hacs` workflows.

## Current state

The rebuild is complete: all seven items below are done. The pure domain model
and planner carry the tested logic (occupancy for all three driver types,
sequencing, overlap detection, AI clamping); the rest is the I/O shell around
them.

1. ~~**Drivers + scheduler.**~~ **Done.** `drivers.py` (Valve/Distributor/Button)
   behind one interface; `scheduler.py` fires derived slots, runs/stops through
   drivers, persists runs and recovers on restart, and adopts manual runs while
   skipping button zones. One pump: a start stops any other running zone.
2. ~~**Config flow.**~~ **Done.** Hub flow (weather, pump sensor, AI task, bases,
   plan time, margin) plus a zone subentry flow with a driver-specific second
   step (valve; valve + outlets + gap; or start/stop buttons).
3. ~~**Platforms.**~~ **Done.** switch/number/select/time/sensor/button/
   binary_sensor; start times are read-only sensors; any duration/enabled/base
   change calls `scheduler.recompute_start_times` → `planner.apply_start_times`.

4. ~~**AI layer.**~~ **Done.** `ai.py` builds the `ai_task` `structure` from the
   configured zones, requests the nightly plan, and applies it through
   `planner.clamp_zone_plan` / `clamp_rain_threshold`. Every field falls back to
   the current value, running zones are deferred, an overlap rolls the plan back
   and raises a repair issue, and any exception keeps yesterday's plan.
   `tests/test_ai.py` exercises the malformed responses (missing keys, wrong
   types, out-of-range durations, unknown schedule, a non-seasonal zone the AI
   may not toggle, a running zone), the overlap rollback, and a failing
   `ai_task` service.

5. ~~**Pump watchdog.**~~ **Done.** The scheduler evaluates flow on every tick
   (and on stop): a zone open past `NO_FLOW_GRACE_MINUTES` with the configured
   pump sensor still off sets `scheduler.no_flow` and raises the `pump_no_flow`
   repair issue; it clears when the pump reports flow or the zone stops.
   Membership is by the tracked run, so Gazon (a button zone with no valve to
   watch) still counts as running. Inert when no pump sensor is configured.
   Covered in `tests/test_scheduler.py`.
6. ~~**Repair issues.**~~ **Done.** Each driver exposes `required_entities`; the
   scheduler checks them every tick and raises a per-zone `zone_entity_missing`
   repair issue when a configured valve/button no longer exists in HA, clearing
   it when the entity returns. Covered in `tests/test_scheduler.py`.
7. ~~**Quality scale.**~~ **Done.** `manifest.json` declares
   `"quality_scale": "silver"`; every platform sets `PARALLEL_UPDATES = 0`
   (all entities are local push over the dispatcher), unique ids are set on
   every entity, the hub entry is single-instance (`already_configured`), and
   unavailability is surfaced via the no-flow and missing-entity repair issues.

## Migrating from the YAML package

No automatic migration — the old helpers are `input_*` entities the user
created, and deleting them from code would be rude. Set up the integration,
recreate the five zones, repoint the Mushroom dashboard, then remove
`irrigation.yaml` and `irrigation_ai.yaml`.

Dashboard entity mapping: `input_number.irrigation_zoneN_duration` →
`number.<zone>_duration`; `input_select.irrigation_zoneN_schedule` →
`select.<zone>_schedule`; `input_datetime.irrigation_zoneN_start` →
`sensor.<zone>_morning_start` (now read-only);
`input_text.irrigation_ai_last_summary` →
`state_attr('sensor.irrigation_daily_plan', 'narrative')`.
