"""Read-only probe: pull meter rows from a recent completed session and
aggregate the unique (measurand, unit, phase) tuples."""
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


pkg = types.ModuleType("tapelectric")
pkg.__path__ = [str(ROOT)]
sys.modules["tapelectric"] = pkg
_load("tapelectric.const", ROOT / "const.py")
_load("tapelectric.ocpp", ROOT / "ocpp.py")
api = _load("tapelectric.api", ROOT / "api.py")

import aiohttp


async def main():
    key = os.environ["TAP_API_KEY"]
    async with aiohttp.ClientSession() as sess:
        c = api.TapElectricClient(key, sess)
        sessions = await c.list_charger_sessions(limit=10)

        # Pick sessions with real energy to inspect.
        candidates = [s for s in sessions if (s.get("wh") or 0) > 0][:3]
        print(f"[info] inspecting {len(candidates)} completed sessions "
              f"with non-zero wh")

        all_tuples: set[tuple] = set()
        sample_rows: list[dict] = []
        for s in candidates:
            sid = s["id"]
            print(f"\n── session {sid}  (wh={s.get('wh')}  "
                  f"started={s.get('startedAt')}  ended={s.get('endedAt')}) ──")
            md = await c.session_meter_data(sid, limit=100)
            print(f"  rows={len(md)}")
            if md and not sample_rows:
                sample_rows = md[:3]
            for row in md:
                all_tuples.add((
                    row.get("measurand"),
                    row.get("unit"),
                    row.get("phase"),
                ))

        # Also pull meter data for the active session explicitly.
        active = [s for s in sessions if s.get("endedAt") is None]
        for s in active:
            sid = s["id"]
            md = await c.session_meter_data(sid, limit=100)
            print(f"\n── active session {sid} meter rows={len(md)} ──")
            for row in md:
                all_tuples.add((
                    row.get("measurand"),
                    row.get("unit"),
                    row.get("phase"),
                ))

        print("\n── UNIQUE (measurand, unit, phase) tuples across all probed sessions ──")
        for t in sorted(all_tuples, key=lambda x: (x[0] or "", x[2] or "")):
            print(f"  {t}")

        print("\n── sample row keys (first 3 rows) ──")
        for r in sample_rows:
            print(f"  {sorted(r.keys())}")

        # Session summary
        print("\n── session summary table ──")
        print(f"  {'id':<46} {'charger.id':<18} {'conn':<4} {'wh':>6} "
              f"{'endedAt':<25}")
        for s in sessions:
            print(f"  {s.get('id',''):<46} "
                  f"{(s.get('charger') or {}).get('id',''):<18} "
                  f"{(s.get('charger') or {}).get('connectorId',''):<4} "
                  f"{s.get('wh',''):>6} "
                  f"{s.get('endedAt') or 'null':<25}")


asyncio.run(main())
