"""Tests for custom_components.tapelectric.ocpp — payload builders.

Validates the public /chargers/{id}/ocpp envelope per Tap's official
OpenAPI spec: flat camelCase, ocppVersion="ocpp1.6", data as JSON-string.
"""
from __future__ import annotations

import json
import time

import pytest

from tapelectric.ocpp import (
    build_ocpp_request,
    remote_start_transaction,
    remote_stop_transaction,
    reset,
    set_charging_profile,
    unlock_connector,
)


# ── Envelope shape ──────────────────────────────────────────────────────


def test_build_ocpp_request_shape():
    """Envelope is flat (no `request` wrapper), camelCase keys."""
    env = build_ocpp_request("Reset", {"type": "Soft"})
    assert "request" not in env
    assert env["action"] == "Reset"
    assert env["ocppVersion"] == "ocpp1.6"
    assert "data" in env


def test_build_ocpp_request_data_is_json_string():
    """`data` must be a JSON-encoded string, not a nested object —
    sending it as a dict triggers HTTP 400 from Tap's validator."""
    env = build_ocpp_request("Reset", {"type": "Soft"})
    assert isinstance(env["data"], str)
    parsed = json.loads(env["data"])
    assert parsed == {"type": "Soft"}


def test_build_ocpp_request_ocpp_version_default_is_ocpp16():
    """Default ocppVersion is the string 'ocpp1.6' (not null, not '1.6').
    Null triggers '$.ocppVersion unknown' from the server."""
    env = build_ocpp_request("Reset", {"type": "Soft"})
    assert env["ocppVersion"] == "ocpp1.6"


def test_build_ocpp_request_explicit_version_is_kept():
    env = build_ocpp_request("Reset", {"type": "Hard"}, ocpp_version="ocpp2.0.1")
    assert env["ocppVersion"] == "ocpp2.0.1"


# ── Reset ───────────────────────────────────────────────────────────────


def test_reset_soft_and_hard():
    soft = reset("Soft")
    hard = reset("Hard")
    assert soft["action"] == "Reset"
    assert json.loads(soft["data"])["type"] == "Soft"
    assert json.loads(hard["data"])["type"] == "Hard"


# ── SetChargingProfile ──────────────────────────────────────────────────


def test_set_charging_profile_stop():
    env = set_charging_profile(connector_id=1, limit_amps=0.0)
    assert env["action"] == "SetChargingProfile"
    data = json.loads(env["data"])
    assert data["connectorId"] == 1
    profile = data["csChargingProfiles"]
    assert profile["chargingProfilePurpose"] == "TxDefaultProfile"
    assert profile["chargingProfileKind"] == "Absolute"
    period = profile["chargingSchedule"]["chargingSchedulePeriod"][0]
    assert period["startPeriod"] == 0
    assert period["limit"] == 0.0


def test_set_charging_profile_resume():
    env = set_charging_profile(connector_id=1, limit_amps=16.0)
    data = json.loads(env["data"])
    period = (
        data["csChargingProfiles"]["chargingSchedule"]["chargingSchedulePeriod"][0]
    )
    assert period["limit"] == 16.0


def test_set_charging_profile_number_phases():
    env = set_charging_profile(connector_id=1, limit_amps=10.0, number_phases=3)
    period = json.loads(env["data"])["csChargingProfiles"][
        "chargingSchedule"
    ]["chargingSchedulePeriod"][0]
    assert period["numberPhases"] == 3


def test_set_charging_profile_omits_number_phases_when_not_given():
    env = set_charging_profile(connector_id=1, limit_amps=10.0)
    period = json.loads(env["data"])["csChargingProfiles"][
        "chargingSchedule"
    ]["chargingSchedulePeriod"][0]
    assert "numberPhases" not in period


def test_set_charging_profile_auto_profile_id_is_monotonic():
    """Successive calls produce non-decreasing profile IDs.

    The module uses int(time.time()) which can tie within the same
    second; we only assert monotonic non-decreasing, not strictly
    increasing.
    """
    a = set_charging_profile(connector_id=1, limit_amps=10.0)
    b = set_charging_profile(connector_id=1, limit_amps=12.0)
    id_a = json.loads(a["data"])["csChargingProfiles"]["chargingProfileId"]
    id_b = json.loads(b["data"])["csChargingProfiles"]["chargingProfileId"]
    assert id_b >= id_a


def test_set_charging_profile_explicit_profile_id_wins():
    env = set_charging_profile(connector_id=1, limit_amps=10.0, profile_id=42)
    assert (
        json.loads(env["data"])["csChargingProfiles"]["chargingProfileId"] == 42
    )


def test_set_charging_profile_stack_level_default_zero():
    env = set_charging_profile(connector_id=1, limit_amps=10.0)
    assert json.loads(env["data"])["csChargingProfiles"]["stackLevel"] == 0


# ── RemoteStartTransaction ──────────────────────────────────────────────


def test_remote_start_transaction_minimal():
    """idTag is the RFID chip UID. Connector defaults to 'charger picks'."""
    env = remote_start_transaction("2BFB3974")
    assert env["action"] == "RemoteStartTransaction"
    data = json.loads(env["data"])
    assert data["idTag"] == "2BFB3974"
    assert "connectorId" not in data


def test_remote_start_transaction_with_connector():
    env = remote_start_transaction("2BFB3974", connector_id=1)
    data = json.loads(env["data"])
    assert data["connectorId"] == 1


# ── RemoteStopTransaction ───────────────────────────────────────────────


def test_remote_stop_transaction():
    """transactionId comes from the running session's StartTransaction OCPP
    message — fetch via GET /chargers/{id}/ocpp?action=StartTransaction&limit=1."""
    env = remote_stop_transaction(42)
    assert env["action"] == "RemoteStopTransaction"
    data = json.loads(env["data"])
    assert data["transactionId"] == 42


def test_remote_stop_transaction_coerces_str_to_int():
    """Defensive: accept string-shaped ids (some Tap fixtures emit them as
    strings) and coerce to int for the OCPP payload."""
    env = remote_stop_transaction("42")  # type: ignore[arg-type]
    data = json.loads(env["data"])
    assert data["transactionId"] == 42
    assert isinstance(data["transactionId"], int)


# ── UnlockConnector ─────────────────────────────────────────────────────


def test_unlock_connector_default():
    env = unlock_connector()
    assert env["action"] == "UnlockConnector"
    data = json.loads(env["data"])
    assert data["connectorId"] == 1


def test_unlock_connector_explicit():
    env = unlock_connector(connector_id=2)
    data = json.loads(env["data"])
    assert data["connectorId"] == 2
