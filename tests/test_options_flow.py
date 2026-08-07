"""Options-flow tests — menu routing, advanced mode, and charging cards."""
from __future__ import annotations

import pytest

pytestmark = [pytest.mark.requires_ha]


async def test_options_menu_entry_point(hass):
    from pytest_homeassistant_custom_component.common import MockConfigEntry
    from custom_components.tapelectric.const import DOMAIN

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"api_key": "sk_ok", "advanced_mode": False},
        version=3,
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] == "menu"
    assert result["step_id"] == "init"


async def test_options_general_updates_options(hass):
    from pytest_homeassistant_custom_component.common import MockConfigEntry
    from custom_components.tapelectric.const import DOMAIN

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"api_key": "sk_ok", "advanced_mode": False},
        version=3,
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
    from custom_components.tapelectric.const import DOMAIN

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            "api_key": "sk_ok",
            "advanced_mode": True,
            "advanced_email": "e@x.com",
            "advanced_refresh_token": "rt",
        },
        version=3,
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


# ── advanced_remote (remote start/stop) step ─────────────────────────────
#
# The step builds its form dynamically from the running coordinator's
# charger list. These tests stash a minimal coordinator stub in
# hass.data[DOMAIN][entry_id]["coordinator"] so the form renders with
# realistic per-charger outlet_id fields without standing up the full
# integration.

def _install_fake_coordinator(hass, entry_id, chargers):
    from types import SimpleNamespace

    from custom_components.tapelectric.const import DOMAIN

    hass.data.setdefault(DOMAIN, {})[entry_id] = {
        "coordinator": SimpleNamespace(
            data=SimpleNamespace(chargers=list(chargers)),
        ),
    }


async def _open_remote_form(hass, entry):
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input={"next_step_id": "advanced_menu"},
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input={"next_step_id": "advanced_remote"},
    )
    return result


async def test_remote_settings_form_prefills_existing_values(hass):
    """Form must surface stored profile_id and outlet IDs."""
    from pytest_homeassistant_custom_component.common import MockConfigEntry
    from custom_components.tapelectric.const import (
        CONF_ADVANCED_PROFILE_ID,
        DATA_DEFAULT_OUTLET_IDS,
        DOMAIN,
    )

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            "api_key": "sk_ok",
            "advanced_mode": True,
            CONF_ADVANCED_PROFILE_ID: "usr_existing",
            DATA_DEFAULT_OUTLET_IDS: {"EVB-P22208163": "ou_" + "a" * 32},
        },
        version=3,
    )
    entry.add_to_hass(hass)
    _install_fake_coordinator(
        hass, entry.entry_id,
        [{"id": "EVB-P22208163", "name": "Garage"}],
    )

    result = await _open_remote_form(hass, entry)
    assert result["type"] == "form"
    assert result["step_id"] == "advanced_remote"

    schema = result["data_schema"].schema
    defaults = {
        getattr(key, "schema", key): key.default()
        for key in schema
        if hasattr(key, "default")
    }
    assert defaults.get(CONF_ADVANCED_PROFILE_ID) == "usr_existing"
    outlet_label = "Outlet ID for Garage (EVB-P22208163)"
    assert defaults.get(outlet_label) == "ou_" + "a" * 32


async def test_remote_settings_no_chargers_renders_profile_field(hass):
    """Without a coordinator the form still renders profile_id."""
    from pytest_homeassistant_custom_component.common import MockConfigEntry
    from custom_components.tapelectric.const import (
        CONF_ADVANCED_PROFILE_ID,
        DOMAIN,
    )

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"api_key": "sk_ok", "advanced_mode": True},
        version=3,
    )
    entry.add_to_hass(hass)
    # Deliberately no fake coordinator — simulates a freshly-installed
    # entry whose first refresh hasn't populated data yet.

    result = await _open_remote_form(hass, entry)
    assert result["type"] == "form"
    keys = {
        getattr(key, "schema", key) for key in result["data_schema"].schema
    }
    assert CONF_ADVANCED_PROFILE_ID in keys
    # No outlet fields when the coordinator can't tell us which chargers exist.
    assert not any(
        isinstance(k, str) and k.startswith("Outlet ID for ") for k in keys
    )


async def test_remote_settings_multi_charger_renders_one_field_each(hass):
    """One outlet_id field per known charger."""
    from pytest_homeassistant_custom_component.common import MockConfigEntry
    from custom_components.tapelectric.const import DOMAIN

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"api_key": "sk_ok", "advanced_mode": True},
        version=3,
    )
    entry.add_to_hass(hass)
    _install_fake_coordinator(
        hass, entry.entry_id,
        [
            {"id": "EVB-P22208163", "name": "Garage"},
            {"id": "EVB-P22208164", "name": "Driveway"},
        ],
    )

    result = await _open_remote_form(hass, entry)
    keys = {
        getattr(key, "schema", key) for key in result["data_schema"].schema
    }
    assert "Outlet ID for Garage (EVB-P22208163)" in keys
    assert "Outlet ID for Driveway (EVB-P22208164)" in keys


