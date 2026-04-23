"""Tests for TapManagementClient — Firebase-authenticated mgmt calls."""
from __future__ import annotations

import asyncio
import re
from datetime import datetime, timezone

import aiohttp
import pytest

from tapelectric.api_management import (
    MGMT_BASE_URL,
    ManagementSession,
    TapManagementAuthError,
    TapManagementClient,
    TapManagementError,
    TapManagementNetworkError,
    TapManagementNotFound,
    TapManagementRateLimited,
)
from tapelectric.auth_firebase import AuthTokens, TapFirebaseAuth


class _FakeAuth:
    """Drop-in for TapFirebaseAuth that never actually refreshes."""
    async def ensure_valid(self, tokens):
        return tokens


def _fresh_tokens() -> AuthTokens:
    return AuthTokens(
        id_token="id_TOKEN",
        refresh_token="rt_TOKEN",
        expires_at=datetime(2999, 1, 1, tzinfo=timezone.utc),
        user_id="uid_1",
        email="driver@x.com",
    )


def _run(coro):
    return asyncio.run(coro)


# ── ManagementSession dataclass ────────────────────────────────────────────

def test_from_dict_flat_shape():
    raw = {
        "session_id":  "cs_1",
        "charger_id":  "EVB-1",
        "start_date":  "2026-04-23T10:00:00Z",
        "end_date":    None,
        "energy_wh":   1234,
        "currency":    "EUR",
        "fleet_driver_name": "Alice",
    }
    s = ManagementSession.from_dict(raw)
    assert s.session_id == "cs_1"
    assert s.charger_id == "EVB-1"
    assert s.is_active is True                 # end_date None
    assert s.energy_kwh == pytest.approx(1.234)


def test_from_dict_invalid_type_raises():
    with pytest.raises(TapManagementError):
        ManagementSession.from_dict("not a dict")  # type: ignore[arg-type]


def test_from_detail_nested_shape():
    raw = {
        "id":           "cs_2",
        "start_date":   "2026-04-22T10:00:00Z",
        "end_date":     "2026-04-22T16:00:00Z",
        "energy_wh":    5000,
        "location_details": {
            "charger_id":    "EVB-2",
            "charger_name":  "Garage",
            "location_name": "Home",
            "address":       "Rue de Test 1",
            "city":          "Brussels",
            "zip":           "1000",
            "country":       "BE",
            "latitude":      50.85,
            "longitude":     4.35,
            "evse_id":       "BE*TAP*E001",
        },
        "cpo_details": {
            "currency":        "EUR",
            "transaction_id":  10065,
        },
        "fleet_details": {
            "fleet_id":    "flt_1",
            "fleet_name":  "Test Fleet",
            "retail_tariff": {"priceIncVat": 0.40},
            "reimbursement": {"amount": 1.50},
        },
    }
    s = ManagementSession.from_detail(raw)
    assert s.session_id == "cs_2"
    assert s.charger_id == "EVB-2"
    assert s.zip_code == "1000"
    assert s.country == "BE"
    assert s.transaction_id == 10065
    assert s.fleet_id == "flt_1"
    assert s.retail_tariff == {"priceIncVat": 0.40}
    assert s.fleet_driver_reimbursement_cost == 1.50
    assert s.is_active is False
    # duration_seconds: 6 hours
    assert s.duration_seconds == 6 * 3600


def test_energy_kwh_none_if_energy_wh_none():
    s = ManagementSession()
    assert s.energy_kwh is None


def test_started_at_handles_bad_timestamp():
    s = ManagementSession(start_date="not-a-time")
    assert s.started_at is None


def test_is_active_true_when_no_end_date():
    s = ManagementSession(end_date=None, start_date="2026-01-01T00:00:00Z")
    assert s.is_active is True


# ── TapManagementClient — HTTP plumbing ────────────────────────────────────

def _any_mgmt(path: str) -> re.Pattern[str]:
    """Match both with and without query string — aioresponses can be finicky."""
    return re.compile(re.escape(f"{MGMT_BASE_URL}{path}") + r"(\?.*)?$")


def test_discover_account_id(mock_aioresponse):
    mock_aioresponse.get(
        _any_mgmt("/accounts"),
        payload=[{"id": "macc_abc"}],
    )

    async def _do():
        async with aiohttp.ClientSession() as s:
            client = TapManagementClient(s, _FakeAuth(), _fresh_tokens())
            return await client.discover_account_id()

    assert _run(_do()) == "macc_abc"


def test_discover_account_id_items_wrapper(mock_aioresponse):
    mock_aioresponse.get(
        _any_mgmt("/accounts"),
        payload={"items": [{"id": "macc_items"}]},
    )

    async def _do():
        async with aiohttp.ClientSession() as s:
            client = TapManagementClient(s, _FakeAuth(), _fresh_tokens())
            return await client.discover_account_id()

    assert _run(_do()) == "macc_items"


