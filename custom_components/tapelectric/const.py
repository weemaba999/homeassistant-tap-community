"""Constants for the Tap Electric integration.

Endpoint status after Reference-verification (2026-04-22):
  ✅ verified in production            — schema + path confirmed
  ⚠️  write-side, needs live test      — SetChargingProfile / Reset
"""
from __future__ import annotations

from datetime import timedelta

DOMAIN = "tapelectric"
MANUFACTURER = "Tap Electric B.V."

# ── API base (verified) ─────────────────────────────────────────────────
DEFAULT_BASE_URL = "https://api.tapelectric.app"
API_VERSION = "v1"

# ── Authentication (verified) ───────────────────────────────────────────
AUTH_SCHEME = "x-api-key"
AUTH_HEADER_BEARER = "Authorization"
AUTH_HEADER_APIKEY = "X-Api-Key"
AUTH_HEADER_TAP = "X-Tap-Api-Key"

# ── Read endpoints (all ✅ verified) ─────────────────────────────────────
PATH_CHARGERS_LIST = "/chargers"
PATH_CHARGER_DETAIL = "/chargers/{charger_id}"
PATH_CHARGER_OCPP_GET = "/chargers/{charger_id}/ocpp"     # message history

# NEW — the /charger-sessions resource (has charger.id + charger.connectorId;
# supersedes the older /sessions which was driver-centric and unmatchable).
PATH_CHARGER_SESSIONS = "/charger-sessions"
PATH_SESSION_METER_DATA = "/charger-sessions/{session_id}/session-meter-data"

PATH_LOCATIONS_LIST = "/locations"
PATH_WEBHOOKS = "/webhooks"
PATH_TARIFFS = "/tariffs"

# ── Write endpoints (⚠️ need live test) ──────────────────────────────────
PATH_CHARGER_OCPP_SEND = "/chargers/{charger_id}/ocpp"    # POST
PATH_CHARGER_RESET = "/chargers/{charger_id}/reset"        # POST

# ── OCPP message passthrough ────────────────────────────────────────────
# Tap doesn't expose direct "remote start / remote stop" endpoints. Writing
# is limited to the two OcppAction values below, per the Reference enum:
OCPP_ACTION_SET_CHARGING_PROFILE = "SetChargingProfile"
OCPP_ACTION_RESET = "Reset"

# OcppVersion enum — Tap's OCPP validator rejects the string "1.6" with
# "$.ocppVersion unknown" and accepts null. The spec marks the field
# nullable; send null by default and let the server infer from the
# charger's registered protocol version.
OCPP_VERSION_DEFAULT: str | None = None

# SetChargingProfile with these limits = soft "stop" / "resume":
CHARGING_LIMIT_OFF_A = 0.0
CHARGING_LIMIT_DEFAULT_A = 16.0

# ── Polling ─────────────────────────────────────────────────────────────
SCAN_INTERVAL_ACTIVE = timedelta(seconds=30)
SCAN_INTERVAL_IDLE = timedelta(minutes=5)
METER_DATA_LIMIT = 50   # last N measurements per poll

# ── Plug-state gate for "session is live" ───────────────────────────────
PLUGGED_CONNECTOR_STATES = frozenset(
    {"CHARGING", "SUSPENDEDEV", "SUSPENDEDEVSE", "PREPARING"}
)

# Connector statuses that indicate an active fault on the physical plug
# (as opposed to the charger-level status field).
FAULTED_CONNECTOR_STATES = frozenset({"FAULTED"})

# Charger-level statuses that indicate something is wrong with the unit
# itself (network, firmware, hardware). "UNAVAILABLE" is ambiguous — it
# may mean offline OR intentionally disabled — so we treat it as a fault
# only when combined with no recent updatedAt.
FAULTED_CHARGER_STATES = frozenset({"FAULTED", "ERROR"})

# ── OCPP 1.6 measurands ─────────────────────────────────────────────────
# One row per measurand we expose. Each entry is:
#   (ocpp_measurand_name, internal_key, default_enabled)
#
# internal_key becomes part of unique_id, so it is FROZEN once released.
# default_enabled controls whether the entity shows up without the user
# explicitly enabling it in the entity registry.
#
# Phase-C audit (2026-04-23, see docs/API_INVENTORY.md): EVBox Elvi only
# emits the `Energy` measurand over OCPP. Assuming the Alfen/Wallbox/
# Zaptec floor is higher than EVBox is a lossy bet for home installs.
# Only the two energy registers are default-enabled; every other
# measurand ships disabled and is one registry toggle away for users
# whose charger publishes it.
MEASURANDS: list[tuple[str, str, bool]] = [
    ("Energy.Active.Import.Register",  "energy_active_import_register", True),
    # Short form that some EVBox-class firmwares emit; same meaning as
    # Energy.Active.Import.Register but separate unique_id so we don't
    # lose data when firmware switches forms.
    ("Energy",                         "energy_short",                  True),
    ("Energy.Active.Import.Interval",  "energy_active_import_interval", False),
    ("Energy.Active.Export.Register",  "energy_active_export_register", False),
    ("Energy.Reactive.Import.Register","energy_reactive_import",        False),
    ("Power.Active.Import",            "power_active_import",           False),
    ("Power.Active.Export",            "power_active_export",           False),
    ("Power.Offered",                  "power_offered",                 False),
    ("Power.Reactive.Import",          "power_reactive",                False),
    ("Power.Factor",                   "power_factor",                  False),
    ("Current.Import",                 "current_import",                False),
    ("Current.Export",                 "current_export",                False),
    ("Current.Offered",                "current_offered",               False),
    ("Voltage",                        "voltage",                       False),
    ("Frequency",                      "frequency",                     False),
    ("SoC",                            "soc",                           False),
    ("Temperature",                    "temperature",                   False),
]

