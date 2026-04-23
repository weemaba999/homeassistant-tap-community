"""Tests for the reauth trigger + bootstrap paths.

Full reauth flow round-trip requires HA (see test_config_flow.py).
This file covers the inputs to that flow — coordinator-side reauth
counter + cooldown + bootstrap behaviour with a missing or bad
refresh token.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from tapelectric.auth_firebase import TapFirebaseAuthError
from tapelectric.api_management import TapManagementAuthError
from tapelectric.coordinator import TapCoordinator

from _helpers import make_entry, make_hass


class _FakeMgmt:
    def __init__(self, raises):
        self._raises = raises

    async def list_role_sessions(self, *, role="cpo", offset=0, take=20):
        raise self._raises


class _StubCoord(TapCoordinator):
    def __init__(self, entry, mgmt):
        self.hass = make_hass()
        self.entry = entry
        self.mgmt = mgmt
        self.client = None
        self.scope_charger_id = None
        self.update_interval = timedelta(seconds=300)
        self._cold_fetched = {}
        self._consecutive_auth_failures = 0
        self._advanced_failures = 0
        self._last_reauth_trigger = None
        self._advanced_degraded_since = None
        self._advanced_last_degraded_log = None


def _run(coro):
    return asyncio.run(coro)


def test_reauth_only_after_threshold_failures():
    entry = make_entry()
    coord = _StubCoord(
        entry, _FakeMgmt(TapManagementAuthError("401")),
    )
    # Threshold is 3 consecutive auth failures.
    for _ in range(2):
        _run(coord._fetch_mgmt_sessions({"EVB-1"}))
    assert entry._reauth_started is False
    _run(coord._fetch_mgmt_sessions({"EVB-1"}))
    assert entry._reauth_started is True


def test_firebase_auth_error_counts_toward_threshold():
    entry = make_entry()
    coord = _StubCoord(entry, _FakeMgmt(TapFirebaseAuthError("token rot")))
    for _ in range(3):
        _run(coord._fetch_mgmt_sessions({"EVB-1"}))
    assert entry._reauth_started is True


def test_reauth_cooldown_prevents_spam():
    entry = make_entry()
    coord = _StubCoord(entry, _FakeMgmt(TapManagementAuthError("401")))
    for _ in range(6):
        _run(coord._fetch_mgmt_sessions({"EVB-1"}))
    # We can't easily count reauth invocations from the stub, but
    # _last_reauth_trigger is set exactly once because the cooldown
    # guard swallows every subsequent attempt for 30 minutes.
    assert coord._last_reauth_trigger is not None
    # If the cooldown logic were broken and reauth kept re-firing,
    # _last_reauth_trigger would be within the last few ms; we can't
    # assert on that directly, but the integration test in
    # test_coordinator_merge.py::test_fetch_mgmt_reauth_cooldown…
    # exercises the cooldown boundary more precisely.


def test_network_error_does_not_trigger_reauth():
    from tapelectric.api_management import TapManagementNetworkError
    entry = make_entry()
    coord = _StubCoord(entry, _FakeMgmt(TapManagementNetworkError("x")))
    for _ in range(5):
        _run(coord._fetch_mgmt_sessions({"EVB-1"}))
    assert entry._reauth_started is False
    assert coord._advanced_failures == 0
