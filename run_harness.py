"""v2 read-only smoke harness.

Mirrors the `python3 -m tapelectric.api` entrypoint exactly, but bypasses
the package __init__.py (which imports homeassistant, not available in
this LXC). Only calls GET endpoints — no OCPP passthrough / Reset.
"""
from __future__ import annotations

import asyncio
import importlib.util
import json as _json
import os
import pathlib
import sys
import types

ROOT = pathlib.Path(__file__).parent / "custom_components" / "tapelectric"


def _load(name: str, path: pathlib.Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


# Fake the parent package so relative imports ("from .const import …")
# resolve without running the real __init__.py.
pkg = types.ModuleType("tapelectric")
pkg.__path__ = [str(ROOT)]
sys.modules["tapelectric"] = pkg
_load("tapelectric.const", ROOT / "const.py")
_load("tapelectric.ocpp", ROOT / "ocpp.py")
api = _load("tapelectric.api", ROOT / "api.py")

import aiohttp


async def _main() -> None:
    key = os.environ.get("TAP_API_KEY")
    if not key:
        raise SystemExit("Set TAP_API_KEY env var")
    async with aiohttp.ClientSession() as sess:
        c = api.TapElectricClient(key, sess)

        print("── Chargers ──")
        chargers = await c.list_chargers()
        print(_json.dumps(chargers, indent=2)[:1200])

        print("\n── Charger sessions (last 5) ──")
        sessions = await c.list_charger_sessions(limit=5)
        print(_json.dumps(sessions, indent=2)[:1500])

        active = [s for s in sessions if s.get("endedAt") is None]
        if active:
            sid = active[0]["id"]
            print(f"\n── Meter data for active session {sid} ──")
            md = await c.session_meter_data(sid, limit=20)
            print(_json.dumps(md, indent=2)[:2000])
        elif sessions:
            sid = sessions[0]["id"]
            print(f"\n── Meter data for last finished session {sid} ──")
            md = await c.session_meter_data(sid, limit=20)
            print(_json.dumps(md, indent=2)[:2000])


asyncio.run(_main())
