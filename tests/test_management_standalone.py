"""Standalone smoke test for TapManagementClient.

Chains phase-1 Firebase auth with phase-2 management calls. Prompts
interactively for credentials, then exercises:
  A. discover_account_id()
  B. list_role_sessions(take=5)
  C. get_session(<first returned session's id>)

No Home Assistant dependency; runs on Python 3.10+ with aiohttp.

Exit codes: 0 all pass, 1 any failure, 2 empty input, 130 Ctrl-C.
"""
from __future__ import annotations

import asyncio
import getpass
import logging
import pathlib
import sys

_HERE = pathlib.Path(__file__).resolve().parent
_MODULE_DIR = _HERE.parent / "custom_components" / "tapelectric"
sys.path.insert(0, str(_MODULE_DIR))

import aiohttp  # noqa: E402

from auth_firebase import (  # noqa: E402
    TapFirebaseAuth,
    TapFirebaseAuthError,
    TapFirebaseInvalidCredentials,
    TapFirebaseNetworkError,
)
from api_management import (  # noqa: E402
    ManagementSession,
    TapManagementAuthError,
    TapManagementClient,
    TapManagementError,
    TapManagementNetworkError,
    TapManagementNotFound,
)

logging.basicConfig(level=logging.DEBUG, format="%(levelname)s %(name)s: %(message)s")
logging.getLogger("aiohttp").setLevel(logging.INFO)

PASS = "\033[32mPASS\033[0m"
FAIL = "\033[31mFAIL\033[0m"


def _fmt_session(tag: str, s: ManagementSession) -> None:
    print(f"  {tag}:")
    print(f"    session_id        = {s.session_id}")
    print(f"    charger_id        = {s.charger_id}")
    print(f"    is_active         = {s.is_active}")
    print(f"    energy_wh         = {s.energy_wh}")
    print(f"    energy_kwh        = {s.energy_kwh}")
    print(f"    started_at        = {s.started_at.isoformat() if s.started_at else None}")
    print(f"    ended_at          = {s.ended_at.isoformat() if s.ended_at else None}")
    print(f"    duration_seconds  = {s.duration_seconds}")
    print(f"    fleet_driver_name = {s.fleet_driver_name}")
    print(f"    location_name     = {s.location_name}")


