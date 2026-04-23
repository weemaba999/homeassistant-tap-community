"""Tests for button.py — ResetButton."""
from __future__ import annotations

import asyncio

import pytest

import tapelectric.button as button_mod
from tapelectric.button import ResetButton
from tapelectric.const import DATA_RESET_TYPE
from tapelectric.coordinator import TapData

from _helpers import make_entry, make_hass


class _FakeClient:
    def __init__(self):
        self.calls = []

    async def reset_charger_direct(self, cid):
        self.calls.append({"cid": cid, "type": "direct"})


class _FakeCoord:
    def __init__(self, data):
        self.data = data


def _data():
    return TapData(chargers=[{"id": "EVB-1", "connectors": []}])


def test_reset_button_calls_direct_endpoint(monkeypatch):
    """Current implementation uses reset_charger_direct; reset_type is
    read from entry.data but not forwarded (direct endpoint is type-less).
    """
    monkeypatch.setattr(button_mod, "_ensure_write_enabled", lambda h, e: None)

    class _CoordWithRefresh(_FakeCoord):
        async def async_request_refresh(self):
            return None

    client = _FakeClient()
    btn = ResetButton(
        make_hass(), make_entry(), _CoordWithRefresh(_data()), client, "EVB-1",
    )
    asyncio.run(btn.async_press())
    assert client.calls == [{"cid": "EVB-1", "type": "direct"}]


def test_reset_button_selected_type_reads_from_entry_data():
    btn = ResetButton(
        make_hass(),
        make_entry(data={DATA_RESET_TYPE: {"EVB-1": "Hard"}}),
        _FakeCoord(_data()), _FakeClient(), "EVB-1",
    )
    assert btn._selected_reset_type() == "Hard"


def test_reset_button_selected_type_defaults_soft():
    btn = ResetButton(
        make_hass(), make_entry(), _FakeCoord(_data()), _FakeClient(),
        "EVB-1",
    )
    assert btn._selected_reset_type() == "Soft"


def test_reset_button_unique_id():
    btn = ResetButton(
        make_hass(), make_entry(), _FakeCoord(_data()),
        _FakeClient(), "EVB-1",
    )
    assert "EVB-1" in btn._attr_unique_id
    assert "reset" in btn._attr_unique_id.lower()
