"""Standalone test for coordinator merge + degradation logic.

Boots the coordinator's pure + stateful logic without a real Home
Assistant install. We stub the small HA surface the coordinator
touches (DataUpdateCoordinator, ConfigEntry, HomeAssistant, a few
helpers) and importlib-load the real integration modules by file path
into a synthetic `tapelectric` package — deliberately skipping the
real __init__.py to avoid its transitive HA imports.

Run:
    python3 tests/test_coordinator_merge_standalone.py

Exit codes: 0 all pass, 1 any failure.
"""
from __future__ import annotations

import asyncio
import importlib.util
import logging
import pathlib
import sys
import types
from datetime import datetime, timedelta, timezone

_HERE = pathlib.Path(__file__).resolve().parent
_PKG_DIR = _HERE.parent / "custom_components" / "tapelectric"


# ── HA stubs (minimal — only what coordinator / its deps actually touch) ─

def _install_ha_stubs() -> None:
    ha = types.ModuleType("homeassistant")
    ha.__path__ = []
    sys.modules["homeassistant"] = ha

    # config_entries — a ConfigEntry stand-in that matches what we use.
    ce_mod = types.ModuleType("homeassistant.config_entries")

    class _ConfigEntry:
        def __init__(self, entry_id: str = "stub", data=None, options=None) -> None:
            self.entry_id = entry_id
            self.data = data or {}
            self.options = options or {}
            self.version = 2
            self._reauth_started = False

        def async_start_reauth(self, hass):   # HA ≥ 2024.11 API
            self._reauth_started = True

    ce_mod.ConfigEntry = _ConfigEntry
    sys.modules["homeassistant.config_entries"] = ce_mod

    # core
    core_mod = types.ModuleType("homeassistant.core")

    class _HomeAssistant:
        data: dict = {}

        def __init__(self):
            self.config_entries = types.SimpleNamespace(
                flow=types.SimpleNamespace(async_init=lambda *a, **k: None),
            )

        def async_create_task(self, coro):
            if asyncio.iscoroutine(coro):
                coro.close()

    core_mod.HomeAssistant = _HomeAssistant
    sys.modules["homeassistant.core"] = core_mod

    # helpers — must be a package for helpers.update_coordinator to resolve.
    helpers_pkg = types.ModuleType("homeassistant.helpers")
    helpers_pkg.__path__ = []
    sys.modules["homeassistant.helpers"] = helpers_pkg

    uc_mod = types.ModuleType("homeassistant.helpers.update_coordinator")

    class _DataUpdateCoordinator:
        # Keep `DataUpdateCoordinator[TapData]` parseable by exposing a
        # __class_getitem__ — the real HA class is Generic, our stub
        # mimics just that subscription.
        def __class_getitem__(cls, item):
            return cls

        def __init__(self, hass, logger, *, name, update_interval):
            self.hass = hass
            self.logger = logger
            self.name = name
            self.update_interval = update_interval
            self.data = None

        async def async_request_refresh(self):
            return None

    class _UpdateFailed(Exception):
        pass

    uc_mod.DataUpdateCoordinator = _DataUpdateCoordinator
    uc_mod.UpdateFailed = _UpdateFailed
    sys.modules["homeassistant.helpers.update_coordinator"] = uc_mod


def _install_tapelectric_stubs() -> None:
    """Load the real modules we want, stub the ones we don't."""
    # Fake package — gives relative imports a home WITHOUT running the real
    # __init__.py (which pulls in many HA things we don't need for this test).
    pkg = types.ModuleType("tapelectric")
    pkg.__path__ = [str(_PKG_DIR)]
    sys.modules["tapelectric"] = pkg

    # Stub tapelectric.api — coordinator only needs the 3 symbols below.
    api_stub = types.ModuleType("tapelectric.api")

    class _TapElectricError(Exception):
        pass

    class _TapElectricAuthError(_TapElectricError):
        pass

    class _TapElectricClient:     # never instantiated in these tests
        pass

    api_stub.TapElectricError = _TapElectricError
    api_stub.TapElectricAuthError = _TapElectricAuthError
    api_stub.TapElectricClient = _TapElectricClient
    sys.modules["tapelectric.api"] = api_stub

    # Stub tapelectric.repairs — coordinator calls 4 no-op helpers.
    repairs_stub = types.ModuleType("tapelectric.repairs")

    def _noop(*a, **k):
        return None

    repairs_stub.note_auth_failure = _noop
    repairs_stub.clear_auth_failure = _noop
    repairs_stub.note_charger_offline = _noop
    repairs_stub.clear_charger_offline = _noop
    sys.modules["tapelectric.repairs"] = repairs_stub

    # Load real modules by file path under their package names.
    for modname, filename in [
        ("tapelectric.const",          "const.py"),
        ("tapelectric.auth_firebase",  "auth_firebase.py"),
        ("tapelectric.api_management", "api_management.py"),
        ("tapelectric.coordinator",    "coordinator.py"),
    ]:
        spec = importlib.util.spec_from_file_location(
            modname, _PKG_DIR / filename,
        )
        assert spec and spec.loader, f"spec failed for {modname}"
        module = importlib.util.module_from_spec(spec)
        sys.modules[modname] = module
        spec.loader.exec_module(module)


