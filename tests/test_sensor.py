"""Tests for sensor.py.

Focuses on the bits the phase-C spec singles out:
  * `source` attribute on LastSessionEnergySensor — distinguishes
    management-API data from public-API fallback.
  * Advanced-gated creation: CurrentSession* sensors only register
    when entry.data[CONF_ADVANCED_MODE] is truthy.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from homeassistant.config_entries import ConfigEntry

from tapelectric.api_management import ManagementSession
from tapelectric.coordinator import TapData
from tapelectric.sensor import (
    CurrentSessionDurationSensor,
    CurrentSessionEnergySensor,
    LastSessionEnergySensor,
    SessionEnergySensor,
)


class _FakeCoord:
    """Minimal coordinator shim exposing only what the sensors read."""
    def __init__(self, *, data, entry=None):
        self.data = data
        self.entry = entry or ConfigEntry()

    def stale_threshold(self):
        return timedelta(minutes=15)


def _tap_data_with_charger(cid: str = "EVB-1") -> TapData:
    return TapData(
        chargers=[{
            "id": cid,
            "connectors": [{"id": "1", "status": "CHARGING"}],
            "updatedAt": "2026-04-23T10:00:00Z",
        }],
        recent_sessions=[],
        meter_by_charger={},
        active_by_charger={cid: None},
    )


# ── last_session_energy: source attribute ─────────────────────────────────

def test_last_session_source_is_management_when_mgmt_has_closed():
    cid = "EVB-1"
    data = _tap_data_with_charger(cid)
    data.mgmt_fresh = True
    data.mgmt_last_closed_by_charger = {
        cid: ManagementSession(
            session_id="cs_closed", charger_id=cid, energy_wh=9721,
            start_date="2026-04-22T10:54:33Z",
            end_date="2026-04-22T16:41:03Z",
            location_name="Home", fleet_driver_name="Alice",
        ),
    }

    sensor = LastSessionEnergySensor(_FakeCoord(data=data), cid)
    assert sensor.native_value == pytest.approx(9.721)
    attrs = sensor.extra_state_attributes
    assert attrs["source"] == "management"
    assert attrs["session_id"] == "cs_closed"
    assert attrs["location_name"] == "Home"
    assert attrs["fleet_driver"] == "Alice"


def test_last_session_source_is_public_when_no_mgmt():
    cid = "EVB-1"
    data = _tap_data_with_charger(cid)
    data.recent_sessions = [{
        "id": "cs_public",
        "charger": {"id": cid, "connectorId": "1"},
        "location": {"id": "loc_1"},
        "startedAt": "2026-04-22T10:00:00Z",
        "endedAt":   "2026-04-22T12:00:00Z",
        "wh": 5000,
    }]

    sensor = LastSessionEnergySensor(_FakeCoord(data=data), cid)
    assert sensor.native_value == pytest.approx(5.0)
    attrs = sensor.extra_state_attributes
    assert attrs["source"] == "public"
    assert attrs["session_id"] == "cs_public"


def test_last_session_native_none_when_no_data():
    cid = "EVB-1"
    data = _tap_data_with_charger(cid)
    sensor = LastSessionEnergySensor(_FakeCoord(data=data), cid)
    assert sensor.native_value is None


def test_last_session_falls_back_to_public_when_mgmt_stale():
    cid = "EVB-1"
    data = _tap_data_with_charger(cid)
    # mgmt_fresh=False → mgmt_last_closed returns None
    data.mgmt_fresh = False
    data.mgmt_last_closed_by_charger = {
        cid: ManagementSession(
            session_id="cs_mgmt", charger_id=cid, energy_wh=1000,
        ),
    }
    data.recent_sessions = [{
        "id": "cs_pub",
        "charger": {"id": cid},
        "location": {"id": "loc_1"},
        "startedAt": "2026-04-22T10:00:00Z",
        "endedAt":   "2026-04-22T11:00:00Z",
        "wh": 2500,
    }]

    sensor = LastSessionEnergySensor(_FakeCoord(data=data), cid)
    # Public fallback wins because mgmt is stale.
    assert sensor.native_value == pytest.approx(2.5)
    assert sensor.extra_state_attributes["source"] == "public"


# ── SessionEnergySensor — to_kwh conversion ───────────────────────────────

def test_session_energy_converts_wh_to_kwh():
    assert SessionEnergySensor._to_kwh(9721, "Wh") == pytest.approx(9.721)
    assert SessionEnergySensor._to_kwh(1.5, "kWh") == pytest.approx(1.5)
    assert SessionEnergySensor._to_kwh(None, "Wh") is None
    # Default unit path (missing/unknown → treated as Wh).
    assert SessionEnergySensor._to_kwh(1000, None) == pytest.approx(1.0)


def test_session_energy_zero_when_no_active_session():
    cid = "EVB-1"
    data = _tap_data_with_charger(cid)
    sensor = SessionEnergySensor(_FakeCoord(data=data), cid)
    assert sensor.native_value == 0.0


# ── Advanced-gated creation ──────────────────────────────────────────────

def test_advanced_sensors_constructible_when_mgmt_has_active():
    """The CurrentSession* sensors exist regardless; it's async_setup_entry
    that gates their *registration* on advanced_mode. We verify the
    constructor works and the available property keys off mgmt data."""
    cid = "EVB-1"
    data = _tap_data_with_charger(cid)
    data.mgmt_fresh = True
    now = datetime.now(timezone.utc)
    data.mgmt_active_by_charger = {
        cid: ManagementSession(
            session_id="cs_a", charger_id=cid, energy_wh=2500,
            start_date=now.isoformat().replace("+00:00", "Z"),
            end_date=None,
        ),
    }

    energy = CurrentSessionEnergySensor(_FakeCoord(data=data), cid)
    duration = CurrentSessionDurationSensor(_FakeCoord(data=data), cid)
    assert energy.native_value == pytest.approx(2.5)
    # Duration is non-negative seconds since start.
    assert isinstance(duration.native_value, int)
    assert duration.native_value >= 0


def test_advanced_sensor_unavailable_when_mgmt_not_fresh():
    cid = "EVB-1"
    data = _tap_data_with_charger(cid)
    data.mgmt_fresh = False
    data.mgmt_active_by_charger = {cid: None}

    energy = CurrentSessionEnergySensor(_FakeCoord(data=data), cid)
    # When mgmt isn't fresh, data.mgmt_active() returns None and
    # native_value is None.
    assert energy.native_value is None
    # available reads is_charging_active which is None when mgmt stale;
    # bool(None) is False, so sensor is unavailable.
    assert energy.available is False
