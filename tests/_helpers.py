"""Env-aware factories used by every test that constructs a ConfigEntry
or HomeAssistant stand-in.

Why this exists: bare `ConfigEntry()` and `HomeAssistant()` calls work
against conftest's HA stubs (HA not installed) but fail in CI where
real HA is installed — both classes reject construction without args.
These factories pick the right backing implementation per environment
so the test bodies don't need env branches.

Public surface:
    make_entry(**kwargs)   — MockConfigEntry (HA) | _StubConfigEntry
    make_hass()            — _FakeHass (both envs; deliberately NOT a
                              real HomeAssistant — that needs an event
                              loop + config dir, overkill for our tests)

Mutation contract: `make_hass().config_entries.async_update_entry(entry,
data=..., options=..., version=...)` works on both MockConfigEntry and
_StubConfigEntry. Real ConfigEntry blocks `entry.data = ...` via a
custom `__setattr__`; we bypass that with `object.__setattr__` which
writes the attribute directly without triggering the guard.
"""
from __future__ import annotations

import asyncio
import importlib
import types
from typing import Any


def _ha_available() -> bool:
    try:
        importlib.import_module("homeassistant")
        importlib.import_module("pytest_homeassistant_custom_component")
    except ImportError:
        return False
    return True


HA_AVAILABLE = _ha_available()


class _StubConfigEntry:
    """Minimal ConfigEntry shape the integration reads from / writes to.

    Used when HA isn't installed. Attributes are freely writable.
    """
    def __init__(
        self,
        *,
        entry_id: str = "test_entry",
        version: int = 2,
        data: dict | None = None,
        options: dict | None = None,
        unique_id: str | None = None,
        domain: str = "tapelectric",
        title: str = "Tap Electric (test)",
        source: str = "user",
    ) -> None:
        self.entry_id = entry_id
        self.version = version
        self.data = dict(data or {})
        self.options = dict(options or {})
        self.unique_id = unique_id
        self.domain = domain
        self.title = title
        self.source = source
        self.state = "loaded"
        self._reauth_started = False

    def async_start_reauth(self, hass) -> None:
        self._reauth_started = True

    def add_update_listener(self, listener):
        return lambda: None

    def async_on_unload(self, func) -> None:
        return None


def _obj_setattr(obj: Any, name: str, value: Any) -> None:
    """Bypass descriptor guards (real ConfigEntry protects `.data` /
    `.options` / `.version`) and write the attribute directly. No-op-
    equivalent for plain classes."""
    object.__setattr__(obj, name, value)


class _FakeHass:
    """Sync-friendly HA test double.

    Only implements the surface our integration code touches in unit
    tests: `.config_entries.async_update_entry`, `.config_entries.flow`,
    `.config_entries.async_reload`, `.data`, `.services`, `.bus`,
    `.async_create_task`. Emphatically not a HomeAssistant subclass —
    constructing the real class needs a running event loop plus a
    config directory, and our tests don't need either.

    To track reauth-style triggers, inspect `entry._reauth_started`
    (both _StubConfigEntry and MockConfigEntry expose it because
    our stub sets it explicitly and the coordinator calls
    `entry.async_start_reauth(hass)` which MockConfigEntry handles
    via its own bookkeeping).
    """
    def __init__(self) -> None:
        self.data: dict = {}
        self.services = types.SimpleNamespace(
            has_service=lambda *a, **k: False,
            async_register=lambda *a, **k: None,
        )
        self.bus = types.SimpleNamespace(
            async_fire=lambda *a, **k: None,
        )

        def _async_update_entry(entry, **kwargs):
            if "data" in kwargs:
                _obj_setattr(entry, "data", dict(kwargs["data"]))
            if "options" in kwargs:
                _obj_setattr(entry, "options", dict(kwargs["options"]))
            if "version" in kwargs:
                _obj_setattr(entry, "version", kwargs["version"])

        async def _async_reload(entry_id: str) -> None:
            return None

        self.config_entries = types.SimpleNamespace(
            flow=types.SimpleNamespace(
                async_init=lambda *a, **k: None,
            ),
            async_update_entry=_async_update_entry,
            async_reload=_async_reload,
        )

    def async_create_task(self, coro):
        if asyncio.iscoroutine(coro):
            coro.close()


def make_entry(
    *,
    entry_id: str = "test_entry",
    version: int = 2,
    data: dict | None = None,
    options: dict | None = None,
    domain: str = "tapelectric",
    title: str = "Tap Electric (test)",
    unique_id: str | None = None,
    source: str = "user",
):
    """Return a ConfigEntry-shaped object appropriate to the environment.

    Both backends expose `._reauth_started: bool` so tests can assert
    whether the coordinator triggered a re-auth. Real ConfigEntry's
    `async_start_reauth` would try to spin up HA's flow manager;
    we short-circuit it here by replacing the bound method with a
    flag-setter.
    """
    if HA_AVAILABLE:
        import types as _types

        from pytest_homeassistant_custom_component.common import MockConfigEntry
        entry = MockConfigEntry(
            entry_id=entry_id,
            version=version,
            data=dict(data or {}),
            options=dict(options or {}),
            domain=domain,
            title=title,
            unique_id=unique_id,
            source=source,
        )
        # Reauth tracker — MockConfigEntry doesn't expose one natively.
        object.__setattr__(entry, "_reauth_started", False)

        def _fake_start_reauth(self, hass, context=None, data=None):
            object.__setattr__(self, "_reauth_started", True)

        entry.async_start_reauth = _types.MethodType(_fake_start_reauth, entry)
        return entry
    return _StubConfigEntry(
        entry_id=entry_id,
        version=version,
        data=data or {},
        options=options or {},
        unique_id=unique_id,
        domain=domain,
        title=title,
        source=source,
    )


def make_hass() -> _FakeHass:
    """Return a sync-friendly hass-like test double."""
    return _FakeHass()
