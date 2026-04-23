"""Tests for async_migrate_entry in __init__.py — v1 → v2 migration.

This test loads __init__.py in isolation. It depends on the same HA
stubs as the other tests, plus a stub for the webhook + repairs modules
so import doesn't trip on Platform.SWITCH / Repairs wiring.
"""
from __future__ import annotations

import asyncio
import importlib.util
import pathlib
import sys
import types

import pytest

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant


_PKG_DIR = pathlib.Path(__file__).resolve().parent.parent / "custom_components" / "tapelectric"


@pytest.fixture(scope="module")
def init_module():
    """Load tapelectric.__init__ under the conftest stubs.

    Real __init__ imports webhook + repairs — both already registered
    as tapelectric.* modules by conftest. We can safely load it.
    """
    if "tapelectric._main_init" in sys.modules:
        return sys.modules["tapelectric._main_init"]

    # Stub webhook (not stubbed by conftest — __init__ imports it).
    if "tapelectric.webhook" not in sys.modules:
        wb = types.ModuleType("tapelectric.webhook")
        async def _noop_async(*a, **k):
            return None
        wb.async_register_webhook = _noop_async
        wb.async_unregister_webhook = _noop_async
        sys.modules["tapelectric.webhook"] = wb

    spec = importlib.util.spec_from_file_location(
        "tapelectric._main_init", _PKG_DIR / "__init__.py",
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules["tapelectric._main_init"] = module
    spec.loader.exec_module(module)
    return module


def test_migrate_v1_to_v2_adds_advanced_mode_false(init_module):
    entry = ConfigEntry(
        version=1,
        data={"api_key": "sk_old"},
    )
    hass = HomeAssistant()
    ok = asyncio.run(init_module.async_migrate_entry(hass, entry))
    assert ok is True
    assert entry.version == 2
    assert entry.data["advanced_mode"] is False
    # api_key preserved.
    assert entry.data["api_key"] == "sk_old"


def test_migrate_already_v2_is_noop(init_module):
    entry = ConfigEntry(
        version=2,
        data={"api_key": "sk_x", "advanced_mode": True,
              "advanced_refresh_token": "rt_keep"},
    )
    hass = HomeAssistant()
    asyncio.run(init_module.async_migrate_entry(hass, entry))
    assert entry.version == 2
    assert entry.data["advanced_refresh_token"] == "rt_keep"


def test_options_view_merges_defaults(init_module):
    entry = ConfigEntry(options={"scan_interval_active_s": 45})
    merged = init_module.options_view(entry)
    assert merged["scan_interval_active_s"] == 45
    # defaulted key still present.
    assert "scan_interval_idle_s" in merged


def test_is_write_enabled_defaults_true(init_module):
    entry = ConfigEntry()
    assert init_module.is_write_enabled(entry) is True


def test_is_write_enabled_false_when_off(init_module):
    entry = ConfigEntry(options={"write_enabled": False})
    assert init_module.is_write_enabled(entry) is False


def test_ensure_write_enabled_raises_when_off(init_module):
    from homeassistant.exceptions import HomeAssistantError

    entry = ConfigEntry(options={"write_enabled": False})
    with pytest.raises(HomeAssistantError):
        init_module.ensure_write_enabled(HomeAssistant(), entry)