_install_ha_stubs()
_install_tapelectric_stubs()

# Now real imports from the loaded modules.
from tapelectric.api_management import (  # noqa: E402
    ManagementSession,
    TapManagementNetworkError,
)
from tapelectric.auth_firebase import TapFirebaseAuthError  # noqa: E402
from tapelectric.coordinator import TapCoordinator, TapData  # noqa: E402
from homeassistant.config_entries import ConfigEntry         # noqa: E402


logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

PASS = "\033[32mPASS\033[0m"
FAIL = "\033[31mFAIL\033[0m"

_FAILURES: list[str] = []


def _check(label: str, cond: bool, detail: str = "") -> None:
    if cond:
        print(f"  {PASS}  {label}")
    else:
        print(f"  {FAIL}  {label}  {detail}")
        _FAILURES.append(label)


def _mk_session(
    *, session_id: str, charger_id: str, energy_wh: int,
    started_at: datetime, ended_at: datetime | None,
) -> ManagementSession:
    return ManagementSession(
        session_id=session_id,
        charger_id=charger_id,
        start_date=started_at.isoformat().replace("+00:00", "Z"),
        end_date=(ended_at.isoformat().replace("+00:00", "Z")
                  if ended_at else None),
        energy_wh=energy_wh,
    )


# ── Stub coordinator that bypasses DataUpdateCoordinator machinery ────

class _StubCoord(TapCoordinator):
    def __init__(self, mgmt, entry):    # noqa: D401 - stub
        from homeassistant.core import HomeAssistant
        self.hass = HomeAssistant()
        self.mgmt = mgmt
        self.entry = entry
        self.client = None
        self.scope_charger_id = None
        self.update_interval = timedelta(seconds=300)
        self._cold_fetched = {}
        self._consecutive_auth_failures = 0
        self._advanced_failures = 0
        self._last_reauth_trigger = None
        self._advanced_degraded_since = None
        self._advanced_last_degraded_log = None


class _FakeMgmt:
    def __init__(self, *, sessions=None, raises=None):
        self._sessions = sessions or []
        self._raises = raises

    async def list_role_sessions(self, *, role="cpo", offset=0, take=20):
        if self._raises is not None:
            raise self._raises
        return list(self._sessions)


# ── Tests ─────────────────────────────────────────────────────────────

def test_bucketise_basics() -> None:
    print("\n[1] bucketise_mgmt_sessions — pure split")
    now = datetime.now(timezone.utc)
    sessions = [
        _mk_session(
            session_id="cs_old", charger_id="EVB-A", energy_wh=1000,
            started_at=now - timedelta(hours=5),
            ended_at=now - timedelta(hours=4),
        ),
        _mk_session(
            session_id="cs_new", charger_id="EVB-A", energy_wh=5000,
            started_at=now - timedelta(minutes=10), ended_at=None,
        ),
        _mk_session(
            session_id="cs_b", charger_id="EVB-B", energy_wh=200,
            started_at=now - timedelta(hours=1),
            ended_at=now - timedelta(minutes=45),
        ),
        _mk_session(
            session_id="cs_ignore", charger_id="EVB-UNKNOWN", energy_wh=999,
            started_at=now, ended_at=None,
        ),
    ]
    active, closed = TapCoordinator.bucketise_mgmt_sessions(
        sessions, {"EVB-A", "EVB-B"},
    )
    _check(
        "active session EVB-A is cs_new",
        active["EVB-A"] is not None and active["EVB-A"].session_id == "cs_new",
    )
    _check("closed EVB-A is cs_old (older, closed)",
           closed["EVB-A"] is not None and closed["EVB-A"].session_id == "cs_old")
    _check("closed EVB-B is cs_b",
           closed["EVB-B"] is not None and closed["EVB-B"].session_id == "cs_b")
    _check("EVB-B has no active session",
           active["EVB-B"] is None)
    _check(
        "unknown charger ignored",
        "EVB-UNKNOWN" not in active and "EVB-UNKNOWN" not in closed,
    )


