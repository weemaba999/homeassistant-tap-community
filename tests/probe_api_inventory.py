"""Exercise every read endpoint and dump raw JSON to fixtures/.

Runs against the live Tap Electric API so we can build a field-level
inventory for phase-4 planning. Non-mutating: only GETs.

Usage:
    # Loads sk_ key from ~/tapelectric_ha/.tap.env automatically.
    # Firebase creds either as env vars or interactive prompt:
    #
    #   TAP_EMAIL=you@x.com TAP_PASSWORD=hunter2 \\
    #       python3 tests/probe_api_inventory.py
    #
    # Or just run it and it'll prompt.
    #
    # Set SKIP_MGMT=1 to probe only the public API.

Fixtures land in tests/fixtures/api_inventory/. Secrets (sk_ key,
Firebase idToken, refresh token, email, password) are never written.
"""
from __future__ import annotations

import asyncio
import getpass
import json
import os
import pathlib
import sys
from typing import Any

_HERE = pathlib.Path(__file__).resolve().parent
_REPO = _HERE.parent
_MODULE_DIR = _REPO / "custom_components" / "tapelectric"
sys.path.insert(0, str(_MODULE_DIR))

import aiohttp  # noqa: E402

from auth_firebase import (  # noqa: E402
    TapFirebaseAuth,
    TapFirebaseAuthError,
    TapFirebaseInvalidCredentials,
)
from api_management import TapManagementClient, TapManagementError  # noqa: E402

FIXTURES = _REPO / "tests" / "fixtures" / "api_inventory"
ENV_FILE = _REPO / ".tap.env"

PUBLIC_BASE = "https://api.tapelectric.app/api/v1"
ACTIVE_SESSION_ID = "cs_9d10ef9f319548f4949cf741b913fc95"   # currently live
CLOSED_SESSION_ID = "cs_7f45d8e483ea4ba3b75723ddb7ebd15d"   # already closed


def _load_sk_key() -> str:
    env_key = os.environ.get("TAP_API_KEY")
    if env_key:
        return env_key.strip()
    if not ENV_FILE.exists():
        raise SystemExit(f"No TAP_API_KEY in env and {ENV_FILE} missing")
    for line in ENV_FILE.read_text().splitlines():
        line = line.strip()
        if line.startswith("#") or "=" not in line:
            continue
        line = line.removeprefix("export ").strip()
        k, _, v = line.partition("=")
        if k.strip() == "TAP_API_KEY":
            return v.strip().strip("'\"")
    raise SystemExit(f"TAP_API_KEY not found in {ENV_FILE}")


def _write(name: str, payload: Any, meta: dict[str, Any] | None = None) -> None:
    FIXTURES.mkdir(parents=True, exist_ok=True)
    out = {"_meta": meta or {}, "data": payload}
    path = FIXTURES / name
    path.write_text(json.dumps(out, indent=2, sort_keys=False))
    size = path.stat().st_size
    print(f"  wrote {name}  ({size} bytes)")


async def _get_public(
    session: aiohttp.ClientSession, sk_key: str,
    path: str, params: dict | None = None,
) -> tuple[int, Any]:
    url = f"{PUBLIC_BASE}{path}"
    headers = {
        "Accept": "application/json",
        "X-Api-Key": sk_key,
    }
    async with session.get(url, headers=headers, params=params, timeout=30) as resp:
        status = resp.status
        text = await resp.text()
        try:
            data = json.loads(text) if text else None
        except json.JSONDecodeError:
            data = {"_raw_text": text[:2000]}
    return status, data


