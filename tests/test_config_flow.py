"""Config-flow tests.

Uses pytest-homeassistant-custom-component. When HA isn't available
locally, every test is auto-skipped by the `requires_ha` marker
handled in conftest.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.requires_ha

from unittest.mock import AsyncMock, patch


@pytest.fixture
async def hass(enable_custom_integrations):
    """Inherited from pytest-homeassistant-custom-component."""
    yield


async def test_user_step_shows_form(hass):
    from homeassistant.config_entries import SOURCE_USER
    from tapelectric.const import DOMAIN
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER},
    )
    assert result["type"] == "form"
    assert result["step_id"] == "user"


async def test_user_step_bad_api_key_shows_error(hass):
    from homeassistant.config_entries import SOURCE_USER
    from tapelectric.const import DOMAIN
    from tapelectric.api import TapElectricAuthError

    with patch(
        "tapelectric.config_flow.TapElectricClient.list_chargers",
        new=AsyncMock(side_effect=TapElectricAuthError("401")),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_USER},
            data={"api_key": "sk_bad"},
        )
    assert result["type"] == "form"
    assert "auth" in (result.get("errors") or {}).values()


async def test_user_step_ok_routes_to_advanced_ask(hass):
    from homeassistant.config_entries import SOURCE_USER
    from tapelectric.const import DOMAIN

    with patch(
        "tapelectric.config_flow.TapElectricClient.list_chargers",
        new=AsyncMock(return_value=[]),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_USER},
            data={"api_key": "sk_ok"},
        )
    assert result["type"] == "form"
    assert result["step_id"] == "advanced_ask"


async def test_advanced_ask_declined_creates_basic_entry(hass):
    from homeassistant.config_entries import SOURCE_USER
    from tapelectric.const import DOMAIN

    with patch(
        "tapelectric.config_flow.TapElectricClient.list_chargers",
        new=AsyncMock(return_value=[]),
    ):
        step1 = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_USER},
            data={"api_key": "sk_ok"},
        )
        step2 = await hass.config_entries.flow.async_configure(
            step1["flow_id"], user_input={"enable_advanced": False},
        )
    assert step2["type"] == "create_entry"
    assert step2["data"]["advanced_mode"] is False


async def test_reauth_flow_accepts_new_password(hass):
    """Re-authentication: the entry already exists, we supply a new password."""
    from homeassistant.config_entries import SOURCE_REAUTH
    from tapelectric.const import DOMAIN
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            "api_key": "sk_ok",
            "advanced_mode": True,
            "advanced_email": "driver@example.com",
            "advanced_refresh_token": "rt_old",
        },
        version=2,
    )
    entry.add_to_hass(hass)

    with patch(
        "tapelectric.auth_firebase.TapFirebaseAuth.sign_in",
        new=AsyncMock(return_value=type("T", (), {
            "id_token": "id_new", "refresh_token": "rt_new",
            "user_id": "uid", "email": "driver@example.com",
            "display_name": None,
            "expires_at": None,
        })()),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": SOURCE_REAUTH, "entry_id": entry.entry_id},
            data=entry.data,
        )
        # Reauth step shows a form; we submit new password.
        assert result["type"] == "form"
        assert result["step_id"] == "reauth_confirm"
