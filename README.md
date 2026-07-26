# Irrigation Scheduler

A Home Assistant custom integration that schedules irrigation zones around
existing valve or switch entities. Configured entirely from the UI — no YAML.

## Features

- One device per zone, added and edited from the integration page
- Per-zone start time, duration and weekdays, all adjustable at runtime — each
  weekday is its own switch
- Weather-aware rain skip: skips on the forecast probability, and falls back to
  the forecast amount in millimetres when a provider gives no probability
- Optional **sequential mode** that runs overlapping zones one at a time, for
  plumbing that can't supply two zones at once
- Runs survive a Home Assistant restart: a valve is never left open because HA
  rebooted mid-cycle
- Optional adoption of manual runs, so a valve opened from HomeKit, Google Home
  or by hand still gets closed after the zone's duration
- Skips and flags a run when the zone's valve is unavailable, and raises a
  repair issue if the valve entity is renamed away or removed
- Run now / stop buttons per zone, plus a global stop

## Install

1. HACS → three-dot menu → Custom repositories → add this repo as an
   **Integration**.
2. Install, then restart Home Assistant.
3. Settings → Devices & Services → **Add Integration** → Irrigation Scheduler.
   Pick your weather entity (optional — it only drives the rain skip).
4. On the integration page, **Add zone** once per zone: name it, pick its valve
   or switch entity, and set the start time, duration and days.

## Entities

Per zone: `switch.<zone>_enabled`, seven weekday switches
`switch.<zone>_monday` … `switch.<zone>_sunday`, `number.<zone>_duration`,
`time.<zone>_start_time`, `sensor.<zone>_next_run`,
`sensor.<zone>_finishes_at`, `sensor.<zone>_status`, `button.<zone>_run_now`,
`button.<zone>_stop`.

Hub: `switch.irrigation_master`, `switch.irrigation_rain_skip`,
`switch.irrigation_run_zones_sequentially`,
`number.irrigation_rain_threshold`, `number.irrigation_rain_amount_threshold`,
`button.irrigation_stop_all`. When a weather entity is configured you also get
`sensor.irrigation_rain_probability`.

## Services

`irrigation_scheduler.run_zone` (optional `duration` in minutes) and
`irrigation_scheduler.stop_zone`, both targeting a zone's **Run now** button
entity.

## Rain skip

With **Rain skip** on, a scheduled run is skipped when the forecast crosses a
threshold. The integration prefers the forecast *probability* (`Rain threshold`,
in %); if the weather provider exposes only a precipitation *amount* it falls
back to `Rain amount threshold` (in mm). When no forecast is available the run
is **not** skipped — a missing forecast is never treated as "0% / dry".

## Sequential mode

With **Run zones sequentially** on, a zone that would start while another is
already running is queued and starts when the running zone finishes — useful
when the water supply can't feed two zones at once. With it off, zones run
independently and may overlap. A valve opened by hand mid-cycle (an adopted run)
is never queued, since it is already physically open.

## Manual runs

Each zone has an **Adopt manual runs** option, off by default. When on, opening
the valve from outside the integration starts a tracked run that will be closed
after the zone's duration. When off, manual runs are left entirely alone. If a
valve seems to close itself a fixed interval after you open it by hand, this
setting is the first thing to check.

## Development

See `CLAUDE.md`.
