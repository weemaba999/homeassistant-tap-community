# Phase A — community-ready CORE

Feature-complete minimum to take the v2 integration from "works on my
charger" to "works on any Tap Electric charger in the fleet." Phases B
(OptionsFlow, diagnostics) and C (tests, release hygiene) are not in
scope here.

## New platforms

- **binary_sensor** — `online`, `plug_connected`, `charging`, `fault`
  (per connector where applicable).
- **number** — `charge_current_limit` (per connector). Slider value is
  persisted in the config entry so reloads don't reset it.
- **button** — `reset_soft` (enabled), `reset_hard` (disabled by default).

## Sensor platform

- Always-register pattern. Every OCPP 1.6 measurand gets a sensor at
  setup time; entities flip to `unavailable` when `measuredAt` is older
  than `STALE_THRESHOLD` (15 min). This preserves HA history across
  firmware changes and avoids the "where did my sensor go" problem after
  an integration reload.
- 17 measurands covered (`const.MEASURANDS`), including the niche ones
  (V2G export, reactive power, power factor, frequency, offered power).
  Niche entities default to `entity_registry_enabled_default = False`.
- Phase-aware measurands (`Current.Import/Export/Offered`, `Voltage`)
  fan out to L1/L2/L3.
- New `ChargerInfoSensor` (off by default) surfaces firmware, brand,
  serial, and similar metadata as attributes — useful for diagnostics.
- New `TariffSensor` (off by default) — best-effort read from /tariffs.
  See caveat below.

### unique_id changes (BREAKING vs v2)

These were renamed to fit the new scheme (`{cid}_{internal_key}[_phase]`):

| v2                      | Phase A                           |
| ----------------------- | --------------------------------- |
| `{cid}_session_power`   | `{cid}_power_active_import`       |
| `{cid}_current_l1/2/3`  | `{cid}_current_import_l1/2/3`     |

Impact: zero on this user's charger (EVBox firmware only emits `Energy`,
so neither sensor was ever populated in v2). Users with richer firmware
will lose history on these two entity families.

### unique_ids preserved (NOT renamed)

`{cid}_status`, `{cid}_connector_status`, `{cid}_session_energy`,
`{cid}_session_duration`, `{cid}_last_session_energy`,
`{cid}_soc`, `{cid}_temperature`, `{cid}_voltage_l1/2/3`
(latter three happened to match the new scheme already).

## Switch platform

- `ChargeAllowedSwitch` derives its resume current from
  `connector.maxAmperage` at turn-on time, falling back to
  `CONF_MAX_CHARGE_AMPS` from the config entry. Same pattern for
  `number_phases`, derived from `connector.currentType`. Config value
  is now the fallback, not the primary.

## Coordinator

- `TapData.measurand_freshness()` helper used by sensors to decide
  `available`.
- `TapData.connector()` / `connectors()` / `is_plugged(connector_id=…)`
  helpers centralise connector lookups and normalise string vs int ids
  (the API returns `"1"`, not `1`).
- Cold-start meter fetch: when a charger has no active session, fetch
  meter data from the most recent ended session ONCE. Later polls don't
  re-fetch unless the "last ended session" changes. Lets entities
  discover which measurands the firmware emits without waiting for a new
  plug-in.
- Tariffs fetched best-effort per poll; silently `[]` on any API error
  (the endpoint is scope-variable; see caveat below).

## Constants

- Full OCPP 1.6 measurand table (`MEASURANDS`) with lookup dicts.
- `PHASES = ("L1", "L2", "L3")`, `PHASE_AWARE_MEASURANDS` set.
- `STALE_THRESHOLD` (15 min) drives the sensor availability cutoff.
- `CONF_MIN_CHARGE_AMPS` / `DEFAULT_MIN_CHARGE_AMPS` (6 A) for the
  number entity's lower bound.
- `DEFAULT_MAX_CHARGE_AMPS` (32 A) — fallback when firmware hides the
  connector rating.
- `DATA_APPLIED_LIMITS` — config-entry data key for the slider's
  persisted value.
- Removed the old individual `MEASURAND_POWER` / `_ENERGY` / `_CURRENT`
  / `_VOLTAGE` / `_SOC` / `_TEMPERATURE` constants — sensor.py now loops
  over `MEASURANDS` instead.

## Known caveats

- **Tap `/tariffs` is lookup-by-id on this API key scope.** A plain GET
  returns 400 (`tariffId field is required`). The integration treats
  this as "no tariffs visible" and falls back to `[]`. The TariffSensor
  is off by default, so no user-visible impact. A future phase can add
  charger-→tariff resolution if/when Tap exposes it.
- **Sparse charger metadata from Tap's list endpoint.** On this fleet
  the charger object returns only `id`, `serverPartition`, `locationId`,
  `status`, `connectors`, `updatedAt`. No `name`, `brand`, `model`,
  `firmwareVersion`, `serialNumber`, `maxAmperage`, `currentType`,
  `internet`. All fallbacks defensively handle missing fields. This
  isn't a regression — v2 had the same gap — but it's worth calling
  out because several Phase A features (ChargerInfoSensor, switch
  max-amperage derivation, online binary sensor) depend on fields that
  happen to be empty here.
- **No live write tests performed.** Phase A adds `button.*` and
  `number.set` entities that call write endpoints. They use the same
  OCPP envelope as v2's services (which the user hasn't live-tested
  yet either), so behaviour should be identical — but this is unverified
  against a physical charger.
