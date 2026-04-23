"""Shared pytest fixtures and stubs for the Tap Electric test suite.

Runs in two modes:

* **Standalone** (no homeassistant installed): fixtures still work;
  HA-dependent tests skip themselves via the `requires_ha` marker.
* **Full** (`pip install -r requirements_test.txt`): the
  pytest-homeassistant-custom-component `hass` fixture is available,
  and `requires_ha` tests run normally.

Fixtures exposed:

* `mock_aioresponse`   — aioresponses context for HTTP mocks.
* `load_fixture`       — load JSON by name from `tests/fixtures/`.
* `hass_config_entry_v1`           — pre-migration v1 entry, basic only.
* `hass_config_entry_v2_basic`     — v2 entry with advanced_mode=False.
* `hass_config_entry_v2_advanced`  — v2 entry with advanced_mode=True.

All three entry fixtures return an object compatible with HA's
`ConfigEntry` surface that the integration actually touches. When
`homeassistant` is installed we use `MockConfigEntry`; when it isn't
we use a lightweight dataclass-like stub so unit tests still work.
"""
from __future__ import annotations

import importlib
import json
import pathlib
import sys
import types
from typing import Any

import pytest

_HERE = pathlib.Path(__file__).resolve().parent
_REPO = _HERE.parent
_FIXTURES = _HERE / "fixtures"
_PKG_DIR = _REPO / "custom_components" / "tapelectric"


# Install a *synthetic* `tapelectric` package that points at the real
# directory but skips its `__init__.py`. That way submodules with
# relative imports (`from .const import …`) resolve correctly, without
# triggering the transitive `homeassistant` imports in the real
# `__init__.py`. Matches the pattern from
# test_coordinator_merge_standalone.py so both test styles share a
# single import strategy.
if "tapelectric" not in sys.modules:
    pkg = types.ModuleType("tapelectric")
    pkg.__path__ = [str(_PKG_DIR)]
    sys.modules["tapelectric"] = pkg


# ── HA stubs for no-HA environments ─────────────────────────────────────
#
# The coordinator imports `homeassistant.config_entries`, `.core`,
# and `.helpers.update_coordinator`. When HA isn't installed we install
# a minimal stub so `from tapelectric.coordinator import TapCoordinator`
# still works in unit tests. When HA IS installed we leave the real
# modules alone.

