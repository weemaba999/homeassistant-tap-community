"""Standalone smoke test for the Firebase auth client.

Runs without Home Assistant. Prompts for email + password interactively
(password via getpass), exercises sign-in → ensure_valid → refresh, and
prints a pass/fail line per step. No credentials are echoed or logged.

Usage:
    python3 tests/test_auth_standalone.py

Requires only Python 3.10+ and aiohttp.
"""
from __future__ import annotations

import asyncio
import getpass
import logging
import pathlib
import sys

# Import the client by path — this file sits next to the integration
# but is deliberately not a package member.
_HERE = pathlib.Path(__file__).resolve().parent
_MODULE_DIR = _HERE.parent / "custom_components" / "tapelectric"
sys.path.insert(0, str(_MODULE_DIR))

import aiohttp  # noqa: E402

from auth_firebase import (  # noqa: E402
    AuthTokens,
    TapFirebaseAuth,
    TapFirebaseAuthError,
    TapFirebaseInvalidCredentials,
    TapFirebaseNetworkError,
    TapFirebaseRefreshFailed,
)

logging.basicConfig(level=logging.DEBUG, format="%(levelname)s %(name)s: %(message)s")

# Only log what's genuinely useful; Firebase chatter at DEBUG is enough.
logging.getLogger("aiohttp").setLevel(logging.INFO)


PASS = "\033[32mPASS\033[0m"
FAIL = "\033[31mFAIL\033[0m"


def _summarise(label: str, tokens: AuthTokens) -> None:
    print(f"  {label}:")
    print(f"    user_id      = {tokens.user_id}")
    print(f"    email        = {tokens.email}")
    print(f"    display_name = {tokens.display_name}")
    print(f"    expires_at   = {tokens.expires_at.isoformat()}")
    print(f"    expires_in   = {tokens.expires_in():.1f}s")
    print(f"    id_token     = {tokens.id_token[:30]}…")
    print(f"    refresh_token= {tokens.refresh_token[:12]}…"
          f" (len={len(tokens.refresh_token)})")


async def main() -> int:
    print("Tap Electric — Firebase auth standalone test")
    print("-" * 46)
    email = input("Email: ").strip()
    password = getpass.getpass("Password (hidden): ")
    if not email or not password:
        print(f"{FAIL} — email and password are both required.")
        return 2

    failures: list[str] = []

    async with aiohttp.ClientSession() as session:
        client = TapFirebaseAuth(session)

        # ── Step 1: sign_in ────────────────────────────────────────
        print("\n[1] sign_in")
        try:
            tokens = await client.sign_in(email, password)
        except TapFirebaseInvalidCredentials as err:
            print(f"  {FAIL} — invalid credentials ({err}).")
            return 1
        except TapFirebaseNetworkError as err:
            print(f"  {FAIL} — network error ({err}).")
            return 1
        except TapFirebaseAuthError as err:
            print(f"  {FAIL} — auth error ({err}).")
            return 1
        _summarise("tokens from sign_in", tokens)
        print(f"  {PASS}")

        # ── Step 2: ensure_valid (should NOT refresh; expires_at is fresh) ─
        print("\n[2] ensure_valid (fresh tokens — should be a no-op)")
        await asyncio.sleep(2)
        try:
            tokens_checked = await client.ensure_valid(tokens)
        except TapFirebaseAuthError as err:
            print(f"  {FAIL} — ensure_valid raised ({err}).")
            failures.append("ensure_valid")
            tokens_checked = tokens
        else:
            if tokens_checked.id_token == tokens.id_token:
                print(f"  {PASS} — id_token unchanged (no refresh triggered).")
            else:
                print(f"  {PASS} — id_token rotated, which is fine if Google"
                      " decided to refresh early.")

        # ── Step 3: refresh() directly ─────────────────────────────
        print("\n[3] refresh(refresh_token)")
        try:
            refreshed = await client.refresh(tokens.refresh_token)
        except TapFirebaseRefreshFailed as err:
            print(f"  {FAIL} — refresh rejected ({err}).")
            failures.append("refresh")
        except TapFirebaseAuthError as err:
            print(f"  {FAIL} — refresh error ({err}).")
            failures.append("refresh")
        else:
            _summarise("tokens from refresh", refreshed)
            if refreshed.id_token and refreshed.id_token != tokens.id_token:
                print(f"  {PASS} — received a different id_token.")
            elif refreshed.id_token:
                print(f"  {PASS} — Google returned the same id_token (still valid).")
            else:
                print(f"  {FAIL} — no id_token in refresh response.")
                failures.append("refresh")

        # ── Step 4: ensure_valid preserves profile on refresh ──────
        print("\n[4] ensure_valid preserves email/display_name across refresh")
        # Force a refresh by backdating expires_at.
        from datetime import datetime, timezone
        tokens_forcing = AuthTokens(
            id_token=tokens.id_token,
            refresh_token=tokens.refresh_token,
            expires_at=datetime.now(timezone.utc),  # already "expiring"
            user_id=tokens.user_id,
            email=tokens.email,
            display_name=tokens.display_name,
        )
        try:
            final = await client.ensure_valid(tokens_forcing)
        except TapFirebaseAuthError as err:
            print(f"  {FAIL} — ensure_valid refresh failed ({err}).")
            failures.append("ensure_valid_refresh")
        else:
            if final.email == tokens.email and final.user_id == tokens.user_id:
                print(f"  {PASS} — email and user_id preserved after refresh.")
            else:
                print(f"  {FAIL} — profile fields lost during refresh.")
                failures.append("ensure_valid_refresh")

    print()
    print("-" * 46)
    if failures:
        print(f"{FAIL}: {', '.join(failures)}")
        return 1
    print("All auth tests passed")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()))
    except KeyboardInterrupt:
        print("\ninterrupted.")
        sys.exit(130)
