"""Tests for select.py — ResetTypeSelect."""
from __future__ import annotations

import asyncio

import pytest

from tapelectric.const import DATA_RESET_TYPE
from tapelectric.coordinator import TapData
from tapelectric.select import ResetTypeSelect

from _helpers import make_entry, make_hass


class _FakeCoord:
    def __init__(self, data):
        self.data = data


def _data():
    return TapData(chargers=[{"id": "EVB-1", "connectors": []}])


def test_select_default_is_soft():
    sel = ResetTypeSelect(
        make_hass(), make_entry(), _FakeCoord(_data()), "EVB-1",
    )
    assert sel.current_option == "Soft"


def test_select_reads_from_entry_data():
    """Reset type is stored in entry.data, not options — flipping it
    shouldn't trigger the options-reload listener."""
    entry = make_entry(data={DATA_RESET_TYPE: {"EVB-1": "Hard"}})
    sel = ResetTypeSelect(
        make_hass(), entry, _FakeCoord(_data()), "EVB-1",
    )
    assert sel.current_option == "Hard"


def test_select_options_exposes_soft_hard():
    sel = ResetTypeSelect(
        make_hass(), make_entry(), _FakeCoord(_data()), "EVB-1",
    )
    assert set(sel._attr_options) == {"Soft", "Hard"}


def test_select_set_value_persists():
    entry = make_entry()
    sel = ResetTypeSelect(
        make_hass(), entry, _FakeCoord(_data()), "EVB-1",
    )
    asyncio.run(sel.async_select_option("Hard"))
    assert entry.data.get(DATA_RESET_TYPE, {}).get("EVB-1") == "Hard"


def test_select_rejects_invalid_option():
    sel = ResetTypeSelect(
        make_hass(), make_entry(), _FakeCoord(_data()), "EVB-1",
    )
    with pytest.raises(ValueError):
        asyncio.run(sel.async_select_option("Nuclear"))


def test_select_disabled_by_default():
    """Reset type is a niche control — off by default.

    Under real HA, SelectEntity exposes `entity_registry_enabled_default`
    as a property that reads from `_attr_entity_registry_enabled_default`.
    So we construct an instance and read the property — the class-level
    attribute is a descriptor, not the bool we set.
    """
    sel = ResetTypeSelect(
        make_hass(), make_entry(), _FakeCoord(_data()), "EVB-1",
    )
    assert sel.entity_registry_enabled_default is False
