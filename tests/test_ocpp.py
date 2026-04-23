"""Tests for custom_components.tapelectric.ocpp — payload builders."""
from __future__ import annotations

import time

import pytest

from tapelectric.ocpp import build_ocpp_request, reset, set_charging_profile


def test_build_ocpp_request_shape():
    env = build_ocpp_request("Reset", {"type": "Soft"})
    assert "request" in env
    inner = env["request"]
    # Per the in-module comment: PascalCase inside `request`.
    assert inner["Action"] == "Reset"
    assert inner["Data"] == {"type": "Soft"}
    assert "OcppVersion" in inner


def test_build_ocpp_request_ocpp_version_defaults_to_none():
    env = build_ocpp_request("Reset", {"type": "Soft"})
    assert env["request"]["OcppVersion"] is None


def test_build_ocpp_request_explicit_version_is_kept():
    env = build_ocpp_request("Reset", {"type": "Hard"}, ocpp_version="2.0.1")
    assert env["request"]["OcppVersion"] == "2.0.1"


def test_reset_soft_and_hard():
    soft = reset("Soft")
    hard = reset("Hard")
    assert soft["request"]["Action"] == "Reset"
    assert soft["request"]["Data"]["type"] == "Soft"
    assert hard["request"]["Data"]["type"] == "Hard"


def test_set_charging_profile_stop():
    env = set_charging_profile(connector_id=1, limit_amps=0.0)
    req = env["request"]
    assert req["Action"] == "SetChargingProfile"
    data = req["Data"]
    assert data["connectorId"] == 1
    profile = data["csChargingProfiles"]
    assert profile["chargingProfilePurpose"] == "TxDefaultProfile"
    assert profile["chargingProfileKind"] == "Absolute"
    period = profile["chargingSchedule"]["chargingSchedulePeriod"][0]
    assert period["startPeriod"] == 0
    assert period["limit"] == 0.0


def test_set_charging_profile_resume():
    env = set_charging_profile(connector_id=1, limit_amps=16.0)
    period = (
        env["request"]["Data"]["csChargingProfiles"]
           ["chargingSchedule"]["chargingSchedulePeriod"][0]
    )
    assert period["limit"] == 16.0


def test_set_charging_profile_number_phases():
    env = set_charging_profile(
        connector_id=1, limit_amps=10.0, number_phases=3,
    )
    period = (
        env["request"]["Data"]["csChargingProfiles"]
           ["chargingSchedule"]["chargingSchedulePeriod"][0]
    )
    assert period["numberPhases"] == 3


def test_set_charging_profile_omits_number_phases_when_not_given():
    env = set_charging_profile(connector_id=1, limit_amps=10.0)
    period = (
        env["request"]["Data"]["csChargingProfiles"]
           ["chargingSchedule"]["chargingSchedulePeriod"][0]
    )
    assert "numberPhases" not in period


def test_set_charging_profile_auto_profile_id_is_monotonic():
    """Successive calls produce non-decreasing profile IDs.

    The module uses int(time.time()) which can tie within the same
    second; we only assert monotonic non-decreasing, not strictly
    increasing.
    """
    a = set_charging_profile(connector_id=1, limit_amps=10.0)
    b = set_charging_profile(connector_id=1, limit_amps=12.0)
    id_a = a["request"]["Data"]["csChargingProfiles"]["chargingProfileId"]
    id_b = b["request"]["Data"]["csChargingProfiles"]["chargingProfileId"]
    assert id_b >= id_a


def test_set_charging_profile_explicit_profile_id_wins():
    env = set_charging_profile(
        connector_id=1, limit_amps=10.0, profile_id=42,
    )
    assert (
        env["request"]["Data"]["csChargingProfiles"]["chargingProfileId"]
        == 42
    )


def test_set_charging_profile_stack_level_default_zero():
    env = set_charging_profile(connector_id=1, limit_amps=10.0)
    assert (
        env["request"]["Data"]["csChargingProfiles"]["stackLevel"] == 0
    )
