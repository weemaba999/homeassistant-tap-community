"""Config flow (UI setup) and options flow for Tap Electric."""
from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import TapElectricAuthError, TapElectricClient, TapElectricError
from .const import (
    CONF_API_KEY,
    CONF_BASE_URL,
    CONF_CHARGER_ID,
    CONF_WEBHOOK_SECRET,
    DEFAULT_BASE_URL,
    DEFAULT_OPTIONS,
    DOMAIN,
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

STEP_USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_API_KEY): str,
        vol.Optional(CONF_BASE_URL, default=DEFAULT_BASE_URL): str,
        vol.Optional(CONF_CHARGER_ID): str,
        vol.Optional(CONF_WEBHOOK_SECRET): str,
    }
)


def _int_range(opt: str) -> vol.All:
    lo, hi = OPTION_BOUNDS[opt]
    return vol.All(int, vol.Range(min=lo, max=hi))


class TapConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

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
                return self.async_create_entry(
                    title="Tap Electric", data=user_input
                )

        return self.async_show_form(
            step_id="user", data_schema=STEP_USER_SCHEMA, errors=errors
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> config_entries.OptionsFlow:
        return TapOptionsFlowHandler(config_entry)


class TapOptionsFlowHandler(config_entries.OptionsFlow):
    """Options flow — reachable from Devices & Services → Configure.

    Toggling write_enabled triggers an integration reload via the update
    listener registered in __init__.async_setup_entry, so platforms pick
    up the new setting without a user-initiated restart. Scan intervals
    and limits are picked up live by the coordinator on the next tick.
    """

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        self.config_entry = config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None,
    ):
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

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
        return self.async_show_form(step_id="init", data_schema=schema)
