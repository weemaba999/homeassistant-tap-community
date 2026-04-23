# Phase C — HACS community release

Date: 2026-04-23

Goal: turn the phase-1/2/3 codebase into something a stranger can
install from HACS, report a bug against, and contribute to — without
handing any trademarks to Tap Electric B.V. before we have their
blessing.

## What changed

### Packaging

- `manifest.json`: `version` bumped to `1.0.0`,
  `name` → `"Tap Electric Charger (Community)"`,
  `quality_scale: silver`, `documentation` and `issue_tracker`
  placeholders flip to the GitHub repo Bart will create (`weemaba999`
  literal stays — Bart fills it).
- `hacs.json` created at repo root. `country` restricted to
  BE/NL/DE/FR/AT/LU/IE/GB — where Tap operates today.
- `custom_components/tapelectric/info.md` for the HACS store page.

### CI

- `.github/workflows/validate.yml` — on every push/PR: compile,
  pytest + coverage, hassfest, HACS action.
- `.github/workflows/release.yml` — on `v*` tag: verify manifest
  version matches tag, build `tapelectric.zip`, pull the latest
  CHANGELOG section, publish a GitHub release.
- `.github/workflows/stale.yml` — standard 30/60 day auto-stale.
- `requirements_test.txt` with pinned versions of pytest,
  pytest-asyncio, pytest-cov, pytest-homeassistant-custom-component,
  aioresponses, coverage.

### Brand assets

Deliberately NOT shipped. `brands/README.md` documents the decision
and lists what to submit to home-assistant/brands if/when Tap
Electric B.V. grants written permission (or the community
commissions original artwork).

### Translations

- `strings.json` gained an `entity.sensor.charger_status` key so
  the already-used `_attr_translation_key = "charger_status"` in
  `sensor.py` has a localized label.
- `nl.json`: fixed "Laderer-ID" typo → "Laadpaal-ID"; added the
  missing `issues`, `services`, and `device_automation` sections
  so Dutch users get localized Repairs, service descriptions, and
  blueprint trigger / condition / action labels. Language reviewed
  for natural Belgian flavor ("laadsessie", "laadpaal",
  "chauffeur").
- `de.json`, `fr.json`: completed the coverage matrix with
  machine-translated strings for all missing sections. **Flagged
  for native-speaker review** — any fluency PR is welcome.
- `en.json`: added `charger_status` key; otherwise unchanged.

### Entity `entity_registry_enabled_default` audit

Per the live-API inventory (`docs/API_INVENTORY.md`, captured
2026-04-23), **EVBox Elvi only emits the `Energy` OCPP measurand**.
The 30+ speculative measurand entities we were registering were
permanently Unavailable on that hardware — hostile UX for the most
common charger in the Dutch / Belgian home market.

Change applied in `const.py`, `MEASURANDS` list:

| Measurand | Before | After | Why |
| --- | --- | --- | --- |
| Energy.Active.Import.Register | True | **True** | Every charger emits this |
| Energy (short) | True | **True** | EVBox-style fallback |
| Energy.Active.Import.Interval | True | False | Rare outside interval-mode firmwares |
| Energy.Active.Export.Register | False | False | V2G only |
| Energy.Reactive.Import.Register | False | False | Industrial only |
| Power.Active.Import | True | False | Not emitted by EVBox |
| Power.Active.Export | False | False | V2G only |
| Power.Offered | True | False | Not emitted by EVBox |
| Power.Reactive.Import | False | False | Industrial only |
| Power.Factor | False | False | Industrial only |
| Current.Import (+ L1/L2/L3) | True | False | Not emitted by EVBox |
| Current.Export | False | False | V2G only |
| Current.Offered (+ L1/L2/L3) | True | False | Not emitted by EVBox |
| Voltage (+ L1/L2/L3) | True | False | Not emitted by EVBox |
| Frequency | False | False | Rare |
| SoC | True | False | Requires ISO 15118 / HLC; EVBox does not emit |
| Temperature | True | False | Not emitted by EVBox |

Controls stay default-enabled: `charging_allowed` (switch),
`charge_current_limit` (number), `status`, `is_charging`,
`plug_connected`, `online`, `fault`, and the session sensors.
Info and Active tariff stay default-disabled (diagnostic).

