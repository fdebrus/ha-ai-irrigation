# CLAUDE.md

Working notes for Claude Code on this repo. Read this before changing anything.

## What this is

A Home Assistant **custom integration** that schedules four garden irrigation
zones (Jardin, Parking, Entrée, Framboisier). It replaces a YAML package that
used `input_boolean` / `input_number` / `input_datetime` helpers plus
automations. It is a *scheduler*, not a device driver: the valves already exist
in HA and this integration only calls services on them.

Target: Home Assistant **2025.6+**, Python **3.13**.

## Non-negotiable invariants

Break these and the integration gets worse than the YAML it replaced.

1. **Never own the hardware.** Always act on the user's existing valve/switch
   entity via `valve.open_valve` / `valve.close_valve` (or `turn_on`/`turn_off`
   for switch-domain entities). Never talk to a device directly.
2. **A run must survive a restart.** Live runs are persisted to `Store` and
   re-armed in `IrrigationScheduler._async_restore_runs`. If a run's end time
   passed while HA was down, close the valve on startup. A valve left open
   overnight is the worst failure mode this project has.
3. **`None` rain probability is not 0%.** When the forecast is unavailable the
   rain skip is bypassed entirely — the zone waters. Never coerce to `0.0`.
4. **Manual-run adoption is opt-in per zone.** The old YAML package started a
   zone timer whenever the valve turned on from *any* source, which is why runs
   started from HomeKit and Google Home appeared to close themselves after a
   fixed interval. Here that behaviour lives behind `adopt_manual_runs` on the
   zone subentry and defaults to off. Do not make it implicit again.
5. **Entities own the mutable values.** `enabled`, `duration`, `start_time` live
   on `ZoneRuntime` and are backed by `RestoreEntity`. The subentry data only
   supplies initial defaults. Do not add a second write path that edits the
   subentry from an entity — pick one source of truth and it is the entity.
6. **New scheduling rules go in `models.should_start`.** It is a pure function
   so it can be tested without booting HA. Do not inline conditions in the tick.

## Architecture

```
__init__.py     setup/unload, builds runtime data, forwards platforms
const.py        keys and defaults, no logic
models.py       ZoneRuntime / HubRuntime + should_start()  <- pure, tested
coordinator.py  RainCoordinator: weather.get_forecasts -> today's probability
scheduler.py    minute tick, valve I/O, stop timers, Store, adoption listener
config_flow.py  hub flow + ZoneSubentryFlowHandler (add/reconfigure a zone)
entity.py       base classes, device info, dispatcher subscription
switch/number/time/sensor/button.py   platforms
```

Zones are **config subentries** of a single hub entry, one device per zone.
Adding a fifth zone must stay a UI action — never hardcode zone counts.

Platform setup adds zone entities with
`async_add_entities([...], config_subentry_id=subentry_id)` so they land on the
right device. Hub entities are added without that argument.

Scheduling is a **once-a-minute tick** (`async_track_time_change(second=0)`)
rather than one timer per zone start time. This is deliberate: start times are
mutable at runtime via the `time` entities, and re-arming per-zone timers on
every change was the fiddly part. Do not "optimise" this back into per-zone
timers without a strong reason.

## Home Assistant rules Claude Code gets wrong from memory

- No blocking I/O in the event loop. No `requests`, no `time.sleep`, no file
  reads outside `async_add_executor_job`.
- `hass.data[DOMAIN]` is deprecated for this pattern — use `entry.runtime_data`
  with the `type IrrigationConfigEntry = ConfigEntry[IrrigationRuntimeData]`
  alias already defined in `__init__.py`.
- `weather.get_forecasts` is a **service with a response** — it needs
  `blocking=True, return_response=True` and returns
  `{entity_id: {"forecast": [...]}}`. The old `weather.get_forecast` (singular)
  is removed; do not reintroduce it.
- Entity names come from `_attr_translation_key` + `strings.json`, with
  `_attr_has_entity_name = True`. Do not set `_attr_name` directly.
- Every string shown in the UI must exist in **both** `strings.json` and
  `translations/en.json`. They are currently identical; keep them in sync or
  hassfest fails.
- `async_setup_entry` must forward platforms *before* the scheduler starts, so
  restored entity values are in place when the first tick fires.

## Dev loop

