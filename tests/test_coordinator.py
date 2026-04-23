"""Higher-level tests for TapCoordinator update loop + interval switching.

Complements test_coordinator_merge.py by exercising:
  - _async_update_data end-to-end with a faked TapElectricClient
  - dynamic scan-interval switching (basic vs advanced, active vs idle)
  - auth-failure counter + UpdateFailed surface
  - offline reconcile logic
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import UpdateFailed

from tapelectric.api import TapElectricAuthError, TapElectricError
from tapelectric.api_management import ManagementSession
from tapelectric.const import (
    ADVANCED_IDLE_INTERVAL,
    ADVANCED_POLL_INTERVAL,
    OPT_SCAN_INTERVAL_ACTIVE_S,
    OPT_SCAN_INTERVAL_IDLE_S,
)
from tapelectric.coordinator import TapCoordinator


def _run(coro):
    return asyncio.run(coro)


class _FakeClient:
    def __init__(
        self,
        chargers=None,
        sessions=None,
        meter_rows=None,
        raise_list_chargers=None,
        raise_sessions=None,
    ):
        self._chargers = chargers or []
        self._sessions = sessions or []
        self._meter_rows = meter_rows or []
        self._rc = raise_list_chargers
        self._rs = raise_sessions

    async def list_chargers(self):
        if self._rc is not None:
            raise self._rc
        return list(self._chargers)

    async def list_charger_sessions(self, *, limit=100, offset=0, updated_since=None):
        if self._rs is not None:
            raise self._rs
        return list(self._sessions)

    async def session_meter_data(self, session_id, *, limit=50, **kw):
        return list(self._meter_rows)


class _StubCoord(TapCoordinator):
    def __init__(self, *, client, entry, mgmt=None, charger_id=None):
        self.hass = HomeAssistant()
        self.client = client
        self.mgmt = mgmt
        self.entry = entry
        self.scope_charger_id = charger_id
        self.update_interval = timedelta(seconds=300)
        self._cold_fetched = {}
        self._consecutive_auth_failures = 0
        self._advanced_failures = 0
        self._last_reauth_trigger = None
        self._advanced_degraded_since = None
        self._advanced_last_degraded_log = None


# ── happy path ────────────────────────────────────────────────────────────

def test_update_data_populates_chargers_and_sessions():
    chargers = [{
        "id": "EVB-1",
        "status": "AVAILABLE",
        "connectors": [{"id": "1", "status": "CHARGING"}],
        "updatedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }]
    sessions = [{
        "id": "cs_now",
        "charger": {"id": "EVB-1", "connectorId": "1"},
        "location": {"id": "loc_x"},
        "startedAt": "2026-04-23T10:00:00Z",
        "wh": 0,
    }]
    meter_rows = [
        {"measurand": "Energy", "value": 1000, "unit": "Wh",
         "phase": None, "measuredAt": "2026-04-23T10:05:00Z"},
    ]
    coord = _StubCoord(
        client=_FakeClient(
            chargers=chargers, sessions=sessions, meter_rows=meter_rows,
        ),
        entry=ConfigEntry(),
    )
    data = _run(coord._async_update_data())
    assert data.chargers == chargers
    assert data.recent_sessions == sessions
    # Active session detected because a connector is plugged.
    assert data.active_by_charger["EVB-1"]["id"] == "cs_now"
    # Meter data reduced to latest-per-measurand.
    assert data.meter_by_charger["EVB-1"][("Energy", None)]["value"] == 1000


def test_update_data_no_plugged_connector_means_no_active_session():
    chargers = [{
        "id": "EVB-1",
        "connectors": [{"id": "1", "status": "AVAILABLE"}],
        "updatedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }]
    sessions = [{
        "id": "cs_open",
        "charger": {"id": "EVB-1", "connectorId": "1"},
        "startedAt": "2026-04-23T10:00:00Z",
    }]
    coord = _StubCoord(
        client=_FakeClient(chargers=chargers, sessions=sessions),
        entry=ConfigEntry(),
    )
    data = _run(coord._async_update_data())
    assert data.active_by_charger["EVB-1"] is None


def test_update_data_scope_filters_chargers():
    chargers = [
        {"id": "EVB-1", "connectors": [], "updatedAt": None},
        {"id": "EVB-2", "connectors": [], "updatedAt": None},
    ]
    coord = _StubCoord(
        client=_FakeClient(chargers=chargers, sessions=[]),
        entry=ConfigEntry(),
        charger_id="EVB-2",
    )
    data = _run(coord._async_update_data())
    assert [c["id"] for c in data.chargers] == ["EVB-2"]


# ── auth failures ────────────────────────────────────────────────────────

def test_auth_error_raises_update_failed_and_counts():
    coord = _StubCoord(
        client=_FakeClient(raise_list_chargers=TapElectricAuthError("401")),
        entry=ConfigEntry(),
    )
    with pytest.raises(UpdateFailed):
        _run(coord._async_update_data())
    assert coord._consecutive_auth_failures == 1


def test_two_auth_failures_in_a_row_log_repair():
    coord = _StubCoord(
        client=_FakeClient(raise_list_chargers=TapElectricAuthError("401")),
        entry=ConfigEntry(),
    )
    with pytest.raises(UpdateFailed):
        _run(coord._async_update_data())
    with pytest.raises(UpdateFailed):
        _run(coord._async_update_data())
    assert coord._consecutive_auth_failures == 2


def test_auth_success_resets_counter():
    chargers = [{"id": "EVB-1", "connectors": [], "updatedAt": None}]
    # First call fails, second succeeds — counter should drop to 0.
    coord = _StubCoord(
        client=_FakeClient(raise_list_chargers=TapElectricAuthError("x")),
        entry=ConfigEntry(),
    )
    with pytest.raises(UpdateFailed):
        _run(coord._async_update_data())
    coord.client = _FakeClient(chargers=chargers, sessions=[])
    _run(coord._async_update_data())
    assert coord._consecutive_auth_failures == 0


def test_non_auth_tap_error_raises_update_failed():
    coord = _StubCoord(
        client=_FakeClient(raise_list_chargers=TapElectricError("boom")),
        entry=ConfigEntry(),
    )
    with pytest.raises(UpdateFailed):
        _run(coord._async_update_data())
    # Non-auth error doesn't bump the auth counter.
    assert coord._consecutive_auth_failures == 0


# ── interval switching ───────────────────────────────────────────────────

def test_interval_idle_default_with_no_active_session():
    chargers = [{"id": "EVB-1", "connectors": [{"id": "1", "status": "AVAILABLE"}],
                 "updatedAt": None}]
    coord = _StubCoord(
        client=_FakeClient(chargers=chargers, sessions=[]),
        entry=ConfigEntry(options={
            OPT_SCAN_INTERVAL_ACTIVE_S: 30,
            OPT_SCAN_INTERVAL_IDLE_S: 600,
        }),
    )
    _run(coord._async_update_data())
    assert coord.update_interval == timedelta(seconds=600)


def test_interval_active_when_session_live():
    chargers = [{
        "id": "EVB-1",
        "connectors": [{"id": "1", "status": "CHARGING"}],
        "updatedAt": None,
    }]
    sessions = [{
        "id": "cs_live",
        "charger": {"id": "EVB-1", "connectorId": "1"},
        "startedAt": "2026-04-23T10:00:00Z",
    }]
    coord = _StubCoord(
        client=_FakeClient(chargers=chargers, sessions=sessions),
        entry=ConfigEntry(options={
            OPT_SCAN_INTERVAL_ACTIVE_S: 15,
            OPT_SCAN_INTERVAL_IDLE_S: 600,
        }),
    )
    _run(coord._async_update_data())
    assert coord.update_interval == timedelta(seconds=15)


class _FakeMgmt:
    def __init__(self, sessions):
        self._sessions = sessions

    async def list_role_sessions(self, *, role="cpo", offset=0, take=20):
        return list(self._sessions)


def test_interval_switches_to_advanced_cadence_when_mgmt_ok_and_active():
    chargers = [{
        "id": "EVB-1",
        "connectors": [{"id": "1", "status": "CHARGING"}],
        "updatedAt": None,
    }]
    sessions = [{
        "id": "cs_live",
        "charger": {"id": "EVB-1", "connectorId": "1"},
        "startedAt": "2026-04-23T10:00:00Z",
    }]
    mgmt_session = ManagementSession(
        session_id="cs_live", charger_id="EVB-1", energy_wh=500,
        start_date="2026-04-23T10:00:00Z", end_date=None,
    )
    coord = _StubCoord(
        client=_FakeClient(chargers=chargers, sessions=sessions),
        mgmt=_FakeMgmt([mgmt_session]),
        entry=ConfigEntry(),
    )
    data = _run(coord._async_update_data())
    assert data.mgmt_fresh is True
    assert coord.update_interval == timedelta(seconds=ADVANCED_POLL_INTERVAL)


def test_interval_advanced_idle_when_mgmt_ok_but_no_active():
    chargers = [{
        "id": "EVB-1",
        "connectors": [{"id": "1", "status": "AVAILABLE"}],
        "updatedAt": None,
    }]
    coord = _StubCoord(
        client=_FakeClient(chargers=chargers, sessions=[]),
        mgmt=_FakeMgmt([]),
        entry=ConfigEntry(),
    )
    _run(coord._async_update_data())
    assert coord.update_interval == timedelta(seconds=ADVANCED_IDLE_INTERVAL)


# ── offline reconcile ────────────────────────────────────────────────────

def test_offline_reconcile_notes_stale_charger():
    noted = []

    def _fake_note(hass, cid):
        noted.append(cid)

    import tapelectric.coordinator as coord_mod
    original = coord_mod.note_charger_offline
    coord_mod.note_charger_offline = _fake_note
    try:
        chargers = [{
            "id": "EVB-1",
            "connectors": [{"id": "1", "status": "AVAILABLE"}],
            # 48h old.
            "updatedAt": (datetime.now(timezone.utc) - timedelta(hours=48))
                .isoformat().replace("+00:00", "Z"),
        }]
        coord = _StubCoord(
            client=_FakeClient(chargers=chargers, sessions=[]),
            entry=ConfigEntry(),
        )
        _run(coord._async_update_data())
        assert noted == ["EVB-1"]
    finally:
        coord_mod.note_charger_offline = original


def test_offline_reconcile_clears_when_fresh():
    cleared = []

    def _fake_clear(hass, cid):
        cleared.append(cid)

    import tapelectric.coordinator as coord_mod
    original = coord_mod.clear_charger_offline
    coord_mod.clear_charger_offline = _fake_clear
    try:
        chargers = [{
            "id": "EVB-1",
            "connectors": [{"id": "1", "status": "AVAILABLE"}],
            "updatedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        }]
        coord = _StubCoord(
            client=_FakeClient(chargers=chargers, sessions=[]),
            entry=ConfigEntry(),
        )
        _run(coord._async_update_data())
        assert cleared == ["EVB-1"]
    finally:
        coord_mod.clear_charger_offline = original


def test_offline_reconcile_missing_updatedat_clears():
    cleared = []
    import tapelectric.coordinator as coord_mod
    original = coord_mod.clear_charger_offline
    coord_mod.clear_charger_offline = lambda hass, cid: cleared.append(cid)
    try:
        chargers = [{
            "id": "EVB-1",
            "connectors": [],
            "updatedAt": None,
        }]
        coord = _StubCoord(
            client=_FakeClient(chargers=chargers, sessions=[]),
            entry=ConfigEntry(),
        )
        _run(coord._async_update_data())
        assert cleared == ["EVB-1"]
    finally:
        coord_mod.clear_charger_offline = original


# ── stale_threshold() + _opt() ───────────────────────────────────────────

def test_stale_threshold_from_options():
    coord = _StubCoord(
        client=_FakeClient(), entry=ConfigEntry(options={
            "stale_threshold_minutes": 45,
        }),
    )
    assert coord.stale_threshold() == timedelta(minutes=45)


def test_stale_threshold_default_when_unset():
    coord = _StubCoord(client=_FakeClient(), entry=ConfigEntry())
    assert coord.stale_threshold() > timedelta(minutes=0)
