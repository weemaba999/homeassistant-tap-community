"""Firebase Authentication client for Tap Electric's management API.

Standalone module — does NOT import from the rest of the integration
and has no Home Assistant dependency. Can be exercised from any
Python + aiohttp environment.

Background: Tap's mobile / web apps authenticate against Google's
Identity Toolkit using a hard-coded Firebase web API key. The key has
an HTTP-referrer restriction, so every request must carry a
`Referer`/`Origin` of web.tapelectric.app to pass Google's
CheckOrigin filter. We replicate the browser's request exactly.

Two endpoints:
  * POST accounts:signInWithPassword   — exchange email+password for an
                                         id/refresh token pair.
  * POST securetoken.googleapis.com    — exchange a refresh_token for a
                                         fresh id_token when the old
                                         one is about to expire.

Tokens are short-lived JWTs (~3600 s). We refresh proactively with a
5-minute leeway so in-flight requests don't race against expiry.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import aiohttp
from aiohttp import ClientTimeout

_LOGGER = logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────────────────────

FIREBASE_API_KEY = "AIzaSyA-f3GQFOfuJxNOrLixTxMFqcPSundyNe8"
SIGN_IN_URL = "https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword"
REFRESH_URL = "https://securetoken.googleapis.com/v1/token"

# Google's CheckOrigin rejects requests without these headers because
# the API key is referrer-restricted to web.tapelectric.app.
STANDARD_HEADERS: dict[str, str] = {
    "Content-Type": "application/json",
    "Referer": "https://web.tapelectric.app/",
    "Origin": "https://web.tapelectric.app",
}

REFRESH_LEEWAY_SECONDS = 300  # refresh 5 minutes before expiry

_REQUEST_TIMEOUT = ClientTimeout(total=10)


# ── Data ───────────────────────────────────────────────────────────────

@dataclass
class AuthTokens:
    id_token: str
    refresh_token: str
    expires_at: datetime           # UTC
    user_id: str
    email: str | None = None
    display_name: str | None = None

    def expires_in(self, now: datetime | None = None) -> float:
        """Seconds remaining until expiry (negative when already expired)."""
        now = now or datetime.now(timezone.utc)
        return (self.expires_at - now).total_seconds()

    def needs_refresh(self, now: datetime | None = None) -> bool:
        return self.expires_in(now) <= REFRESH_LEEWAY_SECONDS


# ── Exceptions ─────────────────────────────────────────────────────────

class TapFirebaseAuthError(Exception):
    """Base exception for anything this module raises."""


class TapFirebaseInvalidCredentials(TapFirebaseAuthError):
    """Email / password combination was rejected by Firebase."""


class TapFirebaseNetworkError(TapFirebaseAuthError):
    """Connection failed, timed out, or otherwise couldn't reach Google."""


class TapFirebaseRefreshFailed(TapFirebaseAuthError):
    """Refresh token was invalid, revoked, or expired."""


# Firebase identity-toolkit error codes that mean "bad credentials" —
# everything else at 4xx is a generic TapFirebaseAuthError.
_INVALID_CREDENTIAL_CODES = frozenset({
    "INVALID_PASSWORD",
    "EMAIL_NOT_FOUND",
    "INVALID_LOGIN_CREDENTIALS",
    "USER_DISABLED",
    "MISSING_PASSWORD",
    "INVALID_EMAIL",
})

_REFRESH_FAILURE_CODES = frozenset({
    "TOKEN_EXPIRED",
    "USER_DISABLED",
    "USER_NOT_FOUND",
    "INVALID_REFRESH_TOKEN",
    "MISSING_REFRESH_TOKEN",
    "INVALID_GRANT_TYPE",
})


# ── Client ─────────────────────────────────────────────────────────────

