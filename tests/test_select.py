"""Tests for select.py — ResetTypeSelect."""
from __future__ import annotations

import asyncio

import pytest

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from tapelectric.const import DATA_RESET_TYPE
from tapelectric.coordinator import TapData
from tapelectric.select import ResetTypeSelect


class _FakeCoord:
    def __init__(self, data):
        self.data = data


def _data():
    return TapData(chargers=[{"id": "EVB-1", "connectors": []}])


def test_select_default_is_soft():
    sel = ResetTypeSelect(
        HomeAssistant(), ConfigEntry(), _FakeCoord(_data()), "EVB-1",
    )
    assert sel.current_option == "Soft"


def test_select_reads_from_entry_data():
    """Reset type is stored in entry.data, not options — flipping it
    shouldn't trigger the options-reload listener."""
    entry = ConfigEntry(data={DATA_RESET_TYPE: {"EVB-1": "Hard"}})
    sel = ResetTypeSelect(
        HomeAssistant(), entry, _FakeCoord(_data()), "EVB-1",
    )
    assert sel.current_option == "Hard"


def test_select_options_exposes_soft_hard():
    sel = ResetTypeSelect(
        HomeAssistant(), ConfigEntry(), _FakeCoord(_data()), "EVB-1",
    )
    assert set(sel._attr_options) == {"Soft", "Hard"}


def test_select_set_value_persists():
    entry = ConfigEntry()
    sel = ResetTypeSelect(
        HomeAssistant(), entry, _FakeCoord(_data()), "EVB-1",
    )
    asyncio.run(sel.async_select_option("Hard"))
    assert entry.data.get(DATA_RESET_TYPE, {}).get("EVB-1") == "Hard"


def test_select_rejects_invalid_option():
    sel = ResetTypeSelect(
        HomeAssistant(), ConfigEntry(), _FakeCoord(_data()), "EVB-1",
    )
    with pytest.raises(ValueError):
        asyncio.run(sel.async_select_option("Nuclear"))


def test_select_disabled_by_default():
    """Reset type is a niche control — off by default."""
    assert ResetTypeSelect._attr_entity_registry_enabled_default is False
