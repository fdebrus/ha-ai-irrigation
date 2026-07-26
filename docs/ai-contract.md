# AI contract

How the nightly plan is requested, validated and applied. Ported from
`irrigation_ai.yaml`, with the parts that were fragile replaced.

## When it runs

Once a day at the configured planning time (currently 22:30), gated on
`switch.irrigation_ai`. The plan is for **tomorrow**, which is why it runs late
in the evening rather than early morning.

If Home Assistant was down at planning time, generate the plan on startup when
`hub.last_plan_date` is not today. Never let a missing plan block watering.

## What the model is told

Assembled from config and computed values — never from a hand-edited prose
block. That was the main weakness of the YAML version: the `ZONES ET PROFILS`
and `DÉBITS RÉELS` sections were maintained by hand and drifted whenever the
garden changed.

1. **Location and date.** From `hass.config` (Waterloo, Belgium) and `now()`.
2. **Five-day daily forecast.** `weather.get_forecasts`, `type: daily` — date,
   max temperature, precipitation probability.
3. **Per-zone briefing.** One `planner.zone_briefing()` dict per zone: name,
   the user's free-text `description`, driver type, current setpoints, hose
   length, outlet count, computed litres-per-run range, duration bounds, and
   last run timestamp.
4. **Shared-source note.** All zones draw from one pump; start times are
   computed by the system and are not the model's concern.
5. **Seasonal rules** for any zone flagged `seasonal` (Gazon): dormancy is
   survivable, the yo-yo of repeated wake-ups is the real damage, only flip on
   a genuine seasonal turn.
6. **Change-minimally instruction.** If the current plan is fine, return it
   unchanged.

Keep the operator persona. The narrative is requested in the Home Assistant
configured language (`hass.config.language`), so a French installation gets a
French narrative and any other locale gets its own — nothing about the garden's
country is hardcoded in the prompt; the location comes from
`hass.config.location_name`.

## Response schema

Request it as **raw one-line JSON in the instructions** via a format block
built dynamically from the configured zones (`build_response_format`), and
parse the reply leniently — strip markdown fences before `json.loads`
(`parse_plan`), exactly as the YAML package did.

Do **not** use `ai_task.generate_data`'s `structure` parameter. It was tried
first and the Anthropic structured-output endpoint rejected the generated
schema twice in production: `400 — For 'number' type, properties maximum,
minimum are not supported`, and after removing those, `400 — Schema is too
complex` (five 6-value schedule enums). The instructions-based format worked
for a full season; the real enforcement is the clamp layer, not the schema.

One group of keys per zone slug, flat rather than nested:

```
<slug>_duration      integer minutes; bounds spelled out in the format block
<slug>_schedule      string; allowed values = SchedulePreset values, listed
<slug>_second_run    boolean
<slug>_enabled       boolean — ONLY for zones flagged seasonal
rain_threshold       integer, 50-90
narrative            string, in the HA configured language
```

The example object in the format block carries each zone's *current* values,
which doubles as the "change as little as possible" instruction.

## Validation

Nothing the model returns reaches a setpoint except through
`planner.clamp_zone_plan` and `planner.clamp_rain_threshold`. Both fall back to
the zone's **current** value, not to a default, so a partial response means
"change nothing" rather than "reset everything".

- durations clamp to the zone's own `min_duration`/`max_duration`
- schedules must parse as a `SchedulePreset`, else the current one is kept
- `enabled` is rejected unless the zone is flagged seasonal
- `rain_threshold` clamps to 50-90

Every rejection is collected and logged, and surfaced on the plan sensor's
`rejections` attribute. Silent clamping hides a drifting prompt.

## Applying the plan

1. Clamp every zone.
2. Write the setpoints.
3. Recompute start times with `planner.apply_start_times` — durations changed,
   so the sequence must be re-laid-out or zones will collide.
4. Run `planner.find_overlaps` as the final net. A non-empty result is a bug:
   log an error, raise a repair issue, and leave yesterday's plan in place.
5. Store the narrative on `sensor.irrigation_daily_plan` as an attribute.

**Never apply a plan to a zone that is currently running.** Defer that zone's
setpoints until its run finishes.

## Failure behaviour

Any failure — service error, timeout, unparseable response, empty result —
leaves every setpoint untouched, sets `hub.last_plan_failed = True`, and keeps
the previous narrative with a stale marker. Watering proceeds on yesterday's
plan. The system must degrade to a working deterministic scheduler, never to a
stopped one.

## Sensor shape

`sensor.irrigation_daily_plan`
- state: the plan date (`YYYY-MM-DD`), or `unknown` before the first run
- attributes: `narrative` (full text, no truncation), `rejections`,
  `generated_at`, `stale`

The old `input_text` helper capped the summary at 255 characters and the
automation truncated to 250. Attributes have no such limit — do not reintroduce
truncation.