async def probe_public(session: aiohttp.ClientSession, sk_key: str) -> None:
    print("\n=== PUBLIC API (sk_ key) ===")

    probes: list[tuple[str, str, dict | None, str]] = [
        ("chargers_list",
         "/chargers", None,
         "GET /chargers — list all chargers visible to this key"),
        ("charger_sessions_list",
         "/charger-sessions", {"limit": 50},
         "GET /charger-sessions?limit=50"),
        ("charger_session_meter_data_active",
         f"/charger-sessions/{ACTIVE_SESSION_ID}/session-meter-data",
         {"limit": 100},
         "meter data for currently active session"),
        ("charger_session_meter_data_closed",
         f"/charger-sessions/{CLOSED_SESSION_ID}/session-meter-data",
         {"limit": 100},
         "meter data for a closed session"),
        ("charger_session_detail_active",
         f"/charger-sessions/{ACTIVE_SESSION_ID}", None,
         "GET /charger-sessions/{id} — test if singular detail exists"),
        ("charger_session_detail_closed",
         f"/charger-sessions/{CLOSED_SESSION_ID}", None,
         "GET /charger-sessions/{id} — on a closed session"),
        ("locations_list",
         "/locations", None,
         "GET /locations"),
        ("tariffs_list_bare",
         "/tariffs", None,
         "GET /tariffs (no params — may 4xx on this scope)"),
        ("webhooks_list",
         "/webhooks", None,
         "GET /webhooks — registered webhook subscriptions"),
    ]

    # charger-scoped probes — done only after we know an id.
    status, chargers = await _get_public(session, sk_key, "/chargers")
    _write("chargers_list.json", chargers, meta={
        "endpoint": "GET /api/v1/chargers",
        "status": status,
        "description": "list of chargers visible to this sk_ key",
    })
    if status != 200:
        print(f"  !! chargers list returned {status}; charger-scoped probes may fail")

    charger_id = None
    if isinstance(chargers, list) and chargers:
        charger_id = chargers[0].get("id")
    elif isinstance(chargers, dict):
        items = chargers.get("items") or []
        if items:
            charger_id = items[0].get("id")

    for name, path, params, desc in probes[1:]:
        print(f"\n-- {name}: {desc}")
        status, data = await _get_public(session, sk_key, path, params)
        print(f"   HTTP {status}")
        _write(f"{name}.json", data, meta={
            "endpoint": f"GET /api/v1{path}",
            "params": params,
            "status": status,
            "description": desc,
        })

    # Charger-scoped endpoints.
    if charger_id:
        print(f"\n-- charger detail ({charger_id})")
        status, data = await _get_public(session, sk_key, f"/chargers/{charger_id}")
        _write("charger_detail.json", data, meta={
            "endpoint": f"GET /api/v1/chargers/{{charger_id}}",
            "charger_id_used": charger_id,
            "status": status,
            "description": "single charger detail",
        })
        print(f"   HTTP {status}")

        print(f"\n-- charger ocpp messages ({charger_id})")
        status, data = await _get_public(
            session, sk_key, f"/chargers/{charger_id}/ocpp",
            {"limit": 20},
        )
        _write("charger_ocpp_messages.json", data, meta={
            "endpoint": f"GET /api/v1/chargers/{{charger_id}}/ocpp",
            "params": {"limit": 20},
            "charger_id_used": charger_id,
            "status": status,
            "description": "historic OCPP messages for this charger",
        })
        print(f"   HTTP {status}")
    else:
        print("   (no charger_id available — skipping per-charger probes)")

    # "GET /chargers/{id}/reset" — user asked we document only, NOT call.
    # Recorded as meta for the inventory.
    _write("_reset_not_called.json", None, meta={
        "endpoint": "POST /api/v1/chargers/{charger_id}/reset",
        "status": "not_called",
        "description": (
            "Reset is a mutating endpoint (POST). The task explicitly "
            "says document only, do not invoke. Docs in inventory."
        ),
    })