class TapFirebaseAuth:
    """Async Firebase-Authentication client scoped to Tap's web app.

    The caller owns the aiohttp.ClientSession lifecycle — matches the
    pattern used by the existing TapElectricClient in this package.
    """

    def __init__(
        self,
        session: aiohttp.ClientSession,
        api_key: str = FIREBASE_API_KEY,
    ) -> None:
        self._session = session
        self._api_key = api_key

    # ── public API ────────────────────────────────────────────────────

    async def sign_in(self, email: str, password: str) -> AuthTokens:
        """Exchange email+password for a fresh AuthTokens object."""
        _LOGGER.debug("Firebase sign-in for %s", email)
        payload = {
            "email": email,
            "password": password,
            "returnSecureToken": True,
        }
        data = await self._post_json(
            SIGN_IN_URL, json=payload,
            on_4xx_error=self._classify_sign_in_error,
        )
        tokens = _parse_sign_in_response(data)
        _LOGGER.debug(
            "Firebase sign-in success — user_id=%s email=%s expires_at=%s",
            tokens.user_id, tokens.email, tokens.expires_at.isoformat(),
        )
        return tokens

    async def refresh(self, refresh_token: str) -> AuthTokens:
        """Trade a refresh_token for a fresh id_token + (rotated) refresh_token.

        The refresh response doesn't echo email / display_name, so those
        fields of the returned AuthTokens will be None. `ensure_valid`
        carries them over from the prior tokens.
        """
        _LOGGER.debug("Firebase token refresh")
        body = {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
        }
        headers = {
            # Refresh endpoint uses form-urlencoded, not JSON.
            **{k: v for k, v in STANDARD_HEADERS.items()
               if k != "Content-Type"},
            "Content-Type": "application/x-www-form-urlencoded",
        }
        data = await self._post_form(
            REFRESH_URL, data=body, headers=headers,
            on_4xx_error=self._classify_refresh_error,
        )
        tokens = _parse_refresh_response(data)
        _LOGGER.debug(
            "Firebase refresh success — user_id=%s expires_at=%s",
            tokens.user_id, tokens.expires_at.isoformat(),
        )
        return tokens

    async def ensure_valid(self, tokens: AuthTokens) -> AuthTokens:
        """Return a non-expiring-soon AuthTokens, refreshing if needed."""
        if not tokens.needs_refresh():
            return tokens
        refreshed = await self.refresh(tokens.refresh_token)
        # Refresh endpoint doesn't echo the user profile — carry it over
        # so downstream consumers don't suddenly see email=None after a
        # background refresh.
        return AuthTokens(
            id_token=refreshed.id_token,
            refresh_token=refreshed.refresh_token,
            expires_at=refreshed.expires_at,
            user_id=refreshed.user_id or tokens.user_id,
            email=refreshed.email or tokens.email,
            display_name=refreshed.display_name or tokens.display_name,
        )

    # ── HTTP helpers ──────────────────────────────────────────────────

    async def _post_json(
        self,
        url: str,
        *,
        json: dict,
        on_4xx_error,
        headers: dict[str, str] | None = None,
    ):
        return await self._with_retry(
            lambda: self._session.post(
                url,
                params={"key": self._api_key},
                json=json,
                headers=headers or STANDARD_HEADERS,
                timeout=_REQUEST_TIMEOUT,
            ),
            on_4xx_error=on_4xx_error,
        )

    async def _post_form(
        self,
        url: str,
        *,
        data: dict,
        on_4xx_error,
        headers: dict[str, str] | None = None,
    ):
        return await self._with_retry(
            lambda: self._session.post(
                url,
                params={"key": self._api_key},
                data=data,
                headers=headers or STANDARD_HEADERS,
                timeout=_REQUEST_TIMEOUT,
            ),
            on_4xx_error=on_4xx_error,
        )

    async def _with_retry(self, request_factory, *, on_4xx_error):
        """Execute `request_factory()` with one retry on network errors
        and zero retries on 4xx (that's a deterministic server verdict)."""
        attempt = 0
        while True:
            attempt += 1
            try:
                async with request_factory() as resp:
                    body = await resp.json(content_type=None)
                    if resp.status >= 500:
                        # Transient server error. Retry once, fail-fast after.
                        if attempt == 1:
                            _LOGGER.debug(
                                "Firebase 5xx on attempt %d — retrying once",
                                attempt,
                            )
                            continue
                        raise TapFirebaseNetworkError(
                            f"Firebase server error {resp.status}"
                        )
                    if 400 <= resp.status < 500:
                        err = on_4xx_error(body, resp.status)
                        raise err
                    return body
            except (
                aiohttp.ClientConnectorError,
                aiohttp.ServerDisconnectedError,
                asyncio.TimeoutError,
            ) as exc:
                if attempt == 1:
                    _LOGGER.debug(
                        "Firebase network error on attempt 1 — retrying: %s",
                        exc,
                    )
                    continue
                _LOGGER.warning("Firebase unreachable after retry: %s", exc)
                raise TapFirebaseNetworkError(str(exc)) from exc

    # ── 4xx classifiers ───────────────────────────────────────────────

    @staticmethod
    def _classify_sign_in_error(body, status: int) -> TapFirebaseAuthError:
        code = _extract_error_code(body)
        if code in _INVALID_CREDENTIAL_CODES:
            _LOGGER.warning("Firebase sign-in rejected: %s", code)
            return TapFirebaseInvalidCredentials(code)
        _LOGGER.warning("Firebase sign-in failed (%d): %s", status, code)
        return TapFirebaseAuthError(f"HTTP {status}: {code or body!r}")

    @staticmethod
    def _classify_refresh_error(body, status: int) -> TapFirebaseAuthError:
        code = _extract_error_code(body)
        if code in _REFRESH_FAILURE_CODES or code in _INVALID_CREDENTIAL_CODES:
            _LOGGER.warning("Firebase refresh rejected: %s", code)
            return TapFirebaseRefreshFailed(code)
        _LOGGER.warning("Firebase refresh failed (%d): %s", status, code)
        return TapFirebaseAuthError(f"HTTP {status}: {code or body!r}")


# ── Parsing / extraction ───────────────────────────────────────────────

def _extract_error_code(body) -> str | None:
    if not isinstance(body, dict):
        return None
    err = body.get("error")
    if isinstance(err, dict):
        return err.get("message") or err.get("status")
    if isinstance(err, str):
        return err
    return None


def _parse_sign_in_response(body: dict) -> AuthTokens:
    try:
        expires_in = int(body.get("expiresIn") or 0)
        return AuthTokens(
            id_token=body["idToken"],
            refresh_token=body["refreshToken"],
            expires_at=datetime.now(timezone.utc) + timedelta(seconds=expires_in),
            user_id=body["localId"],
            email=body.get("email"),
            display_name=body.get("displayName"),
        )
    except KeyError as err:
        raise TapFirebaseAuthError(
            f"Sign-in response missing field: {err}"
        ) from err


def _parse_refresh_response(body: dict) -> AuthTokens:
    # Refresh endpoint uses snake_case, not camelCase. The two endpoints
    # documented at identitytoolkit vs securetoken aren't consistent.
    try:
        expires_in = int(body.get("expires_in") or 0)
        return AuthTokens(
            id_token=body["id_token"],
            refresh_token=body["refresh_token"],
            expires_at=datetime.now(timezone.utc) + timedelta(seconds=expires_in),
            user_id=body.get("user_id") or "",
            email=None,
            display_name=None,
        )
    except KeyError as err:
        raise TapFirebaseRefreshFailed(
            f"Refresh response missing field: {err}"
        ) from err
