"""Device conditions — "charger X is currently charging / connected / online"."""
from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.components.device_automation import DEVICE_CONDITION_BASE_SCHEMA
from homeassistant.const import (
    CONF_CONDITION,
    CONF_DEVICE_ID,
    CONF_DOMAIN,
    CONF_ENTITY_ID,
    CONF_TYPE,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers import condition, config_validation as cv
from homeassistant.helpers.typing import ConfigType

from .const import DOMAIN
from .device_trigger import _find_entity_id

# condition_type → (unique_id suffix, expected state)
_CONDITIONS: dict[str, tuple[str, str]] = {
    "is_charging":  ("_1_charging",       "on"),
    "is_connected": ("_1_plug_connected", "on"),
    "is_online":    ("_online",           "on"),
}

CONDITION_TYPES = set(_CONDITIONS.keys())

CONDITION_SCHEMA = DEVICE_CONDITION_BASE_SCHEMA.extend(
    {vol.Required(CONF_TYPE): vol.In(CONDITION_TYPES)}
)


async def async_get_conditions(
    hass: HomeAssistant, device_id: str,
) -> list[dict[str, Any]]:
    return [
        {
            CONF_CONDITION: "device",
            CONF_DOMAIN: DOMAIN,
            CONF_DEVICE_ID: device_id,
            CONF_TYPE: c_type,
        }
        for c_type in CONDITION_TYPES
    ]


def async_condition_from_config(
    hass: HomeAssistant, config: ConfigType,
) -> condition.ConditionCheckerType:
    suffix, expected_state = _CONDITIONS[config[CONF_TYPE]]
    entity_id = _find_entity_id(hass, config[CONF_DEVICE_ID], suffix)

    if entity_id is None:
        # No matching entity — condition always evaluates False. Users
        # get a predictable negative instead of a hard error; they can
        # check their device set-up themselves.
        def _always_false(hass: HomeAssistant, variables=None) -> bool:
            return False
        return _always_false

    state_config = {
        CONF_CONDITION: "state",
        CONF_ENTITY_ID: entity_id,
        "state": expected_state,
    }
    state_config = cv.STATE_CONDITION_SCHEMA(state_config)
    return condition.state_from_config(state_config)
