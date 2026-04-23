"""Tests for number.py — ChargeCurrentLimit + AutoStop helpers."""
from __future__ import annotations

import asyncio

import pytest

import tapelectric.number as number_mod
from tapelectric.coordinator import TapData
from tapelectric.number import (
    AutoStopCost,
    AutoStopKWh,
    AutoStopMinutes,
    ChargeCurrentLimit,
)

from _helpers import make_entry, make_hass


class _FakeClient:
    def __init__(self):
        self.calls = []

    async def set_charging_limit(self, cid, *, limit_amps, connector_id, number_phases):
        self.calls.append({"amps": limit_amps, "conn": connector_id,
                           "phases": number_phases})


class _FakeCoord:
    def __init__(self, data):
        self.data = data

    async def async_request_refresh(self):
        return None


def _data(status: str = "AVAILABLE", *, max_amps=None):
    conn = {"id": "1", "status": status}
    if max_amps is not None:
        conn["maxAmperage"] = max_amps
    return TapData(chargers=[{
        "id": "EVB-1",
        "connectors": [conn],
    }])


def _make_limit(data, *, fallback_max=32.0, min_amps=6.0, entry=None, client=None):
    entry = entry or make_entry()
    client = client or _FakeClient()
    return ChargeCurrentLimit(
        make_hass(), entry, _FakeCoord(data), client,
        "EVB-1", 1,
        min_amps=min_amps, fallback_max=fallback_max,
    )


def test_max_uses_connector_value():
    e = _make_limit(_data(max_amps=20))
    assert e.native_max_value == 20.0


def test_max_falls_back_to_config():
    e = _make_limit(_data())
    assert e.native_max_value == 32.0


def test_min_from_constructor():
    e = _make_limit(_data(), min_amps=8.0)
    assert e.native_min_value == 8.0


def test_native_value_defaults_to_max_when_no_bag():
    e = _make_limit(_data(max_amps=16))
    assert e.native_value == 16.0


def test_available_only_when_plugged():
    assert _make_limit(_data("AVAILABLE")).available is False
    assert _make_limit(_data("CHARGING")).available is True
    assert _make_limit(_data("SUSPENDEDEV")).available is True


def test_set_native_value_calls_client_and_persists(monkeypatch):
    monkeypatch.setattr(number_mod, "_ensure_write_enabled", lambda h, e: None)
    entry = make_entry()
    client = _FakeClient()
    e = _make_limit(_data("CHARGING", max_amps=32), entry=entry, client=client)
    asyncio.run(e.async_set_native_value(10.0))
    assert client.calls == [{"amps": 10.0, "conn": 1, "phases": None}]


# ── AutoStop helpers ──────────────────────────────────────────────────────

def test_autostop_kwh_default_zero():
    entry = make_entry()
    e = AutoStopKWh(make_hass(), entry, _FakeCoord(_data()), "EVB-1")
    assert e.native_value == 0.0


def test_autostop_minutes_persists(monkeypatch):
    entry = make_entry()
    e = AutoStopMinutes(make_hass(), entry, _FakeCoord(_data()), "EVB-1")
    asyncio.run(e.async_set_native_value(45.0))
    assert e.native_value == 45.0


def test_autostop_cost_is_readonly_zero():
    entry = make_entry()
    e = AutoStopCost(make_hass(), entry, _FakeCoord(_data()), "EVB-1")
    assert e.native_value == 0.0
