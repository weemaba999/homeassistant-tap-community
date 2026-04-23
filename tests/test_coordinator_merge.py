"""Tests for TapCoordinator's management-session merge + degradation.

Replaces the original test_coordinator_merge_standalone.py — same
coverage, now collected by pytest. The StubCoord bypass is retained
because a full DataUpdateCoordinator lifecycle isn't needed to
exercise the pure merge/degradation logic.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any

import aiohttp
import pytest

from _helpers import make_entry, make_hass

from tapelectric.api_management import (
    ManagementSession,
    TapManagementAuthError,
    TapManagementNetworkError,
)
from tapelectric.auth_firebase import TapFirebaseAuthError
from tapelectric.coordinator import TapCoordinator


def _mk_session(
    *, session_id: str, charger_id: str, energy_wh: int,
    started_at: datetime, ended_at: datetime | None,
) -> ManagementSession:
    return ManagementSession(
        session_id=session_id,
        charger_id=charger_id,
        start_date=started_at.isoformat().replace("+00:00", "Z"),
        end_date=(ended_at.isoformat().replace("+00:00", "Z")
                  if ended_at else None),
        energy_wh=energy_wh,
    )


class _StubCoord(TapCoordinator):
    """Bypass DataUpdateCoordinator wiring for pure-logic tests."""
    def __init__(self, mgmt, entry):
        self.hass = make_hass()
        self.mgmt = mgmt
        self.entry = entry
        self.client = None
        self.scope_charger_id = None
        self.update_interval = timedelta(seconds=300)
        self._cold_fetched = {}
        self._consecutive_auth_failures = 0
        self._advanced_failures = 0
        self._last_reauth_trigger = None
        self._advanced_degraded_since = None
        self._advanced_last_degraded_log = None


class _FakeMgmt:
    def __init__(self, *, sessions=None, raises=None):
        self._sessions = sessions or []
        self._raises = raises

    async def list_role_sessions(self, *, role="cpo", offset=0, take=20):
        if self._raises is not None:
            raise self._raises
        return list(self._sessions)


# ── bucketise_mgmt_sessions (pure) ────────────────────────────────────

def test_bucketise_picks_most_recent_active_and_closed():
    now = datetime.now(timezone.utc)
    sessions = [
        _mk_session(
            session_id="cs_old_active", charger_id="EVB-A", energy_wh=1000,
            started_at=now - timedelta(hours=5),
            ended_at=now - timedelta(hours=4),
        ),
        _mk_session(
            session_id="cs_new_active", charger_id="EVB-A", energy_wh=5000,
            started_at=now - timedelta(minutes=10), ended_at=None,
        ),
        _mk_session(
            session_id="cs_b_closed", charger_id="EVB-B", energy_wh=200,
            started_at=now - timedelta(hours=1),
            ended_at=now - timedelta(minutes=45),
        ),
    ]
    active, closed = TapCoordinator.bucketise_mgmt_sessions(
        sessions, {"EVB-A", "EVB-B"},
    )
    assert active["EVB-A"].session_id == "cs_new_active"
    assert active["EVB-B"] is None
    assert closed["EVB-A"].session_id == "cs_old_active"
    assert closed["EVB-B"].session_id == "cs_b_closed"


def test_bucketise_ignores_unknown_chargers():
    now = datetime.now(timezone.utc)
    sessions = [
        _mk_session(
            session_id="cs_ghost", charger_id="EVB-GHOST", energy_wh=100,
            started_at=now - timedelta(minutes=10), ended_at=None,
        ),
    ]
    active, closed = TapCoordinator.bucketise_mgmt_sessions(
        sessions, {"EVB-REAL"},
    )
    assert active == {"EVB-REAL": None}
    assert closed == {"EVB-REAL": None}


def test_bucketise_handles_empty_input():
    active, closed = TapCoordinator.bucketise_mgmt_sessions([], {"A"})
    assert active == {"A": None}
    assert closed == {"A": None}


# ── _fetch_mgmt_sessions (full path — happy + each failure mode) ─────

def _run(coro):
    return asyncio.run(coro)


def test_fetch_mgmt_happy_path_marks_fresh():
    now = datetime.now(timezone.utc)
    sessions = [_mk_session(
        session_id="cs_1", charger_id="EVB-1", energy_wh=100,
        started_at=now - timedelta(minutes=5), ended_at=None,
    )]
    coord = _StubCoord(mgmt=_FakeMgmt(sessions=sessions), entry=make_entry())
    active, closed, ok = _run(coord._fetch_mgmt_sessions({"EVB-1"}))
    assert ok is True
    assert active["EVB-1"].session_id == "cs_1"
    # auth-fail counter reset after success.
    assert coord._advanced_failures == 0


def test_fetch_mgmt_no_client_returns_empty_not_ok():
    coord = _StubCoord(mgmt=None, entry=make_entry())
    active, closed, ok = _run(coord._fetch_mgmt_sessions({"EVB-1"}))
    assert ok is False
    assert active == {}
    assert closed == {}


def test_fetch_mgmt_auth_error_increments_failure_count():
    entry = make_entry()
    coord = _StubCoord(
        mgmt=_FakeMgmt(raises=TapManagementAuthError("unauthorized")),
        entry=entry,
    )
    _run(coord._fetch_mgmt_sessions({"EVB-1"}))
    assert coord._advanced_failures == 1
    assert entry._reauth_started is False  # threshold is 3


def test_fetch_mgmt_auth_error_triple_triggers_reauth():
    entry = make_entry()
    coord = _StubCoord(
        mgmt=_FakeMgmt(raises=TapFirebaseAuthError("token dead")),
        entry=entry,
    )
    for _ in range(3):
        _run(coord._fetch_mgmt_sessions({"EVB-1"}))
    assert coord._advanced_failures == 3
    assert entry._reauth_started is True


def test_fetch_mgmt_reauth_cooldown_prevents_rapid_retrigger():
    entry = make_entry()
    coord = _StubCoord(
        mgmt=_FakeMgmt(raises=TapFirebaseAuthError("token dead")),
        entry=entry,
    )
    for _ in range(4):
        _run(coord._fetch_mgmt_sessions({"EVB-1"}))
    # reauth triggered exactly once — we can't easily assert that from
    # the stub, but _last_reauth_trigger is set (non-None).
    assert coord._last_reauth_trigger is not None


def test_fetch_mgmt_network_error_degrades_silently():
    coord = _StubCoord(
        mgmt=_FakeMgmt(raises=TapManagementNetworkError("socket reset")),
        entry=make_entry(),
    )
    _, _, ok = _run(coord._fetch_mgmt_sessions({"EVB-1"}))
    assert ok is False
    # Degradation marker set.
    assert coord._advanced_degraded_since is not None
    # Auth failure counter NOT incremented (network != auth).
    assert coord._advanced_failures == 0


def test_fetch_mgmt_client_error_degrades_silently():
    coord = _StubCoord(
        mgmt=_FakeMgmt(raises=aiohttp.ClientError("bad socket")),
        entry=make_entry(),
    )
    _, _, ok = _run(coord._fetch_mgmt_sessions({"EVB-1"}))
    assert ok is False
    assert coord._advanced_degraded_since is not None


def test_fetch_mgmt_recovery_clears_degraded_state():
    entry = make_entry()
    coord = _StubCoord(
        mgmt=_FakeMgmt(raises=TapManagementNetworkError("x")),
        entry=entry,
    )
    _run(coord._fetch_mgmt_sessions({"EVB-1"}))
    assert coord._advanced_degraded_since is not None

    # Swap to a working mgmt and retry.
    coord.mgmt = _FakeMgmt(sessions=[])
    _run(coord._fetch_mgmt_sessions({"EVB-1"}))
    assert coord._advanced_degraded_since is None
    assert coord._advanced_last_degraded_log is None


# ── TapData helpers ───────────────────────────────────────────────────

def test_tapdata_is_charging_active_requires_fresh_mgmt():
    from tapelectric.coordinator import TapData
    d = TapData(
        mgmt_active_by_charger={"A": _mk_session(
            session_id="cs", charger_id="A", energy_wh=0,
            started_at=datetime.now(timezone.utc), ended_at=None,
        )},
        mgmt_fresh=False,
    )
    assert d.is_charging_active("A") is None   # mgmt stale → None


def test_tapdata_is_charging_active_true_when_fresh_and_active():
    from tapelectric.coordinator import TapData
    d = TapData(
        mgmt_active_by_charger={"A": _mk_session(
            session_id="cs", charger_id="A", energy_wh=0,
            started_at=datetime.now(timezone.utc), ended_at=None,
        )},
        mgmt_fresh=True,
    )
    assert d.is_charging_active("A") is True


def test_tapdata_is_plugged_any_connector():
    from tapelectric.coordinator import TapData
    d = TapData(chargers=[{
        "id": "A",
        "connectors": [
            {"id": "1", "status": "AVAILABLE"},
            {"id": "2", "status": "CHARGING"},
        ],
    }])
    assert d.is_plugged("A") is True
    assert d.is_plugged("A", 1) is False
    assert d.is_plugged("A", 2) is True


def test_tapdata_latest_meter_returns_none_when_absent():
    from tapelectric.coordinator import TapData
    d = TapData()
    assert d.latest_meter("A", "Energy") is None


def test_tapdata_measurand_freshness_parses_measuredat():
    from tapelectric.coordinator import TapData
    d = TapData(meter_by_charger={
        "A": {("Energy", None): {"measuredAt": "2026-04-23T10:00:00Z"}},
    })
    ts = d.measurand_freshness("A", "Energy")
    assert ts is not None
    assert ts.year == 2026


def test_tapdata_measurand_freshness_bad_timestamp_none():
    from tapelectric.coordinator import TapData
    d = TapData(meter_by_charger={
        "A": {("Energy", None): {"measuredAt": "bogus"}},
    })
    assert d.measurand_freshness("A", "Energy") is None
