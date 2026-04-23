# Phase B — user control surfaces

Phase A made the integration community-ready at the entity level. Phase
B adds the knobs users need to actually operate it without reading code:
post-setup configuration, a Repairs integration, device automations, and
shipped blueprints. Also tightens the write surface with a kill-switch
all write paths honour.

## Options flow

Added `TapOptionsFlowHandler`, reachable from Settings → Devices &
Services → Tap Electric → Configure. Keys:

| Option                     | Range     | Default |
| -------------------------- | --------- | ------- |
| `scan_interval_active_s`   | 10–300    | 30      |
| `scan_interval_idle_s`     | 60–3600   | 300     |
| `sessions_history_limit`   | 10–500    | 50      |
| `meter_data_limit`         | 20–500    | 100     |
| `stale_threshold_minutes`  | 5–120     | 15      |
| `round_energy_decimals`    | 0–3       | 3       |
| `round_power_decimals`     | 0–3       | 2       |
| `write_enabled`            | bool      | True    |

The coordinator reads polling / limits / stale threshold options live on
each tick. `write_enabled` triggers a reload via an update listener so
the new setting takes effect immediately on every platform.

Toggling `write_enabled` off makes this a **read-only integration**:
`switch.turn_on/off`, `number.set_value` on the current-limit slider,
the reset button, and all four write services raise `HomeAssistantError`
and create a Repairs issue explaining why.

## New platforms

- **select** — `ResetTypeSelect` (Soft/Hard). Off by default. Stored in
  `entry.data` so switching it doesn't trigger a reload.
- **number** — added `AutoStopKWh`, `AutoStopMinutes`, `AutoStopCost`
  per charger. Off by default. Values persisted in `entry.data`. These
  are **HA-local thresholds**, NOT pushed to the charger — they're
  meant to be paired with the shipped `auto_stop_at_kwh.yaml` blueprint
  so users get server-side flexibility plus HA-side stopping logic.

## Button simplification (BREAKING)

Removed the phase-A `reset_soft` and `reset_hard` buttons. Replaced with
a **single** `{cid}_reset` button whose type is chosen by the companion
`{cid}_reset_type` select. Users who scripted the old unique_ids must
update their automations to one of:

- service `tapelectric.reset_charger` with `reset_type: Soft|Hard`,
- device action `reset` (with the device automation system added in
  this release),
- or the new button + select pair.

HA does not auto-migrate orphaned unique_ids; the old entity registry
rows will show as unavailable until the user removes them.

## Repairs integration (`repairs.py`)

Three issue types:

- `auth_expired` — raised after two consecutive 401s from `/chargers`.
- `charger_offline` — charger's `updatedAt` is older than 24h.
- `write_blocked` — a write path refused because `write_enabled=False`
  (rate-limited to at most one issue per hour per entry).

All three clear themselves when the underlying condition resolves.

## Device automations

Three new modules that expose the integration to HA's device-automation
UI — users can pick triggers, conditions, and actions by device rather
than by entity id.

- `device_trigger.py` — `charging_start`, `charging_stop`,
  `plug_connected`, `plug_disconnected`, `fault`.
- `device_condition.py` — `is_charging`, `is_connected`, `is_online`.
- `device_action.py` — `pause`, `resume`, `set_limit`, `reset`. Actions
  route through our services, so the write-enabled guard applies.

Entity resolution is done via the entity registry by matching
`{charger_id}{suffix}` to `unique_id`. If the target entity doesn't
exist yet (e.g. device is disabled), triggers and conditions silently
no-op rather than erroring, which keeps the automation editor usable
during setup.

## Services

Added `tapelectric.push_external_meter_data` (**experimental**). Forwards
an external meter reading (power / energy / current / voltage /
measured_at) to `POST /meters/{meterId}/data` for fleets that feed a
home energy monitor into Tap's load balancing. Marked experimental in
`services.yaml` — the `ExternalMeterData` contract is under-documented
for this key scope. Still honours `write_enabled`.

## Translations (`strings.json`)

Added sections:
- `options.step.init` — labels and description for every option key.
- `issues.*` — text for the three Repairs issue types, with
  placeholders for charger / entry IDs.
- `services.*` — names and descriptions for all 5 services.
- `device_automation.{trigger_type,condition_type,action_type}` —
  translated labels for the device-automation picker.

## Blueprints

Shipped four ready-to-import automation blueprints under
`blueprints/automation/tapelectric/`:

- `solar_excess_charging.yaml` — every 30 s, match charge current to
  available solar excess; pause after N minutes below minimum.
- `dynamic_tariff_pause.yaml` — pause above one price threshold, resume
  below another.
- `auto_stop_at_kwh.yaml` — pair with `number.auto_stop_kwh` to cap a
  session's delivered energy.
- `charger_offline_alert.yaml` — notify when `binary_sensor.*_online`
  goes off for longer than the configured grace period.

## Constants (`const.py`)

- Added `OPT_*` keys, `DEFAULT_OPTIONS`, `OPTION_BOUNDS`.
- Added `DATA_AUTO_STOP`, `DATA_RESET_TYPE` data-bag keys.
- Added `PATH_METER_DATA_PUSH` for the new service.

## Known caveats / deferred

- `push_external_meter_data` has not been tested against a live Tap
  meter — the field layout (`powerW` / `energyWh` / etc.) is inferred
  from common conventions. Mark as experimental until at least one user
  confirms it on a real install.
- Device automations not exercised in the HA UI (needs a running
  instance). The Python side parses cleanly.
- Auto-stop number entities rely on the shipped blueprints (or the
  user's own automation) for enforcement. Nothing in the core platform
  reads them — this is intentional to keep the integration observable
  by default.
- Phase A's removed reset buttons will show as orphaned entities after
  the update. Add a one-time notice in the README so users know to
  delete them.

## Next step for user

- Deploy to HA VM, open `Settings → Devices & Services → Tap Electric
  → Configure` to exercise the OptionsFlow.
- Import `solar_excess_charging.yaml` via Settings → Automations &
  Scenes → Blueprints → Import blueprint.
- Add a device automation referencing the charger to verify the
  device_trigger/condition/action modules load.
