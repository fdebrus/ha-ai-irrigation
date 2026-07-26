Irrigation Scheduler
A Home Assistant custom integration that schedules irrigation zones around
existing valve or switch entities. Configured entirely from the UI — no YAML.
Features
One device per zone, added and edited from the integration page
Per-zone start time, duration and weekdays, all adjustable at runtime
Weather-aware rain skip with a configurable probability threshold
Runs survive a Home Assistant restart: a valve is never left open because HA
rebooted mid-cycle
Optional adoption of manual runs, so a valve opened from HomeKit, Google Home
or by hand still gets closed after the zone's duration
Run now / stop buttons per zone, plus a global stop
Install
HACS → three-dot menu → Custom repositories → add this repo as an
Integration.
Install, then restart Home Assistant.
Settings → Devices & Services → Add Integration → Irrigation Scheduler.
Pick your weather entity (optional — it only drives the rain skip).
On the integration page, Add zone once per zone: name it, pick its valve
or switch entity, and set the start time, duration and days.
Entities
Per zone: `switch.<zone>_enabled`, `number.<zone>_duration`,
`time.<zone>_start_time`, `sensor.<zone>_next_run`,
`sensor.<zone>_finishes_at`, `sensor.<zone>_status`, `button.<zone>_run_now`,
`button.<zone>_stop`.
Hub: `switch.irrigation_master`, `switch.irrigation_rain_skip`,
`number.irrigation_rain_threshold`, `sensor.irrigation_rain_probability`,
`button.irrigation_stop_all`.
Services
`irrigation_scheduler.run_zone` (optional `duration` in minutes) and
`irrigation_scheduler.stop_zone`, both targeting a zone's Run now button
entity.
Manual runs
Each zone has an Adopt manual runs option, off by default. When on, opening
the valve from outside the integration starts a tracked run that will be closed
after the zone's duration. When off, manual runs are left entirely alone. If a
valve seems to close itself a fixed interval after you open it by hand, this
setting is the first thing to check.
Development
See `CLAUDE.md`.