async def main() -> int:
    print("Tap Electric — Management API standalone test")
    print("-" * 50)
    email = input("Email: ").strip()
    password = getpass.getpass("Password (hidden): ")
    if not email or not password:
        print(f"{FAIL} — email and password are both required.")
        return 2

    failures: list[str] = []

    async with aiohttp.ClientSession() as session:
        auth = TapFirebaseAuth(session)

        # ── Sign in (phase-1) ───────────────────────────────────────
        print("\n[0] sign_in (phase 1)")
        try:
            tokens = await auth.sign_in(email, password)
        except TapFirebaseInvalidCredentials as err:
            print(f"  {FAIL} — invalid credentials ({err}).")
            return 1
        except TapFirebaseNetworkError as err:
            print(f"  {FAIL} — network error ({err}).")
            return 1
        except TapFirebaseAuthError as err:
            print(f"  {FAIL} — auth error ({err}).")
            return 1
        print(f"  {PASS} — user_id={tokens.user_id} email={tokens.email}")

        client = TapManagementClient(session, auth, tokens)

        # ── A. discover_account_id ──────────────────────────────────
        print("\n[A] discover_account_id")
        try:
            account_id = await client.discover_account_id()
        except TapManagementAuthError as err:
            print(f"  {FAIL} — auth rejected ({err}).")
            return 1
        except TapManagementNetworkError as err:
            print(f"  {FAIL} — network error ({err}).")
            return 1
        except TapManagementError as err:
            print(f"  {FAIL} — {err}")
            failures.append("discover_account_id")
            account_id = None
        else:
            print(f"  account_id = {account_id}")
            if account_id and account_id.startswith("macc_"):
                print(f"  {PASS}")
            else:
                print(f"  {FAIL} — account id does not look like 'macc_...'.")
                failures.append("discover_account_id")

        if account_id is None:
            print("\nCannot continue without account_id. Aborting.")
            return 1

        # ── B. list_role_sessions ───────────────────────────────────
        print("\n[B] list_role_sessions(take=5)")
        try:
            sessions = await client.list_role_sessions(take=5)
        except TapManagementError as err:
            print(f"  {FAIL} — {err}")
            failures.append("list_role_sessions")
            sessions = []
        else:
            print(f"  received {len(sessions)} session(s)")
            if sessions:
                print(f"  {PASS}")
            else:
                print(f"  {FAIL} — empty list; expected at least one "
                      f"historical session on this account.")
                failures.append("list_role_sessions")

        active = next((s for s in sessions if s.is_active), None)
        closed_sorted = sorted(
            (s for s in sessions if not s.is_active),
            key=lambda s: s.ended_at or s.started_at or s.created_at
                           or __import__("datetime").datetime.min.replace(
                               tzinfo=__import__("datetime").timezone.utc),
            reverse=True,
        )
        most_recent_closed = closed_sorted[0] if closed_sorted else None

        if active is not None:
            _fmt_session("active session", active)
        else:
            print("  (no active session right now — fine.)")
        if most_recent_closed is not None:
            _fmt_session("most recent closed session", most_recent_closed)
        else:
            print("  (no closed session in the first 5 — fine.)")

        # Pick a session to dive into for step C.
        probe_session = active or most_recent_closed
        if probe_session is None and sessions:
            probe_session = sessions[0]

        # ── C. get_session ──────────────────────────────────────────
        if probe_session and probe_session.session_id:
            print(f"\n[C] get_session({probe_session.session_id})")
            try:
                one = await client.get_session(probe_session.session_id)
            except TapManagementNotFound:
                print(f"  {FAIL} — 404; list returned a session id that "
                      f"detail endpoint doesn't recognise.")
                failures.append("get_session")
            except TapManagementError as err:
                print(f"  {FAIL} — {err}")
                failures.append("get_session")
            else:
                _fmt_session("detail", one)
                # Detail-only fields — print whatever came through to
                # confirm the nested mapping lit them up.
                print("  detail-only fields:")
                print(f"    evse_id         = {one.evse_id}")
                print(f"    zip_code        = {one.zip_code}")
                print(f"    country         = {one.country}")
                print(f"    latitude        = {one.latitude}")
                print(f"    longitude       = {one.longitude}")
                print(f"    transaction_id  = {one.transaction_id}")
                print(f"    retail_tariff   = "
                      f"{'present' if one.retail_tariff else 'None'}")
                # Lenient comparison: the detail endpoint echoes fewer
                # fields than the list (no fleet_driver_name,
                # masked_card_uid, etc.). Consider the match "good
                # enough" if charger_id and energy_wh agree.
                charger_ok = (
                    one.charger_id is None
                    or one.charger_id == probe_session.charger_id
                )
                energy_ok = (
                    one.energy_wh is None
                    or probe_session.energy_wh is None
                    or float(one.energy_wh) == float(probe_session.energy_wh)
                )
                if charger_ok and energy_ok:
                    print(f"  {PASS} — charger_id and energy_wh consistent "
                          f"with list entry.")
                else:
                    print(f"  {FAIL} — detail diverges from list "
                          f"(charger_id_ok={charger_ok}, "
                          f"energy_ok={energy_ok}).")
                    failures.append("get_session")
        else:
            print("\n[C] get_session — skipped (no session to probe).")

        await client.close()

    print()
    print("-" * 50)
    if failures:
        print(f"{FAIL}: {', '.join(failures)}")
        return 1
    print("All management tests passed")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()))
    except KeyboardInterrupt:
        print("\ninterrupted.")
        sys.exit(130)
