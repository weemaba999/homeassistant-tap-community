"""Config + Options flow for Tap Electric.

Two-tier setup:
  * Basic  — the sk_ API key (public /api/v1). Always required. This
             is the existing phase-B behaviour and the only thing the
             integration needs to function.
  * Advanced — an optional upgrade. Requires the user's Tap app email
             + password, exchanged for a Firebase refresh token via
             auth_firebase.py. Unlocks /management/* endpoints with
             live session energy. Can be toggled at any time via
             Options → Advanced mode.

The user step collects and validates the sk_ key, then routes to
advanced_ask so the user can opt in or out before the entry is
finalised. If they decline, the entry is created with
`advanced_mode: False` and everything behaves exactly as in phase B.

ConfigFlow VERSION is 3. Older entries migrate sequentially through
__init__.async_migrate_entry.
"""
from __future__ import annotations

import logging
import secrets
from typing import Any

import aiohttp
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .api import TapElectricAuthError, TapElectricClient, TapElectricError
from .api_management import (
    TapManagementAuthError,
    TapManagementClient,
    TapManagementError,
    TapManagementNetworkError,
)
from .auth_firebase import (
    TapFirebaseAuth,
    TapFirebaseAuthError,
    TapFirebaseInvalidCredentials,
    TapFirebaseNetworkError,
)
from .charging_cards import charging_cards, validate_card_input
from .const import (
    CONF_ADVANCED_ACCOUNT_ID,
    CONF_ADVANCED_EMAIL,
    CONF_ADVANCED_FIREBASE_USER_ID,
    CONF_ADVANCED_MODE,
    CONF_ADVANCED_PROFILE_ID,
    CONF_ADVANCED_REFRESH_TOKEN,
    CONF_API_KEY,
    CONF_BASE_URL,
    CONF_CHARGER_ID,
    CONF_WEBHOOK_SECRET,
    DATA_DEFAULT_OUTLET_IDS,
    DEFAULT_BASE_URL,
    DEFAULT_OPTIONS,
    DOMAIN,
    OPT_CHARGING_CARDS,
    OPT_DEFAULT_CHARGING_CARD_ID,
    OPT_METER_DATA_LIMIT,
    OPT_ROUND_ENERGY_DECIMALS,
    OPT_ROUND_POWER_DECIMALS,
    OPT_SCAN_INTERVAL_ACTIVE_S,
    OPT_SCAN_INTERVAL_IDLE_S,
    OPT_SESSIONS_HISTORY_LIMIT,
    OPT_STALE_THRESHOLD_MINUTES,
    OPT_WRITE_ENABLED,
    OPTION_BOUNDS,
)

_LOGGER = logging.getLogger(__name__)


# ── Schemas ────────────────────────────────────────────────────────────

STEP_USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_API_KEY): str,
        vol.Optional(CONF_BASE_URL, default=DEFAULT_BASE_URL): str,
        vol.Optional(CONF_CHARGER_ID): str,
        vol.Optional(CONF_WEBHOOK_SECRET): str,
    }
)

STEP_ADVANCED_ASK_SCHEMA = vol.Schema(
    {
        vol.Required("enable_advanced", default=False): bool,
    }
)

STEP_ADVANCED_CREDS_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_ADVANCED_EMAIL): str,
        vol.Required("password"): str,
    }
)


def _int_range(opt: str) -> vol.All:
    lo, hi = OPTION_BOUNDS[opt]
    return vol.All(int, vol.Range(min=lo, max=hi))


# ── Helpers ────────────────────────────────────────────────────────────

def _map_firebase_error(err: Exception) -> str:
    """Translate an auth_firebase exception to a localisation key."""
    if isinstance(err, TapFirebaseInvalidCredentials):
        code = str(err)
        if "EMAIL_NOT_FOUND" in code:
            return "invalid_email"
        if "INVALID_PASSWORD" in code or "INVALID_LOGIN_CREDENTIALS" in code:
            return "invalid_password"
        if "USER_DISABLED" in code:
            return "user_disabled"
        if "INVALID_EMAIL" in code:
            return "invalid_email"
        return "firebase_unknown"
    if isinstance(err, TapFirebaseNetworkError):
        return "cannot_connect"
    if isinstance(err, TapFirebaseAuthError):
        return "firebase_unknown"
    return "firebase_unknown"


