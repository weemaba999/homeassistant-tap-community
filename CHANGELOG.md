# Changelog

All notable changes to this project land here. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the
project uses [semantic versioning](https://semver.org/).

## [Unreleased]

### Fixed
- Remote Stop remains available for active sessions whose role-session list
  row omits the OCPP transaction ID. The button now fetches session detail on
  demand before sending RemoteStopTransaction. Addresses the Remote Stop path
  reported in #6.

## [1.1.1] - 2026-05-29

### Fixed
- OCPP envelope schema for `/chargers/{id}/ocpp` endpoint
  corrected to flat camelCase with `data` as JSON-encoded string,
  per Tap's OpenAPI spec. Unblocks Reset, RemoteStop, RemoteStart,
  SetChargingProfile, and UnlockConnector via the public API
  path. Contributed by @ronlimon (#4). Verified live on EVBox
  Elvi (Reset) and Ratio io6 (all five actions).
- README `id_tag` format documentation corrected: this is the raw
  NFC chip UID (8 hex characters, e.g. `12AB34CD`), not the
  `ET_`-prefixed visualID shown in the Tap webapp. The same fix
  is applied to the OptionsFlow help strings.

### Added
- Brand icon shipped locally in
  `custom_components/tapelectric/brand/` per the HA 2026.3+ local
  brand images convention. Visible on HA 2026.3 and newer; older
  versions display the default integration icon.

### Known limitations (unchanged from v1.1.0)
- Charger firmware support for OCPP actions is independent of
  this envelope fix. EVBox Elvi firmware accepts Reset but
  rejects RemoteStop and RemoteStart at the OCPP layer (verified
  via direct API testing and via Tap's own webapp). Other
  charger models may behave differently. Seeking beta testers
  via the HA Community forum.

## [1.1.0] — 2026-04-27

Remote Start/Stop charging via the management API, with full UI
configuration. The scope outgrew a patch bump — bumped to 1.1.0
under semver because the public surface gained two new entities,
two new public client methods, and a new options-flow step.

### Fixed

- **OCPP envelope schema and endpoint corrected.** The "Charging
  allowed" switch in 1.0.0 posted to `/chargers/{id}/ocpp` with a
  SetChargingProfile body that the public API rejects with HTTP 400
  ("Data field is required") across every payload variant we
  tested. Reverse-engineered Tap's own webapp on 2026-04-27 and
  confirmed the working schema lives on a different endpoint
  (`/chargerManagement/chargers/{id}/ocppMessages`) with a snake_case
  envelope (`message_type` + `remote_*_transaction_details`).

### Added

- **Remote Start charging button** (`button.tapelectric_start_charging`).
  Advanced mode only. Sends OCPP RemoteStartTransaction with a
  configured RFID `id_tag` and per-charger `outlet_id`. Disabled
  automatically when a session is already active or required
  config is missing.
- **Remote Stop charging button** (`button.tapelectric_stop_charging`).
  Advanced mode only. Sends OCPP RemoteStopTransaction with the
  currently-active management-API transaction id. Disabled when no
  active session is known.
- **Options → Advanced mode → Remote start/stop settings.** New
  options-flow step with three fields:
  - Default RFID `id_tag` (text input; format `TAP-NNNNNN-N`)
  - Per-charger `outlet_id` (text input per known charger,
    dynamically rendered from the coordinator's charger list)
  - Optional `profile_id` override (defaults to the Firebase
    `user_id` when empty)

  Outlet IDs for chargers temporarily unreachable from the
  coordinator are preserved across saves — the form only edits
  what it can currently see. Stored additively in `entry.data`;
  v1.0.0 entries load without migration.
- `TapManagementClient.remote_stop_transaction` and
  `remote_start_transaction` — the new write methods. Auth failures
  (401/403) raise `TapManagementAuthError`; network errors return
  `None` so the button stays responsive. The synchronous response
  envelope from the live API is empty 200, so we synthesise
  `{"status": "Accepted"}`; an explicit `{"status": "Rejected"}` (if
  the API ever surfaces it) is passed through unchanged.
- `tests/probe_har.py` — reproducible HAR-capture parser for the
  next time we need to chase Tap-side schema changes. Masks
  Authorization/Cookie/api-key values automatically.
- **31 new tests** covering the management-API write paths
  (`test_api_management.py`), the Start/Stop button entities
  (`test_button.py`), and the expanded options flow
  (`test_options_flow.py`). Coverage: 68% aggregate, with the
  new modules at `api_management` 89% and `button` 81%.

### Deprecated

- `switch.charging_allowed` (the SetChargingProfile-based "Charging
  allowed" entity). Ships with `entity_registry_enabled_default=
  False`. Existing automations referencing it still resolve to a
  (disabled) entity, so nothing crashes — migrate to the new
  Stop/Start buttons when convenient.

### Known limitations

- **EVBox Elvi firmware silently refuses both RemoteStop and
  RemoteStart.** Verified empirically through both direct API
  testing AND Tap's own webapp — this is an EVBox firmware
  restriction, not a Tap- or integration-side bug. The integration
  logs a warning and stays responsive.
- **Other charger models untested.** Beta testers welcome via the
  HA Community forum thread — community datapoints will determine
  which models we can confidently support in 1.2.0.
- **Per-charger outlet_id input is one long form.** Drivers with
  many chargers see all fields stacked on one page. Pagination
  follow-up planned for 1.2.0.
- **7 tests in `test_options_flow.py` remain xfailed** pending
  HA integration loader plumbing in the test harness. Marked with
  a `TODO v1.2.0` at the top of the module. `strict=False` so they
  flip to passing automatically once the loader plumbing lands.

## [1.0.0] — 2026-04-23

First HACS-releasable version. Brings together every phase A/B/1/2/3
feature plus the phase-C packaging polish.

### Added

- **Phase A — basic mode skeleton**: sk_-key config flow, public REST
  client (`api.py`) covering `/chargers`, `/charger-sessions`,
  `/charger-sessions/{id}/session-meter-data`, `/locations`, plus
  write endpoints for OCPP SetChargingProfile and Reset.
- **Phase B — options, webhooks, controls**: Options flow with
  polling cadence, stale threshold, write-enabled gate; webhook
  handler with HMAC signature verification and replay-protection;
  switch / number / button / select platforms; Repairs integration
  for auth and offline conditions.
- **Phase 1 — Firebase auth**: `auth_firebase.py` with sign-in,
  refresh, leeway-based ensure_valid. Referrer-restricted API key
  handled by always attaching the `web.tapelectric.app` Origin /
  Referer.
- **Phase 2 — Management API client**: `api_management.py` hitting
  `/management/accounts`, `/role-sessions`, `/sessions/{id}` with
  the Firebase ID token. ManagementSession dataclass with list vs.
  detail merge.
- **Phase 3 — Advanced mode in HA**: opt-in advanced mode via
  Options → Advanced mode; coordinator bootstrap keeps the
  integration usable even when the management side fails; token
  rotation via `async_update_entry`; auto-trigger of reauth flow
  after 3 consecutive auth failures, cool-off to prevent spam;
  dynamic scan intervals (advanced cadence vs. basic cadence);
  graceful degradation with one-log-per-hour rate-limit.
- **Advanced-mode sensors**: `current_session_energy`,
  `current_session_duration`, `current_session_started_at`
  (default-enabled); `current_session_driver`,
  `current_session_location` (default-disabled — fleet installs).
- **Phase C — HACS release packaging**:
  - `hacs.json` + `info.md` for the HACS store
  - `manifest.json` bumped to `1.0.0`, `quality_scale: silver`,
    display name `Tap Electric Charger (Community)`
  - GitHub Actions: `validate.yml` (compile + pytest + hassfest +
    HACS action), `release.yml` (tag-driven zip publication),
    `stale.yml` (issue / PR hygiene)
  - Test suite: 180+ tests across api, api_management,
    auth_firebase, ocpp, coordinator (merge + degradation +
    interval switching), sensor (source attribute + advanced
    gating), binary_sensor, switch, number, button, select,
    migration (v1→v2), reauth. Runs with or without HA installed
    (HA-only tests auto-skip locally via `requires_ha` marker).
  - Translations: strings.json and all four languages (en, nl,
    de, fr) expanded to cover every config/options step, issues,
    services, and device_automation. NL reviewed for natural
    Belgian Dutch ("laadsessie", "laadpaal", "chauffeur"). DE and
    FR remain machine-translated; native-speaker review
    welcomed in follow-up PRs.
  - Documentation: README (with entity table, hardware
    compatibility, known limitations), CONTRIBUTING guide, MIT
    LICENSE with trademark clarification,
    `docs/API_INVENTORY.md` capturing the reverse-engineered
    API schema, `brands/README.md` explaining the deferred
    brand-asset submission.

### Changed

- **Entity registry defaults**: every speculative measurand
  (Power.*, Current.*, Voltage, SoC, Temperature, Frequency,
  Power.Factor, Energy.Active.Import.Interval,
  Energy.Active.Export.Register, Energy.Reactive.Import) now ships
  **default-disabled**. Only `Energy.Active.Import.Register` and
  `Energy` (short form) default-enable.
  Rationale: EVBox Elvi (confirmed 2026-04-23) only emits `Energy`
  over OCPP. Leaving ~30 speculative entities always-Unavailable on
  EVBox installs was poor UX. Users whose charger publishes the
  extra measurands can toggle them on in one click via the entity
  registry. Documented in full in `docs/API_INVENTORY.md` §4 and
  `CHANGELOG_PHASE_C.md`.
- HA 2024.11+ compatibility: dropped the obsolete
  `OptionsFlow.config_entry` setter, added `advanced_creds` step
  shim per the phase-3 fix.

### Fixed

- See phase-3 post-deploy commit `3ec0a80` for the HA 2024.11 flow
  regression.

### Known issues

- EVBox's limited OCPP measurand emission is a charger firmware
  limitation, not an integration bug. Default-disabling the
  affected entities is the fix.
- DE and FR translations are machine-translated. Native-speaker
  review is very welcome — open a PR or comment in GitHub issues.

### Upgrading

- v1 config entries migrate automatically to v2 via
  `async_migrate_entry`. `advanced_mode: False` is added; existing
  credentials are untouched.
- If you previously enabled measurand entities manually and they're
  now default-disabled in 1.0.0, your existing entity registry
  entries are **preserved** (unique_ids are unchanged). You'll only
  see the effect on fresh installs.
