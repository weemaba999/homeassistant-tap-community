"""Probe session detail endpoint and look for charger linkage / cost."""
from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import pathlib
import sys
import types

ROOT = pathlib.Path(__file__).parent / "custom_components" / "tapelectric"


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


pkg = types.ModuleType("tapelectric_pkg")
pkg.__path__ = [str(ROOT)]
sys.modules["tapelectric_pkg"] = pkg
const = _load("tapelectric_pkg.const", ROOT / "const.py")
api = _load("tapelectric_pkg.api", ROOT / "api.py")

import aiohttp


def _schema(obj, depth=0, max_depth=5):
    if depth > max_depth:
        return "…"
    if isinstance(obj, dict):
        return {k: _schema(v, depth + 1, max_depth) for k, v in obj.items()}
    if isinstance(obj, list):
        if not obj:
            return []
        return [_schema(obj[0], depth + 1, max_depth), f"<{len(obj)} items>"]
    return type(obj).__name__


async def _get(sess, client, path, params=None):
    url = client._url(path)
    headers = {"Accept": "application/json", **client._auth_headers()}
    async with sess.get(url, headers=headers, params=params) as resp:
        return resp.status, await resp.text(), url


async def main():
    key = os.environ["TAP_API_KEY"]
    async with aiohttp.ClientSession() as sess:
        client = api.TapElectricClient(key, sess)

        # Pull all sessions to pick an active one (missing endedAt) and a completed one.
        _, body, _ = await _get(sess, client, "/sessions")
        sessions = json.loads(body)
        active = next((s for s in sessions if "endedAt" not in s), None)
        completed = next((s for s in sessions if "endedAt" in s), None)

        for label, s in [("ACTIVE", active), ("COMPLETED", completed)]:
            if not s:
                continue
            sid = s["id"]
            print(f"\n── /sessions/{sid}  ({label}) ──")
            status, body, url = await _get(sess, client, f"/sessions/{sid}")
            print(f"  {status}")
            if status == 200:
                data = json.loads(body)
                print(f"  schema={json.dumps(_schema(data), indent=2)}")
                # Show all top-level keys (names only, no values)
                print(f"  all keys: {sorted(data.keys())}")
            else:
                print(f"  body[:300]={body[:300]!r}")

        # Try alternative session endpoints
        print("\n── alternate session-list endpoints ──")
        for p in ["/charge-sessions", "/transactions", "/charging-sessions"]:
            status, body, _ = await _get(sess, client, p)
            print(f"  {p}: {status}  body[:120]={body[:120]!r}")

        # Try alternate active endpoints
        print("\n── alternate 'active' paths ──")
        for p in ["/active-sessions", "/current-sessions", "/sessions/current", "/sessions/live"]:
            status, body, _ = await _get(sess, client, p)
            print(f"  {p}: {status}  body[:120]={body[:120]!r}")

        # /chargers/{id}/sessions
        if active or completed:
            s = active or completed
            cid_guess = None  # session doesn't carry chargerId; still try the charger we know
        print("\n── per-charger session list ──")
        _, body, _ = await _get(sess, client, "/chargers")
        cs = json.loads(body)
        cid = cs[0]["id"] if cs else None
        if cid:
            for p in [f"/chargers/{cid}/sessions", f"/chargers/{cid}/session"]:
                status, body, _ = await _get(sess, client, p)
                print(f"  {p}: {status}  body[:180]={body[:180]!r}")

        # Does /locations exist (we have locationId on the charger)?
        print("\n── locations ──")
        for p in ["/locations", f"/locations/{cs[0]['locationId']}" if cs else "/locations"]:
            status, body, _ = await _get(sess, client, p)
            print(f"  {p}: {status}  body[:250]={body[:250]!r}")

        # Look for webhooks/events endpoints (already mentioned in const.py)
        print("\n── webhooks ──")
        status, body, _ = await _get(sess, client, "/webhooks")
        print(f"  /webhooks: {status}  body[:180]={body[:180]!r}")


asyncio.run(main())
