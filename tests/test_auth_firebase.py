"""Tests for auth_firebase.TapFirebaseAuth.

Exercises sign-in, refresh, ensure_valid (fresh + expired), error
classification, and the "refresh preserves email/uid" guarantee.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import aiohttp
import pytest

from tapelectric.auth_firebase import (
    AuthTokens,
    REFRESH_URL,
    SIGN_IN_URL,
    TapFirebaseAuth,
    TapFirebaseAuthError,
    TapFirebaseInvalidCredentials,
    TapFirebaseNetworkError,
    TapFirebaseRefreshFailed,
)


def _expiring_in(seconds: float) -> datetime:
    return datetime.now(timezone.utc) + timedelta(seconds=seconds)


def _run(coro):
    return asyncio.run(coro)


# ── AuthTokens arithmetic ──────────────────────────────────────────────────

def test_expires_in_positive_for_future_token():
    tokens = AuthTokens(
        id_token="a", refresh_token="b",
        expires_at=_expiring_in(3600),
        user_id="u",
    )
    assert tokens.expires_in() > 3500


def test_needs_refresh_within_leeway():
    tokens = AuthTokens(
        id_token="a", refresh_token="b",
        expires_at=_expiring_in(60),        # inside 5-min leeway
        user_id="u",
    )
    assert tokens.needs_refresh() is True


def test_needs_refresh_false_when_fresh():
    tokens = AuthTokens(
        id_token="a", refresh_token="b",
        expires_at=_expiring_in(1800),
        user_id="u",
    )
    assert tokens.needs_refresh() is False


# ── sign_in happy path ─────────────────────────────────────────────────────

def test_sign_in_returns_tokens(mock_aioresponse):
    mock_aioresponse.post(
        f"{SIGN_IN_URL}?key=test-key",
        payload={
            "idToken": "id_ABC",
            "refreshToken": "rt_xyz",
            "expiresIn": "3600",
            "localId": "uid_123",
            "email": "user@x.com",
            "displayName": "User X",
        },
    )

    async def _do():
        async with aiohttp.ClientSession() as s:
            a = TapFirebaseAuth(s, api_key="test-key")
            return await a.sign_in("user@x.com", "pw")

    tokens = _run(_do())
    assert tokens.id_token == "id_ABC"
    assert tokens.refresh_token == "rt_xyz"
    assert tokens.user_id == "uid_123"
    assert tokens.email == "user@x.com"


@pytest.mark.parametrize("code,exc", [
    ("INVALID_PASSWORD",        TapFirebaseInvalidCredentials),
    ("EMAIL_NOT_FOUND",         TapFirebaseInvalidCredentials),
    ("INVALID_LOGIN_CREDENTIALS", TapFirebaseInvalidCredentials),
    ("USER_DISABLED",           TapFirebaseInvalidCredentials),
    ("MISSING_PASSWORD",        TapFirebaseInvalidCredentials),
    ("INVALID_EMAIL",           TapFirebaseInvalidCredentials),
    ("SOMETHING_ELSE",          TapFirebaseAuthError),
])
def test_sign_in_error_classification(mock_aioresponse, code, exc):
    mock_aioresponse.post(
        f"{SIGN_IN_URL}?key=test-key",
        status=400,
        payload={"error": {"message": code}},
    )

    async def _do():
        async with aiohttp.ClientSession() as s:
            a = TapFirebaseAuth(s, api_key="test-key")
            await a.sign_in("x", "y")

    with pytest.raises(exc):
        _run(_do())


def test_sign_in_retries_on_5xx_then_succeeds(mock_aioresponse):
    url = f"{SIGN_IN_URL}?key=k"
    mock_aioresponse.post(url, status=503, body="")
    mock_aioresponse.post(
        url,
        payload={
            "idToken": "id", "refreshToken": "rt", "expiresIn": "3600",
            "localId": "uid", "email": "e@x.com",
        },
    )

    async def _do():
        async with aiohttp.ClientSession() as s:
            a = TapFirebaseAuth(s, api_key="k")
            return await a.sign_in("e@x.com", "pw")

    tokens = _run(_do())
    assert tokens.user_id == "uid"


def test_sign_in_5xx_twice_raises_network_error(mock_aioresponse):
    url = f"{SIGN_IN_URL}?key=k"
    mock_aioresponse.post(url, status=500, body="")
    mock_aioresponse.post(url, status=500, body="")

    async def _do():
        async with aiohttp.ClientSession() as s:
            a = TapFirebaseAuth(s, api_key="k")
            await a.sign_in("e", "p")

    with pytest.raises(TapFirebaseNetworkError):
        _run(_do())


# ── refresh happy path + errors ───────────────────────────────────────────

def test_refresh_returns_rotated_tokens(mock_aioresponse):
    mock_aioresponse.post(
        f"{REFRESH_URL}?key=k",
        payload={
            "id_token":     "id_new",
            "refresh_token": "rt_new",
            "expires_in":   "3600",
            "user_id":      "uid_new",
        },
    )

    async def _do():
        async with aiohttp.ClientSession() as s:
            a = TapFirebaseAuth(s, api_key="k")
            return await a.refresh("rt_old")

    tokens = _run(_do())
    assert tokens.id_token == "id_new"
    assert tokens.refresh_token == "rt_new"


@pytest.mark.parametrize("code", [
    "TOKEN_EXPIRED", "USER_DISABLED", "USER_NOT_FOUND",
    "INVALID_REFRESH_TOKEN", "MISSING_REFRESH_TOKEN", "INVALID_GRANT_TYPE",
])
def test_refresh_error_codes_raise_refresh_failed(mock_aioresponse, code):
    mock_aioresponse.post(
        f"{REFRESH_URL}?key=k",
        status=400,
        payload={"error": {"message": code}},
    )

    async def _do():
        async with aiohttp.ClientSession() as s:
            a = TapFirebaseAuth(s, api_key="k")
            await a.refresh("rt")

    with pytest.raises(TapFirebaseRefreshFailed):
        _run(_do())


# ── ensure_valid ──────────────────────────────────────────────────────────

def test_ensure_valid_skips_refresh_when_fresh(mock_aioresponse):
    """With a fresh token ensure_valid should NOT hit the refresh URL."""
    fresh = AuthTokens(
        id_token="id", refresh_token="rt",
        expires_at=_expiring_in(1800),
        user_id="u", email="e@x.com",
    )

    async def _do():
        async with aiohttp.ClientSession() as s:
            a = TapFirebaseAuth(s, api_key="k")
            return await a.ensure_valid(fresh)

    # No mock registered for REFRESH_URL; if ensure_valid hits it the
    # test fails via aiohttp ConnectionError.
    out = _run(_do())
    assert out.id_token == "id"


def test_ensure_valid_refreshes_and_preserves_email_uid(mock_aioresponse):
    mock_aioresponse.post(
        f"{REFRESH_URL}?key=k",
        payload={
            "id_token":     "id_new",
            "refresh_token": "rt_new",
            "expires_in":   "3600",
            # refresh response intentionally omits email — ensure_valid
            # should carry it over.
        },
    )
    stale = AuthTokens(
        id_token="id_old", refresh_token="rt_old",
        expires_at=_expiring_in(-1),   # already expired
        user_id="uid_orig", email="driver@x.com", display_name="Driver",
    )

    async def _do():
        async with aiohttp.ClientSession() as s:
            a = TapFirebaseAuth(s, api_key="k")
            return await a.ensure_valid(stale)

    new = _run(_do())
    assert new.id_token == "id_new"
    assert new.refresh_token == "rt_new"
    assert new.email == "driver@x.com"       # carried over
    assert new.user_id == "uid_orig"          # carried over
    assert new.display_name == "Driver"       # carried over
