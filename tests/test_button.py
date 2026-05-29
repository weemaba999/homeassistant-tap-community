"""Tests for button.py — ResetButton + Stop/Start (advanced)."""
from __future__ import annotations

import asyncio

import pytest

import tapelectric.button as button_mod
from tapelectric.api_management import (
    ManagementSession,
    TapManagementAuthError,
)
from tapelectric.button import (
    ResetButton,
    StartChargingButton,
    StopChargingButton,
)
from tapelectric.const import (
    CONF_DEFAULT_ID_TAG,
    DATA_DEFAULT_OUTLET_IDS,
    DATA_RESET_TYPE,
)
from tapelectric.coordinator import TapData

from _helpers import make_entry, make_hass


class _FakeClient:
    def __init__(self):
        self.calls = []

    async def reset_charger(self, cid, reset_type):
        self.calls.append({"cid": cid, "type": reset_type})


class _FakeCoord:
    def __init__(self, data):
        self.data = data

    async def async_request_refresh(self):
        return None


class _FakeMgmt:
    """Stand-in for TapManagementClient capturing remote start/stop calls."""

    def __init__(
        self,
        *,
        stop_result=None,
        start_result=None,
        stop_raises=None,
        start_raises=None,
    ):
        self._stop_result = (
            stop_result if stop_result is not None else {"status": "Accepted"}
        )
        self._start_result = (
            start_result if start_result is not None
            else {"status": "Accepted"}
        )
        self._stop_raises = stop_raises
        self._start_raises = start_raises
        self.stop_calls: list[dict] = []
        self.start_calls: list[dict] = []

    async def remote_stop_transaction(self, charger_id, transaction_id):
        self.stop_calls.append({
            "charger_id": charger_id,
            "transaction_id": transaction_id,
        })
        if self._stop_raises:
            raise self._stop_raises
        return self._stop_result

    async def remote_start_transaction(
        self, charger_id, *, outlet_id=None, id_tag=None, visual_id=None,
    ):
        self.start_calls.append({
            "charger_id": charger_id,
            "outlet_id":  outlet_id,
            "id_tag":     id_tag,
            "visual_id":  visual_id,
        })
        if self._start_raises:
            raise self._start_raises
        return self._start_result


def _data():
    return TapData(chargers=[{"id": "EVB-1", "connectors": []}])


def _data_with_active_mgmt(transaction_id: int | None = 4242):
    data = _data()
    data.mgmt_fresh = True
    data.mgmt_active_by_charger = {
        "EVB-1": ManagementSession(
            session_id="cs_active",
            charger_id="EVB-1",
            start_date="2026-04-27T10:00:00Z",
            end_date=None,
            transaction_id=transaction_id,
        ),
    }
    return data


def _data_charging_no_mgmt():
    """Connector is plugged + CHARGING but mgmt is stale — Start should be
    unavailable because is_plugged() returns True."""
    return TapData(chargers=[{
        "id": "EVB-1",
        "connectors": [{"id": "1", "status": "CHARGING"}],
    }])


