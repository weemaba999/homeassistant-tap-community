"""OCPP 1.6 payload builders for Tap's public /chargers/{id}/ocpp endpoint.

Tap's public passthrough accepts POST /chargers/{id}/ocpp with:
  { "action": OcppAction, "ocppVersion": "ocpp1.6", "data": "<JSON-string>" }

That is: flat envelope (no `request` wrapper), camelCase keys,
`ocppVersion` MUST be the string "ocpp1.6" (not null), and `data` MUST
be a JSON-encoded string (not a nested object). This matches Tap's
official OpenAPI spec at https://api.tapelectric.app/openapi/Tap%20API.json.

Verified live against a Ratio io6 (firmware 3.13.2) on 2026-05-12:
  - Reset.Soft                  → status Accepted, Platform-initiated
  - RemoteStopTransaction       → status Accepted
  - SetChargingProfile          → status Accepted
  - UnlockConnector             → status Accepted
  - RemoteStartTransaction      → status Accepted (PREPARING → CHARGING)

History: an earlier reverse-engineering attempt (April 2026) concluded
that this endpoint required a `{"request": {"Action": ..., "Data": {...}}}`
PascalCase wrapper. That hypothesis was wrong — the wrapper variant
also failed with HTTP 400 ("The Data field is required"), which led
to v1.1.0 using the management API instead. The flat camelCase envelope
documented here is what the public endpoint actually accepts, and what
this module now produces.
"""
from __future__ import annotations

import json
import time
from typing import Any

from .const import (
    OCPP_ACTION_REMOTE_START_TRANSACTION,
    OCPP_ACTION_REMOTE_STOP_TRANSACTION,
    OCPP_ACTION_RESET,
    OCPP_ACTION_SET_CHARGING_PROFILE,
    OCPP_ACTION_UNLOCK_CONNECTOR,
    OCPP_VERSION_DEFAULT,
)


def build_ocpp_request(
    action: str,
    data: dict[str, Any],
    ocpp_version: str = OCPP_VERSION_DEFAULT,
) -> dict[str, Any]:
    """Wrap an OCPP payload in Tap's public-endpoint envelope.

    The server requires camelCase keys at the root, no wrapper, and
    `data` as a JSON-encoded string. `ocppVersion` must be a non-null
    string ("ocpp1.6") — null triggers "$.ocppVersion unknown" from the
    validator.
    """
    return {
        "action": action,
        "ocppVersion": ocpp_version,
        "data": json.dumps(data),
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


def remote_start_transaction(
    id_tag: str,
    *,
    connector_id: int | None = None,
) -> dict[str, Any]:
    """Build a RemoteStartTransaction.req payload.

    Args:
      id_tag:        The RFID chip UID linked to a configured pas
                     (e.g. "2BFB3974"). NOT the system-internal
                     `ET_*`-prefixed identifier visible in Tap UI /
                     message log — that one triggers Authorize Invalid.
                     For new passes, obtain the chip UID from Tap
                     support email — it is not exposed by the public API.
      connector_id:  Optional. Omit to let the charger pick (typically
                     connector 1 on single-outlet chargers).

    Verified live on Ratio io6 (firmware 3.13.2) — charger advances
    PREPARING → CHARGING after a successful RemoteStart.
    """
    data: dict[str, Any] = {"idTag": id_tag}
    if connector_id is not None:
        data["connectorId"] = connector_id
    return build_ocpp_request(OCPP_ACTION_REMOTE_START_TRANSACTION, data)


def remote_stop_transaction(transaction_id: int) -> dict[str, Any]:
    """Build a RemoteStopTransaction.req payload.

    Args:
      transaction_id: OCPP transaction id of the running session.
                      Fetch from GET /chargers/{id}/ocpp?action=
                      StartTransaction&limit=1 — the most-recent
                      StartTransaction message contains it.

    Graceful end-of-session — does not reboot the charger or unlock
    the cable.
    """
    return build_ocpp_request(
        OCPP_ACTION_REMOTE_STOP_TRANSACTION,
        {"transactionId": int(transaction_id)},
    )


def unlock_connector(connector_id: int = 1) -> dict[str, Any]:
    """Build an UnlockConnector.req payload.

    Forces the charger to release the cable from the connector. Use
    when a session has ended but the cable is still mechanically
    locked. Tap forwards this as Platform-initiated to the charger;
    actual unlock behavior is charger-firmware-dependent.
    """
    return build_ocpp_request(
        OCPP_ACTION_UNLOCK_CONNECTOR,
        {"connectorId": int(connector_id)},
    )
