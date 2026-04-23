"""Options-flow tests — menu routing, general settings, advanced mode.

HA-gated; skipped when homeassistant isn't installed.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.requires_ha


async def test_options_menu_entry_point(hass):
    from pytest_homeassistant_custom_component.common import MockConfigEntry
    from tapelectric.const import DOMAIN

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"api_key": "sk_ok", "advanced_mode": False},
        version=2,
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] == "menu"
    assert result["step_id"] == "init"


async def test_options_general_updates_options(hass):
    from pytest_homeassistant_custom_component.common import MockConfigEntry
    from tapelectric.const import DOMAIN

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"api_key": "sk_ok", "advanced_mode": False},
        version=2,
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input={"next_step_id": "general"},
    )
    assert result["type"] == "form"
    assert result["step_id"] == "general"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input={
            "scan_interval_active_s": 45,
            "scan_interval_idle_s": 300,
            "sessions_history_limit": 50,
            "meter_data_limit": 100,
            "stale_threshold_minutes": 15,
            "round_energy_decimals": 3,
            "round_power_decimals": 2,
            "write_enabled": True,
        },
    )
    assert result["type"] == "create_entry"


async def test_options_advanced_disable(hass):
    """Flipping advanced_mode off clears the refresh token."""
    from pytest_homeassistant_custom_component.common import MockConfigEntry
    from tapelectric.const import DOMAIN

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            "api_key": "sk_ok",
            "advanced_mode": True,
            "advanced_email": "e@x.com",
            "advanced_refresh_token": "rt",
        },
        version=2,
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input={"next_step_id": "advanced_menu"},
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input={"next_step_id": "advanced_disable"},
    )
    assert entry.data.get("advanced_mode") is False
    assert entry.data.get("advanced_refresh_token") in (None, "")