Advanced-mode sensors unchanged: `current_session_energy`,
`_duration`, `_started_at` default-enabled;
`current_session_driver`, `_location` default-disabled
(fleet-oriented).

Existing entity registry entries keep their prior state — this only
affects fresh installs. Users whose charger does emit the bigger
measurand set can toggle the corresponding entity on with one click.

### Tests

New suite under `tests/` (pytest-based, `pytest.ini` declares the
`requires_ha` marker and async mode):

| File | Local | CI-only | What it covers |
| --- | --- | --- | --- |
| `test_api.py` | ✔ | | list_chargers, sessions, meter data, tariffs, webhooks, OCPP send/reset, auth header variants, all error-status mappings |
| `test_api_management.py` | ✔ | | accounts discovery, role-sessions, session detail, dataclass parse, retry-once-on-5xx |
| `test_auth_firebase.py` | ✔ | | sign-in happy + error codes, refresh happy + error codes, ensure_valid preserves email/uid |
| `test_ocpp.py` | ✔ | | payload envelope, reset payload, SetChargingProfile payload, auto-id monotonicity |
| `test_coordinator.py` | ✔ | | _async_update_data happy path, scope filter, auth-failure counter, interval switching (basic vs. advanced vs. idle), offline reconcile |
| `test_coordinator_merge.py` | ✔ | | bucketise_mgmt_sessions pure function, _fetch_mgmt_sessions error paths, reauth threshold, cooldown, recovery |
| `test_sensor.py` | ✔ | | **source attribute on last_session_energy**, **advanced-gated sensor availability**, `_to_kwh` conversion |
| `test_binary_sensor.py` | ✔ | | online/fault/plug/charging for all connector-status values, mgmt-preferred-over-public |
| `test_switch.py` | ✔ | | ChargeAllowedSwitch state + commands, maxAmperage preference |
| `test_number.py` | ✔ | | ChargeCurrentLimit, AutoStop{KWh,Minutes,Cost} |
| `test_button.py` | ✔ | | Reset button uses direct endpoint, reads reset type from entry.data |
| `test_select.py` | ✔ | | ResetTypeSelect persistence to entry.data |
| `test_migration.py` | ✔ | | async_migrate_entry v1 → v2, write_enabled gate |
| `test_reauth.py` | ✔ | | threshold, cooldown, firebase-vs-network error discrimination |
| `test_config_flow.py` | | ✔ | user step, bad key, advanced opt-out, reauth flow |
| `test_options_flow.py` | | ✔ | menu routing, general settings, advanced disable |

Local result: **174 passed, 8 skipped** (the 8 are `requires_ha`).

`conftest.py` provides the four fixtures the spec calls for
(`mock_aioresponse`, `load_fixture`, `hass_config_entry_{v1,v2_basic,v2_advanced}`)
plus a stubbing layer that lets the tests run without HA installed.

`tests/fixtures/api_inventory/` was preserved from phase 3 and now
serves as ground truth for `load_fixture()`. Six extra fixtures
added (charger list minimal/multi, webhook events, OCPP ok/400).

## What is deliberately NOT included

- **Deploy to the HA VM**: this phase is repo-work only. Bart
  installs the 1.0.0 release to the VM himself (or uses HACS).
- **Push to git remote**: no `git push` was performed. The branch
  stays local.
- **Brand assets**: no PNGs until Tap permits or the community
  commissions originals.
- **Native DE / FR translations**: flagged for a follow-up PR.

## What Bart should do next

1. Review `CHANGELOG.md` and `README.md` for tone + accuracy.
2. Create the public GitHub repo (suggested name:
   `homeassistant-tap-community`).
3. Replace every `weemaba999` placeholder:
   - `custom_components/tapelectric/manifest.json` (3 occurrences)
   - `README.md` (4 badge URLs + 3 internal links)
   - `custom_components/tapelectric/info.md` (3 occurrences)
4. Push. Enable GitHub Actions. Wait for the first green build.
5. `git tag v1.0.0 && git push origin v1.0.0` when happy.