def test_reset_button_calls_ocpp_endpoint(monkeypatch):
    """Implementation routes through the /ocpp passthrough
    (client.reset_charger) and forwards the reset_type read from
    entry.data, defaulting to Soft.
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
    assert client.calls == [{"cid": "EVB-1", "type": "Soft"}]


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


# ── StopChargingButton ─────────────────────────────────────────────────


def _stop_button(*, data=None, entry=None, mgmt=None) -> StopChargingButton:
    return StopChargingButton(
        make_hass(),
        entry or make_entry(),
        _FakeCoord(data if data is not None else _data_with_active_mgmt()),
        mgmt or _FakeMgmt(),
        "EVB-1",
    )


def test_stop_button_unique_id_contains_charger_id():
    btn = _stop_button()
    assert "EVB-1" in btn._attr_unique_id
    assert "stop_charging" in btn._attr_unique_id


def test_stop_button_unavailable_when_no_active_session():
    btn = _stop_button(data=_data())  # no mgmt_active
    assert btn.available is False


def test_stop_button_available_when_active_session():
    btn = _stop_button()
    assert btn.available is True


def test_stop_button_calls_remote_stop_with_transaction_id(monkeypatch):
    monkeypatch.setattr(button_mod, "_ensure_write_enabled", lambda h, e: None)
    mgmt = _FakeMgmt()
    btn = _stop_button(mgmt=mgmt)
    asyncio.run(btn.async_press())
    assert mgmt.stop_calls == [
        {"charger_id": "EVB-1", "transaction_id": 4242},
    ]


def test_stop_button_handles_rejected_without_crash(
    monkeypatch, caplog,
):
    monkeypatch.setattr(button_mod, "_ensure_write_enabled", lambda h, e: None)
    mgmt = _FakeMgmt(stop_result={"status": "Rejected"})
    btn = _stop_button(mgmt=mgmt)
    import logging
    caplog.set_level(logging.WARNING)
    asyncio.run(btn.async_press())
    # Did not raise; logged a warning mentioning Rejected behaviour
    assert any(
        "rejected" in rec.message.lower() for rec in caplog.records
    )


def test_stop_button_handles_network_error_without_crash(monkeypatch):
    monkeypatch.setattr(button_mod, "_ensure_write_enabled", lambda h, e: None)
    mgmt = _FakeMgmt(stop_result=None)  # client returned None on network err
    btn = _stop_button(mgmt=mgmt)
    asyncio.run(btn.async_press())  # should NOT raise
    assert mgmt.stop_calls  # call was attempted


def test_stop_button_auth_error_logged_not_raised(monkeypatch):
    monkeypatch.setattr(button_mod, "_ensure_write_enabled", lambda h, e: None)
    mgmt = _FakeMgmt(stop_raises=TapManagementAuthError("nope"))
    btn = _stop_button(mgmt=mgmt)
    asyncio.run(btn.async_press())  # the button swallows auth errors


def test_stop_button_press_when_no_active_session_is_noop(
    monkeypatch, caplog,
):
    monkeypatch.setattr(button_mod, "_ensure_write_enabled", lambda h, e: None)
    mgmt = _FakeMgmt()
    btn = _stop_button(data=_data(), mgmt=mgmt)
    import logging
    caplog.set_level(logging.WARNING)
    asyncio.run(btn.async_press())
    assert mgmt.stop_calls == []  # no call when unavailable


# ── StartChargingButton ────────────────────────────────────────────────


def _start_entry(*, id_tag="TAP-1", outlet_id="ou_xyz"):
    data: dict = {}
    if id_tag is not None:
        data[CONF_DEFAULT_ID_TAG] = id_tag
    if outlet_id is not None:
        data[DATA_DEFAULT_OUTLET_IDS] = {"EVB-1": outlet_id}
    return make_entry(data=data)


def _start_button(*, data=None, entry=None, mgmt=None) -> StartChargingButton:
    return StartChargingButton(
        make_hass(),
        entry or _start_entry(),
        _FakeCoord(data if data is not None else _data()),
        mgmt or _FakeMgmt(),
        "EVB-1",
    )


def test_start_button_unique_id_contains_charger_id():
    btn = _start_button()
    assert "EVB-1" in btn._attr_unique_id
    assert "start_charging" in btn._attr_unique_id


def test_start_button_available_when_no_session_and_configured():
    btn = _start_button()
    assert btn.available is True


def test_start_button_unavailable_when_session_already_active():
    btn = _start_button(data=_data_with_active_mgmt())
    assert btn.available is False


def test_start_button_unavailable_when_connector_plugged_charging():
    btn = _start_button(data=_data_charging_no_mgmt())
    assert btn.available is False


def test_start_button_unavailable_without_id_tag():
    btn = _start_button(entry=_start_entry(id_tag=None))
    assert btn.available is False


def test_start_button_unavailable_without_outlet_id():
    btn = _start_button(entry=_start_entry(outlet_id=None))
    assert btn.available is False


def test_start_button_calls_remote_start_with_id_tag(monkeypatch):
    monkeypatch.setattr(button_mod, "_ensure_write_enabled", lambda h, e: None)
    mgmt = _FakeMgmt()
    btn = _start_button(mgmt=mgmt)
    asyncio.run(btn.async_press())
    assert mgmt.start_calls == [{
        "charger_id": "EVB-1",
        "outlet_id":  "ou_xyz",
        "id_tag":     "TAP-1",
        "visual_id":  None,
    }]


def test_start_button_press_missing_config_is_noop(monkeypatch):
    monkeypatch.setattr(button_mod, "_ensure_write_enabled", lambda h, e: None)
    mgmt = _FakeMgmt()
    btn = _start_button(entry=_start_entry(id_tag=None), mgmt=mgmt)
    asyncio.run(btn.async_press())
    assert mgmt.start_calls == []


def test_start_button_rejected_logs_warning(monkeypatch, caplog):
    monkeypatch.setattr(button_mod, "_ensure_write_enabled", lambda h, e: None)
    mgmt = _FakeMgmt(start_result={"status": "Rejected"})
    btn = _start_button(mgmt=mgmt)
    import logging
    caplog.set_level(logging.WARNING)
    asyncio.run(btn.async_press())
    assert any(
        "rejected" in rec.message.lower() for rec in caplog.records
    )


def test_start_button_auth_error_swallowed(monkeypatch):
    monkeypatch.setattr(button_mod, "_ensure_write_enabled", lambda h, e: None)
    mgmt = _FakeMgmt(start_raises=TapManagementAuthError("nope"))
    btn = _start_button(mgmt=mgmt)
    asyncio.run(btn.async_press())  # no exception