async def probe_management(
    session: aiohttp.ClientSession,
    email: str, password: str,
) -> None:
    print("\n=== MANAGEMENT API (Firebase JWT) ===")
    auth = TapFirebaseAuth(session)
    try:
        tokens = await auth.sign_in(email, password)
    except TapFirebaseInvalidCredentials:
        print("  !! invalid Firebase credentials — skipping management probes")
        return
    except TapFirebaseAuthError as err:
        print(f"  !! Firebase auth failed: {err} — skipping management probes")
        return

    client = TapManagementClient(session, auth, tokens)

    # /accounts — raw via client internals so we capture the full body.
    # We replicate _request without discarding the wrapper fields.
    from api_management import MGMT_BASE_URL, STATIC_HEADERS, USER_AGENT

    def _mgmt_headers(account_id: str | None) -> dict[str, str]:
        h = {
            **STATIC_HEADERS,
            "User-Agent": USER_AGENT,
            "Authorization": f"Bearer {tokens.id_token}",
        }
        if account_id:
            h["X-Account-Id"] = account_id
        return h

    async def _get_mgmt(path: str, params: dict | None = None,
                       account_id: str | None = None) -> tuple[int, Any]:
        async with session.get(
            f"{MGMT_BASE_URL}{path}",
            params=params, headers=_mgmt_headers(account_id), timeout=30,
        ) as resp:
            status = resp.status
            text = await resp.text()
            try:
                data = json.loads(text) if text else None
            except json.JSONDecodeError:
                data = {"_raw_text": text[:2000]}
        return status, data

    print("\n-- /accounts (no X-Account-Id yet)")
    status, accounts = await _get_mgmt(
        "/accounts",
        {"take": 5, "offset": 0, "includeConnectedAccounts": "true"},
    )
    print(f"   HTTP {status}")
    _write("mgmt_accounts.json", accounts, meta={
        "endpoint": f"GET {MGMT_BASE_URL}/accounts",
        "params": {"take": 5, "offset": 0, "includeConnectedAccounts": "true"},
        "status": status,
        "description": "user's accounts — returns macc_... ids + metadata",
    })

    account_id: str | None = None
    if isinstance(accounts, list) and accounts:
        account_id = (accounts[0] or {}).get("id")
    elif isinstance(accounts, dict):
        items = accounts.get("items") or []
        if items:
            account_id = (items[0] or {}).get("id")

    if not account_id:
        print("   !! no account_id discovered; remaining mgmt probes skipped")
        return
    print(f"   resolved X-Account-Id={account_id}")

    # /role-sessions with role=cpo
    print("\n-- /role-sessions?role=cpo&take=20")
    status, data = await _get_mgmt(
        "/role-sessions",
        {"role": "cpo", "offset": 0, "take": 20},
        account_id=account_id,
    )
    print(f"   HTTP {status}")
    _write("mgmt_role_sessions_cpo.json", data, meta={
        "endpoint": f"GET {MGMT_BASE_URL}/role-sessions",
        "params": {"role": "cpo", "offset": 0, "take": 20},
        "status": status,
        "description": "historical + active sessions for role=cpo",
    })

    # /sessions/{id} — known active + known closed
    print(f"\n-- /sessions/{ACTIVE_SESSION_ID} (active)")
    status, data = await _get_mgmt(
        f"/sessions/{ACTIVE_SESSION_ID}",
        account_id=account_id,
    )
    print(f"   HTTP {status}")
    _write("mgmt_session_detail_active.json", data, meta={
        "endpoint": f"GET {MGMT_BASE_URL}/sessions/{{session_id}}",
        "session_id_used": ACTIVE_SESSION_ID,
        "status": status,
        "description": "detail for currently-active session",
    })

    print(f"\n-- /sessions/{CLOSED_SESSION_ID} (closed)")
    status, data = await _get_mgmt(
        f"/sessions/{CLOSED_SESSION_ID}",
        account_id=account_id,
    )
    print(f"   HTTP {status}")
    _write("mgmt_session_detail_closed.json", data, meta={
        "endpoint": f"GET {MGMT_BASE_URL}/sessions/{{session_id}}",
        "session_id_used": CLOSED_SESSION_ID,
        "status": status,
        "description": "detail for already-closed session",
    })

    # Role variations — user mentioned role=cpo; probe driver too
    # in case it returns a different shape. Cheap and additive.
    print("\n-- /role-sessions?role=driver&take=5 (exploratory)")
    status, data = await _get_mgmt(
        "/role-sessions",
        {"role": "driver", "offset": 0, "take": 5},
        account_id=account_id,
    )
    print(f"   HTTP {status}")
    _write("mgmt_role_sessions_driver.json", data, meta={
        "endpoint": f"GET {MGMT_BASE_URL}/role-sessions",
        "params": {"role": "driver", "offset": 0, "take": 5},
        "status": status,
        "description": "exploratory — does role=driver return different fields?",
    })

    await client.close()


async def main() -> int:
    sk_key = _load_sk_key()
    FIXTURES.mkdir(parents=True, exist_ok=True)

    skip_mgmt = os.environ.get("SKIP_MGMT") == "1"
    email = os.environ.get("TAP_EMAIL")
    password = os.environ.get("TAP_PASSWORD")

    async with aiohttp.ClientSession() as session:
        await probe_public(session, sk_key)

        if skip_mgmt:
            print("\n=== MANAGEMENT API skipped (SKIP_MGMT=1) ===")
            return 0
        if not email or not password:
            if sys.stdin.isatty():
                print()
                email = input("Firebase email: ").strip()
                password = getpass.getpass("Firebase password (hidden): ")
            else:
                print("\n=== MANAGEMENT API skipped — no creds, non-interactive ===")
                return 0
        if not email or not password:
            print("\n=== MANAGEMENT API skipped — empty creds ===")
            return 0
        await probe_management(session, email, password)

    print(f"\nDone. Fixtures in {FIXTURES}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()))
    except KeyboardInterrupt:
        print("\ninterrupted.")
        sys.exit(130)
