# Changelog

All notable changes to this project land here. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the
project uses [semantic versioning](https://semver.org/).

## [Unreleased]

Nothing pending beyond what shipped in 1.0.0.

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
