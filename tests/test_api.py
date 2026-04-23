"""Tests for custom_components.tapelectric.api — the public REST client."""
from __future__ import annotations

import asyncio
from typing import Any

import aiohttp
import pytest
from aioresponses import aioresponses

from tapelectric.api import (
    TapElectricAuthError,
    TapElectricClient,
    TapElectricError,
    TapElectricNotFoundError,
    TapElectricServerError,
)


@pytest.fixture
def base_url():
    return "https://api.tapelectric.app"


@pytest.fixture
def sk_key():
    return "sk_testkey"


# ── auth header selection ─────────────────────────────────────────────────

@pytest.mark.parametrize("scheme,header,value_prefix", [
    ("x-api-key",     "X-Api-Key",     "sk_"),
    ("x-tap-api-key", "X-Tap-Api-Key", "sk_"),
    ("bearer",        "Authorization", "Bearer sk_"),
])
def test_auth_header_variants(sk_key, scheme, header, value_prefix):
    async def _run():
        async with aiohttp.ClientSession() as s:
            c = TapElectricClient(sk_key, s, auth_scheme=scheme)
            h = c._auth_headers()
            assert header in h
            assert h[header].startswith(value_prefix)
    asyncio.run(_run())


def test_auth_header_basic_strips_sk_prefix():
    async def _run():
        async with aiohttp.ClientSession() as s:
            c = TapElectricClient("sk_secretvalue", s, auth_scheme="basic")
            h = c._auth_headers()
            assert h["Authorization"] == "Basic secretvalue"
    asyncio.run(_run())


def test_auth_header_unknown_scheme_raises():
    async def _run():
        async with aiohttp.ClientSession() as s:
            c = TapElectricClient("sk_x", s, auth_scheme="bogus")
            with pytest.raises(TapElectricError):
                c._auth_headers()
    asyncio.run(_run())


# ── list_chargers / get_charger ───────────────────────────────────────────

def test_list_chargers_returns_array(mock_aioresponse, load_fixture, base_url, sk_key):
    data = load_fixture("charger_list_minimal")
    mock_aioresponse.get(f"{base_url}/api/v1/chargers", payload=data)

    async def _run():
        async with aiohttp.ClientSession() as s:
            c = TapElectricClient(sk_key, s, base_url=base_url)
            chargers = await c.list_chargers()
        return chargers

    chargers = asyncio.run(_run())
    assert chargers == data
    assert chargers[0]["id"] == "EVB-MINIMAL"


def test_list_chargers_unwraps_items_wrapper(mock_aioresponse, base_url, sk_key):
    mock_aioresponse.get(
        f"{base_url}/api/v1/chargers",
        payload={"items": [{"id": "X"}], "total": 1},
    )

    async def _run():
        async with aiohttp.ClientSession() as s:
            c = TapElectricClient(sk_key, s, base_url=base_url)
            return await c.list_chargers()

    result = asyncio.run(_run())
    assert result == [{"id": "X"}]


def test_get_charger_injects_path_param(mock_aioresponse, base_url, sk_key):
    mock_aioresponse.get(
        f"{base_url}/api/v1/chargers/EVB-TEST",
        payload={"id": "EVB-TEST", "status": "AVAILABLE"},
    )

    async def _run():
        async with aiohttp.ClientSession() as s:
            c = TapElectricClient(sk_key, s, base_url=base_url)
            return await c.get_charger("EVB-TEST")

    assert asyncio.run(_run())["id"] == "EVB-TEST"


# ── charger-sessions ──────────────────────────────────────────────────────

def test_list_charger_sessions_passes_limit_offset(
    mock_aioresponse, base_url, sk_key,
):
    mock_aioresponse.get(
        f"{base_url}/api/v1/charger-sessions?limit=25&offset=0",
        payload=[{"id": "cs_a"}],
    )

    async def _run():
        async with aiohttp.ClientSession() as s:
            c = TapElectricClient(sk_key, s, base_url=base_url)
            return await c.list_charger_sessions(limit=25)

    assert asyncio.run(_run())[0]["id"] == "cs_a"


