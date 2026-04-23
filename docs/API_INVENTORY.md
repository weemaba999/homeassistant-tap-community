# Tap Electric API inventory — phase-4 planning

Captured **2026-04-23** against the live API using the integration's own
sk_ key and (for the management side) a Firebase-authenticated session.
All schemas below are what the API **actually returned on the EVBox
charger `EVB-P22208163`**. Fields documented elsewhere (SDKs, mobile
app) but never seen on this account are marked accordingly — they will
be verified opportunistically as new deployments are added.

Live response bodies are under
[`tests/fixtures/api_inventory/`](../tests/fixtures/api_inventory/).

**Scope.** Only read (GET) endpoints were exercised. Mutating endpoints
(`POST /chargers/{id}/reset`, `POST /chargers/{id}/ocpp`,
`POST /meters/{id}/data`) are catalogued for reference but were **not
called** per task constraints.

Secrets (sk_ key, Firebase idToken/refresh token, email, password) are
not included in any fixture — probes strip them and capture only the
response body.

---

## 1. Authentication surfaces

| Surface | Base URL | Credential | Required headers |
| --- | --- | --- | --- |
| Public REST | `https://api.tapelectric.app/api/v1` | `sk_...` developer key | `X-Api-Key: <sk_>` |
| Management | `https://tap-electric-app-api.azurewebsites.net/api/1.0/management` | Firebase ID token (short-lived JWT, ~3600s) | `Authorization: Bearer <id_token>`, `X-Api-Key: 5l^01Wmxs5ux` (static app key), `X-Account-Id: macc_...`, plus `Referer`/`Origin: https://web.tapelectric.app` |

The Firebase ID token is issued by Google Identity Toolkit against a
**referrer-restricted** web API key (`AIzaSyA-...`). Every request to
`identitytoolkit` / `securetoken` must carry the `web.tapelectric.app`
Referer or Google's `CheckOrigin` filter returns 400. Already handled
in [`auth_firebase.py`](../custom_components/tapelectric/auth_firebase.py).

---

## 2. Public API (sk_ key scope)

### 2.1 `GET /api/v1/chargers`

List chargers visible to this key. Currently consumed — drives every
charger-level entity (status, faults, connector state, device metadata).

| Query param | Type | Description | Observed |
| --- | --- | --- | --- |
| _(none observed in use)_ | | The public docs mention filtering but none were needed for this scope. | n/a |

**Response** (fixture: [`chargers_list.json`](../tests/fixtures/api_inventory/chargers_list.json)): an array of:

| Field | Type | Example | Null on EVBox? | Consumed? |
| --- | --- | --- | --- | --- |
| `id` | string | `"EVB-P22208163"` | never | yes — unique_id seed, FK for sessions |
| `serverPartition` | string | `"pz53nktwbzjz"` | never | no |
| `locationId` | string | `"loc_001575dc9f2b4780a27d2649106ca343"` | never | yes — cross-ref to `/locations` |
| `status` | enum string | `"AVAILABLE"` | never | yes — main charger-level state machine |
| `connectors` | array | see below | always present, len=1 on EVBox | yes |
| `connectors[].id` | string | `"1"` | never | yes — connector entity key |
| `connectors[].status` | enum string | `"SUSPENDEDEV"` | never | yes — plug state, session gate |
| `updatedAt` | RFC3339 string | `"2026-04-23T15:02:21.0215956Z"` | never | yes — stale-detection / Repairs issue |

**Fields the code tries to read but EVBox does not emit** (see
`sensor.py:188-192`, `binary_sensor.py:68-72`): `brand`, `name`,
`model`, `firmwareVersion`, `serialNumber`. These fall back to
`MANUFACTURER` + truncated id. **Confirmed absent** on this scope —
not a bug in the probe, the API literally omits them.