async def _advanced_sign_in(
    session: aiohttp.ClientSession, email: str, password: str,
) -> tuple[dict[str, Any] | None, str | None]:
    """Sign in + discover account. Returns (entry_data_fragment, error_key)."""
    auth = TapFirebaseAuth(session)
    try:
        tokens = await auth.sign_in(email, password)
    except TapFirebaseAuthError as err:
        return None, _map_firebase_error(err)
    except aiohttp.ClientError:
        return None, "cannot_connect"

    client = TapManagementClient(session, auth, tokens)
    try:
        account_id = await client.discover_account_id()
    except TapManagementAuthError:
        return None, "firebase_unknown"
    except TapManagementNetworkError:
        return None, "cannot_connect"
    except TapManagementError:
        return None, "account_discovery_failed"

    return (
        {
            CONF_ADVANCED_MODE: True,
            CONF_ADVANCED_EMAIL: tokens.email or email,
            CONF_ADVANCED_REFRESH_TOKEN: client.tokens.refresh_token,
            CONF_ADVANCED_ACCOUNT_ID: account_id,
            CONF_ADVANCED_FIREBASE_USER_ID: tokens.user_id,
            # The chargerManagement write endpoint requires X-Profile-Id.
            # The Firebase user id (usr_*) doubles as the profile id for
            # normal personal accounts; fleet drivers may need to
            # override this from Options if their fleet ties them to a
            # different profile.
            CONF_ADVANCED_PROFILE_ID: tokens.user_id,
        },
        None,
    )


# ── ConfigFlow ─────────────────────────────────────────────────────────

class TapConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 3

    def __init__(self) -> None:
        super().__init__()
        # Collected across the multi-step wizard.
        self._pending_data: dict[str, Any] = {}
        # Stored for reauth: reference to the entry being re-authenticated.
        self._reauth_entry: config_entries.ConfigEntry | None = None

    # ── step: user (sk_ API key) ──────────────────────────────────────

    async def async_step_user(self, user_input: dict[str, Any] | None = None):
        errors: dict[str, str] = {}
        if user_input is not None:
            session = async_get_clientsession(self.hass)
            client = TapElectricClient(
                api_key=user_input[CONF_API_KEY],
                session=session,
                base_url=user_input.get(CONF_BASE_URL, DEFAULT_BASE_URL),
            )
            try:
                await client.list_chargers()
            except TapElectricAuthError:
                errors["base"] = "auth"
            except TapElectricError:
                errors["base"] = "cannot_connect"
            else:
                await self.async_set_unique_id(
                    f"{user_input.get(CONF_BASE_URL, DEFAULT_BASE_URL)}|"
                    f"{user_input[CONF_API_KEY][:6]}"
                )
                self._abort_if_unique_id_configured()
                self._pending_data = dict(user_input)
                return await self.async_step_advanced_ask()

        return self.async_show_form(
            step_id="user", data_schema=STEP_USER_SCHEMA, errors=errors,
        )

    # ── step: advanced_ask (opt in / out) ─────────────────────────────

    async def async_step_advanced_ask(
        self, user_input: dict[str, Any] | None = None,
    ):
        if user_input is not None:
            if user_input.get("enable_advanced"):
                return await self.async_step_advanced_creds()
            # Opt out: finalise entry with advanced_mode=False.
            return self._finalise(advanced_data={CONF_ADVANCED_MODE: False})

        return self.async_show_form(
            step_id="advanced_ask",
            data_schema=STEP_ADVANCED_ASK_SCHEMA,
        )

    # ── step: advanced_creds ──────────────────────────────────────────

    async def async_step_advanced_creds(
        self, user_input: dict[str, Any] | None = None,
    ):
        errors: dict[str, str] = {}
        if user_input is not None:
            session = async_get_clientsession(self.hass)
            fragment, err_key = await _advanced_sign_in(
                session,
                user_input[CONF_ADVANCED_EMAIL].strip(),
                user_input["password"],
            )
            if err_key:
                errors["base"] = err_key
            else:
                assert fragment is not None
                return self._finalise(advanced_data=fragment)

        return self.async_show_form(
            step_id="advanced_creds",
            data_schema=STEP_ADVANCED_CREDS_SCHEMA,
            errors=errors,
        )

    def _finalise(self, *, advanced_data: dict[str, Any]):
        data = {**self._pending_data, **advanced_data}
        return self.async_create_entry(title="Tap Electric", data=data)

    # ── Reauth (triggered by coordinator on repeated auth failure) ────

    async def async_step_reauth(
        self, entry_data: dict[str, Any],
    ):
        """Entry point when HA calls entry.async_start_reauth_flow()."""
        self._reauth_entry = self.hass.config_entries.async_get_entry(
            self.context["entry_id"]
        )
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None,
    ):
        entry = self._reauth_entry
        if entry is None:
            return self.async_abort(reason="reauth_entry_missing")

        stored_email = entry.data.get(CONF_ADVANCED_EMAIL, "")
        errors: dict[str, str] = {}
        if user_input is not None:
            session = async_get_clientsession(self.hass)
            fragment, err_key = await _advanced_sign_in(
                session, stored_email, user_input["password"],
            )
            if err_key:
                errors["base"] = err_key
            else:
                assert fragment is not None
                self.hass.config_entries.async_update_entry(
                    entry, data={**entry.data, **fragment},
                )
                await self.hass.config_entries.async_reload(entry.entry_id)
                _LOGGER.info(
                    "Re-authenticated advanced mode for %s", stored_email,
                )
                return self.async_abort(reason="reauth_successful")

        schema = vol.Schema({vol.Required("password"): str})
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=schema,
            description_placeholders={"email": stored_email},
            errors=errors,
        )

    # ── Options flow handle ───────────────────────────────────────────

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> config_entries.OptionsFlow:
        return TapOptionsFlowHandler(config_entry)


