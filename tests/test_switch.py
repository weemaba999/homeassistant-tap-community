"""Tests for switch.py — ChargeAllowedSwitch state + commands."""
from __future__ import annotations

import asyncio

import pytest

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

import tapelectric.switch as switch_mod
from tapelectric.coordinator import TapData
from tapelectric.switch import ChargeAllowedSwitch


class _FakeClient:
    def __init__(self):
        self.resume_calls = []
        self.pause_calls = []

    async def resume_charging(self, cid, *, limit_amps, connector_id, number_phases):
        self.resume_calls.append(
            {"cid": cid, "amps": limit_amps, "conn": connector_id,
             "phases": number_phases},
        )

    async def pause_charging(self, cid, *, connector_id):
        self.pause_calls.append({"cid": cid, "conn": connector_id})


class _FakeCoord:
    def __init__(self, data):
        self.data = data

    async def async_request_refresh(self):
        return None


def _data_with_connector(status: str, *, max_amps=None):
    conn = {"id": "1", "status": status}
    if max_amps is not None:
        conn["maxAmperage"] = max_amps
    return TapData(chargers=[{
        "id": "EVB-1",
        "connectors": [conn],
        "updatedAt": None,
    }])


def _make_switch(data, hass=None, entry=None, client=None, fallback_amps=16.0):
    hass = hass or HomeAssistant()
    entry = entry or ConfigEntry()
    client = client or _FakeClient()
    return ChargeAllowedSwitch(
        hass, entry, _FakeCoord(data), client,
        "EVB-1", 1, fallback_amps,
    )


def test_switch_is_on_when_charging():
    s = _make_switch(_data_with_connector("CHARGING"))
    assert s.is_on is True


def test_switch_is_on_during_suspended_ev():
    s = _make_switch(_data_with_connector("SUSPENDEDEV"))
    assert s.is_on is True


def test_switch_off_when_available():
    s = _make_switch(_data_with_connector("AVAILABLE"))
    assert s.is_on is False


def test_switch_unavailable_when_faulted():
    s = _make_switch(_data_with_connector("FAULTED"))
    assert s.available is False


def test_switch_unavailable_when_connector_unavailable():
    s = _make_switch(_data_with_connector("UNAVAILABLE"))
    assert s.available is False


def test_turn_on_uses_connector_max_amperage(monkeypatch):
    monkeypatch.setattr(switch_mod, "_ensure_write_enabled", lambda h, e: None)
    client = _FakeClient()
    s = _make_switch(
        _data_with_connector("AVAILABLE", max_amps=32),
        client=client,
    )
    asyncio.run(s.async_turn_on())
    assert client.resume_calls == [
        {"cid": "EVB-1", "amps": 32.0, "conn": 1, "phases": None},
    ]


def test_turn_on_falls_back_to_fallback_when_no_max_amperage(monkeypatch):
    monkeypatch.setattr(switch_mod, "_ensure_write_enabled", lambda h, e: None)
    client = _FakeClient()
    s = _make_switch(
        _data_with_connector("AVAILABLE"), client=client, fallback_amps=20.0,
    )
    asyncio.run(s.async_turn_on())
    assert client.resume_calls[0]["amps"] == 20.0


def test_turn_off_calls_pause(monkeypatch):
    monkeypatch.setattr(switch_mod, "_ensure_write_enabled", lambda h, e: None)
    client = _FakeClient()
    s = _make_switch(_data_with_connector("CHARGING"), client=client)
    asyncio.run(s.async_turn_off())
    assert client.pause_calls == [{"cid": "EVB-1", "conn": 1}]