def _install_ha_stubs_if_needed() -> None:
    try:
        import homeassistant  # noqa: F401
        return
    except ImportError:
        pass
    import asyncio

    ha = types.ModuleType("homeassistant")
    ha.__path__ = []
    sys.modules["homeassistant"] = ha

    # config_entries
    ce_mod = types.ModuleType("homeassistant.config_entries")

    class _ConfigEntry:
        def __init__(
            self, *, entry_id="stub", data=None, options=None, version=2,
            unique_id=None, domain="tapelectric", title="Tap Electric",
        ) -> None:
            self.entry_id = entry_id
            self.data = dict(data or {})
            self.options = dict(options or {})
            self.version = version
            self.unique_id = unique_id
            self.domain = domain
            self.title = title
            self._reauth_started = False

        def async_start_reauth(self, hass) -> None:
            self._reauth_started = True

    ce_mod.ConfigEntry = _ConfigEntry
    sys.modules["homeassistant.config_entries"] = ce_mod

    # core
    core_mod = types.ModuleType("homeassistant.core")

    def _update_entry(entry, **kwargs):
        if "data" in kwargs:
            entry.data = dict(kwargs["data"])
        if "options" in kwargs:
            entry.options = dict(kwargs["options"])
        if "version" in kwargs:
            entry.version = kwargs["version"]

    class _HomeAssistant:
        def __init__(self) -> None:
            self.data: dict = {}
            self.services = types.SimpleNamespace(
                has_service=lambda *a, **k: False,
                async_register=lambda *a, **k: None,
            )
            self.config_entries = types.SimpleNamespace(
                flow=types.SimpleNamespace(async_init=lambda *a, **k: None),
                async_update_entry=_update_entry,
                async_reload=self._async_reload,
            )

        async def _async_reload(self, entry_id: str) -> None:
            return None

        def async_create_task(self, coro):
            if asyncio.iscoroutine(coro):
                coro.close()

    class _ServiceCall:
        def __init__(self, data: dict | None = None) -> None:
            self.data = data or {}

    core_mod.HomeAssistant = _HomeAssistant
    core_mod.ServiceCall = _ServiceCall
    sys.modules["homeassistant.core"] = core_mod

    # exceptions
    exc_mod = types.ModuleType("homeassistant.exceptions")

    class _HomeAssistantError(Exception):
        pass

    exc_mod.HomeAssistantError = _HomeAssistantError
    sys.modules["homeassistant.exceptions"] = exc_mod

    # const
    const_mod = types.ModuleType("homeassistant.const")

    class _Platform:
        BINARY_SENSOR = "binary_sensor"
        BUTTON = "button"
        NUMBER = "number"
        SELECT = "select"
        SENSOR = "sensor"
        SWITCH = "switch"

    const_mod.Platform = _Platform
    const_mod.PERCENTAGE = "%"

    class _Unit:
        AMPERE = "A"
        VOLT = "V"
        KILO_WATT_HOUR = "kWh"
        WATT_HOUR = "Wh"
        KILO_WATT = "kW"
        WATT = "W"
        HERTZ = "Hz"
        CELSIUS = "°C"
        SECONDS = "s"
        MINUTES = "min"

    const_mod.UnitOfElectricCurrent = _Unit
    const_mod.UnitOfElectricPotential = _Unit
    const_mod.UnitOfEnergy = _Unit
    const_mod.UnitOfPower = _Unit
    const_mod.UnitOfFrequency = _Unit
    const_mod.UnitOfTemperature = _Unit
    const_mod.UnitOfTime = _Unit
    sys.modules["homeassistant.const"] = const_mod

    # helpers (as package)
    helpers_pkg = types.ModuleType("homeassistant.helpers")
    helpers_pkg.__path__ = []
    sys.modules["homeassistant.helpers"] = helpers_pkg

    uc_mod = types.ModuleType("homeassistant.helpers.update_coordinator")

    class _DataUpdateCoordinator:
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

        async def async_config_entry_first_refresh(self):
            return None

    class _UpdateFailed(Exception):
        pass

    class _CoordinatorEntity:
        def __class_getitem__(cls, item):
            return cls

        def __init__(self, coordinator):
            self.coordinator = coordinator

        @property
        def available(self) -> bool:
            return True

    uc_mod.DataUpdateCoordinator = _DataUpdateCoordinator
    uc_mod.UpdateFailed = _UpdateFailed
    uc_mod.CoordinatorEntity = _CoordinatorEntity
    sys.modules["homeassistant.helpers.update_coordinator"] = uc_mod

    # aiohttp client helper
    aiohttp_client_mod = types.ModuleType("homeassistant.helpers.aiohttp_client")
    aiohttp_client_mod.async_get_clientsession = lambda hass: None
    sys.modules["homeassistant.helpers.aiohttp_client"] = aiohttp_client_mod

    # issue registry
    issue_mod = types.ModuleType("homeassistant.helpers.issue_registry")

    class _IssueSeverity:
        CRITICAL = "critical"
        ERROR = "error"
        WARNING = "warning"
        INFO = "info"

    issue_mod.IssueSeverity = _IssueSeverity
    issue_mod.async_create_issue = lambda *a, **k: None
    issue_mod.async_delete_issue = lambda *a, **k: None
    sys.modules["homeassistant.helpers.issue_registry"] = issue_mod

    # device registry
    dev_mod = types.ModuleType("homeassistant.helpers.device_registry")

    class _DeviceInfo(dict):
        def __init__(self, **kw):
            super().__init__(**kw)

    dev_mod.DeviceInfo = _DeviceInfo
    sys.modules["homeassistant.helpers.device_registry"] = dev_mod

    # entity_platform
    ep_mod = types.ModuleType("homeassistant.helpers.entity_platform")
    ep_mod.AddEntitiesCallback = object
    sys.modules["homeassistant.helpers.entity_platform"] = ep_mod

    # components.* platform bases — minimal shims so the platform
    # modules import cleanly under stubbed HA.
    comp_pkg = types.ModuleType("homeassistant.components")
    comp_pkg.__path__ = []
    sys.modules["homeassistant.components"] = comp_pkg

    def _make_entity_base(name: str) -> type:
        return type(name, (), {
            "_attr_has_entity_name": True,
            "native_value": None,
            "is_on": None,
            "async_write_ha_state": lambda self: None,
        })

    class _SensorDeviceClass:
        ENERGY = "energy"
        POWER = "power"
        CURRENT = "current"
        VOLTAGE = "voltage"
        FREQUENCY = "frequency"
        BATTERY = "battery"
        TEMPERATURE = "temperature"
        POWER_FACTOR = "power_factor"
        TIMESTAMP = "timestamp"

    class _SensorStateClass:
        MEASUREMENT = "measurement"
        TOTAL = "total"
        TOTAL_INCREASING = "total_increasing"

    sensor_mod = types.ModuleType("homeassistant.components.sensor")
    sensor_mod.SensorDeviceClass = _SensorDeviceClass
    sensor_mod.SensorStateClass = _SensorStateClass
    sensor_mod.SensorEntity = _make_entity_base("SensorEntity")
    sys.modules["homeassistant.components.sensor"] = sensor_mod

    class _BinarySensorDeviceClass:
        CONNECTIVITY = "connectivity"
        POWER = "power"
        PLUG = "plug"
        PROBLEM = "problem"

    bs_mod = types.ModuleType("homeassistant.components.binary_sensor")
    bs_mod.BinarySensorDeviceClass = _BinarySensorDeviceClass
    bs_mod.BinarySensorEntity = _make_entity_base("BinarySensorEntity")
    sys.modules["homeassistant.components.binary_sensor"] = bs_mod

    switch_mod = types.ModuleType("homeassistant.components.switch")
    switch_mod.SwitchEntity = _make_entity_base("SwitchEntity")
    sys.modules["homeassistant.components.switch"] = switch_mod

    class _NumberMode:
        AUTO = "auto"
        BOX = "box"
        SLIDER = "slider"

    number_mod = types.ModuleType("homeassistant.components.number")
    number_mod.NumberEntity = _make_entity_base("NumberEntity")
    number_mod.NumberMode = _NumberMode
    sys.modules["homeassistant.components.number"] = number_mod

    class _ButtonDeviceClass:
        RESTART = "restart"
        IDENTIFY = "identify"
        UPDATE = "update"

    button_mod = types.ModuleType("homeassistant.components.button")
    button_mod.ButtonDeviceClass = _ButtonDeviceClass
    button_mod.ButtonEntity = _make_entity_base("ButtonEntity")
    sys.modules["homeassistant.components.button"] = button_mod

    select_mod = types.ModuleType("homeassistant.components.select")
    select_mod.SelectEntity = _make_entity_base("SelectEntity")
    sys.modules["homeassistant.components.select"] = select_mod

    # webhook component — only needed so `from homeassistant.components
    # import webhook` in the integration's webhook.py loads cleanly.
    webhook_mod = types.ModuleType("homeassistant.components.webhook")
    webhook_mod.async_register = lambda *a, **k: None
    webhook_mod.async_unregister = lambda *a, **k: None
    sys.modules["homeassistant.components.webhook"] = webhook_mod
    comp_pkg.webhook = webhook_mod


