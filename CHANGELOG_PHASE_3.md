# Phase 3 — Advanced mode wired into the integration

Phase 1 and 2 delivered `auth_firebase.py` and `api_management.py` as
standalone modules, verified live against Tap's management API. Phase 3
wires them into the HA integration as an **optional upgrade** on top of
the basic `sk_` API key setup. Architecture BRAVO: basic is always
required, advanced is opt-in and degrades silently on failure.

## Files touched

- `const.py` — 5 new `CONF_ADVANCED_*` keys, `ADVANCED_POLL_INTERVAL`,
  `ADVANCED_IDLE_INTERVAL`.
- `__init__.py` — `async_migrate_entry` (v1 → v2 adds
  `advanced_mode: False`); `_bootstrap_advanced_client` builds a
  `TapManagementClient` from the stored refresh token, rotates the
  token on refresh, never raises; coordinator now gets `mgmt=...` kwarg.
- `config_flow.py` — `VERSION = 2`; new `advanced_ask` + `advanced_creds`
  steps after user step; reauth flow (`async_step_reauth` +
  `async_step_reauth_confirm`); options flow restructured as a menu
  with `general` (all 8 existing options) + `advanced_menu` (enable /
  update / disable). Firebase / management errors mapped to localised
  keys (`invalid_email`, `invalid_password`, `user_disabled`,
  `firebase_unknown`, `account_discovery_failed`, `cannot_connect`).
- `coordinator.py` — accepts optional `mgmt`; new `_fetch_mgmt_sessions`
  + static `bucketise_mgmt_sessions`; dynamic interval switches to
  `ADVANCED_POLL_INTERVAL`/`ADVANCED_IDLE_INTERVAL` when mgmt is fresh;
  `_mark_degraded` logs at WARNING once per hour; `_maybe_trigger_reauth`
  fires HA's reauth flow after 3 consecutive auth failures with a
  30-minute cool-off. `TapData` gained `mgmt_active_by_charger`,
  `mgmt_last_closed_by_charger`, `mgmt_fresh`, and three helper methods
  (`mgmt_active`, `mgmt_last_closed`, `is_charging_active`).
- `sensor.py` — 5 new advanced-mode-gated sensors per charger
  (`current_session_energy`, `..._duration`, `..._driver`, `..._location`,
  `..._started_at`). `LastSessionEnergySensor` now prefers the
  management closed-session when available, with a `source: "management"
  | "public"` attribute for debuggability.
- `binary_sensor.py` — `ChargingBinarySensor.is_on` prefers
  `coordinator.data.is_charging_active()` when not None (i.e. when mgmt
  is fresh), falls back to connector-status otherwise. `source`
  attribute exposed.
- `api_management.py` — import fix: `from .auth_firebase import …` with
  bare-import fallback so the module still works in the standalone test.
- `strings.json` — added `options.step.init`, `options.step.advanced_menu`,
  `options.step.advanced_creds`, new config steps (`advanced_ask`,
  `advanced_creds`, `reauth_confirm`), new error keys, new entity names.
- `translations/{en,nl,de,fr}.json` — new files.
- `tests/test_coordinator_merge_standalone.py` — NEW.
- `CHANGELOG_PHASE_3.md` — this file.

## Unchanged

- `api.py`, `auth_firebase.py` (other than the new import shim),
  `button.py`, `switch.py`, `number.py`, `select.py`, `repairs.py`,
  `webhook.py`, `ocpp.py`, `device_trigger/condition/action.py`.
- `blueprints/`, `manifest.json`, integration display name.

## Entity count delta

| Mode            | Sensors per charger                                          |
| --------------- | ------------------------------------------------------------ |
| Basic-only (v1 migrated, advanced_mode=False) | same as phase B. No new sensors registered. |
| Advanced (opt-in) | +5 sensors: `current_session_energy`, `_duration`, `_driver`, `_location`, `_started_at`. All registered unconditionally; available-gated behind `coordinator.data.is_charging_active()`. |