**Charger `status` enum** observed: `AVAILABLE`. Other values referenced
in `const.py` (`FAULTED`, `ERROR`, `UNAVAILABLE`) are from the OCPP/API
spec but unseen on this account during the capture window.

**Connector `status` enum** observed: `SUSPENDEDEV`. The integration's
`PLUGGED_CONNECTOR_STATES` also whitelists `CHARGING`, `SUSPENDEDEVSE`,
`PREPARING`; `FAULTED` gates the fault binary sensor.

### 2.2 `GET /api/v1/chargers/{charger_id}`

Single-charger detail. Currently **not consumed** by the integration;
`/chargers` already returns everything and costs the same round trip.

Fixture ([`charger_detail.json`](../tests/fixtures/api_inventory/charger_detail.json))
confirms the response is **byte-identical** to the matching item in
`/chargers` on this scope — no extra fields. No reason to split the
call.

### 2.3 `POST /api/v1/chargers/{charger_id}/reset` — **not called**

Documented for completeness; left un-invoked per task. The integration
exposes two reset paths:

- `POST /chargers/{id}/ocpp` with the OCPP `Reset` action (preferred —
  sends `Soft`/`Hard` explicitly)
- `POST /chargers/{id}/reset` with an empty body (fallback — Tap's
  reference doesn't document the response shape)

The `reset_type` select (`DATA_RESET_TYPE`) already feeds the first
path. No phase-4 follow-up needed unless Tap publishes a response
schema.

### 2.4 `GET /api/v1/charger-sessions?limit=50`

List of sessions (current + historical) visible to this key. Currently
consumed — drives `sensor.tap_current_session_*`, phase-A session
history, kWh/time/cost heuristics.

| Query param | Type | Observed default / range | Description |
| --- | --- | --- | --- |
| `limit` | int | 50 used in fixture; integration allows 10–500 via `OPT_SESSIONS_HISTORY_LIMIT` | page size |
| `offset` | int | 0 | pagination offset |
| `updatedSince` | RFC3339 string | unused by integration | filter to sessions modified after this instant — supported per `api.py:157` |

**Response** (fixture: [`charger_sessions_list.json`](../tests/fixtures/api_inventory/charger_sessions_list.json), 50 items). Union of top-level keys across all 50 entries: `['charger', 'endedAt', 'id', 'location', 'startedAt', 'updatedAt', 'wh']`.

| Field | Type | Example | Null on EVBox? | Consumed? |
| --- | --- | --- | --- | --- |
| `id` | string | `"cs_9d10ef9f319548f4949cf741b913fc95"` | never | yes — session identity |
| `location.id` | string | `"loc_001575dc9f2b4780a27d2649106ca343"` | never | yes — attributes |
| `charger.id` | string | `"EVB-P22208163"` | never | yes — join to chargers |
| `charger.connectorId` | string | `"1"` | never | yes — connector disambig |
| `wh` | integer | `9721` (Wh) | never, can be 0 | yes — kWh entity + fallback when meter-data is empty |
| `startedAt` | RFC3339 string | `"2026-04-22T10:54:33Z"` | never | yes — session start |
| `endedAt` | RFC3339 string or **absent** | `"2026-04-22T16:41:03Z"` | **11/50 rows have no `endedAt`** — some are live, some are orphans (car unplugged without StopTransaction) | yes — "is session active" heuristic, see `PLUGGED_CONNECTOR_STATES` cross-check |
| `updatedAt` | RFC3339 string | `"2026-04-23T04:03:51.330433Z"` | never | yes — sort + staleness |

**Notably absent** on this scope (fields sometimes seen in Tap's fleet
key tier): `driver`, `rfidTag`, `tariff`, `costs`, `kWhImported`,
`ocppStartTxId`. These only surface via the management API (§3).

**Orphan sessions observation.** 11/50 sessions (22%) have no
`endedAt` but are older than a week. That's **not** a "currently
active" signal — the coordinator correctly gates on
`PLUGGED_CONNECTOR_STATES` before trusting `endedAt=null`.

### 2.5 `GET /api/v1/charger-sessions/{session_id}` — **404 (does not exist)**

Probed both an active (`cs_9d10ef9f3...`) and a closed session
(`cs_7f45d8e48...`). Both **return 404** on the public API (fixtures:
`charger_session_detail_active.json`, `charger_session_detail_closed.json`).

**Consequence for phase 4.** If you want single-session detail from a
sk_ key, the only way is to filter `/charger-sessions` client-side.
For nested fields (location name, fleet driver, tariff), use the
management API's `/sessions/{id}` instead — see §3.3.

### 2.6 `GET /api/v1/charger-sessions/{session_id}/session-meter-data?limit=100`

OCPP MeterValues for one session. Currently consumed — per-measurand
sensors (energy, power, current, voltage, SoC, temperature).

| Query param | Type | Description |
| --- | --- | --- |
| `limit` | int (default 50 in integration, capped at 500) | page size |
| `offset` | int | pagination offset |
| `from` | RFC3339 string | filter by `measuredAt >= from` |
| `to` | RFC3339 string | filter by `measuredAt <= to` |

**Response** (fixtures: `charger_session_meter_data_active.json`,
`charger_session_meter_data_closed.json`). Array of:

| Field | Type | Example | Null on EVBox? | Consumed? |
| --- | --- | --- | --- | --- |
| `id` | string | `"0"`, `"1"` | never; looks like a row index | no |
| `ocppMessageId` | UUID string | `"317bb69f-df11-4675-b72b-41af722cbbdc"` | **present only on final "end of session" row**, absent during the session | no |
| `value` | number | `0`, `9721` | never | yes — entity native_value |
| `unit` | string | `"Wh"` | never | yes — for `_to_kwh()` conversion |
| `measurand` | OCPP enum | `"Energy"` | never | yes — entity routing |
| `chargerId` | string | `"EVB-P22208163"` | never | yes — join back to charger |
| `phase` | string or **absent** | (not present on EVBox) | always absent on EVBox | yes — would disambig L1/L2/L3 |
| `transactionId` | string | `"10065"` | never | no (not used yet — **phase-4 candidate**) |
| `measuredAt` | RFC3339 string | `"2026-04-22T16:41:03Z"` | never | yes — freshness |

**The big finding: EVBox only emits `Energy`.** The `MEASURANDS` list
in `const.py:87-108` enumerates 17 measurands × up to 3 phases (≈30
sensors). On this charger, fixtures show only `Energy` in `Wh` — no
`Power.Active.Import`, no `Current.Import`, no `Voltage`, no `SoC`, no
`Temperature`. All those entities exist but stay `unknown` forever.
Two possible phase-4 actions:

1. Mark the niche measurand entities `entity_registry_enabled_default=False`
   to stop polluting the device page (already true for V2G export,
   power factor, reactive — but not for the EV-expected Power /
   Voltage / Current / SoC set).
2. Detect "this charger has never emitted measurand X" and hide the
   entity outright after N successful polls without data.

### 2.7 `GET /api/v1/locations`

Currently consumed. Fixture
([`locations_list.json`](../tests/fixtures/api_inventory/locations_list.json)):
1 entry, minimal:

| Field | Type | Example | Null on EVBox? | Consumed? |
| --- | --- | --- | --- | --- |
| `id` | string | `"loc_001575dc9f2b4780a27d2649106ca343"` | never | yes |
| `latitude` | number | `0` | **yes — reports 0/0 (unset)** | no |
| `longitude` | number | `0` | **yes — reports 0/0 (unset)** | no |

**Fields absent on EVBox but expected from docs:** `name`, `address`,
`city`, `country`, `zip`, `timezone`. The coordinator reads
`(location).name` (`sensor.py:179`) — will silently return `None`, which
is fine.

### 2.8 `GET /api/v1/tariffs`

Returns **400** without a `tariffId` query param. Fixture
([`tariffs_list_bare.json`](../tests/fixtures/api_inventory/tariffs_list_bare.json)):

```json
{"errors": {"tariffId": ["The tariffId field is required."]}}
```

**Not a list endpoint.** Retrieval requires knowing a tariff id up-front
(typically from a fleet session detail). Integration's
`list_tariffs()` (`api.py:205-216`) is dead code on sk_-key scope —
acknowledged in `coordinator.py:12` comment.

### 2.9 `GET /api/v1/webhooks`

Returns `[]` — no webhook subscriptions registered against this key.
Fixture: [`webhooks_list.json`](../tests/fixtures/api_inventory/webhooks_list.json).

Event payloads (`TokenAuthorization`, `SessionStarted`,
`SessionUpdated`, `SessionEnded`) are referenced in `const.py:202-205`
and received via HA's webhook component — schema derives from what
Tap sends, not from this endpoint. Not relevant for phase-4 unless we
start auto-provisioning webhooks.

### 2.10 `GET /api/v1/chargers/{charger_id}/ocpp`

Historic OCPP message log. Currently supported by `api.py` but **not
polled** by the coordinator. Fixture shows `[]` for EVBox within the
last 20 messages — either the endpoint is scope-restricted or the
charger hasn't produced visible OCPP traffic via this key.

| Query param | Type | Description |
| --- | --- | --- |
| `limit` / `offset` | int | paging |
| `action` | OCPP action string | filter by message type (`BootNotification`, `Heartbeat`, `StatusNotification`, `MeterValues`, `StartTransaction`, `StopTransaction`, …) |
| `from` / `to` | RFC3339 | time window |

**Phase-4 candidate: maybe.** Would be great for a "recent OCPP
activity" log card / diagnostic sensor, but the empty response here
suggests it's not populated reliably. Re-probe once another charger
(non-EVBox) is attached before committing.

---

## 3. Management API (Firebase-authenticated)

> Fixtures **not captured in this run** — mgmt probe requires
> interactive Firebase credentials. Schemas below are derived from
> `api_management.py` (which has been verified against live data in
> the phase-3 implementation) and the phase-2 standalone test.
> To capture live fixtures: run `python3 tests/probe_api_inventory.py`
> and enter credentials at the prompt; fixtures `mgmt_accounts.json`,
> `mgmt_role_sessions_cpo.json`, `mgmt_role_sessions_driver.json`,
> `mgmt_session_detail_active.json`, `mgmt_session_detail_closed.json`
> will land next to the public ones.

### 3.1 `GET /management/accounts`

Used exclusively to discover the `macc_...` account id needed for
every subsequent request. Currently consumed via
`TapManagementClient.discover_account_id()`.

| Query param | Type | Used value | Description |
| --- | --- | --- | --- |
| `take` | int | 5 | page size |
| `offset` | int | 0 | pagination |
| `includeConnectedAccounts` | `"true"` / `"false"` | `"true"` | include accounts the user has delegated access to |

**Response** — array (or wrapper `{items: [...]}`) of account objects.
Only the first item's `id` (`macc_...`) is consumed. Other fields
observed in the phase-2 harness: `role` (cpo / fleet / driver), human
name, possibly `isConnected` flag. Not read today.

### 3.2 `GET /management/role-sessions?role=cpo&take=20`

The **only** path to live session energy (`energy_wh` updates every
~30s while the car is charging) and to rich fleet/driver metadata.
Consumed heavily in phase-3 for the advanced-mode sensors.

| Query param | Type | Description |
| --- | --- | --- |
| `role` | enum (`cpo`, `driver`, `fleet`) | which of the user's roles to pull sessions for |
| `offset` | int | pagination |
| `take` | int | page size (integration uses 20) |

**Response** — array of `ManagementSession` rows (see
`api_management.py:94-213`). Fields:

| Field | Type | Example | Expected on EVBox? | Consumed? |
| --- | --- | --- | --- | --- |
| `session_id` | string | `"cs_..."` | yes | yes |
| `charger_id` | string | `"EVB-P22208163"` | yes | yes |
| `charger_name` | string | user-assigned label | null on EVBox (no name set) | yes — attr only |
| `start_date` / `end_date` | RFC3339 string; `end_date` null while active | | yes | yes |
| `energy_wh` | number (Wh) | `9721` | yes — the reason we use this API | yes — live during session |
| `currency` | ISO string | `"EUR"` | likely; populated from fleet | yes — attrs |
| `token_visual_id` | string | masked RFID id | usually null on EVBox home-charger with no fleet | yes — attrs |
| `fleet_id` | string | `"flt_..."` | null on EVBox (no fleet) | no |
| `fleet_name` | string | | null on EVBox | no |
| `fleet_driver_name` | string | | null on EVBox | yes — attr, useful for shared fleets |
| `fleet_driver_cost_ex_vat` | number | | null on EVBox | no (**phase-4 candidate**) |
| `fleet_driver_cost_incl_vat` | number | | null on EVBox | no (**phase-4 candidate** — session cost sensor) |
| `fleet_driver_reimbursement_cost` | number | | null on EVBox | no (**phase-4 candidate**) |
| `vat_percent` | number | | null on EVBox | no |
| `location_name` | string | `"Home"` | likely null on EVBox | yes — attr |
| `address` / `city` | string | | null on EVBox | no |
| `operator` / `service_provider` | string | | null on EVBox | no |
| `masked_card_uid` | string | `"****1234"` | null on EVBox (no RFID used) | yes — attr |
| `created` | RFC3339 | | yes | yes (fallback sort) |

**What "null on EVBox" means above.** This user's EVBox is a
home-charger without an attached fleet or RFID deployment. A fleet
operator would see all the fleet/driver/cost/vat fields populated.
Mark all of them `entity_registry_enabled_default=False` and let fleet
users opt in.

### 3.3 `GET /management/sessions/{session_id}`

Per-session detail, richer nesting than the list view. Consumed by
the phase-3 "cold-fetch" path (on newly closed sessions).

**Response** — nested shape (see `ManagementSession.from_detail`):

| Path | Type | On EVBox? | Consumed? |
| --- | --- | --- | --- |
| `id` | string | yes | yes |
| `start_date` / `end_date` | RFC3339 | yes | yes |
| `energy_wh` | number | yes | yes |
| `location_details.charger_id` / `charger_name` | string | yes / null | yes |
| `location_details.location_name` | string | null on EVBox | yes — attr |
| `location_details.address` / `city` / `zip` / `country` | string | null on EVBox | yes — attrs (**phase-4 candidate:** expose as device attrs) |
| `location_details.latitude` / `longitude` | number | null on EVBox (consistent with §2.7) | yes — attr |
| `location_details.evse_id` | string | usually null on home chargers | yes — attr |
| `cpo_details.currency` | ISO string | yes | yes |
| `cpo_details.transaction_id` | int | yes (OCPP tx id, e.g. 10064) | yes — **phase-4 candidate**: bridge to OCPP message log |
| `fleet_details.fleet_id` / `fleet_name` | string | null on EVBox | no |
| `fleet_details.retail_tariff` | object | null on EVBox; expected shape `{priceIncVat, priceExVat, currency, validFrom}` on fleet accounts | yes — raw kept, not parsed (**phase-4 candidate**) |
| `fleet_details.reimbursement.amount` | number | null on EVBox | yes — attr |

**Detail-vs-list asymmetry.** The detail endpoint does **not** echo
several flat fields that only the list returns (`fleet_driver_name`,
`vat_percent`, `masked_card_uid`, `operator`, `service_provider`,
`token_visual_id`, `created`, cost breakdowns). Coordinator already
merges both in `coordinator.py:440-444` — worth documenting explicitly
for phase-4 refactors so nobody accidentally drops a list call.

### 3.4 Exploratory: `role=driver`, `role=fleet`

Noted in the probe as exploratory. `role=driver` should return the
same row shape but only sessions where the user is the driver
(typically all of them on a home install). `role=fleet` requires the
user to be a fleet admin and is unlikely to return anything on this
account.

---

## 4. Phase-4 candidate roll-up

Strongest candidates — worth implementing:

| Candidate | Source endpoint | Why | Difficulty |
| --- | --- | --- | --- |
| **Session cost sensor** (state=€ cost, attrs currency/vat) | mgmt `/role-sessions` `fleet_driver_cost_incl_vat` + `currency` | Most asked-for upgrade for fleet users. Already flowing through `ManagementSession`. | Low — add sensor entity, disabled-by-default |
| **Session reimbursement sensor** | mgmt `/role-sessions` `fleet_driver_reimbursement_cost` | Same pipeline as above; valuable for employees on reimbursement plans. | Low |
| **Location attributes** (address, city, country, zip, timezone, lat/lon) on the charger device | mgmt `/sessions/{id}.location_details` | Today only `location.name` reaches the UI. Would fix the "where is my charger?" problem when EVBox returns 0/0 on public `/locations`. | Low |
| **OCPP transaction-id link** | mgmt `/sessions/{id}.cpo_details.transaction_id` | Bridge to `/chargers/{id}/ocpp?action=StartTransaction`; foundation for any "live OCPP activity" feature. | Low (just expose as attribute) |
| **Retail tariff breakdown** | mgmt `/sessions/{id}.fleet_details.retail_tariff` | Enables kWh-to-cost on the HA side even when the API doesn't cost the session out. | Medium — schema reverse-engineered from one fleet sample; validate first |

Maybe:

| Candidate | Source | Why maybe | Blocker |
| --- | --- | --- | --- |
| **OCPP message log sensor/diagnostic** | public `/chargers/{id}/ocpp` | Useful for debugging; could power a repairs-style "charger silent for X hours" hint | Endpoint returned `[]` on EVBox in this run — need another charger deployment to confirm it's not universally empty |
| **RFID card attribute** (`token_visual_id`, `masked_card_uid`) | mgmt `/role-sessions` | Nice for multi-user households / fleets | Null on this account; verify when a fleet charger is attached |
| **Fleet driver identity** (`fleet_driver_name`) | mgmt `/role-sessions` | Same as above | Only populated for fleet accounts |

Skip:

| Endpoint/field | Reason |
| --- | --- |
| `GET /chargers/{id}` | Byte-identical to the matching item in `/chargers` — no upside |
| `GET /charger-sessions/{id}` | **404** on public API |
| `GET /tariffs` | Not a list endpoint (needs a pre-known `tariffId`); retail tariff is better reached via mgmt `/sessions/{id}.fleet_details.retail_tariff` |
| `GET /locations.latitude/longitude` | Returns 0/0 on EVBox (unset) — unreliable for map use |
| Measurand entities for `Power.*`, `Voltage`, `Current.*`, `SoC`, `Temperature` on EVBox installs | Charger never emits them on this key scope — keep the entity definitions (other hardware might) but default-disable and auto-hide after N empty polls |

---

## 5. Reproducing this capture

```bash
source .tap.env                            # loads TAP_API_KEY
python3 tests/probe_api_inventory.py       # prompts for Firebase email+password
#   SKIP_MGMT=1 python3 tests/probe_api_inventory.py     # public side only
#   TAP_EMAIL=... TAP_PASSWORD=... python3 tests/probe_api_inventory.py
```

Fixtures overwrite in place. The probe is read-only — it does not
mutate the charger and deliberately skips the reset endpoint.
