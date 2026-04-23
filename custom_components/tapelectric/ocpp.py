"""OCPP 1.6 payload builders for the Tap message-passthrough endpoint.

Tap accepts POST /api/v1/chargers/{id}/ocpp with
  { "request": { "action": OcppAction, "ocppVersion": OcppVersion|null,
                 "data": object } }

The Reference documented a flat envelope with `data` as a string —
both wrong. Verified against the live server via HA 400-error
bodies; see ocpp_ha_verification notes for history.

Only two OcppAction values are accepted: SetChargingProfile, Reset.
RemoteStartTransaction / RemoteStopTransaction are NOT supported by Tap
— use set_charging_profile(limit=0) for "stop" and a non-zero limit for
"resume". The charge-session itself is driver-initiated (RFID / app).
"""
from __future__ import annotations

import time
from typing import Any

from .const import (
    OCPP_ACTION_RESET,
    OCPP_ACTION_SET_CHARGING_PROFILE,
    OCPP_VERSION_DEFAULT,
)


def build_ocpp_request(
    action: str,
    data: dict[str, Any],
    ocpp_version: str | None = OCPP_VERSION_DEFAULT,
) -> dict[str, Any]:
    """Wrap an OCPP payload in Tap's OcppMessageRequest envelope.

    The server requires PascalCase keys inside the `request` object
    (Action, OcppVersion, Data) — camelCase binds to null and
    triggers "The Data field is required" from the validator.
    Envelope key `request` stays camelCase (that binds fine).
    """
    return {
        "request": {
            "Action": action,
            "OcppVersion": ocpp_version,
            "Data": data,
        }
    }


def set_charging_profile(
    *,
    connector_id: int = 1,
    limit_amps: float,
    profile_id: int | None = None,
    stack_level: int = 0,
    number_phases: int | None = None,
) -> dict[str, Any]:
    """Build a SetChargingProfile.req payload.

    TxDefaultProfile with Absolute kind and a single unbounded period:
    simplest possible profile that takes effect immediately and stays
    active for the remainder of any transaction on this connector.

    Args:
      connector_id:  Target connector (0 = charger-wide, ≥1 = specific).
      limit_amps:    0.0 = effective stop; >0 = amps per phase.
      profile_id:    Unique ID; defaults to a monotonic timestamp-derived
                     value so successive writes always win (OCPP rule:
                     new profile replaces old with same stackLevel).
      stack_level:   Higher wins. 0 is fine for single-author scenarios.
      number_phases: 1 or 3; omit for charger-decides.
    """
    if profile_id is None:
        profile_id = int(time.time())

    period: dict[str, Any] = {"startPeriod": 0, "limit": float(limit_amps)}
    if number_phases is not None:
        period["numberPhases"] = number_phases

    return build_ocpp_request(
        OCPP_ACTION_SET_CHARGING_PROFILE,
        {
            "connectorId": connector_id,
            "csChargingProfiles": {
                "chargingProfileId": profile_id,
                "stackLevel": stack_level,
                "chargingProfilePurpose": "TxDefaultProfile",
                "chargingProfileKind": "Absolute",
                "chargingSchedule": {
                    "chargingRateUnit": "A",
                    "chargingSchedulePeriod": [period],
                },
            },
        },
    )


def reset(reset_type: str = "Soft") -> dict[str, Any]:
    """Build a Reset.req payload. reset_type: 'Soft' | 'Hard'."""
    return build_ocpp_request(OCPP_ACTION_RESET, {"type": reset_type})