Write surface unchanged. No new services. Write-enabled guard still
gates every write path as in phase B.

## Backward compatibility

A v1 entry migrated to v2 with `advanced_mode: False`:

- Same entities, same unique_ids, same names.
- Same interval behaviour (reads `OPT_SCAN_INTERVAL_*` from options).
- Option flow still offers all 8 existing general settings. Users
  who enter the options flow see a menu with "General" and
  "Advanced mode" — "General" opens exactly the same form as phase B.
- Reauth flow for the basic sk_ key path is unaffected (that path
  doesn't exist in our current integration, so no regression possible).

The test harness exercises the merge logic and degradation state
machine to catch any regression in the coordinator's behaviour with
`mgmt=None`.

## Validation

- `compileall` over all 19 integration modules: **ok**
- JSON validation of `strings.json`, `manifest.json`, and all 4
  translation files: **ok**
- YAML validation of `services.yaml`: **ok**
- Phase 1 / 2 / 3 standalone tests all compile: **ok**
- **`test_coordinator_merge_standalone.py`** (6 test groups,
  22 assertions total): **all pass**
  1. `bucketise_mgmt_sessions` pure split across 4 synthetic sessions
  2. `TapData.mgmt_active` / `is_charging_active` gate via `mgmt_fresh`
  3. `_fetch_mgmt_sessions` success path (counters reset)
  4. `_fetch_mgmt_sessions` with `mgmt=None` (basic-only fallback)
  5. 3× auth failure triggers reauth once via `entry.async_start_reauth`
  6. Network failure → degraded state, auth counter untouched

## Nothing deployed

The HA VM files are untouched (verified via mtimes at `06:21`,
`08:16`, `09:36`). No copies, no pyc busts. The LXC code is ready to
be deployed when you are; deployment steps are the standard pattern
from previous phases (`cp` the affected files to
`/mnt/ha_config/custom_components/tapelectric/`, clear pyc, reload
the integration or restart HA).

## Known limitations / deferred

- **Session detail not fetched eagerly.** `current_session_evse_id`,
  `_latitude`, `_longitude`, `_transaction_id` are not populated by the
  merge — they'd require a second call per active session
  (`get_session(cs_...)`). Deferred to phase 4 when we can justify the
  extra request cadence.
- **Firebase token age not exposed** as a diagnostic sensor. Phase 4
  can add `sensor.advanced_token_expires_in` if useful.
- **DE + FR translations are machine-grade** per phase-3 spec ("FR and
  DE: machine-translate acceptable, flag in CHANGELOG"). NL is natural
  Belgian Dutch; EN matches `strings.json` verbatim.
- **`push_external_meter_data` service** stays experimental from phase
  B; not changed in this phase.

## Next step for you

1. Deploy to HA VM:
   ```
   for f in __init__.py api_management.py auth_firebase.py binary_sensor.py \
            config_flow.py const.py coordinator.py sensor.py strings.json; do
     cp /home/weemaba/tapelectric_ha/custom_components/tapelectric/$f \
        /mnt/ha_config/custom_components/tapelectric/$f
   done
   cp -r /home/weemaba/tapelectric_ha/custom_components/tapelectric/translations \
         /mnt/ha_config/custom_components/tapelectric/
   rm -f /mnt/ha_config/custom_components/tapelectric/__pycache__/*.pyc
   ```
2. Restart Home Assistant (full restart recommended: migration happens
   once on setup, and the translation files are picked up at load).
3. Verify the v1→v2 migration logged: `Migrated Tap Electric entry from
   v1 to v2 …`. Existing entities should match the pre-migration
   snapshot.
4. Options → Advanced mode → Enable. Enter credentials. Expect the
   entry to reload automatically and 5 new sensors per charger to
   appear.
5. Start a physical charge session. `current_session_energy` should
   update every 30 s. Stop charging and verify
   `last_session_energy` updates with `source: "management"` in its
   attributes.
6. Let it idle for >5 min with no active session — interval should
   settle on `ADVANCED_IDLE_INTERVAL` (300 s).