def test_list_charger_sessions_adds_updated_since(
    mock_aioresponse, base_url, sk_key,
):
    mock_aioresponse.get(
        f"{base_url}/api/v1/charger-sessions"
        f"?limit=100&offset=0&updatedSince=2026-01-01T00:00:00Z",
        payload=[],
    )

    async def _run():
        async with aiohttp.ClientSession() as s:
            c = TapElectricClient(sk_key, s, base_url=base_url)
            return await c.list_charger_sessions(
                updated_since="2026-01-01T00:00:00Z",
            )

    assert asyncio.run(_run()) == []


def test_session_meter_data(mock_aioresponse, base_url, sk_key):
    mock_aioresponse.get(
        f"{base_url}/api/v1/charger-sessions/cs_foo/session-meter-data"
        f"?limit=10&offset=0",
        payload=[{"id": "0", "value": 42}],
    )

    async def _run():
        async with aiohttp.ClientSession() as s:
            c = TapElectricClient(sk_key, s, base_url=base_url)
            return await c.session_meter_data("cs_foo", limit=10)

    result = asyncio.run(_run())
    assert result[0]["value"] == 42


# ── locations / tariffs / webhooks ────────────────────────────────────────

def test_list_locations(mock_aioresponse, base_url, sk_key):
    mock_aioresponse.get(
        f"{base_url}/api/v1/locations", payload=[{"id": "loc1"}],
    )

    async def _run():
        async with aiohttp.ClientSession() as s:
            c = TapElectricClient(sk_key, s, base_url=base_url)
            return await c.list_locations()

    assert asyncio.run(_run())[0]["id"] == "loc1"


def test_list_tariffs_bare_list(mock_aioresponse, base_url, sk_key):
    mock_aioresponse.get(
        f"{base_url}/api/v1/tariffs", payload=[{"id": "t1"}],
    )

    async def _run():
        async with aiohttp.ClientSession() as s:
            c = TapElectricClient(sk_key, s, base_url=base_url)
            return await c.list_tariffs()

    assert asyncio.run(_run()) == [{"id": "t1"}]


def test_list_tariffs_items_wrapper(mock_aioresponse, base_url, sk_key):
    mock_aioresponse.get(
        f"{base_url}/api/v1/tariffs",
        payload={"items": [{"id": "t2"}]},
    )

    async def _run():
        async with aiohttp.ClientSession() as s:
            c = TapElectricClient(sk_key, s, base_url=base_url)
            return await c.list_tariffs()

    assert asyncio.run(_run()) == [{"id": "t2"}]


# ── error mapping ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("status,exc", [
    (401, TapElectricAuthError),
    (403, TapElectricAuthError),
    (404, TapElectricNotFoundError),
    (500, TapElectricServerError),
    (502, TapElectricServerError),
    (400, TapElectricError),
])
def test_error_status_maps_to_exception(
    mock_aioresponse, base_url, sk_key, status, exc,
):
    mock_aioresponse.get(
        f"{base_url}/api/v1/chargers", status=status, body="boom",
    )

    async def _run():
        async with aiohttp.ClientSession() as s:
            c = TapElectricClient(sk_key, s, base_url=base_url)
            await c.list_chargers()

    with pytest.raises(exc):
        asyncio.run(_run())


def test_empty_body_returns_none(mock_aioresponse, base_url, sk_key):
    """_request() returns None on 2xx with empty body."""
    mock_aioresponse.get(
        f"{base_url}/api/v1/chargers", status=200, body="",
    )

    async def _run():
        async with aiohttp.ClientSession() as s:
            c = TapElectricClient(sk_key, s, base_url=base_url)
            return await c.list_chargers()

    # list_chargers coerces None/empty to [].
    assert asyncio.run(_run()) == []


# ── OCPP passthrough + reset ──────────────────────────────────────────────

def test_send_ocpp_message_posts_payload(
    mock_aioresponse, load_fixture, base_url, sk_key,
):
    ok = load_fixture("ocpp_message_send_ok")
    mock_aioresponse.post(
        f"{base_url}/api/v1/chargers/EVB-1/ocpp", payload=ok,
    )

    async def _run():
        async with aiohttp.ClientSession() as s:
            c = TapElectricClient(sk_key, s, base_url=base_url)
            return await c.send_ocpp_message(
                "EVB-1",
                {"request": {"Action": "Reset", "Data": {"type": "Soft"}}},
            )

    assert asyncio.run(_run())["status"] == "Accepted"


