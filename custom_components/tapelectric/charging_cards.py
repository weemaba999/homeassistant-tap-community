"""Saved charging-card helpers."""
from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Any

from .const import (
    DATA_SELECTED_CHARGING_CARD_IDS,
    OPT_CHARGING_CARDS,
    OPT_DEFAULT_CHARGING_CARD_ID,
)

_ID_TAG_RE = re.compile(r"^[0-9A-F]{8}$")


def normalize_id_tag(value: str) -> str:
    """Normalize a user-entered NFC UID."""
    return re.sub(r"[\s:-]", "", value).upper()


def is_valid_id_tag(value: str) -> bool:
    """Return whether a normalized UID is exactly eight hex characters."""
    return _ID_TAG_RE.fullmatch(value) is not None


def validate_card_input(
    cards: Iterable[Mapping[str, Any]],
    label: str,
    id_tag: str,
    *,
    exclude_card_id: str | None = None,
    validate_id_tag: bool = True,
) -> tuple[str, str, dict[str, str]]:
    """Normalize and validate one card form submission."""
    clean_label = label.strip()
    clean_id_tag = normalize_id_tag(id_tag)
    other_cards = [
        card for card in cards if card.get("id") != exclude_card_id
    ]
    errors: dict[str, str] = {}

    if not clean_label:
        errors["label"] = "empty_label"
    elif any(
        str(card.get("label", "")).strip().casefold()
        == clean_label.casefold()
        for card in other_cards
    ):
        errors["label"] = "duplicate_label"

    if validate_id_tag and not is_valid_id_tag(clean_id_tag):
        errors["id_tag"] = "invalid_id_tag"
    elif clean_id_tag and any(
        normalize_id_tag(str(card.get("id_tag", ""))) == clean_id_tag
        for card in other_cards
    ):
        errors["id_tag"] = "duplicate_id_tag"

    return clean_label, clean_id_tag, errors


def charging_cards(options: Mapping[str, Any]) -> list[dict[str, str]]:
    """Return stored cards, or no cards when persisted data is ambiguous."""
    raw_cards = options.get(OPT_CHARGING_CARDS)
    if not isinstance(raw_cards, list):
        return []

    cards: list[dict[str, str]] = []
    for raw_card in raw_cards:
        if not isinstance(raw_card, dict):
            return []
        card_id = raw_card.get("id")
        label = raw_card.get("label")
        id_tag = raw_card.get("id_tag")
        if not all(isinstance(value, str) and value.strip()
                   for value in (card_id, label, id_tag)):
            return []
        cards.append({
            "id": card_id.strip(),
            "label": label.strip(),
            "id_tag": id_tag.strip(),
        })

    if len({card["id"] for card in cards}) != len(cards):
        return []
    if len({card["label"].casefold() for card in cards}) != len(cards):
        return []
    if len({normalize_id_tag(card["id_tag"]) for card in cards}) != len(cards):
        return []
    return cards


def card_by_id(
    options: Mapping[str, Any], card_id: object,
) -> dict[str, str] | None:
    """Resolve one unambiguous saved card by its internal ID."""
    if not isinstance(card_id, str):
        return None
    return next(
        (card for card in charging_cards(options) if card["id"] == card_id),
        None,
    )


def selected_card(
    data: Mapping[str, Any],
    options: Mapping[str, Any],
    charger_id: str,
) -> dict[str, str] | None:
    """Resolve the explicitly selected card for one charger."""
    selections = data.get(DATA_SELECTED_CHARGING_CARD_IDS)
    if not isinstance(selections, dict) or charger_id not in selections:
        return None
    return card_by_id(options, selections[charger_id])


def seed_default_selections(
    data: Mapping[str, Any],
    options: Mapping[str, Any],
    charger_ids: Iterable[str],
) -> dict[str, Any]:
    """Seed only chargers that have never stored a card selection."""
    default_id = options.get(OPT_DEFAULT_CHARGING_CARD_ID)
    if card_by_id(options, default_id) is None:
        return dict(data)

    raw_selections = data.get(DATA_SELECTED_CHARGING_CARD_IDS)
    selections = (
        dict(raw_selections) if isinstance(raw_selections, dict) else {}
    )
    for charger_id in charger_ids:
        selections.setdefault(charger_id, default_id)

    new_data = dict(data)
    if selections:
        new_data[DATA_SELECTED_CHARGING_CARD_IDS] = selections
    return new_data