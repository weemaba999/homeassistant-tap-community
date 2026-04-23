"""Tap Electric management API client (advanced / authenticated side).

Sits on top of auth_firebase.TapFirebaseAuth. Uses the Firebase idToken
as a Bearer credential against Tap's management endpoints, which expose
richer, live data than the public /api/v1 surface — notably:
  GET /management/role-sessions     live energy_wh on active sessions
  GET /management/accounts          provides the macc_... account id
                                    needed on every other request

The public /api/v1 endpoints used by the rest of the integration stay
untouched; this module is a standalone addition. It does not import
from coordinator / sensor / __init__.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import aiohttp
from aiohttp import ClientTimeout

# Import from the sibling module. In HA this module is loaded as
# `custom_components.tapelectric.api_management`, so a relative import
# resolves. In the standalone test harness the module dir is on
# sys.path, so a bare import works. Support both.
try:
    from .auth_firebase import (
        AuthTokens,
        TapFirebaseAuth,
        TapFirebaseAuthError,
        TapFirebaseInvalidCredentials,
        TapFirebaseRefreshFailed,
    )
except ImportError:
    from auth_firebase import (  # type: ignore[no-redef]
        AuthTokens,
        TapFirebaseAuth,
        TapFirebaseAuthError,
        TapFirebaseInvalidCredentials,
        TapFirebaseRefreshFailed,
    )

_LOGGER = logging.getLogger(__name__)


# ── Constants ──────────────────────────────────────────────────────────

MGMT_BASE_URL = (
    "https://tap-electric-app-api.azurewebsites.net/api/1.0/management"
)

STATIC_HEADERS: dict[str, str] = {
    "X-Api-Key":       "5l^01Wmxs5ux",
    "X-App-Version":   "1.154.0",
    "X-Portal-Host":   "web.tapelectric.app",
    "Origin":          "https://web.tapelectric.app",
    "Referer":         "https://web.tapelectric.app/",
    "Content-Type":    "application/json",
}

DEFAULT_TIMEOUT = 15  # seconds
USER_AGENT = "HomeAssistant-tapelectric-community/0.0.1"

_TIMEOUT = ClientTimeout(total=DEFAULT_TIMEOUT)


# ── Exceptions ─────────────────────────────────────────────────────────

class TapManagementError(Exception):
    """Base for anything this module raises."""


class TapManagementAuthError(TapManagementError):
    """401 — token rejected / expired / refresh failure."""


class TapManagementNotFound(TapManagementError):
    """404 — resource does not exist for this account."""


class TapManagementRateLimited(TapManagementError):
    """429 — too many requests; back off."""


class TapManagementNetworkError(TapManagementError):
    """Connection / timeout / persistent 5xx."""


# ── Data ───────────────────────────────────────────────────────────────

@dataclass
class ManagementSession:
    """One entry from /management/role-sessions.

    Raw datetime fields (`start_date`, `end_date`, `created`) are kept
    as the wire strings; parsed `datetime` equivalents are exposed via
    `started_at` / `ended_at` / `created_at` properties so we never blow
    up on a malformed timestamp — the property just returns None.
    """

    session_id: str | None = None
    charger_id: str | None = None
    charger_name: str | None = None
    start_date: str | None = None
    end_date: str | None = None
    energy_wh: float | None = None
    currency: str | None = None
    token_visual_id: str | None = None
    fleet_id: str | None = None
    fleet_name: str | None = None
    fleet_driver_name: str | None = None
    fleet_driver_cost_ex_vat: float | None = None
    fleet_driver_cost_incl_vat: float | None = None
    fleet_driver_reimbursement_cost: float | None = None
    vat_percent: float | None = None
    location_name: str | None = None
    address: str | None = None
    city: str | None = None
    operator: str | None = None
    service_provider: str | None = None
    masked_card_uid: str | None = None
    created: str | None = None
    # Detail-only fields (populated by from_detail; stay None via from_dict
    # unless the list endpoint surfaces them too). `zip` would shadow the
    # builtin so we call it zip_code.
    evse_id: str | None = None
    zip_code: str | None = None
    country: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    transaction_id: int | None = None
    retail_tariff: dict | None = None
    # Any field the server surfaces that we don't know about stays here
    # for debugging without forcing a schema bump.
    raw: dict = field(default_factory=dict)

    # ── computed / derived ─────────────────────────────────────────
    @property
    def energy_kwh(self) -> float | None:
        if self.energy_wh is None:
            return None
        try:
            return float(self.energy_wh) / 1000
        except (TypeError, ValueError):
            return None

    @property
    def is_active(self) -> bool:
        return self.end_date is None

    @property
    def started_at(self) -> datetime | None:
        return _parse_iso(self.start_date)

    @property
    def ended_at(self) -> datetime | None:
        return _parse_iso(self.end_date)

    @property
    def created_at(self) -> datetime | None:
        return _parse_iso(self.created)

    @property
    def duration_seconds(self) -> int | None:
        if self.is_active:
            return None
        s, e = self.started_at, self.ended_at
        if s is None or e is None:
            return None
        return max(0, int((e - s).total_seconds()))

    # ── construction ───────────────────────────────────────────────
    @classmethod
    def from_dict(cls, data: dict) -> "ManagementSession":
        """Parse a /role-sessions list entry — flat snake_case shape."""
        if not isinstance(data, dict):
            raise TapManagementError(f"Expected object, got {type(data).__name__}")
        return cls(
            session_id=                    data.get("session_id"),
            charger_id=                    data.get("charger_id"),
            charger_name=                  data.get("charger_name"),
            start_date=                    data.get("start_date"),
            end_date=                      data.get("end_date"),
            energy_wh=                     data.get("energy_wh"),
            currency=                      data.get("currency"),
            token_visual_id=               data.get("token_visual_id"),
            fleet_id=                      data.get("fleet_id"),
            fleet_name=                    data.get("fleet_name"),
            fleet_driver_name=             data.get("fleet_driver_name"),
            fleet_driver_cost_ex_vat=      data.get("fleet_driver_cost_ex_vat"),
            fleet_driver_cost_incl_vat=    data.get("fleet_driver_cost_incl_vat"),
            fleet_driver_reimbursement_cost=data.get("fleet_driver_reimbursement_cost"),
            vat_percent=                   data.get("vat_percent"),
            location_name=                 data.get("location_name"),
            address=                       data.get("address"),
            city=                          data.get("city"),
            operator=                      data.get("operator"),
            service_provider=              data.get("service_provider"),
            masked_card_uid=               data.get("masked_card_uid"),
            created=                       data.get("created"),
            # Best-effort: list response isn't known to carry these today,
            # but if the backend ever flattens them in we'll pick them up.
            evse_id=                       data.get("evse_id"),
            zip_code=                      data.get("zip") or data.get("zip_code"),
            country=                       data.get("country"),
            latitude=                      data.get("latitude"),
            longitude=                     data.get("longitude"),
            transaction_id=                data.get("transaction_id"),
            raw=data,
        )

    @classmethod
    def from_detail(cls, data: dict) -> "ManagementSession":
        """Parse a /sessions/{id} detail response — nested shape.

        The detail endpoint groups fields into `location_details`,
        `cpo_details`, and `fleet_details` objects. A number of flat
        fields available from the list (fleet_driver_name, vat_percent,
        masked_card_uid, operator, service_provider, token_visual_id,
        created, cost breakdowns) are not echoed here — they stay None.
        That asymmetry is expected; callers who need them should merge
        a list entry with the detail.
        """
        if not isinstance(data, dict):
            raise TapManagementError(f"Expected object, got {type(data).__name__}")
        loc = data.get("location_details") or {}
        cpo = data.get("cpo_details") or {}
        fleet = data.get("fleet_details") or {}
        reimb = (fleet.get("reimbursement") or {}) if isinstance(fleet, dict) else {}

        return cls(
            session_id=                    data.get("id"),
            start_date=                    data.get("start_date"),
            end_date=                      data.get("end_date"),
            energy_wh=                     data.get("energy_wh"),
            charger_id=                    loc.get("charger_id"),
            charger_name=                  loc.get("charger_name"),
            location_name=                 loc.get("location_name"),
            address=                       loc.get("address"),
            city=                          loc.get("city"),
            zip_code=                      loc.get("zip"),
            country=                       loc.get("country"),
            latitude=                      loc.get("latitude"),
            longitude=                     loc.get("longitude"),
            evse_id=                       loc.get("evse_id"),
            currency=                      cpo.get("currency"),
            transaction_id=                cpo.get("transaction_id"),
            fleet_id=                      fleet.get("fleet_id"),
            fleet_name=                    fleet.get("fleet_name"),
            fleet_driver_reimbursement_cost=reimb.get("amount") if isinstance(reimb, dict) else None,
            retail_tariff=                 fleet.get("retail_tariff"),
            raw=data,
        )


def _parse_iso(ts: str | None) -> datetime | None:
    if not isinstance(ts, str) or not ts:
        return None
    try:
        # Z-suffix → +00:00 for fromisoformat on older Pythons.
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None


# ── Client ─────────────────────────────────────────────────────────────

class TapManagementClient:
    """Async client for the Tap Electric management API.

    The caller owns the aiohttp session and the TapFirebaseAuth
    instance. Tokens are refreshed in-place — the client writes the
    returned AuthTokens back to `self.tokens` after every ensure_valid
    so subsequent calls benefit from the single refresh.
    """

    def __init__(
        self,
        session: aiohttp.ClientSession,
        auth: TapFirebaseAuth,
        tokens: AuthTokens,
        account_id: str | None = None,
    ) -> None:
        self._session = session
        self.auth = auth
        self.tokens = tokens
        self.account_id = account_id

    # ── public surface ────────────────────────────────────────────────

    async def discover_account_id(self) -> str:
        """Populate (and return) the macc_... account id for this user."""
        data = await self._request(
            "GET", "/accounts",
            params={"take": 5, "offset": 0, "includeConnectedAccounts": "true"},
            allow_missing_account_id=True,   # we're fetching it right now
        )
        items = data if isinstance(data, list) else (data or {}).get("items") or []
        if not items:
            raise TapManagementError("No accounts returned for this user")
        first = items[0]
        if not isinstance(first, dict) or not first.get("id"):
            raise TapManagementError(
                f"Account list entry missing 'id': {first!r}"
            )
        self.account_id = first["id"]
        _LOGGER.debug("Discovered X-Account-Id=%s", self.account_id)
        return self.account_id

    async def list_role_sessions(
        self, role: str = "cpo", offset: int = 0, take: int = 50,
    ) -> list[ManagementSession]:
        data = await self._request(
            "GET", "/role-sessions",
            params={"role": role, "offset": offset, "take": take},
        )
        if not isinstance(data, list):
            # Some endpoints wrap the list; tolerate both shapes.
            data = (data or {}).get("items") or []
        return [ManagementSession.from_dict(d) for d in data]

    async def get_session(self, session_id: str) -> ManagementSession:
        data = await self._request("GET", f"/sessions/{session_id}")
        if not isinstance(data, dict):
            raise TapManagementError(
                f"Expected object from /sessions/{session_id}, got {type(data).__name__}"
            )
        return ManagementSession.from_detail(data)

    async def close(self) -> None:
        """Release any client-owned resources (none currently)."""
        return None

    # ── plumbing ──────────────────────────────────────────────────────

    async def _ensure_tokens(self) -> None:
        """Refresh the id_token if it's close to expiry. Wrap any auth
        failure into TapManagementAuthError so callers see a consistent
        error surface."""
        try:
            self.tokens = await self.auth.ensure_valid(self.tokens)
        except (TapFirebaseRefreshFailed, TapFirebaseInvalidCredentials) as err:
            raise TapManagementAuthError(
                f"Re-authentication required: {err}"
            ) from err
        except TapFirebaseAuthError as err:
            raise TapManagementAuthError(str(err)) from err

    def _build_headers(self, *, allow_missing_account_id: bool) -> dict[str, str]:
        headers = {
            **STATIC_HEADERS,
            "User-Agent":    USER_AGENT,
            "Authorization": f"Bearer {self.tokens.id_token}",
        }
        if self.account_id:
            headers["X-Account-Id"] = self.account_id
        elif not allow_missing_account_id:
            _LOGGER.warning(
                "X-Account-Id not yet discovered — call discover_account_id() "
                "before other endpoints, or expect a 4xx."
            )
        return headers

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict | None = None,
        allow_missing_account_id: bool = False,
    ) -> Any:
        await self._ensure_tokens()
        url = f"{MGMT_BASE_URL}{path}"
        headers = self._build_headers(
            allow_missing_account_id=allow_missing_account_id,
        )

        attempt = 0
        while True:
            attempt += 1
            try:
                async with self._session.request(
                    method, url,
                    params=params, headers=headers, timeout=_TIMEOUT,
                ) as resp:
                    status = resp.status
                    body_bytes = await resp.read()
                    if 200 <= status < 300:
                        _LOGGER.debug(
                            "%s %s -> %d (%d bytes)",
                            method, path, status, len(body_bytes),
                        )
                        if not body_bytes:
                            return None
                        return await _decode_json(resp, body_bytes)

                    if status == 401:
                        _LOGGER.warning(
                            "%s %s -> 401; id_token likely stale despite "
                            "leeway. Forcing a refresh on next call.",
                            method, path,
                        )
                        raise TapManagementAuthError(
                            f"Unauthorized ({status}) on {method} {path}"
                        )
                    if status == 404:
                        raise TapManagementNotFound(
                            f"Not found: {method} {path}"
                        )
                    if status == 429:
                        raise TapManagementRateLimited(
                            f"Rate limited on {method} {path}"
                        )
                    if 500 <= status < 600:
                        if attempt == 1:
                            _LOGGER.debug(
                                "%s %s -> %d on attempt 1, retrying once",
                                method, path, status,
                            )
                            continue
                        _LOGGER.error(
                            "%s %s failed with %d after retry",
                            method, path, status,
                        )
                        raise TapManagementNetworkError(
                            f"Server error {status} on {method} {path}"
                        )
                    # Other 4xx — surface with body snippet for diagnosis.
                    snippet = body_bytes[:400].decode("utf-8", errors="replace")
                    raise TapManagementError(
                        f"HTTP {status} on {method} {path}: {snippet}"
                    )
            except (
                aiohttp.ServerDisconnectedError,
                aiohttp.ClientConnectorError,
                asyncio.TimeoutError,
            ) as err:
                if attempt == 1:
                    _LOGGER.debug(
                        "%s %s network error on attempt 1, retrying: %s",
                        method, path, err,
                    )
                    continue
                _LOGGER.error(
                    "%s %s failed after retry: %s", method, path, err,
                )
                raise TapManagementNetworkError(str(err)) from err


async def _decode_json(resp: aiohttp.ClientResponse, body: bytes) -> Any:
    """Decode a JSON body tolerant of missing / odd content-type headers."""
    import json
    try:
        return json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as err:
        raise TapManagementError(
            f"Could not decode JSON from {resp.url}: {err}"
        ) from err