def test_set_charging_limit_composes_payload(
    mock_aioresponse, base_url, sk_key,
):
    mock_aioresponse.post(
        f"{base_url}/api/v1/chargers/EVB-1/ocpp",
        payload={"status": "Accepted"},
    )

    async def _run():
        async with aiohttp.ClientSession() as s:
            c = TapElectricClient(sk_key, s, base_url=base_url)
            return await c.set_charging_limit(
                "EVB-1", limit_amps=16.0, connector_id=1,
            )

    assert asyncio.run(_run())["status"] == "Accepted"


def test_pause_charging_uses_zero_amps_internally(
    mock_aioresponse, base_url, sk_key,
):
    mock_aioresponse.post(
        f"{base_url}/api/v1/chargers/EVB-1/ocpp",
        payload={"status": "Accepted"},
    )

    async def _run():
        async with aiohttp.ClientSession() as s:
            c = TapElectricClient(sk_key, s, base_url=base_url)
            return await c.pause_charging("EVB-1")

    assert asyncio.run(_run())["status"] == "Accepted"


def test_reset_charger_via_passthrough(
    mock_aioresponse, load_fixture, base_url, sk_key,
):
    ok = load_fixture("ocpp_message_send_ok")
    mock_aioresponse.post(
        f"{base_url}/api/v1/chargers/EVB-1/ocpp", payload=ok,
    )

    async def _run():
        async with aiohttp.ClientSession() as s:
            c = TapElectricClient(sk_key, s, base_url=base_url)
            return await c.reset_charger("EVB-1", reset_type="Hard")

    assert asyncio.run(_run())["status"] == "Accepted"


def test_ocpp_send_400_surfaces_as_error(
    mock_aioresponse, load_fixture, base_url, sk_key,
):
    bad = load_fixture("ocpp_message_send_400")
    mock_aioresponse.post(
        f"{base_url}/api/v1/chargers/EVB-1/ocpp", status=400, payload=bad,
    )

    async def _run():
        async with aiohttp.ClientSession() as s:
            c = TapElectricClient(sk_key, s, base_url=base_url)
            await c.send_ocpp_message("EVB-1", {"junk": True})

    with pytest.raises(TapElectricError):
        asyncio.run(_run())


# ── OCPP message history ──────────────────────────────────────────────────

def test_get_ocpp_messages_passes_optional_params(
    mock_aioresponse, base_url, sk_key,
):
    mock_aioresponse.get(
        f"{base_url}/api/v1/chargers/EVB-1/ocpp?limit=5&action=Heartbeat",
        payload=[{"action": "Heartbeat"}],
    )

    async def _run():
        async with aiohttp.ClientSession() as s:
            c = TapElectricClient(sk_key, s, base_url=base_url)
            return await c.get_ocpp_messages(
                "EVB-1", limit=5, action="Heartbeat",
            )

    result = asyncio.run(_run())
    assert result[0]["action"] == "Heartbeat"


def test_push_external_meter_data(mock_aioresponse, base_url, sk_key):
    mock_aioresponse.post(
        f"{base_url}/api/v1/meters/meter_1/data",
        payload={"accepted": True},
    )

    async def _run():
        async with aiohttp.ClientSession() as s:
            c = TapElectricClient(sk_key, s, base_url=base_url)
            return await c.push_external_meter_data(
                "meter_1", {"powerW": 1234},
            )

    assert asyncio.run(_run())["accepted"] is True


def test_reset_charger_direct(mock_aioresponse, base_url, sk_key):
    mock_aioresponse.post(
        f"{base_url}/api/v1/chargers/EVB-1/reset", status=200, body="",
    )

    async def _run():
        async with aiohttp.ClientSession() as s:
            c = TapElectricClient(sk_key, s, base_url=base_url)
            return await c.reset_charger_direct("EVB-1")

    assert asyncio.run(_run()) is None
