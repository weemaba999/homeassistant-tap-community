"""Device actions — device-scoped shortcuts for pause / resume / set
limit / reset. These translate to calls on our services, which in turn
go through the write-enabled guard.
"""
from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.components.device_automation import (
    DEVICE_ACTION_BASE_SCHEMA,
    InvalidDeviceAutomationConfig,
)
from homeassistant.const import (
    CONF_DEVICE_ID,
    CONF_DOMAIN,
    CONF_TYPE,
)
from homeassistant.core import Context, HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.typing import ConfigType, TemplateVarsType

from .const import DOMAIN
from .device_trigger import _charger_id_for_device

ACTION_TYPES = {"pause", "resume", "set_limit", "reset"}

# set_limit needs a limit_amps field. Everything else is fire-and-forget.
ACTION_SCHEMA = DEVICE_ACTION_BASE_SCHEMA.extend(
    {
        vol.Required(CONF_TYPE): vol.In(ACTION_TYPES),
        vol.Optional("limit_amps"):  vol.Coerce(float),
        vol.Optional("connector_id"): vol.Coerce(int),
        vol.Optional("reset_type"):  vol.In(["Soft", "Hard"]),
    }
)


async def async_get_actions(
    hass: HomeAssistant, device_id: str,
) -> list[dict[str, Any]]:
    return [
        {
            CONF_DOMAIN: DOMAIN,
            CONF_DEVICE_ID: device_id,
            CONF_TYPE: a_type,
        }
        for a_type in ACTION_TYPES
    ]


async def async_call_action_from_config(
    hass: HomeAssistant,
    config: ConfigType,
    variables: TemplateVarsType,
    context: Context | None,
) -> None:
    charger_id = _charger_id_for_device(hass, config[CONF_DEVICE_ID])
    if charger_id is None:
        raise InvalidDeviceAutomationConfig(
            f"No Tap charger device found for device_id {config[CONF_DEVICE_ID]}"
        )
    connector_id = int(config.get("connector_id", 1))

    a_type = config[CONF_TYPE]
    if a_type == "pause":
        await hass.services.async_call(
            DOMAIN, "pause_charging",
            {"charger_id": charger_id, "connector_id": connector_id},
            blocking=True, context=context,
        )
    elif a_type == "resume":
        data: dict[str, Any] = {
            "charger_id": charger_id, "connector_id": connector_id,
        }
        if "limit_amps" in config:
            data["limit_amps"] = float(config["limit_amps"])
        await hass.services.async_call(
            DOMAIN, "resume_charging", data, blocking=True, context=context,
        )
    elif a_type == "set_limit":
        if "limit_amps" not in config:
            raise InvalidDeviceAutomationConfig(
                "set_limit requires limit_amps"
            )
        await hass.services.async_call(
            DOMAIN, "set_charging_limit",
            {
                "charger_id": charger_id, "connector_id": connector_id,
                "limit_amps": float(config["limit_amps"]),
            },
            blocking=True, context=context,
        )
    elif a_type == "reset":
        await hass.services.async_call(
            DOMAIN, "reset_charger",
            {
                "charger_id": charger_id,
                "reset_type": config.get("reset_type", "Soft"),
            },
            blocking=True, context=context,
        )
