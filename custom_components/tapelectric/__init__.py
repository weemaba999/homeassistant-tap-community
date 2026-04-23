"""Tap Electric integration for Home Assistant."""
from __future__ import annotations

import logging
import secrets

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from datetime import datetime, timezone

import aiohttp

from .api import TapElectricClient, TapElectricError
from .api_management import TapManagementClient, TapManagementError
from .auth_firebase import (
    AuthTokens,
    TapFirebaseAuth,
    TapFirebaseAuthError,
)
from .const import (
    CONF_ADVANCED_ACCOUNT_ID,
    CONF_ADVANCED_EMAIL,
    CONF_ADVANCED_FIREBASE_USER_ID,
    CONF_ADVANCED_MODE,
    CONF_ADVANCED_REFRESH_TOKEN,
    CONF_API_KEY,
    CONF_BASE_URL,
    CONF_CHARGER_ID,
    CONF_WEBHOOK_SECRET,
    DEFAULT_BASE_URL,
    DEFAULT_OPTIONS,
    DOMAIN,
    OPT_WRITE_ENABLED,
)
from .coordinator import TapCoordinator
from .repairs import note_write_blocked
from .webhook import async_register_webhook, async_unregister_webhook

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SWITCH,
]


def options_view(entry: ConfigEntry) -> dict:
    """DEFAULT_OPTIONS merged with the entry's current option overrides.

    Use this everywhere that reads options so a missing key never falls
    back to `None` and breaks arithmetic.
    """
    return {**DEFAULT_OPTIONS, **entry.options}


def is_write_enabled(entry: ConfigEntry) -> bool:
    return bool(options_view(entry).get(OPT_WRITE_ENABLED, True))