```bash
# One-off (pytest + pytest-homeassistant-custom-component live here now)
pip install -r requirements_dev.txt

# Tests (pure logic runs without HA; the rest uses pytest-homeassistant-custom-component)
pytest -q

# Live testing against real HA: symlink the component into your config
ln -s "$PWD/custom_components/irrigation_scheduler" \
      /path/to/ha/config/custom_components/irrigation_scheduler
```

Reload without a full restart from **Developer Tools → YAML → Reload custom
integrations**, then reload the config entry from the integration page. Full
restarts are only needed after `manifest.json` changes.

For a throwaway instance, `ludeeus/integration_blueprint` has a devcontainer and
`scripts/develop` that boots HA with this folder mounted; copying those two in
is the fastest way to get an isolated test HA.

Verify before opening a PR: `pytest`, then the `hassfest` and `hacs` GitHub
Actions in `.github/workflows/validate.yml`.

## Current state

Working: config flow with subentries, per-zone entities, minute tick, rain skip,
run/stop buttons and entity services, restart recovery, manual-run adoption.

Tested: `should_start` logic, config-entry setup/unload with a zone subentry,
and the scheduler's dangerous paths — restart recovery (expired run closes the
valve, future run re-arms its stop timer), adoption on/off, `_self_driven`
suppression, and stop cancelling a pending close.

Known gaps — pick these up in roughly this order:

1. ~~**No tests beyond `should_start`.**~~ **Done.** `tests/test_setup.py`
   covers setup/unload; `tests/test_scheduler.py` covers restart recovery (both
   branches), the adoption listener with `adopt_manual_runs` on and off, the
   self-driven suppression, and stop cancelling the pending stop timer.
2. ~~**Sequential mode.**~~ **Done.** A hub-level "Run zones sequentially"
   switch (`HubRuntime.sequential`) queues a start that would overlap another
   running zone and releases it when that zone stops. Adopted runs bypass the
   queue (the valve is already open); the queue is not persisted (a queued run
   never opened a valve, so a restart safely forgets it). Status sensor shows
   `queued`.
3. ~~**`weekdays` is subentry-only.**~~ **Done.** Seven per-zone `switch`
   entities (`weekday_mon`..`weekday_sun`, config category) own the zone's
   `weekdays` list and restore it across restarts; the subentry still seeds the
   initial values. Same source-of-truth model as `duration`/`start_time`
   (invariant 5): the subentry selector remains as an initial default only.
4. ~~**Valve unavailability.**~~ **Done.** `async_start_zone` checks the valve
   state before opening (non-adopted starts only); if it is missing or
   `unavailable` it logs a warning, sets `last_skipped_reason` /status to
   `valve_unavailable`, does not start a phantom run, and releases the next
   queued zone.
5. ~~**Precipitation in mm.**~~ **Done.** `RainCoordinator` now returns a
   `RainForecast(probability, precipitation_mm)`. `should_start` uses
   probability when present and falls back to the mm amount otherwise, against a
   new `rain_mm_threshold` (hub number entity, default 2 mm). A missing
   probability still never reads as 0% — it defers to mm, and both missing means
   the zone waters.
6. ~~**No repair issues.**~~ **Done.** `IrrigationScheduler._check_valve_entities`
   (run at start) raises a `valve_missing_<subentry_id>` repair issue when a
   zone's valve is neither registered nor present, and clears it once the valve
   resolves. The registry check tolerates startup load order.
7. ~~**Quality scale.**~~ **Done.** `manifest.json` now declares
   `"quality_scale": "silver"`. The integration already meets the substantive
   silver bar: every entity has a unique id, the single hub entry is guarded by
   a unique id, config-entry unload is clean, and valve unavailability is
   handled (gap 4). The value is a plain manifest string at runtime; the
   `hassfest`/`hacs` GitHub Actions are the authoritative check.

## Migrating from the YAML package

There is no automatic migration and there should not be — the old helpers are
`input_*` entities the user created, and deleting them from code would be rude.
Migration is: set up the integration, recreate the four zones in the UI, update
the Mushroom dashboard to the new entity IDs, then delete
`config/packages/irrigation.yaml` and the `packages:` include if nothing else
uses it. Old dashboard cards reference `input_number.irrigation_zoneN_duration`;
the new equivalent is `number.<zone>_duration`.
