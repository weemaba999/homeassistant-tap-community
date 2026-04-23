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

import aiohttp

from .api import TapElectricAuthError, TapElectricClient, TapElectricError
from .api_management import (
    ManagementSession,
    TapManagementAuthError,
    TapManagementClient,
    TapManagementError,
    TapManagementNetworkError,
)
from .auth_firebase import TapFirebaseAuthError
from .const import (
    ADVANCED_IDLE_INTERVAL,
    ADVANCED_POLL_INTERVAL,
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


def _newer(a: datetime | None, b: datetime | None) -> bool:
    """Return True if `a` is strictly more recent than `b`, treating
    missing timestamps as 'oldest possible'."""
    if a is None:
        return False
    if b is None:
        return True
    return a > b


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

    # Advanced-mode overlays. Populated only when the coordinator has a
    # working TapManagementClient and the management fetch succeeds on
    # the current tick. Absent keys mean "no data for this charger" —
    # entities should fall back to the public data (is_charging binary
    # sensor already does).
    mgmt_active_by_charger: dict[str, ManagementSession | None] = field(
        default_factory=dict,
    )
    mgmt_last_closed_by_charger: dict[str, ManagementSession | None] = field(
        default_factory=dict,
    )
    # True when the last successful tick had a working management fetch.
    # Sensors check this to decide "am I allowed to trust mgmt_active?"
    mgmt_fresh: bool = False

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

    def mgmt_active(self, charger_id: str) -> ManagementSession | None:
        """Advanced-mode active session (None when mgmt is off or stale)."""
        if not self.mgmt_fresh:
            return None
        return self.mgmt_active_by_charger.get(charger_id)

    def mgmt_last_closed(self, charger_id: str) -> ManagementSession | None:
        """Most recent closed session per management API, if any."""
        if not self.mgmt_fresh:
            return None
        return self.mgmt_last_closed_by_charger.get(charger_id)

    def is_charging_active(self, charger_id: str) -> bool | None:
        """Authoritative 'is this charger charging right now?' per mgmt.

        Returns None when mgmt is off or the current tick is degraded —
        caller should fall back to the connector-status binary sensor.
        """
        if not self.mgmt_fresh:
            return None
        s = self.mgmt_active_by_charger.get(charger_id)
        return bool(s) if s is not None else False

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
    # Trigger reauth after N consecutive management-auth failures, and
    # at most once per cool-off period (so we don't spawn a reauth flow
    # on every 30-second tick while the user hasn't responded yet).
    _ADVANCED_FAILURE_REAUTH_THRESHOLD = 3
    _ADVANCED_REAUTH_COOLDOWN = timedelta(minutes=30)
    _DEGRADED_LOG_INTERVAL = timedelta(hours=1)

    def __init__(
        self,
        hass: HomeAssistant,
        client: TapElectricClient,
        entry: ConfigEntry,
        *,
        mgmt: TapManagementClient | None = None,
        charger_id: str | None = None,
    ) -> None:
        super().__init__(
            hass, _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=DEFAULT_OPTIONS[OPT_SCAN_INTERVAL_IDLE_S]),
        )
        self.client = client
        self.mgmt = mgmt
        self.entry = entry
        self.scope_charger_id = charger_id
        self._cold_fetched: dict[str, str] = {}
        self._consecutive_auth_failures = 0

        # Advanced-mode bookkeeping. Only meaningful when self.mgmt is
        # not None, but initialised unconditionally so the rest of the
        # class doesn't need branches.
        self._advanced_failures = 0
        self._last_reauth_trigger: datetime | None = None
        self._advanced_degraded_since: datetime | None = None
        self._advanced_last_degraded_log: datetime | None = None

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

        # Advanced-mode fetch (management API). Never allowed to fail
        # the whole coordinator — degrade silently, log rate-limited.
        mgmt_active, mgmt_closed, mgmt_ok = await self._fetch_mgmt_sessions(
            {c.get("id") for c in chargers if c.get("id")},
        )

        # Tick-based interval. In advanced mode we switch to the
        # dedicated ADVANCED_POLL/IDLE cadence so the user gets 30-s
        # updates on live sessions regardless of basic-mode options.
        any_active_basic = any(v is not None for v in active_by_charger.values())
        any_active_mgmt = any(v is not None for v in mgmt_active.values())
        any_active = any_active_basic or any_active_mgmt

        if self.mgmt is not None and mgmt_ok:
            new_interval = timedelta(seconds=(
                ADVANCED_POLL_INTERVAL if any_active else ADVANCED_IDLE_INTERVAL
            ))
        else:
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
            mgmt_active_by_charger=mgmt_active,
            mgmt_last_closed_by_charger=mgmt_closed,
            mgmt_fresh=mgmt_ok,
        )

    # ── Advanced mode / management fetch ───────────────────────────────

    async def _fetch_mgmt_sessions(
        self, charger_ids: set[str],
    ) -> tuple[
        dict[str, ManagementSession | None],
        dict[str, ManagementSession | None],
        bool,
    ]:
        """Fetch + bucketise management sessions per charger.

        Returns (active_by_cid, last_closed_by_cid, ok). On any failure
        `ok` is False and both dicts are empty — callers then use
        mgmt_fresh=False and fall back to basic-mode data.
        """
        empty: dict[str, ManagementSession | None] = {}
        if self.mgmt is None:
            return empty, empty, False

        try:
            sessions = await self.mgmt.list_role_sessions(take=20)
        except (TapManagementAuthError, TapFirebaseAuthError) as err:
            self._advanced_failures += 1
            _LOGGER.warning(
                "Advanced-mode auth failed (%d/%d): %s",
                self._advanced_failures,
                self._ADVANCED_FAILURE_REAUTH_THRESHOLD, err,
            )
            if self._advanced_failures >= self._ADVANCED_FAILURE_REAUTH_THRESHOLD:
                self._maybe_trigger_reauth()
            self._mark_degraded(err)
            return empty, empty, False
        except (
            TapManagementNetworkError,
            TapManagementError,
            aiohttp.ClientError,
            asyncio.TimeoutError,
        ) as err:
            self._mark_degraded(err)
            return empty, empty, False

        # Success — reset auth-failure counter + degradation state.
        self._advanced_failures = 0
        if self._advanced_degraded_since is not None:
            _LOGGER.info("Advanced mode recovered after degradation.")
        self._advanced_degraded_since = None
        self._advanced_last_degraded_log = None

        active_by_cid, closed_by_cid = self.bucketise_mgmt_sessions(
            sessions, charger_ids,
        )
        return active_by_cid, closed_by_cid, True

    @staticmethod
    def bucketise_mgmt_sessions(
        sessions: list[ManagementSession],
        charger_ids: set[str],
    ) -> tuple[
        dict[str, ManagementSession | None],
        dict[str, ManagementSession | None],
    ]:
        """Pure splitter: returns (active_by_cid, closed_by_cid).

        For every known charger_id, pick the single most recent active
        session (by started_at) and the single most recent closed
        session (by ended_at). Sessions whose charger_id isn't in the
        set we know about are ignored.
        """
        active_by_cid: dict[str, ManagementSession | None] = {
            cid: None for cid in charger_ids
        }
        closed_by_cid: dict[str, ManagementSession | None] = {
            cid: None for cid in charger_ids
        }
        for s in sessions:
            cid = s.charger_id
            if not cid or cid not in charger_ids:
                continue
            if s.is_active:
                prev = active_by_cid.get(cid)
                if prev is None or _newer(s.started_at, prev.started_at):
                    active_by_cid[cid] = s
            else:
                prev = closed_by_cid.get(cid)
                if prev is None or _newer(s.ended_at, prev.ended_at):
                    closed_by_cid[cid] = s
        return active_by_cid, closed_by_cid

    def _mark_degraded(self, err: Exception) -> None:
        now = datetime.now(timezone.utc)
        if self._advanced_degraded_since is None:
            self._advanced_degraded_since = now
        # Rate-limit the log to once per hour.
        last = self._advanced_last_degraded_log
        if last is None or (now - last) >= self._DEGRADED_LOG_INTERVAL:
            _LOGGER.warning(
                "Advanced mode degraded — falling back to basic-only data "
                "(since %s): %s",
                self._advanced_degraded_since.isoformat(), err,
            )
            self._advanced_last_degraded_log = now

    def _maybe_trigger_reauth(self) -> None:
        """Start HA's reauth flow for advanced credentials, cooled-off."""
        now = datetime.now(timezone.utc)
        last = self._last_reauth_trigger
        if last is not None and (now - last) < self._ADVANCED_REAUTH_COOLDOWN:
            return
        self._last_reauth_trigger = now
        _LOGGER.warning(
            "Starting advanced-mode re-authentication flow after %d "
            "consecutive auth failures.", self._advanced_failures,
        )
        try:
            # HA ≥ 2024.11 API; falls back to legacy helper if unavailable.
            self.entry.async_start_reauth(self.hass)
        except AttributeError:
            # Pre-2024.11 — fire manually.
            self.hass.async_create_task(
                self.hass.config_entries.flow.async_init(
                    DOMAIN,
                    context={
                        "source": "reauth",
                        "entry_id": self.entry.entry_id,
                    },
                    data={**self.entry.data},
                )
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