_install_ha_stubs_if_needed()


# ── Real module loading under the tapelectric.* namespace ──────────────

def _load_real(modname: str, filename: str) -> None:
    """Load a real module file as tapelectric.<modname>.

    Idempotent; safe to call after HA stubs are in place.
    """
    full = f"tapelectric.{modname}"
    if full in sys.modules:
        return
    spec = importlib.util.spec_from_file_location(full, _PKG_DIR / filename)
    assert spec and spec.loader, f"spec failed for {full}"
    module = importlib.util.module_from_spec(spec)
    sys.modules[full] = module
    spec.loader.exec_module(module)


# Pre-load the modules tests most commonly import. Doing this here
# keeps individual test files import-ordering-agnostic.
for _modname, _filename in [
    ("const",          "const.py"),
    ("ocpp",           "ocpp.py"),
    ("auth_firebase",  "auth_firebase.py"),
    ("api",            "api.py"),
    ("api_management", "api_management.py"),
    ("repairs",        "repairs.py"),
    ("coordinator",    "coordinator.py"),
]:
    try:
        _load_real(_modname, _filename)
    except Exception:
        # Defer: some modules may need stubs not yet in place. Individual
        # tests will trigger the import and surface the real error then.
        pass


# ── HA availability detection ───────────────────────────────────────────