MEASURAND_TO_KEY = {m[0]: m[1] for m in MEASURANDS}
KEY_TO_MEASURAND = {m[1]: m[0] for m in MEASURANDS}
DEFAULT_ENABLED = {m[0]: m[2] for m in MEASURANDS}

PHASES = ("L1", "L2", "L3")   # N voltage-only and rare; skip for now.
PHASE_AWARE_MEASURANDS = frozenset({
    "Current.Import", "Current.Export", "Current.Offered", "Voltage",
})

# ── Config flow keys ────────────────────────────────────────────────────
CONF_API_KEY = "api_key"
CONF_BASE_URL = "base_url"
CONF_CHARGER_ID = "charger_id"
CONF_WEBHOOK_SECRET = "webhook_secret"
CONF_MAX_CHARGE_AMPS = "max_charge_amps"
CONF_MIN_CHARGE_AMPS = "min_charge_amps"

# ── Advanced mode (Firebase-authenticated management API) ──────────────
# Optional upgrade on top of the basic sk_ API key. Advanced mode enables
# live session energy and richer session metadata via Tap's Firebase-
# authenticated /management/* endpoints. When disabled the integration
# behaves exactly as in phase B — basic sk_ API key only.
CONF_ADVANCED_MODE = "advanced_mode"
CONF_ADVANCED_EMAIL = "advanced_email"
CONF_ADVANCED_REFRESH_TOKEN = "advanced_refresh_token"
CONF_ADVANCED_ACCOUNT_ID = "advanced_account_id"
CONF_ADVANCED_FIREBASE_USER_ID = "advanced_firebase_user_id"

# Advanced-mode polling cadence. Independent of the public API's
# OPT_SCAN_INTERVAL_* so that users who disable basic-mode fast polling
# still get live session numbers on the advanced side.
ADVANCED_POLL_INTERVAL = 30      # seconds, while any charger is active
ADVANCED_IDLE_INTERVAL = 300     # seconds, when no session is active

DEFAULT_MIN_CHARGE_AMPS = 6.0        # OCPP minimum (and EU Mode-3 minimum)
DEFAULT_MAX_CHARGE_AMPS = 32.0       # fallback when firmware hides the rating

# ── Per-entry persistent state keys (hass.config_entries data bag) ──────
# Last-applied charging limit per (charger_id, connector_id), so the
# NumberEntity survives reloads with its slider value intact.
DATA_APPLIED_LIMITS = "applied_limits"

# Per-charger HA-local thresholds for the auto-stop helpers. The
# integration does not push these to the charger — they're read by
# blueprint automations. Schema (in entry.options):
#   auto_stop: { <charger_id>: {kwh: float, minutes: int, cost: float} }
DATA_AUTO_STOP = "auto_stop"

# Current selection for the reset-type SelectEntity (per charger).
# Stored in entry.options so it survives reloads.
#   reset_type: { <charger_id>: "Soft" | "Hard" }
DATA_RESET_TYPE = "reset_type"

# ── OptionsFlow keys (all live under entry.options) ─────────────────────
OPT_SCAN_INTERVAL_ACTIVE_S = "scan_interval_active_s"
OPT_SCAN_INTERVAL_IDLE_S = "scan_interval_idle_s"
OPT_SESSIONS_HISTORY_LIMIT = "sessions_history_limit"
OPT_STALE_THRESHOLD_MINUTES = "stale_threshold_minutes"
OPT_WRITE_ENABLED = "write_enabled"
OPT_METER_DATA_LIMIT = "meter_data_limit"
OPT_ROUND_ENERGY_DECIMALS = "round_energy_decimals"
OPT_ROUND_POWER_DECIMALS = "round_power_decimals"

DEFAULT_OPTIONS: dict[str, int | bool] = {
    OPT_SCAN_INTERVAL_ACTIVE_S: 30,
    OPT_SCAN_INTERVAL_IDLE_S: 300,
    OPT_SESSIONS_HISTORY_LIMIT: 50,
    OPT_STALE_THRESHOLD_MINUTES: 15,
    OPT_WRITE_ENABLED: True,
    OPT_METER_DATA_LIMIT: 100,
    OPT_ROUND_ENERGY_DECIMALS: 3,
    OPT_ROUND_POWER_DECIMALS: 2,
}

OPTION_BOUNDS: dict[str, tuple[int, int]] = {
    OPT_SCAN_INTERVAL_ACTIVE_S: (10, 300),
    OPT_SCAN_INTERVAL_IDLE_S: (60, 3600),
    OPT_SESSIONS_HISTORY_LIMIT: (10, 500),
    OPT_STALE_THRESHOLD_MINUTES: (5, 120),
    OPT_METER_DATA_LIMIT: (20, 500),
    OPT_ROUND_ENERGY_DECIMALS: (0, 3),
    OPT_ROUND_POWER_DECIMALS: (0, 3),
}

# ── External meter push (experimental) ──────────────────────────────────
PATH_METER_DATA_PUSH = "/meters/{meter_id}/data"

# ── Webhook (verified) ──────────────────────────────────────────────────
WEBHOOK_SIGNATURE_HEADER = "X-Tap-Signature"
WEBHOOK_TIMESTAMP_HEADER = "X-Tap-Timestamp"
WEBHOOK_MAX_AGE_SECONDS = 300

EVENT_TOKEN_AUTHORIZATION = "TokenAuthorization"
EVENT_SESSION_STARTED = "SessionStarted"
EVENT_SESSION_UPDATED = "SessionUpdated"
EVENT_SESSION_ENDED = "SessionEnded"