def ensure_write_enabled(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Guard for every write path. Raises HomeAssistantError if disabled.

    Also creates a Repairs issue (rate-limited inside note_write_blocked)
    so the user sees why the write was refused without having to read the
    log.
    """
    if not is_write_enabled(entry):
        note_write_blocked(hass, entry.entry_id)
        raise HomeAssistantError(
            "Tap Electric write operations are disabled in the integration "
            "options. Enable 'write_enabled' under Configure to allow this."
        )


async def _bootstrap_advanced_client(
    hass: HomeAssistant,
    entry: ConfigEntry,
    session: aiohttp.ClientSession,
) -> TapManagementClient | None:
    """Build a TapManagementClient from the stored refresh token.

    Never raises — on any failure we log and return None. Coordinator
    handles the None case as basic-only mode.
    """
    refresh_token = entry.data.get(CONF_ADVANCED_REFRESH_TOKEN)
    if not refresh_token:
        _LOGGER.warning(
            "Advanced mode enabled but no refresh token stored; "
            "falling back to basic-only.",
        )
        return None

    auth = TapFirebaseAuth(session)
    # Synthesise an already-expired AuthTokens so ensure_valid immediately
    # triggers a refresh. On success Firebase rotates the refresh token —
    # we persist the new value so the next restart uses it.
    stub_tokens = AuthTokens(
        id_token="",
        refresh_token=refresh_token,
        expires_at=datetime.now(timezone.utc),
        user_id=entry.data.get(CONF_ADVANCED_FIREBASE_USER_ID) or "",
        email=entry.data.get(CONF_ADVANCED_EMAIL),
    )
    try:
        tokens = await auth.ensure_valid(stub_tokens)
    except TapFirebaseAuthError as err:
        _LOGGER.warning(
            "Advanced-mode bootstrap refused refresh token (%s). "
            "Re-authenticate via Options → Advanced mode. Running "
            "basic-only for now.", err,
        )
        return None

    # Persist the rotated refresh token (and updated profile fields).
    new_data = {
        **entry.data,
        CONF_ADVANCED_REFRESH_TOKEN: tokens.refresh_token,
    }
    if tokens.user_id:
        new_data[CONF_ADVANCED_FIREBASE_USER_ID] = tokens.user_id
    if tokens.email:
        new_data[CONF_ADVANCED_EMAIL] = tokens.email
    if new_data != entry.data:
        hass.config_entries.async_update_entry(entry, data=new_data)

    client = TapManagementClient(
        session, auth, tokens,
        account_id=entry.data.get(CONF_ADVANCED_ACCOUNT_ID),
    )
    # If we forgot to persist account_id at setup, recover it now.
    if client.account_id is None:
        try:
            await client.discover_account_id()
            hass.config_entries.async_update_entry(
                entry,
                data={**entry.data,
                      CONF_ADVANCED_ACCOUNT_ID: client.account_id,
                      CONF_ADVANCED_REFRESH_TOKEN: client.tokens.refresh_token},
            )
        except TapManagementError as err:
            _LOGGER.warning(
                "Advanced-mode account discovery failed: %s. "
                "Running basic-only.", err,
            )
            return None
    _LOGGER.info(
        "Advanced mode active for %s (account %s)",
        tokens.email or "<unknown>", client.account_id,
    )
    return client


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Migrate old entry data. v1 → v2 adds the advanced-mode fields."""
    if entry.version == 1:
        new_data = {
            **entry.data,
            CONF_ADVANCED_MODE: False,
        }
        hass.config_entries.async_update_entry(
            entry, data=new_data, version=2,
        )
        _LOGGER.info(
            "Migrated Tap Electric entry from v1 to v2 "
            "(advanced mode available as opt-in via Options).",
        )
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Tap Electric from a config entry."""
    session = async_get_clientsession(hass)
    client = TapElectricClient(
        api_key=entry.data[CONF_API_KEY],
        session=session,
        base_url=entry.data.get(CONF_BASE_URL, DEFAULT_BASE_URL),
    )

    # Advanced-mode management client is OPTIONAL. If bootstrap fails we
    # keep the integration running in basic-only mode; the coordinator
    # is built with mgmt=None and the advanced sensors stay unavailable
    # until the user re-authenticates via Options → Advanced mode.
    mgmt_client = None
    if entry.data.get(CONF_ADVANCED_MODE):
        mgmt_client = await _bootstrap_advanced_client(hass, entry, session)

    coordinator = TapCoordinator(
        hass, client, entry,
        mgmt=mgmt_client,
        charger_id=entry.data.get(CONF_CHARGER_ID),
    )
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {
        "client": client,
        "mgmt": mgmt_client,
        "coordinator": coordinator,
        "entry": entry,
    }

    # Reload on options change so Platform setups see new values.
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    secret = entry.data.get(CONF_WEBHOOK_SECRET)
    if secret:
        if "webhook_id" not in entry.data:
            hass.config_entries.async_update_entry(
                entry,
                data={**entry.data, "webhook_id": secrets.token_hex(16)},
            )
        await async_register_webhook(hass, entry, secret)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    _register_services(hass)
    return True


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the entry when options change."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)
    if entry.data.get(CONF_WEBHOOK_SECRET):
        await async_unregister_webhook(hass, entry)
    return unload_ok


# ── Services: service.yaml defines UI schemas, actual handlers live here ──

def _register_services(hass: HomeAssistant) -> None:
    """Register Tap services once per HA run."""
    if hass.services.has_service(DOMAIN, "pause_charging"):
        return

    async def _pause(call: ServiceCall) -> None:
        charger_id = call.data["charger_id"]
        client, entry = _resolve(hass, charger_id)
        ensure_write_enabled(hass, entry)
        try:
            await client.pause_charging(
                charger_id, connector_id=call.data.get("connector_id", 1),
            )
        except TapElectricError as err:
            _LOGGER.error("pause_charging %s: %s", charger_id, err)
            raise

    async def _resume(call: ServiceCall) -> None:
        charger_id = call.data["charger_id"]
        client, entry = _resolve(hass, charger_id)
        ensure_write_enabled(hass, entry)
        try:
            await client.resume_charging(
                charger_id,
                limit_amps=float(call.data.get("limit_amps", 16.0)),
                connector_id=call.data.get("connector_id", 1),
                number_phases=call.data.get("number_phases"),
            )
        except TapElectricError as err:
            _LOGGER.error("resume_charging %s: %s", charger_id, err)
            raise

    async def _set_limit(call: ServiceCall) -> None:
        charger_id = call.data["charger_id"]
        client, entry = _resolve(hass, charger_id)
        ensure_write_enabled(hass, entry)
        try:
            await client.set_charging_limit(
                charger_id,
                limit_amps=float(call.data["limit_amps"]),
                connector_id=call.data.get("connector_id", 1),
                number_phases=call.data.get("number_phases"),
            )
        except TapElectricError as err:
            _LOGGER.error("set_charging_limit %s: %s", charger_id, err)
            raise

    async def _reset(call: ServiceCall) -> None:
        charger_id = call.data["charger_id"]
        client, entry = _resolve(hass, charger_id)
        ensure_write_enabled(hass, entry)
        try:
            await client.reset_charger(
                charger_id, reset_type=call.data.get("reset_type", "Soft"),
            )
        except TapElectricError as err:
            _LOGGER.error("reset_charger %s: %s", charger_id, err)
            raise

    async def _push_meter(call: ServiceCall) -> None:
        """Forward an external meter reading to Tap's load-balancing API.

        Experimental — the ExternalMeterData contract isn't fully
        documented; we post what the user supplies and let the server
        validate.
        """
        # Any entry will do — this endpoint isn't charger-scoped. Pick
        # the first one and reuse its client/options.
        first = next(iter(hass.data.get(DOMAIN, {}).values()), None)
        if first is None:
            raise HomeAssistantError("No Tap Electric config entry set up")
        entry: ConfigEntry = first["entry"]
        ensure_write_enabled(hass, entry)
        client: TapElectricClient = first["client"]
        meter_id = call.data["meter_id"]
        payload = {
            k: v for k, v in {
                "powerW":     call.data.get("power_w"),
                "energyWh":   call.data.get("energy_wh"),
                "currentA":   call.data.get("current_a"),
                "voltageV":   call.data.get("voltage_v"),
                "measuredAt": call.data.get("measured_at"),
            }.items() if v is not None
        }
        try:
            await client.push_external_meter_data(meter_id, payload)
        except TapElectricError as err:
            _LOGGER.error("push_external_meter_data %s: %s", meter_id, err)
            raise

    hass.services.async_register(DOMAIN, "pause_charging", _pause)
    hass.services.async_register(DOMAIN, "resume_charging", _resume)
    hass.services.async_register(DOMAIN, "set_charging_limit", _set_limit)
    hass.services.async_register(DOMAIN, "reset_charger", _reset)
    hass.services.async_register(DOMAIN, "push_external_meter_data", _push_meter)


def _resolve(hass: HomeAssistant, charger_id: str) -> tuple[TapElectricClient, ConfigEntry]:
    """Return (client, entry) for the entry that owns this charger."""
    for bucket in hass.data.get(DOMAIN, {}).values():
        coord: TapCoordinator = bucket["coordinator"]
        if coord.data and coord.data.charger(charger_id):
            return bucket["client"], bucket["entry"]
    first = next(iter(hass.data.get(DOMAIN, {}).values()), None)
    if first is None:
        raise HomeAssistantError("No Tap Electric config entry set up")
    return first["client"], first["entry"]