# ── OptionsFlow ────────────────────────────────────────────────────────

class TapOptionsFlowHandler(config_entries.OptionsFlow):
    """Two-tier options flow: a top-level menu routes to the existing
    general options or to the advanced-mode submenu."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        self._card_draft: list[dict[str, str]] | None = None
        self._default_card_id: str | None = None
        self._editing_card_id: str | None = None

    # ── menu root ─────────────────────────────────────────────────────

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None,
    ):
        return self.async_show_menu(
            step_id="init",
            menu_options=["general", "advanced_menu"],
        )

    # ── sub-flow: existing general options ────────────────────────────

    async def async_step_general(
        self, user_input: dict[str, Any] | None = None,
    ):
        if user_input is not None:
            # Preserve advanced-mode related options we don't surface here.
            preserved = {
                k: v for k, v in self.config_entry.options.items()
                if k not in DEFAULT_OPTIONS
            }
            return self.async_create_entry(
                title="", data={**preserved, **user_input},
            )

        current = {**DEFAULT_OPTIONS, **self.config_entry.options}
        schema = vol.Schema(
            {
                vol.Required(
                    OPT_SCAN_INTERVAL_ACTIVE_S,
                    default=current[OPT_SCAN_INTERVAL_ACTIVE_S],
                ): _int_range(OPT_SCAN_INTERVAL_ACTIVE_S),
                vol.Required(
                    OPT_SCAN_INTERVAL_IDLE_S,
                    default=current[OPT_SCAN_INTERVAL_IDLE_S],
                ): _int_range(OPT_SCAN_INTERVAL_IDLE_S),
                vol.Required(
                    OPT_SESSIONS_HISTORY_LIMIT,
                    default=current[OPT_SESSIONS_HISTORY_LIMIT],
                ): _int_range(OPT_SESSIONS_HISTORY_LIMIT),
                vol.Required(
                    OPT_METER_DATA_LIMIT,
                    default=current[OPT_METER_DATA_LIMIT],
                ): _int_range(OPT_METER_DATA_LIMIT),
                vol.Required(
                    OPT_STALE_THRESHOLD_MINUTES,
                    default=current[OPT_STALE_THRESHOLD_MINUTES],
                ): _int_range(OPT_STALE_THRESHOLD_MINUTES),
                vol.Required(
                    OPT_ROUND_ENERGY_DECIMALS,
                    default=current[OPT_ROUND_ENERGY_DECIMALS],
                ): _int_range(OPT_ROUND_ENERGY_DECIMALS),
                vol.Required(
                    OPT_ROUND_POWER_DECIMALS,
                    default=current[OPT_ROUND_POWER_DECIMALS],
                ): _int_range(OPT_ROUND_POWER_DECIMALS),
                vol.Required(
                    OPT_WRITE_ENABLED,
                    default=bool(current[OPT_WRITE_ENABLED]),
                ): bool,
            }
        )
        return self.async_show_form(step_id="general", data_schema=schema)

    # ── sub-flow: advanced-mode menu ──────────────────────────────────

    async def async_step_advanced_menu(
        self, user_input: dict[str, Any] | None = None,
    ):
        enabled = bool(self.config_entry.data.get(CONF_ADVANCED_MODE))
        options: list[str] = []
        if enabled:
            options = [
                "advanced_update",
                "advanced_remote",
                "advanced_cards",
                "advanced_disable",
            ]
        else:
            options = ["advanced_enable"]
        return self.async_show_menu(
            step_id="advanced_menu",
            menu_options=options,
        )

    # ── sub-flow: saved charging cards ───────────────────────────────

    def _ensure_card_draft(self) -> None:
        if self._card_draft is not None:
            return
        self._card_draft = [
            dict(card) for card in charging_cards(self.config_entry.options)
        ]
        stored_default = self.config_entry.options.get(
            OPT_DEFAULT_CHARGING_CARD_ID,
        )
        self._default_card_id = (
            stored_default
            if any(card["id"] == stored_default for card in self._card_draft)
            else None
        )

    def _card_options(self) -> list[SelectOptionDict]:
        self._ensure_card_draft()
        assert self._card_draft is not None
        return [
            {"value": card["id"], "label": card["label"]}
            for card in self._card_draft
        ]

    def _card_by_id(self, card_id: str | None) -> dict[str, str] | None:
        self._ensure_card_draft()
        assert self._card_draft is not None
        return next(
            (card for card in self._card_draft if card["id"] == card_id),
            None,
        )

    async def async_step_advanced_cards(
        self, user_input: dict[str, Any] | None = None,
    ):
        self._ensure_card_draft()
        assert self._card_draft is not None
        menu_options = ["card_add"]
        if self._card_draft:
            menu_options.extend(["card_edit", "card_remove", "card_default"])
        menu_options.append("card_save")

        default_card = self._card_by_id(self._default_card_id)
        return self.async_show_menu(
            step_id="advanced_cards",
            menu_options=menu_options,
            description_placeholders={
                "cards": ", ".join(card["label"] for card in self._card_draft)
                or "-",
                "default": default_card["label"] if default_card else "-",
            },
        )

    async def async_step_card_add(
        self, user_input: dict[str, Any] | None = None,
    ):
        self._ensure_card_draft()
        assert self._card_draft is not None
        errors: dict[str, str] = {}
        label = ""
        if user_input is not None:
            label, id_tag, errors = validate_card_input(
                self._card_draft,
                user_input.get("label", ""),
                user_input.get("id_tag", ""),
            )
            if not errors:
                card_id = secrets.token_hex(8)
                while self._card_by_id(card_id) is not None:
                    card_id = secrets.token_hex(8)
                self._card_draft.append({
                    "id": card_id, "label": label, "id_tag": id_tag,
                })
                if self._default_card_id is None:
                    self._default_card_id = card_id
                return await self.async_step_advanced_cards()

        return self.async_show_form(
            step_id="card_add",
            data_schema=vol.Schema({
                vol.Required("label", default=label): TextSelector(),
                vol.Required("id_tag"): TextSelector(TextSelectorConfig(
                    type=TextSelectorType.PASSWORD,
                )),
            }),
            errors=errors,
        )

    async def async_step_card_edit(
        self, user_input: dict[str, Any] | None = None,
    ):
        self._ensure_card_draft()
        if user_input is not None:
            self._editing_card_id = user_input["card_id"]
            return await self.async_step_card_edit_details()
        return self.async_show_form(
            step_id="card_edit",
            data_schema=vol.Schema({
                vol.Required("card_id"): SelectSelector(
                    SelectSelectorConfig(
                        options=self._card_options(),
                        mode=SelectSelectorMode.DROPDOWN,
                    )
                ),
            }),
        )

    async def async_step_card_edit_details(
        self, user_input: dict[str, Any] | None = None,
    ):
        card = self._card_by_id(self._editing_card_id)
        if card is None:
            return await self.async_step_advanced_cards()

        errors: dict[str, str] = {}
        label = card["label"]
        if user_input is not None:
            raw_id_tag = user_input.get("id_tag", "")
            label, normalized_id_tag, errors = validate_card_input(
                self._card_draft or [],
                user_input.get("label", ""),
                raw_id_tag or card["id_tag"],
                exclude_card_id=card["id"],
                validate_id_tag=bool(raw_id_tag),
            )
            if not errors:
                card["label"] = label
                if raw_id_tag:
                    card["id_tag"] = normalized_id_tag
                self._editing_card_id = None
                return await self.async_step_advanced_cards()

        return self.async_show_form(
            step_id="card_edit_details",
            data_schema=vol.Schema({
                vol.Required("label", default=label): TextSelector(),
                vol.Optional("id_tag", default=""): TextSelector(
                    TextSelectorConfig(type=TextSelectorType.PASSWORD)
                ),
            }),
            errors=errors,
        )

    async def async_step_card_remove(
        self, user_input: dict[str, Any] | None = None,
    ):
        self._ensure_card_draft()
        assert self._card_draft is not None
        errors: dict[str, str] = {}
        if user_input is not None:
            if not user_input.get("confirm"):
                errors["confirm"] = "confirm_remove"
            else:
                card_id = user_input["card_id"]
                self._card_draft = [
                    card for card in self._card_draft
                    if card["id"] != card_id
                ]
                if self._default_card_id == card_id:
                    self._default_card_id = None
                return await self.async_step_advanced_cards()

        return self.async_show_form(
            step_id="card_remove",
            data_schema=vol.Schema({
                vol.Required("card_id"): SelectSelector(
                    SelectSelectorConfig(
                        options=self._card_options(),
                        mode=SelectSelectorMode.DROPDOWN,
                    )
                ),
                vol.Required("confirm", default=False): bool,
            }),
            errors=errors,
        )

    async def async_step_card_default(
        self, user_input: dict[str, Any] | None = None,
    ):
        self._ensure_card_draft()
        if user_input is not None:
            selected_id = user_input["card_id"] or None
            self._default_card_id = selected_id
            return await self.async_step_advanced_cards()

        return self.async_show_form(
            step_id="card_default",
            data_schema=vol.Schema({
                vol.Required(
                    "card_id",
                    default=self._default_card_id or self._card_draft[0]["id"],
                ): SelectSelector(SelectSelectorConfig(
                    options=self._card_options(),
                    mode=SelectSelectorMode.DROPDOWN,
                )),
            }),
        )

    async def async_step_card_save(
        self, user_input: dict[str, Any] | None = None,
    ):
        self._ensure_card_draft()
        assert self._card_draft is not None
        if self._card_draft and self._card_by_id(self._default_card_id) is None:
            return await self.async_step_card_default()

        new_options = dict(self.config_entry.options)
        if self._card_draft:
            new_options[OPT_CHARGING_CARDS] = [
                dict(card) for card in self._card_draft
            ]
        else:
            new_options.pop(OPT_CHARGING_CARDS, None)

        if self._card_by_id(self._default_card_id) is not None:
            new_options[OPT_DEFAULT_CHARGING_CARD_ID] = self._default_card_id
        else:
            new_options.pop(OPT_DEFAULT_CHARGING_CARD_ID, None)
        return self.async_create_entry(title="", data=new_options)

    def _known_chargers(self) -> list[tuple[str, str]]:
        """Return [(charger_id, display_name), …] from the running coordinator.

        Empty list if the integration isn't loaded yet (the form still
        renders with id_tag + profile_id, just no outlet fields).
        """
        bucket = (
            self.hass.data.get(DOMAIN, {}).get(self.config_entry.entry_id)
            or {}
        )
        coord = bucket.get("coordinator")
        if coord is None or getattr(coord, "data", None) is None:
            return []
        out: list[tuple[str, str]] = []
        for c in getattr(coord.data, "chargers", []) or []:
            cid = c.get("id")
            if not cid:
                continue
            name = c.get("name") or f"Charger {cid[:8]}"
            out.append((cid, name))
        return out

    @staticmethod
    def _outlet_field_label(name: str, cid: str) -> str:
        """Schema key that also serves as the user-facing field label.

        HA falls back to the key when no `data.<key>` translation matches,
        so the label reads naturally without needing one translation per
        dynamic charger.
        """
        return f"Outlet ID for {name} ({cid})"

    async def async_step_advanced_remote(
        self, user_input: dict[str, Any] | None = None,
    ):
        """Remote start/stop configuration: outlet IDs and profile override.

        Stored as top-level entry.data keys (additive, backwards compatible
        with v1.0.0 entries that won't have them). Empty values clear the
        stored setting so users can remove a configured value without
        editing core.config_entries.json by hand.
        """
        chargers = self._known_chargers()
        stored_profile = (
            self.config_entry.data.get(CONF_ADVANCED_PROFILE_ID) or ""
        )
        raw_outlets = self.config_entry.data.get(DATA_DEFAULT_OUTLET_IDS)
        stored_outlets: dict[str, str] = (
            dict(raw_outlets) if isinstance(raw_outlets, dict) else {}
        )

        if user_input is not None:
            profile = (
                (user_input.get(CONF_ADVANCED_PROFILE_ID) or "").strip()
                or None
            )
            # Start from the existing dict so outlet IDs for chargers the
            # coordinator no longer reports (e.g. user temporarily offline)
            # aren't silently lost.
            outlets: dict[str, str] = dict(stored_outlets)
            for cid, name in chargers:
                label = self._outlet_field_label(name, cid)
                raw = (user_input.get(label) or "").strip()
                if raw:
                    outlets[cid] = raw
                else:
                    outlets.pop(cid, None)

            new_data = {**self.config_entry.data}
            if profile is None:
                new_data.pop(CONF_ADVANCED_PROFILE_ID, None)
            else:
                new_data[CONF_ADVANCED_PROFILE_ID] = profile
            if outlets:
                new_data[DATA_DEFAULT_OUTLET_IDS] = outlets
            else:
                new_data.pop(DATA_DEFAULT_OUTLET_IDS, None)

            self.hass.config_entries.async_update_entry(
                self.config_entry, data=new_data,
            )
            await self.hass.config_entries.async_reload(
                self.config_entry.entry_id,
            )
            return self.async_create_entry(title="", data={})

        schema_dict: dict[Any, Any] = {}
        for cid, name in chargers:
            label = self._outlet_field_label(name, cid)
            schema_dict[
                vol.Optional(label, default=stored_outlets.get(cid, ""))
            ] = str
        schema_dict[
            vol.Optional(CONF_ADVANCED_PROFILE_ID, default=stored_profile)
        ] = str

        return self.async_show_form(
            step_id="advanced_remote", data_schema=vol.Schema(schema_dict),
        )

    async def async_step_advanced_enable(
        self, user_input: dict[str, Any] | None = None,
    ):
        return await self._advanced_creds_step(user_input)

    async def async_step_advanced_update(
        self, user_input: dict[str, Any] | None = None,
    ):
        return await self._advanced_creds_step(user_input)

    async def _advanced_creds_step(
        self, user_input: dict[str, Any] | None,
    ):
        errors: dict[str, str] = {}
        stored_email = self.config_entry.data.get(CONF_ADVANCED_EMAIL) or ""
        if user_input is not None:
            session = async_get_clientsession(self.hass)
            email = user_input.get(CONF_ADVANCED_EMAIL, stored_email).strip()
            fragment, err_key = await _advanced_sign_in(
                session, email, user_input["password"],
            )
            if err_key:
                errors["base"] = err_key
            else:
                assert fragment is not None
                self.hass.config_entries.async_update_entry(
                    self.config_entry,
                    data={**self.config_entry.data, **fragment},
                )
                await self.hass.config_entries.async_reload(
                    self.config_entry.entry_id,
                )
                return self.async_create_entry(title="", data={})

        schema = vol.Schema(
            {
                vol.Required(
                    CONF_ADVANCED_EMAIL, default=stored_email,
                ): str,
                vol.Required("password"): str,
            }
        )
        return self.async_show_form(
            step_id="advanced_creds",
            data_schema=schema,
            errors=errors,
        )

    async def async_step_advanced_creds(
        self, user_input: dict[str, Any] | None = None,
    ):
        return await self._advanced_creds_step(user_input)

    async def async_step_advanced_disable(
        self, user_input: dict[str, Any] | None = None,
    ):
        # Flip the flag + clear secrets; leave email + account id for
        # convenience if the user re-enables later (email is not secret).
        new_data = {
            **self.config_entry.data,
            CONF_ADVANCED_MODE: False,
            CONF_ADVANCED_REFRESH_TOKEN: None,
        }
        self.hass.config_entries.async_update_entry(
            self.config_entry, data=new_data,
        )
        await self.hass.config_entries.async_reload(
            self.config_entry.entry_id,
        )
        return self.async_create_entry(title="", data={})
