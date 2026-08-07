"""Tests for saved charging-card helpers."""
from __future__ import annotations

from tapelectric.charging_cards import (
    charging_cards,
    is_valid_id_tag,
    normalize_id_tag,
    seed_default_selections,
    selected_card,
    validate_card_input,
)
from tapelectric.const import DATA_SELECTED_CHARGING_CARD_IDS


_OPTIONS = {
    "charging_cards": [
        {"id": "work", "label": "Employer", "id_tag": "12AB34CD"},
        {"id": "home", "label": "Personal", "id_tag": "89ABCDEF"},
    ],
    "default_charging_card_id": "work",
}


def test_normalize_and_validate_id_tag():
    assert normalize_id_tag("12-ab:34 cd") == "12AB34CD"
    assert is_valid_id_tag("12AB34CD") is True
    assert is_valid_id_tag("TAP-1234") is False


def test_card_input_normalizes_and_rejects_duplicates():
    label, id_tag, errors = validate_card_input(
        _OPTIONS["charging_cards"], " Partner ", "aa-bb:cc dd",
    )
    assert (label, id_tag, errors) == ("Partner", "AABBCCDD", {})

    _, _, errors = validate_card_input(
        _OPTIONS["charging_cards"], " employer ", "00112233",
    )
    assert errors == {"label": "duplicate_label"}

    _, _, errors = validate_card_input(
        _OPTIONS["charging_cards"], "Partner", "12-ab-34-cd",
    )
    assert errors == {"id_tag": "duplicate_id_tag"}


def test_card_input_rejects_empty_label_and_invalid_uid():
    _, _, errors = validate_card_input([], "  ", "not-a-uid")
    assert errors == {
        "label": "empty_label",
        "id_tag": "invalid_id_tag",
    }


def test_ambiguous_persisted_cards_fail_closed():
    options = {
        "charging_cards": [
            {"id": "one", "label": "Card", "id_tag": "12AB34CD"},
            {"id": "two", "label": "card", "id_tag": "89ABCDEF"},
        ],
    }
    assert charging_cards(options) == []

    options["charging_cards"] = [
        {"id": "one", "label": "One", "id_tag": "12AB34CD"},
        {"id": "two", "label": "Two", "id_tag": "12-ab-34-cd"},
    ]
    assert charging_cards(options) == []


def test_selected_card_requires_explicit_valid_selection():
    data = {DATA_SELECTED_CHARGING_CARD_IDS: {"EVB-1": "home"}}
    assert selected_card(data, _OPTIONS, "EVB-1") == _OPTIONS["charging_cards"][1]
    assert selected_card(data, _OPTIONS, "EVB-2") is None


def test_default_seeds_only_missing_chargers():
    data = {DATA_SELECTED_CHARGING_CARD_IDS: {"EVB-1": "removed-card"}}
    seeded = seed_default_selections(data, _OPTIONS, ["EVB-1", "EVB-2"])
    assert seeded[DATA_SELECTED_CHARGING_CARD_IDS] == {
        "EVB-1": "removed-card",
        "EVB-2": "work",
    }