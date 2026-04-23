"""Async HTTP client for the Tap Electric API.

All read endpoints verified 2026-04-22 against the Reference docs.
Write endpoints (OCPP passthrough + Reset) are the final unverified
surface — they exist and 200 on the test-request button, but real
behaviour on a physical charger is untested from this client.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

import aiohttp
from aiohttp import ClientResponseError, ClientTimeout

from .const import (
    API_VERSION,
    AUTH_HEADER_APIKEY,
    AUTH_HEADER_BEARER,
    AUTH_HEADER_TAP,
    AUTH_SCHEME,
    CHARGING_LIMIT_DEFAULT_A,
    CHARGING_LIMIT_OFF_A,
    DEFAULT_BASE_URL,
    METER_DATA_LIMIT,
    PATH_CHARGER_DETAIL,
    PATH_CHARGER_OCPP_GET,
    PATH_CHARGER_OCPP_SEND,
    PATH_CHARGER_RESET,
    PATH_CHARGER_SESSIONS,
    PATH_CHARGERS_LIST,
    PATH_LOCATIONS_LIST,
    PATH_METER_DATA_PUSH,
    PATH_SESSION_METER_DATA,
    PATH_TARIFFS,
)
from .ocpp import reset as ocpp_reset
from .ocpp import set_charging_profile

_LOGGER = logging.getLogger(__name__)
_TIMEOUT = ClientTimeout(total=20)


class TapElectricError(Exception):
    """Base exception for Tap Electric API."""


class TapElectricAuthError(TapElectricError):
    """401 / 403 — API key missing or invalid."""


class TapElectricNotFoundError(TapElectricError):
    """404 — resource not found."""


class TapElectricServerError(TapElectricError):
    """5xx — server side problem, retry may help."""


class TapElectricClient:
    """Thin async wrapper around the Tap Electric REST API."""

    def __init__(
        self,
        api_key: str,
        session: aiohttp.ClientSession,
        base_url: str = DEFAULT_BASE_URL,
        auth_scheme: str = AUTH_SCHEME,
    ) -> None:
        self._api_key = api_key
        self._session = session
        self._base_url = base_url.rstrip("/")
        self._auth_scheme = auth_scheme

    # ── internal helpers ────────────────────────────────────────────────

    def _url(self, path: str, **fmt: Any) -> str:
        path = path.format(**fmt) if fmt else path
        return f"{self._base_url}/api/{API_VERSION}{path}"

    def _auth_headers(self) -> dict[str, str]:
        if self._auth_scheme == "x-api-key":
            return {AUTH_HEADER_APIKEY: self._api_key}
        if self._auth_scheme == "bearer":
            return {AUTH_HEADER_BEARER: f"Bearer {self._api_key}"}
        if self._auth_scheme == "x-tap-api-key":
            return {AUTH_HEADER_TAP: self._api_key}
        if self._auth_scheme == "basic":
            return {AUTH_HEADER_BEARER: f"Basic {self._api_key.removeprefix('sk_')}"}
        raise TapElectricError(f"Unknown auth_scheme: {self._auth_scheme}")

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict | None = None,
        params: dict | None = None,
        **fmt: Any,
    ) -> Any:
        url = self._url(path, **fmt)
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            **self._auth_headers(),
        }
        _LOGGER.debug("%s %s params=%s body=%s", method, url, params, json)
        try:
            async with self._session.request(
                method, url,
                headers=headers, json=json, params=params, timeout=_TIMEOUT,
            ) as resp:
                text = await resp.text()
                if resp.status in (401, 403):
                    raise TapElectricAuthError(
                        f"Auth failed ({resp.status}): {text[:200]}"
                    )
                if resp.status == 404:
                    raise TapElectricNotFoundError(f"Not found: {url}")
                if resp.status >= 500:
                    raise TapElectricServerError(
                        f"Server error {resp.status}: {text[:200]}"
                    )
                if resp.status >= 400:
                    raise TapElectricError(
                        f"HTTP {resp.status} on {method} {url}: {text[:200]}"
                    )
                if not text:
                    return None
                return await resp.json(content_type=None)
        except asyncio.TimeoutError as err:
            raise TapElectricServerError(f"Timeout calling {url}") from err
        except ClientResponseError as err:
            raise TapElectricError(f"Transport error: {err}") from err

    # ── Chargers (verified) ─────────────────────────────────────────────

    async def list_chargers(self) -> list[dict]:
        data = await self._request("GET", PATH_CHARGERS_LIST)
        if isinstance(data, dict) and "items" in data:
            return data["items"]
        return data or []

    async def get_charger(self, charger_id: str) -> dict:
        return await self._request(
            "GET", PATH_CHARGER_DETAIL, charger_id=charger_id
        )

    # ── Charger sessions (verified, replaces /sessions) ─────────────────

    async def list_charger_sessions(
        self,
        *,
        limit: int = 100,
        offset: int = 0,
        updated_since: str | None = None,   # ISO8601
    ) -> list[dict]:
        """All charger-sessions visible to this API key.

        Schema per item (verified):
          {id, location:{id}, charger:{id, connectorId},
           wh, startedAt, endedAt|null, updatedAt}
        """
        params: dict[str, Any] = {"limit": limit, "offset": offset}
        if updated_since:
            params["updatedSince"] = updated_since
        data = await self._request("GET", PATH_CHARGER_SESSIONS, params=params)
        return data or []

    async def session_meter_data(
        self,
        session_id: str,
        *,
        limit: int = METER_DATA_LIMIT,
        offset: int = 0,
        from_date: str | None = None,
        to_date: str | None = None,
    ) -> list[dict]:
        """OCPP MeterValues for one session.

        Schema per item (verified):
          {id, ocppMessageId|null, value, unit, measurand, chargerId,
           phase|null, transactionId, measuredAt}
        """
        params: dict[str, Any] = {"limit": limit, "offset": offset}
        if from_date:
            params["from"] = from_date
        if to_date:
            params["to"] = to_date
        data = await self._request(
            "GET", PATH_SESSION_METER_DATA,
            params=params, session_id=session_id,
        )
        return data or []

    # ── Locations (verified) ────────────────────────────────────────────

    async def list_locations(self) -> list[dict]:
        data = await self._request("GET", PATH_LOCATIONS_LIST)
        return data or []

    # ── Tariffs (scope-variable) ────────────────────────────────────────

    async def list_tariffs(self) -> list[dict]:
        """GET /api/v1/tariffs — tariffs visible to this API key scope.

        Behaviour observed in the field: some keys get a flat list, others
        need a tariffId query param and return 4xx without one. The caller
        is expected to tolerate either. We normalise the response shape
        (dict with `items` vs. bare list) here.
        """
        data = await self._request("GET", PATH_TARIFFS)
        if isinstance(data, dict) and "items" in data:
            return data["items"]
        return data or []

    # ── OCPP passthrough (write — needs live test) ──────────────────────

    async def send_ocpp_message(
        self, charger_id: str, payload: dict
    ) -> Any:
        """Raw OCPP message send. Use set_charging_limit() or reset_charger()
        for the common cases."""
        return await self._request(
            "POST", PATH_CHARGER_OCPP_SEND,
            json=payload, charger_id=charger_id,
        )

    async def get_ocpp_messages(
        self,
        charger_id: str,
        *,
        limit: int | None = None,
        offset: int | None = None,
        action: str | None = None,
        from_date: str | None = None,
        to_date: str | None = None,
    ) -> list[dict]:
        """Historic OCPP message log for this charger."""
        params: dict[str, Any] = {}
        if limit is not None:
            params["limit"] = limit
        if offset is not None:
            params["offset"] = offset
        if action:
            params["action"] = action
        if from_date:
            params["from"] = from_date
        if to_date:
            params["to"] = to_date
        data = await self._request(
            "GET", PATH_CHARGER_OCPP_GET,
            params=params or None, charger_id=charger_id,
        )
        return data or []

    async def set_charging_limit(
        self,
        charger_id: str,
        *,
        limit_amps: float,
        connector_id: int = 1,
        number_phases: int | None = None,
    ) -> Any:
        """High-level helper: set the active charging profile limit.

        limit_amps=0 pauses charging (car/charger enters SUSPENDEDEVSE).
        limit_amps>0 resumes at that current per phase.
        """
        payload = set_charging_profile(
            connector_id=connector_id,
            limit_amps=limit_amps,
            number_phases=number_phases,
        )
        return await self.send_ocpp_message(charger_id, payload)

    async def pause_charging(
        self, charger_id: str, *, connector_id: int = 1
    ) -> Any:
        return await self.set_charging_limit(
            charger_id, limit_amps=CHARGING_LIMIT_OFF_A,
            connector_id=connector_id,
        )

    async def resume_charging(
        self,
        charger_id: str,
        *,
        limit_amps: float = CHARGING_LIMIT_DEFAULT_A,
        connector_id: int = 1,
        number_phases: int | None = None,
    ) -> Any:
        return await self.set_charging_limit(
            charger_id, limit_amps=limit_amps,
            connector_id=connector_id, number_phases=number_phases,
        )

    async def reset_charger(
        self, charger_id: str, reset_type: str = "Soft"
    ) -> Any:
        """Trigger a charger reboot. Soft = graceful, Hard = power cycle.

        NOTE: Tap also exposes POST /chargers/{id}/reset which may not take
        a body. Using the OCPP passthrough is more explicit; fall back to
        the dedicated endpoint if the passthrough returns a 4xx.
        """
        return await self.send_ocpp_message(charger_id, ocpp_reset(reset_type))

    async def reset_charger_direct(self, charger_id: str) -> Any:
        """Alternate reset path: POST /chargers/{id}/reset with empty body."""
        return await self._request(
            "POST", PATH_CHARGER_RESET, charger_id=charger_id,
        )

    # ── External meter push (experimental — Tap's ExternalMeterData
    # contract isn't in the public reference for this key scope; the
    # server is the source of truth on which fields are required) ──
    async def push_external_meter_data(
        self, meter_id: str, payload: dict,
    ) -> Any:
        """POST /meters/{meter_id}/data — used by load-balancing setups."""
        return await self._request(
            "POST", PATH_METER_DATA_PUSH,
            json=payload, meter_id=meter_id,
        )


# ── Standalone test harness ─────────────────────────────────────────────
if __name__ == "__main__":
    import os
    import json as _json

    async def _main() -> None:
        key = os.environ.get("TAP_API_KEY")
        if not key:
            raise SystemExit("Set TAP_API_KEY env var")
        async with aiohttp.ClientSession() as sess:
            c = TapElectricClient(key, sess)

            print("── Chargers ──")
            chargers = await c.list_chargers()
            print(_json.dumps(chargers, indent=2)[:1200])

            print("\n── Charger sessions (last 5) ──")
            sessions = await c.list_charger_sessions(limit=5)
            print(_json.dumps(sessions, indent=2)[:1500])

            # If there is an active session, pull live meter data for it.
            active = [s for s in sessions if s.get("endedAt") is None]
            if active:
                sid = active[0]["id"]
                print(f"\n── Meter data for active session {sid} ──")
                md = await c.session_meter_data(sid, limit=20)
                print(_json.dumps(md, indent=2)[:2000])
            elif sessions:
                sid = sessions[0]["id"]
                print(f"\n── Meter data for last finished session {sid} ──")
                md = await c.session_meter_data(sid, limit=20)
                print(_json.dumps(md, indent=2)[:2000])

    asyncio.run(_main())
