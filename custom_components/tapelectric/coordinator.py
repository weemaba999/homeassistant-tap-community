"""Data update coordinator for Tap Electric.

Strategy:
  1. Fetch all chargers (authoritative for connector status).
  2. Fetch recent charger-sessions once per tick.
  3. For each charger: if a session is active, fetch its live meter data;
     otherwise (cold start / idle) fetch the most recent ended session's
     meter data ONCE so entities can discover what the firmware emits
     without waiting for a new session.
  4. Reduce meter data into a latest-value-per-(measurand, phase) map per
     charger so sensors have O(1) lookup and can compute freshness.
  5. /tariffs is NOT polled — the endpoint requires a tariffId and has
     no list form. TapData.tariffs is always `[]` for now; TariffSensor
     handles that gracefully.
  6. Raise Repairs issues for persistent auth failures and long-offline
     chargers; clear them when the condition goes away.

Options-driven (all read from entry.options with DEFAULT_OPTIONS merged in):
  scan_interval_active_s / scan_interval_idle_s
  sessions_history_limit
  meter_data_limit
  stale_threshold_minutes   — exposed to entities via `stale_threshold()`
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import TapElectricAuthError, TapElectricClient, TapElectricError
from .const import (
    DEFAULT_OPTIONS,
    DOMAIN,
    OPT_METER_DATA_LIMIT,
    OPT_SCAN_INTERVAL_ACTIVE_S,
    OPT_SCAN_INTERVAL_IDLE_S,
    OPT_SESSIONS_HISTORY_LIMIT,
    OPT_STALE_THRESHOLD_MINUTES,
    PLUGGED_CONNECTOR_STATES,
)
from .repairs import (
    clear_auth_failure,
    clear_charger_offline,
    note_auth_failure,
    note_charger_offline,
)

_LOGGER = logging.getLogger(__name__)

# Key for the reduced meter map: (measurand, phase_or_None)
MeterKey = tuple[str, str | None]

# A charger that hasn't ticked updatedAt in this long gets a Repairs issue.
_OFFLINE_REPAIR_THRESHOLD = timedelta(hours=24)


@dataclass
class TapData:
    chargers: list[dict] = field(default_factory=list)
    recent_sessions: list[dict] = field(default_factory=list)
    # chargerId -> latest measurement map
    meter_by_charger: dict[str, dict[MeterKey, dict]] = field(default_factory=dict)
    # chargerId -> active session dict (or None)
    active_by_charger: dict[str, dict | None] = field(default_factory=dict)
    # Always [] at the moment — /tariffs needs a tariffId and isn't
    # enumerable. Kept on the dataclass so TariffSensor stays import-
    # and registration-safe even when empty.
    tariffs: list[dict] = field(default_factory=list)

    def charger(self, charger_id: str) -> dict | None:
        for c in self.chargers:
            if c.get("id") == charger_id:
                return c
        return None

    def connectors(self, charger_id: str) -> list[dict]:
        return (self.charger(charger_id) or {}).get("connectors") or []

    def connector(self, charger_id: str, connector_id: int | str) -> dict | None:
        want = str(connector_id)
        for conn in self.connectors(charger_id):
            if str(conn.get("id")) == want:
                return conn
        return None

    def is_plugged(self, charger_id: str, connector_id: int | str | None = None) -> bool:
        if connector_id is None:
            return any(
                (conn.get("status") in PLUGGED_CONNECTOR_STATES)
                for conn in self.connectors(charger_id)
            )
        conn = self.connector(charger_id, connector_id)
        return bool(conn and conn.get("status") in PLUGGED_CONNECTOR_STATES)

    def active_for(self, charger_id: str) -> dict | None:
        return self.active_by_charger.get(charger_id)

    def latest_meter(
        self, charger_id: str, measurand: str, phase: str | None = None
    ) -> dict | None:
        return self.meter_by_charger.get(charger_id, {}).get((measurand, phase))

    def measurand_freshness(
        self, charger_id: str, measurand: str, phase: str | None = None,
    ) -> datetime | None:
        m = self.latest_meter(charger_id, measurand, phase)
        if not m:
            return None
        ts = m.get("measuredAt")
        if not isinstance(ts, str):
            return None
        try:
            return datetime.fromisoformat(ts.replace("Z", "+00:00"))
        except ValueError:
            return None


class TapCoordinator(DataUpdateCoordinator[TapData]):
    def __init__(
        self,
        hass: HomeAssistant,
        client: TapElectricClient,
        entry: ConfigEntry,
        charger_id: str | None = None,
    ) -> None:
        super().__init__(
            hass, _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=DEFAULT_OPTIONS[OPT_SCAN_INTERVAL_IDLE_S]),
        )
        self.client = client
        self.entry = entry
        self.scope_charger_id = charger_id
        self._cold_fetched: dict[str, str] = {}
        self._consecutive_auth_failures = 0

    # ── option accessors ───────────────────────────────────────────────

    def _opt(self, key: str) -> Any:
        return {**DEFAULT_OPTIONS, **self.entry.options}.get(key)

    def stale_threshold(self) -> timedelta:
        return timedelta(minutes=int(self._opt(OPT_STALE_THRESHOLD_MINUTES)))

    # ── update ─────────────────────────────────────────────────────────

    async def _async_update_data(self) -> TapData:
        sessions_limit = int(self._opt(OPT_SESSIONS_HISTORY_LIMIT))
        meter_limit = int(self._opt(OPT_METER_DATA_LIMIT))

        # NOTE: /tariffs is NOT a list endpoint on this API — per Tap's
        # OpenAPI it requires a tariffId query param and otherwise
        # returns 400 every call. We stopped polling it to keep the
        # debug log clean; if a tariffId is ever exposed on a charger
        # or connector object we can fetch that specific tariff later.
        tariffs: list[dict] = []
        try:
            chargers, sessions = await asyncio.gather(
                self.client.list_chargers(),
                self.client.list_charger_sessions(limit=sessions_limit),
            )
        except TapElectricAuthError as err:
            self._consecutive_auth_failures += 1
            if self._consecutive_auth_failures >= 2:
                note_auth_failure(self.hass, self.entry.entry_id)
            raise UpdateFailed(f"Authentication failed: {err}") from err
        except TapElectricError as err:
            raise UpdateFailed(f"Tap API error: {err}") from err

        # Auth good — clear any previous auth issue.
        if self._consecutive_auth_failures:
            clear_auth_failure(self.hass, self.entry.entry_id)
            self._consecutive_auth_failures = 0

        if self.scope_charger_id:
            chargers = [c for c in chargers if c.get("id") == self.scope_charger_id]

        active_by_charger: dict[str, dict | None] = {}
        last_ended_by_charger: dict[str, dict | None] = {}

        for c in chargers:
            cid = c.get("id")
            if not cid:
                continue
            plugged = any(
                (conn.get("status") in PLUGGED_CONNECTOR_STATES)
                for conn in (c.get("connectors") or [])
            )
            matching = [
                s for s in sessions
                if (s.get("charger") or {}).get("id") == cid
            ]
            if plugged:
                open_candidates = [s for s in matching if s.get("endedAt") is None]
                open_candidates.sort(
                    key=lambda s: s.get("startedAt") or "", reverse=True
                )
                active_by_charger[cid] = (
                    open_candidates[0] if open_candidates else None
                )
            else:
                active_by_charger[cid] = None

            ended = [s for s in matching if s.get("endedAt") is not None]
            ended.sort(key=lambda s: s.get("endedAt") or "", reverse=True)
            last_ended_by_charger[cid] = ended[0] if ended else None

        meter_by_charger: dict[str, dict[MeterKey, dict]] = {}

        async def _fetch_meter(cid: str, sid: str) -> None:
            try:
                rows = await self.client.session_meter_data(sid, limit=meter_limit)
            except TapElectricError as err:
                _LOGGER.debug("Meter fetch failed for %s: %s", sid, err)
                return
            latest: dict[MeterKey, dict] = {}
            for r in rows:
                key: MeterKey = (r.get("measurand"), r.get("phase"))
                prev = latest.get(key)
                if prev is None or (r.get("measuredAt") or "") > (prev.get("measuredAt") or ""):
                    latest[key] = r
            if latest:
                meter_by_charger[cid] = latest

        tasks: list[Any] = []
        for cid, active in active_by_charger.items():
            if active and active.get("id"):
                tasks.append(_fetch_meter(cid, active["id"]))
                continue
            ended = last_ended_by_charger.get(cid)
            if ended and ended.get("id") and self._cold_fetched.get(cid) != ended["id"]:
                tasks.append(_fetch_meter(cid, ended["id"]))
                self._cold_fetched[cid] = ended["id"]

        if tasks:
            await asyncio.gather(*tasks)

        # Tick-based interval: active (short) when any charger is plugged,
        # idle (long) otherwise.
        any_active = any(v is not None for v in active_by_charger.values())
        new_interval = timedelta(seconds=int(self._opt(
            OPT_SCAN_INTERVAL_ACTIVE_S if any_active else OPT_SCAN_INTERVAL_IDLE_S
        )))
        if new_interval != self.update_interval:
            self.update_interval = new_interval

        # Offline detection — compare updatedAt to wall clock.
        self._reconcile_offline_issues(chargers)

        return TapData(
            chargers=chargers,
            recent_sessions=sessions,
            meter_by_charger=meter_by_charger,
            active_by_charger=active_by_charger,
            tariffs=tariffs,
        )

    def _reconcile_offline_issues(self, chargers: list[dict]) -> None:
        now = datetime.now(timezone.utc)
        for c in chargers:
            cid = c.get("id")
            if not cid:
                continue
            ts = c.get("updatedAt")
            updated: datetime | None = None
            if isinstance(ts, str):
                try:
                    updated = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                except ValueError:
                    updated = None
            if updated is None:
                clear_charger_offline(self.hass, cid)
                continue
            if (now - updated) >= _OFFLINE_REPAIR_THRESHOLD:
                note_charger_offline(self.hass, cid)
            else:
                clear_charger_offline(self.hass, cid)

