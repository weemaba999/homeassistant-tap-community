"""Tests for binary_sensor.py — online, plug, charging, fault logic."""
from __future__ import annotations

import pytest

from tapelectric.binary_sensor import (
    ChargingBinarySensor,
    FaultBinarySensor,
    OnlineBinarySensor,
    PlugConnectedBinarySensor,
)
from tapelectric.coordinator import TapData


class _FakeCoord:
    def __init__(self, data):
        self.data = data


def _data(charger):
    return TapData(chargers=[charger])


def test_online_true_if_any_connector_real():
    c = {
        "id": "A",
        "status": "UNAVAILABLE",  # unreliable — connector wins
        "connectors": [{"id": "1", "status": "AVAILABLE"}],
    }
    s = OnlineBinarySensor(_FakeCoord(_data(c)), "A")
    assert s.is_on is True


def test_online_false_when_all_unavailable():
    c = {
        "id": "A",
        "status": "AVAILABLE",
        "connectors": [{"id": "1", "status": "UNAVAILABLE"}],
    }
    s = OnlineBinarySensor(_FakeCoord(_data(c)), "A")
    assert s.is_on is False


def test_fault_detects_charger_level_fault():
    c = {
        "id": "A",
        "status": "FAULTED",
        "connectors": [{"id": "1", "status": "AVAILABLE"}],
    }
    s = FaultBinarySensor(_FakeCoord(_data(c)), "A")
    assert s.is_on is True


def test_fault_detects_connector_level_fault():
    c = {
        "id": "A",
        "status": "AVAILABLE",
        "connectors": [{"id": "1", "status": "FAULTED"}],
    }
    s = FaultBinarySensor(_FakeCoord(_data(c)), "A")
    assert s.is_on is True
    assert s.extra_state_attributes["faulted_connectors"] == ["1"]


def test_fault_clear_when_healthy():
    c = {
        "id": "A", "status": "AVAILABLE",
        "connectors": [{"id": "1", "status": "AVAILABLE"}],
    }
    s = FaultBinarySensor(_FakeCoord(_data(c)), "A")
    assert s.is_on is False


@pytest.mark.parametrize("status,expected", [
    ("CHARGING",       True),
    ("SUSPENDEDEV",    True),
    ("SUSPENDEDEVSE",  True),
    ("PREPARING",      True),
    ("AVAILABLE",      False),
    ("UNAVAILABLE",    False),
    ("FAULTED",        False),
])
def test_plug_connected_various_connector_states(status, expected):
    c = {
        "id": "A",
        "connectors": [{"id": "1", "status": status}],
    }
    s = PlugConnectedBinarySensor(_FakeCoord(_data(c)), "A", 1)
    assert s.is_on is expected


def test_charging_prefers_mgmt_over_connector():
    """When mgmt says charging is active, trust it over a stale OCPP connector."""
    c = {
        "id": "A",
        "connectors": [{"id": "1", "status": "AVAILABLE"}],  # stale
    }
    data = _data(c)
    data.mgmt_fresh = True
    from tapelectric.api_management import ManagementSession
    data.mgmt_active_by_charger = {"A": ManagementSession(
        session_id="cs_live", charger_id="A", energy_wh=100,
    )}
    s = ChargingBinarySensor(_FakeCoord(data), "A", 1)
    assert s.is_on is True


def test_charging_falls_back_to_connector_when_no_mgmt():
    c = {
        "id": "A",
        "connectors": [{"id": "1", "status": "CHARGING"}],
    }
    s = ChargingBinarySensor(_FakeCoord(_data(c)), "A", 1)
    assert s.is_on is True


def test_charging_source_attribute_reflects_data_provenance():
    # No mgmt data → source=public
    c = {"id": "A", "connectors": [{"id": "1", "status": "CHARGING"}]}
    s = ChargingBinarySensor(_FakeCoord(_data(c)), "A", 1)
    assert s.extra_state_attributes["source"] == "public"