def test_discover_account_id_empty_raises(mock_aioresponse):
    mock_aioresponse.get(_any_mgmt("/accounts"), payload=[])

    async def _do():
        async with aiohttp.ClientSession() as s:
            client = TapManagementClient(s, _FakeAuth(), _fresh_tokens())
            await client.discover_account_id()

    with pytest.raises(TapManagementError):
        _run(_do())


def test_list_role_sessions(mock_aioresponse):
    mock_aioresponse.get(
        _any_mgmt("/role-sessions"),
        payload=[
            {"session_id": "cs_a", "charger_id": "EVB-1", "energy_wh": 100},
            {"session_id": "cs_b", "charger_id": "EVB-1", "energy_wh": 200},
        ],
    )

    async def _do():
        async with aiohttp.ClientSession() as s:
            client = TapManagementClient(
                s, _FakeAuth(), _fresh_tokens(), account_id="macc_x",
            )
            return await client.list_role_sessions(take=10)

    rows = _run(_do())
    assert len(rows) == 2
    assert rows[0].session_id == "cs_a"


def test_list_role_sessions_items_wrapper(mock_aioresponse):
    mock_aioresponse.get(
        _any_mgmt("/role-sessions"),
        payload={"items": [{"session_id": "cs_a"}]},
    )

    async def _do():
        async with aiohttp.ClientSession() as s:
            client = TapManagementClient(
                s, _FakeAuth(), _fresh_tokens(), account_id="macc_x",
            )
            return await client.list_role_sessions()

    rows = _run(_do())
    assert rows[0].session_id == "cs_a"


def test_get_session_returns_detail(mock_aioresponse):
    mock_aioresponse.get(
        _any_mgmt("/sessions/cs_abc"),
        payload={
            "id":          "cs_abc",
            "energy_wh":   500,
            "start_date":  "2026-04-23T10:00:00Z",
            "end_date":    "2026-04-23T11:00:00Z",
            "location_details": {"charger_id": "EVB-X"},
        },
    )

    async def _do():
        async with aiohttp.ClientSession() as s:
            client = TapManagementClient(
                s, _FakeAuth(), _fresh_tokens(), account_id="macc_x",
            )
            return await client.get_session("cs_abc")

    session = _run(_do())
    assert session.charger_id == "EVB-X"
    assert session.energy_kwh == pytest.approx(0.5)


def test_get_session_non_object_body_raises(mock_aioresponse):
    mock_aioresponse.get(
        _any_mgmt("/sessions/cs_bad"), payload=["not", "a", "dict"],
    )

    async def _do():
        async with aiohttp.ClientSession() as s:
            client = TapManagementClient(
                s, _FakeAuth(), _fresh_tokens(), account_id="macc_x",
            )
            await client.get_session("cs_bad")

    with pytest.raises(TapManagementError):
        _run(_do())


# ── error status handling ─────────────────────────────────────────────────

@pytest.mark.parametrize("status,exc", [
    (401, TapManagementAuthError),
    (404, TapManagementNotFound),
    (429, TapManagementRateLimited),
])
def test_error_status_mapping(mock_aioresponse, status, exc):
    mock_aioresponse.get(
        _any_mgmt("/role-sessions"), status=status, body="boom",
    )

    async def _do():
        async with aiohttp.ClientSession() as s:
            client = TapManagementClient(
                s, _FakeAuth(), _fresh_tokens(), account_id="macc_x",
            )
            await client.list_role_sessions()

    with pytest.raises(exc):
        _run(_do())


def test_5xx_retries_once_then_raises_network_error(mock_aioresponse):
    mock_aioresponse.get(_any_mgmt("/role-sessions"), status=500, body="")
    mock_aioresponse.get(_any_mgmt("/role-sessions"), status=500, body="")

    async def _do():
        async with aiohttp.ClientSession() as s:
            client = TapManagementClient(
                s, _FakeAuth(), _fresh_tokens(), account_id="macc_x",
            )
            await client.list_role_sessions()

    with pytest.raises(TapManagementNetworkError):
        _run(_do())


def test_5xx_then_ok_succeeds(mock_aioresponse):
    mock_aioresponse.get(_any_mgmt("/role-sessions"), status=503, body="")
    mock_aioresponse.get(
        _any_mgmt("/role-sessions"),
        payload=[{"session_id": "cs_after_retry"}],
    )

    async def _do():
        async with aiohttp.ClientSession() as s:
            client = TapManagementClient(
                s, _FakeAuth(), _fresh_tokens(), account_id="macc_x",
            )
            return await client.list_role_sessions()

    rows = _run(_do())
    assert rows[0].session_id == "cs_after_retry"


def test_close_is_noop_and_returns_none():
    async def _do():
        async with aiohttp.ClientSession() as s:
            client = TapManagementClient(s, _FakeAuth(), _fresh_tokens())
            return await client.close()

    assert _run(_do()) is None