async def test_remote_settings_save_persists_to_entry_data(hass):
    """Submitting the form writes profile_id and outlet IDs to entry.data."""
    from pytest_homeassistant_custom_component.common import MockConfigEntry
    from custom_components.tapelectric.const import (
        CONF_ADVANCED_PROFILE_ID,
        DATA_DEFAULT_OUTLET_IDS,
        DOMAIN,
    )

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"api_key": "sk_ok", "advanced_mode": True},
        version=3,
    )
    entry.add_to_hass(hass)
    _install_fake_coordinator(
        hass, entry.entry_id,
        [{"id": "EVB-P22208163", "name": "Garage"}],
    )

    result = await _open_remote_form(hass, entry)
    outlet_label = "Outlet ID for Garage (EVB-P22208163)"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input={
            outlet_label: "ou_" + "b" * 32,
            CONF_ADVANCED_PROFILE_ID: "",
        },
    )
    assert result["type"] == "create_entry"
    assert entry.data.get(DATA_DEFAULT_OUTLET_IDS) == {
        "EVB-P22208163": "ou_" + "b" * 32,
    }
    assert entry.data.get(CONF_ADVANCED_PROFILE_ID) in (None, "")


# ── charging cards CRUD ─────────────────────────────────────────────────

async def _open_cards_menu(hass, entry):
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input={"next_step_id": "advanced_menu"},
    )
    return await hass.config_entries.options.async_configure(
        result["flow_id"], user_input={"next_step_id": "advanced_cards"},
    )


def _card_entry(*, cards=None, default=None, data=None):
    from pytest_homeassistant_custom_component.common import MockConfigEntry
    from custom_components.tapelectric.const import DOMAIN

    options = {}
    if cards is not None:
        options["charging_cards"] = cards
    if default is not None:
        options["default_charging_card_id"] = default
    return MockConfigEntry(
        domain=DOMAIN,
        data={"api_key": "sk_ok", "advanced_mode": True, **(data or {})},
        options=options,
        version=3,
    )


async def test_charging_cards_add_normalizes_and_sets_first_default(hass):
    entry = _card_entry()
    entry.add_to_hass(hass)
    result = await _open_cards_menu(hass, entry)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input={"next_step_id": "card_add"},
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input={"label": " Employer ", "id_tag": "12-ab:34 cd"},
    )
    assert result["type"] == "menu"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input={"next_step_id": "card_save"},
    )
    card = result["data"]["charging_cards"][0]
    assert card["label"] == "Employer"
    assert card["id_tag"] == "12AB34CD"
    assert result["data"]["default_charging_card_id"] == card["id"]


async def test_charging_cards_reject_duplicate_label_and_uid(hass):
    cards = [{"id": "work", "label": "Employer", "id_tag": "12AB34CD"}]
    entry = _card_entry(cards=cards, default="work")
    entry.add_to_hass(hass)
    result = await _open_cards_menu(hass, entry)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input={"next_step_id": "card_add"},
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input={"label": " employer ", "id_tag": "12-ab-34-cd"},
    )
    assert result["type"] == "form"
    assert result["errors"] == {
        "label": "duplicate_label",
        "id_tag": "duplicate_id_tag",
    }


async def test_charging_cards_edit_blank_uid_preserves_secret(hass):
    cards = [{"id": "work", "label": "Employer", "id_tag": "12AB34CD"}]
    entry = _card_entry(cards=cards, default="work")
    entry.add_to_hass(hass)
    result = await _open_cards_menu(hass, entry)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input={"next_step_id": "card_edit"},
    )
    assert "12AB34CD" not in str(result["data_schema"])
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input={"card_id": "work"},
    )
    assert "12AB34CD" not in str(result["data_schema"])
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input={"label": "Employer / Shell", "id_tag": ""},
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input={"next_step_id": "card_save"},
    )
    assert result["data"]["charging_cards"] == [{
        "id": "work", "label": "Employer / Shell", "id_tag": "12AB34CD",
    }]


async def test_charging_cards_remove_leaves_selection_stale(hass):
    cards = [{"id": "work", "label": "Employer", "id_tag": "12AB34CD"}]
    selections = {"selected_charging_card_ids": {"EVB-1": "work"}}
    entry = _card_entry(cards=cards, default="work", data=selections)
    entry.add_to_hass(hass)
    result = await _open_cards_menu(hass, entry)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input={"next_step_id": "card_remove"},
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input={"card_id": "work", "confirm": True},
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input={"next_step_id": "card_save"},
    )
    assert "charging_cards" not in result["data"]
    assert "default_charging_card_id" not in result["data"]
    assert entry.data["selected_charging_card_ids"] == {"EVB-1": "work"}


async def test_charging_cards_set_default_does_not_change_selections(hass):
    cards = [
        {"id": "work", "label": "Employer", "id_tag": "12AB34CD"},
        {"id": "home", "label": "Personal", "id_tag": "89ABCDEF"},
    ]
    selections = {"selected_charging_card_ids": {"EVB-1": "work"}}
    entry = _card_entry(cards=cards, default="work", data=selections)
    entry.add_to_hass(hass)
    result = await _open_cards_menu(hass, entry)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input={"next_step_id": "card_default"},
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input={"card_id": "home"},
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input={"next_step_id": "card_save"},
    )
    assert result["data"]["default_charging_card_id"] == "home"
    assert entry.data["selected_charging_card_ids"] == {"EVB-1": "work"}


async def test_charging_cards_require_default_when_cards_remain(hass):
    cards = [
        {"id": "work", "label": "Employer", "id_tag": "12AB34CD"},
        {"id": "home", "label": "Personal", "id_tag": "89ABCDEF"},
    ]
    entry = _card_entry(cards=cards, default="work")
    entry.add_to_hass(hass)
    result = await _open_cards_menu(hass, entry)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input={"next_step_id": "card_remove"},
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input={"card_id": "work", "confirm": True},
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input={"next_step_id": "card_save"},
    )
    assert result["type"] == "form"
    assert result["step_id"] == "card_default"
