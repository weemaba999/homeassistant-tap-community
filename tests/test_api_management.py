"""Tests for TapManagementClient — Firebase-authenticated mgmt calls."""
from __future__ import annotations

import asyncio
import re
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import aiohttp
import pytest

from tapelectric.api_management import (
    MGMT_BASE_URL,
    MGMT_WRITE_BASE_URL,
    PATH_OCPP_MESSAGES,
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


# ── OCPP write methods (remote start/stop) ────────────────────────────────

def _ocpp_url(charger_id: str) -> str:
    return f"{MGMT_WRITE_BASE_URL}{PATH_OCPP_MESSAGES.format(charger_id=charger_id)}"


def test_remote_stop_transaction_happy(mock_aioresponse):
    """Live API returns 200 + empty body — we synthesise Accepted."""
    mock_aioresponse.post(_ocpp_url("EVB-1"), status=200, body="")

    async def _do():
        async with aiohttp.ClientSession() as s:
            client = TapManagementClient(
                s, _FakeAuth(), _fresh_tokens(),
                account_id="acc_x", profile_id="usr_x",
            )
            return await client.remote_stop_transaction("EVB-1", 9999)

    result = _run(_do())
    assert result == {"status": "Accepted"}


def test_remote_stop_transaction_rejected(mock_aioresponse):
    """If the API ever surfaces an explicit Rejected, pass it through."""
    mock_aioresponse.post(
        _ocpp_url("EVB-1"),
        status=200,
        payload={"status": "Rejected"},
    )

    async def _do():
        async with aiohttp.ClientSession() as s:
            client = TapManagementClient(
                s, _FakeAuth(), _fresh_tokens(),
                account_id="acc_x", profile_id="usr_x",
            )
            return await client.remote_stop_transaction("EVB-1", 9999)

    assert _run(_do()) == {"status": "Rejected"}


def test_remote_stop_transaction_auth_error(mock_aioresponse):
    mock_aioresponse.post(_ocpp_url("EVB-1"), status=401, body="nope")

    async def _do():
        async with aiohttp.ClientSession() as s:
            client = TapManagementClient(
                s, _FakeAuth(), _fresh_tokens(),
                account_id="acc_x", profile_id="usr_x",
            )
            await client.remote_stop_transaction("EVB-1", 9999)

    with pytest.raises(TapManagementAuthError):
        _run(_do())


def test_remote_stop_transaction_network_error_returns_none(mock_aioresponse):
    # 500 twice → TapManagementNetworkError → swallowed → None
    mock_aioresponse.post(_ocpp_url("EVB-1"), status=500, body="")
    mock_aioresponse.post(_ocpp_url("EVB-1"), status=500, body="")

    async def _do():
        async with aiohttp.ClientSession() as s:
            client = TapManagementClient(
                s, _FakeAuth(), _fresh_tokens(),
                account_id="acc_x", profile_id="usr_x",
            )
            return await client.remote_stop_transaction("EVB-1", 9999)

    assert _run(_do()) is None


def test_remote_stop_transaction_403_is_auth_error(mock_aioresponse):
    mock_aioresponse.post(_ocpp_url("EVB-1"), status=403, body="")

    async def _do():
        async with aiohttp.ClientSession() as s:
            client = TapManagementClient(
                s, _FakeAuth(), _fresh_tokens(),
                account_id="acc_x", profile_id="usr_x",
            )
            await client.remote_stop_transaction("EVB-1", 9999)

    with pytest.raises(TapManagementAuthError):
        _run(_do())


def test_remote_stop_transaction_requires_tid():
    async def _do():
        async with aiohttp.ClientSession() as s:
            client = TapManagementClient(
                s, _FakeAuth(), _fresh_tokens(),
                account_id="acc_x", profile_id="usr_x",
            )
            await client.remote_stop_transaction("EVB-1", None)  # type: ignore[arg-type]

    with pytest.raises(TapManagementError):
        _run(_do())


def test_remote_start_transaction_happy(mock_aioresponse):
    mock_aioresponse.post(_ocpp_url("EVB-1"), status=200, body="")

    async def _do():
        async with aiohttp.ClientSession() as s:
            client = TapManagementClient(
                s, _FakeAuth(), _fresh_tokens(),
                account_id="acc_x", profile_id="usr_x",
            )
            return await client.remote_start_transaction(
                "EVB-1", outlet_id="ou_abc", id_tag="TAP-1",
            )

    assert _run(_do()) == {"status": "Accepted"}


def test_remote_start_transaction_missing_id_tag_raises():
    async def _do():
        async with aiohttp.ClientSession() as s:
            client = TapManagementClient(
                s, _FakeAuth(), _fresh_tokens(),
                account_id="acc_x", profile_id="usr_x",
            )
            await client.remote_start_transaction(
                "EVB-1", outlet_id="ou_abc", id_tag=None,
            )

    with pytest.raises(TapManagementError, match="id_tag"):
        _run(_do())


def test_remote_start_transaction_missing_outlet_id_raises():
    async def _do():
        async with aiohttp.ClientSession() as s:
            client = TapManagementClient(
                s, _FakeAuth(), _fresh_tokens(),
                account_id="acc_x", profile_id="usr_x",
            )
            await client.remote_start_transaction(
                "EVB-1", outlet_id=None, id_tag="TAP-1",
            )

    with pytest.raises(TapManagementError, match="outlet_id"):
        _run(_do())


def test_remote_start_transaction_auth_error(mock_aioresponse):
    mock_aioresponse.post(_ocpp_url("EVB-1"), status=401, body="")

    async def _do():
        async with aiohttp.ClientSession() as s:
            client = TapManagementClient(
                s, _FakeAuth(), _fresh_tokens(),
                account_id="acc_x", profile_id="usr_x",
            )
            await client.remote_start_transaction(
                "EVB-1", outlet_id="ou_abc", id_tag="TAP-1",
            )

    with pytest.raises(TapManagementAuthError):
        _run(_do())


def test_remote_stop_sends_correct_envelope(mock_aioresponse):
    """Verify the request body matches the schema captured from the webapp."""
    mock_aioresponse.post(_ocpp_url("EVB-1"), status=200, body="")

    async def _do():
        async with aiohttp.ClientSession() as s:
            client = TapManagementClient(
                s, _FakeAuth(), _fresh_tokens(),
                account_id="acc_x", profile_id="usr_x",
            )
            await client.remote_stop_transaction("EVB-1", 9999)

    _run(_do())
    # aioresponses exposes the request history via .requests
    posts = [
        (k, v) for k, v in mock_aioresponse.requests.items()
        if k[0] == "POST"
    ]
    assert posts, "no POST recorded"
    call = posts[0][1][0]
    body = call.kwargs.get("json")
    assert body == {
        "message_type": "remotestoptransaction",
        "remote_stop_transaction_details": {"transaction_id": 9999},
    }


def test_remote_start_sends_correct_envelope(mock_aioresponse):
    mock_aioresponse.post(_ocpp_url("EVB-1"), status=200, body="")

    async def _do():
        async with aiohttp.ClientSession() as s:
            client = TapManagementClient(
                s, _FakeAuth(), _fresh_tokens(),
                account_id="acc_x", profile_id="usr_x",
            )
            await client.remote_start_transaction(
                "EVB-1", outlet_id="ou_xyz", id_tag="TAP-1",
            )

    _run(_do())
    posts = [
        (k, v) for k, v in mock_aioresponse.requests.items()
        if k[0] == "POST"
    ]
    assert posts
    body = posts[0][1][0].kwargs.get("json")
    assert body == {
        "message_type": "remotestarttransaction",
        "remote_start_transaction_details": {
            "outlet_id": "ou_xyz",
            "id_tag":    "TAP-1",
            "visual_id": None,
        },
    }


def test_remote_start_redacts_echoed_id_tag(monkeypatch, caplog):
    client = TapManagementClient(
        None, _FakeAuth(), _fresh_tokens(), account_id="acc_test",
    )
    monkeypatch.setattr(
        client,
        "_request",
        AsyncMock(side_effect=TapManagementError(
            'HTTP 400: {"id_tag":"12AB34CD"}',
        )),
    )
    body = {
        "message_type": "remotestarttransaction",
        "remote_start_transaction_details": {
            "outlet_id": "ou_xyz",
            "id_tag": "12AB34CD",
            "visual_id": None,
        },
    }

    _run(client._post_ocpp_message("EVB-1", body))

    assert "12AB34CD" not in caplog.text
    assert "<redacted>" in caplog.text


def test_remote_stop_sets_required_headers(mock_aioresponse):
    """X-Account-Id and X-Profile-Id must be on the wire."""
    mock_aioresponse.post(_ocpp_url("EVB-1"), status=200, body="")

    async def _do():
        async with aiohttp.ClientSession() as s:
            client = TapManagementClient(
                s, _FakeAuth(), _fresh_tokens(),
                account_id="acc_test", profile_id="usr_test",
            )
            await client.remote_stop_transaction("EVB-1", 1)

    _run(_do())
    posts = [
        (k, v) for k, v in mock_aioresponse.requests.items()
        if k[0] == "POST"
    ]
    headers = posts[0][1][0].kwargs.get("headers") or {}
    assert headers.get("X-Account-Id") == "acc_test"
    assert headers.get("X-Profile-Id") == "usr_test"
    assert headers.get("X-Api-Key")  # static; just confirm it's present
