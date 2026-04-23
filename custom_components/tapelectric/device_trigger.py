"""Device triggers — let users pick "when charger X starts charging" in
the automation UI without having to know entity IDs.

We translate our trigger types to the built-in state trigger by resolving
the target entity from the device + unique_id suffix.
"""
from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.components.device_automation import DEVICE_TRIGGER_BASE_SCHEMA
from homeassistant.components.homeassistant.triggers import state as state_trigger
from homeassistant.const import (
    CONF_DEVICE_ID,
    CONF_DOMAIN,
    CONF_ENTITY_ID,
    CONF_PLATFORM,
    CONF_TYPE,
)
from homeassistant.core import CALLBACK_TYPE, HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.trigger import TriggerInfo
from homeassistant.helpers.typing import ConfigType

from .const import DOMAIN

# Trigger_type → (unique_id suffix template, from_state, to_state)
# suffix "{cid}_*" — we resolve the cid from the device's identifiers.
_TRIGGERS: dict[str, tuple[str, str | None, str | None]] = {
    "charging_start":     ("_1_charging",        "off",  "on"),
    "charging_stop":      ("_1_charging",        "on",   "off"),
    "plug_connected":     ("_1_plug_connected",  "off",  "on"),
    "plug_disconnected":  ("_1_plug_connected",  "on",   "off"),
    "fault":              ("_fault",             "off",  "on"),
}

TRIGGER_TYPES = set(_TRIGGERS.keys())

TRIGGER_SCHEMA = DEVICE_TRIGGER_BASE_SCHEMA.extend(
    {vol.Required(CONF_TYPE): vol.In(TRIGGER_TYPES)}
)


def _charger_id_for_device(hass: HomeAssistant, device_id: str) -> str | None:
    from homeassistant.helpers import device_registry as dr
    dev = dr.async_get(hass).async_get(device_id)
    if not dev:
        return None
    for domain, cid in dev.identifiers:
        if domain == DOMAIN:
            return cid
    return None


def _find_entity_id(
    hass: HomeAssistant, device_id: str, unique_id_suffix: str,
) -> str | None:
    cid = _charger_id_for_device(hass, device_id)
    if not cid:
        return None
    target_unique_id = f"{cid}{unique_id_suffix}"
    registry = er.async_get(hass)
    for ent in er.async_entries_for_device(registry, device_id, include_disabled_entities=True):
        if ent.unique_id == target_unique_id:
            return ent.entity_id
    return None


async def async_get_triggers(
    hass: HomeAssistant, device_id: str,
) -> list[dict[str, Any]]:
    triggers: list[dict[str, Any]] = []
    for t_type in TRIGGER_TYPES:
        triggers.append({
            CONF_PLATFORM: "device",
            CONF_DOMAIN: DOMAIN,
            CONF_DEVICE_ID: device_id,
            CONF_TYPE: t_type,
        })
    return triggers


async def async_attach_trigger(
    hass: HomeAssistant,
    config: ConfigType,
    action,
    trigger_info: TriggerInfo,
) -> CALLBACK_TYPE:
    t_type = config[CONF_TYPE]
    suffix, from_state, to_state = _TRIGGERS[t_type]
    entity_id = _find_entity_id(hass, config[CONF_DEVICE_ID], suffix)
    if entity_id is None:
        # No matching entity yet — return a no-op detach. The automation
        # will simply never fire, which is the right behaviour for a
        # freshly added device that hasn't finished setup.
        def _noop() -> None:
            return
        return _noop

    state_config = {
        state_trigger.CONF_PLATFORM: "state",
        CONF_ENTITY_ID: entity_id,
        state_trigger.CONF_FROM: from_state,
        state_trigger.CONF_TO: to_state,
    }
    state_config = state_trigger.TRIGGER_SCHEMA(state_config)
    return await state_trigger.async_attach_trigger(
        hass, state_config, action, trigger_info, platform_type="device",
    )