def test_tapdata_helpers() -> None:
    print("\n[2] TapData — mgmt helpers respect mgmt_fresh gate")
    sess = _mk_session(
        session_id="cs_1", charger_id="EVB-A", energy_wh=1234,
        started_at=datetime.now(timezone.utc), ended_at=None,
    )
    d_fresh = TapData(
        chargers=[{"id": "EVB-A"}],
        mgmt_active_by_charger={"EVB-A": sess},
        mgmt_last_closed_by_charger={"EVB-A": None},
        mgmt_fresh=True,
    )
    _check("mgmt_active returns session when fresh",
           d_fresh.mgmt_active("EVB-A") is sess)
    _check("is_charging_active True on fresh+active",
           d_fresh.is_charging_active("EVB-A") is True)

    d_stale = TapData(
        chargers=[{"id": "EVB-A"}],
        mgmt_active_by_charger={"EVB-A": sess},
        mgmt_fresh=False,
    )
    _check("mgmt_active returns None when not fresh",
           d_stale.mgmt_active("EVB-A") is None)
    _check("is_charging_active None when not fresh (basic fallback)",
           d_stale.is_charging_active("EVB-A") is None)


async def test_fetch_success() -> None:
    print("\n[3] _fetch_mgmt_sessions — success path")
    now = datetime.now(timezone.utc)
    sessions = [
        _mk_session(
            session_id="cs_live", charger_id="EVB-A", energy_wh=4200,
            started_at=now - timedelta(minutes=5), ended_at=None,
        ),
    ]
    coord = _StubCoord(
        mgmt=_FakeMgmt(sessions=sessions),
        entry=ConfigEntry(data={"advanced_mode": True}),
    )
    active, closed, ok = await coord._fetch_mgmt_sessions({"EVB-A"})
    _check("ok flag True on success", ok is True)
    _check("advanced_failures reset to 0 after success",
           coord._advanced_failures == 0)
    _check("active EVB-A populated",
           active["EVB-A"] is not None
           and active["EVB-A"].session_id == "cs_live")
    _check("degraded_since cleared after success",
           coord._advanced_degraded_since is None)


async def test_fetch_basic_only() -> None:
    print("\n[4] _fetch_mgmt_sessions — mgmt=None (basic-only fallback)")
    coord = _StubCoord(mgmt=None, entry=ConfigEntry(data={}))
    active, closed, ok = await coord._fetch_mgmt_sessions({"EVB-A"})
    _check("ok flag False when mgmt is None", ok is False)
    _check("active map empty", active == {})
    _check("closed map empty", closed == {})
    _check("no state change on basic-only tick",
           coord._advanced_failures == 0
           and coord._advanced_degraded_since is None)


async def test_fetch_auth_failure_counts() -> None:
    print("\n[5] _fetch_mgmt_sessions — 3x auth failure triggers reauth once")
    entry = ConfigEntry(data={"advanced_mode": True})
    mgmt = _FakeMgmt(raises=TapFirebaseAuthError("INVALID_REFRESH_TOKEN"))
    coord = _StubCoord(mgmt=mgmt, entry=entry)

    for i in range(1, 4):
        active, closed, ok = await coord._fetch_mgmt_sessions({"EVB-A"})
        _check(
            f"attempt {i}: ok=False",
            ok is False and active == {} and closed == {},
        )
    _check("failure counter reached 3", coord._advanced_failures == 3)
    _check("entry.async_start_reauth was invoked",
           getattr(entry, "_reauth_started", False) is True)


async def test_fetch_network_failure_degrades() -> None:
    print("\n[6] _fetch_mgmt_sessions — network failure marks degraded")
    mgmt = _FakeMgmt(raises=TapManagementNetworkError("boom"))
    coord = _StubCoord(mgmt=mgmt, entry=ConfigEntry(data={}))
    active, closed, ok = await coord._fetch_mgmt_sessions({"EVB-A"})
    _check("ok=False on network failure", ok is False)
    _check("degraded_since populated",
           coord._advanced_degraded_since is not None)
    _check("auth counter NOT incremented on network-only failure",
           coord._advanced_failures == 0)


async def _main_async() -> int:
    test_bucketise_basics()
    test_tapdata_helpers()
    await test_fetch_success()
    await test_fetch_basic_only()
    await test_fetch_auth_failure_counts()
    await test_fetch_network_failure_degrades()

    print()
    if _FAILURES:
        print(f"{FAIL}: {len(_FAILURES)} failure(s): {', '.join(_FAILURES)}")
        return 1
    print(f"{PASS}: all merge tests passed")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(_main_async()))