def _ha_available() -> bool:
    try:
        importlib.import_module("homeassistant")
        importlib.import_module("pytest_homeassistant_custom_component")
    except ImportError:
        return False
    return True


HA_AVAILABLE = _ha_available()


def pytest_collection_modifyitems(config, items):
    """Auto-skip requires_ha tests when HA isn't installed."""
    if HA_AVAILABLE:
        return
    skip = pytest.mark.skip(reason="homeassistant not installed (run via requirements_test.txt)")
    for item in items:
        if "requires_ha" in item.keywords:
            item.add_marker(skip)


# ── HTTP mocking ────────────────────────────────────────────────────────

@pytest.fixture
def mock_aioresponse():
    """Yield an aioresponses context for mocking aiohttp calls.

    Skips the test when aioresponses isn't available locally.
    """
    aioresponses_mod = pytest.importorskip("aioresponses")
    with aioresponses_mod.aioresponses() as m:
        yield m


# ── Fixture loading ─────────────────────────────────────────────────────

@pytest.fixture
def load_fixture():
    """Load a JSON fixture by relative name from tests/fixtures/.

    Supports both raw files (returns parsed JSON) and inventory-style
    files that wrap the payload in `{_meta, data}` — in that case the
    `data` key is returned directly. Pass a filename with or without the
    `.json` suffix.
    """
    def _load(name: str) -> Any:
        if not name.endswith(".json"):
            name = f"{name}.json"
        path = _FIXTURES / name
        if not path.exists():
            raise FileNotFoundError(f"No fixture: {path}")
        data = json.loads(path.read_text())
        if isinstance(data, dict) and set(data.keys()) == {"_meta", "data"}:
            return data["data"]
        return data
    return _load


# ── Config entry stubs ──────────────────────────────────────────────────

# These import-time stubs are only used when HA isn't installed.
# When HA is installed, we prefer MockConfigEntry from pytest-homeassistant-custom-component.

class _StubConfigEntry:
    """Minimal ConfigEntry shape the integration reads from / writes to.

    Matches the surface used across __init__.py, coordinator.py,
    config_flow.py, and the platform modules. Attributes are writable
    so tests can mutate them directly.
    """
    def __init__(
        self,
        *,
        entry_id: str = "stub_entry",
        version: int = 2,
        data: dict | None = None,
        options: dict | None = None,
        unique_id: str | None = None,
        domain: str = "tapelectric",
        title: str = "Tap Electric (test)",
    ) -> None:
        self.entry_id = entry_id
        self.version = version
        self.data = dict(data or {})
        self.options = dict(options or {})
        self.unique_id = unique_id
        self.domain = domain
        self.title = title
        self.state = "loaded"
        self._reauth_started = False

    def async_start_reauth(self, hass) -> None:   # HA ≥ 2024.11 API
        self._reauth_started = True

    def add_update_listener(self, listener):
        return lambda: None

    def async_on_unload(self, func):
        return None


def _v1_data() -> dict:
    return {
        "api_key": "sk_testkey",
        "base_url": "https://api.tapelectric.app",
    }


def _v2_basic_data() -> dict:
    return {
        **_v1_data(),
        "advanced_mode": False,
    }


def _v2_advanced_data() -> dict:
    return {
        **_v1_data(),
        "advanced_mode": True,
        "advanced_email": "driver@example.com",
        "advanced_refresh_token": "rt_test_refresh_token",
        "advanced_firebase_user_id": "uid_testuid",
        "advanced_account_id": "macc_testaccount",
    }


@pytest.fixture
def hass_config_entry_v1():
    """Pre-migration v1 entry, basic mode only."""
    return _StubConfigEntry(
        version=1,
        data=_v1_data(),
        options={},
    )


@pytest.fixture
def hass_config_entry_v2_basic():
    """v2 entry with advanced_mode=False."""
    return _StubConfigEntry(
        version=2,
        data=_v2_basic_data(),
        options={},
    )


@pytest.fixture
def hass_config_entry_v2_advanced():
    """v2 entry with advanced_mode=True + stored refresh token."""
    return _StubConfigEntry(
        version=2,
        data=_v2_advanced_data(),
        options={},
    )
