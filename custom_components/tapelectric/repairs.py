"""Repairs integration — surfaces actionable issues in the HA Repairs panel.

Three conditions generate issues:
  - auth_expired       two consecutive 401 responses from /chargers
  - charger_offline    charger has been UNAVAILABLE for > 24h
  - write_blocked      user attempted a write while write_enabled=False
                       (rate-limited to avoid issue-spam on a busy
                       automation; re-raised at most once per hour per
                       config entry)
"""
from __future__ import annotations

import time

from homeassistant.core import HomeAssistant
from homeassistant.helpers.issue_registry import (
    IssueSeverity,
    async_create_issue,
    async_delete_issue,
)

from .const import DOMAIN

_WRITE_BLOCKED_COOLDOWN_S = 3600
_WRITE_BLOCKED_LAST: dict[str, float] = {}


def _issue_id(kind: str, key: str) -> str:
    return f"{kind}__{key}"


# ── auth_expired ────────────────────────────────────────────────────────

def note_auth_failure(hass: HomeAssistant, entry_id: str) -> None:
    async_create_issue(
        hass, DOMAIN, _issue_id("auth_expired", entry_id),
        is_fixable=False,
        severity=IssueSeverity.ERROR,
        translation_key="auth_expired",
        translation_placeholders={"entry_id": entry_id},
    )


def clear_auth_failure(hass: HomeAssistant, entry_id: str) -> None:
    async_delete_issue(hass, DOMAIN, _issue_id("auth_expired", entry_id))


# ── charger_offline ─────────────────────────────────────────────────────

def note_charger_offline(hass: HomeAssistant, charger_id: str) -> None:
    async_create_issue(
        hass, DOMAIN, _issue_id("charger_offline", charger_id),
        is_fixable=False,
        severity=IssueSeverity.WARNING,
        translation_key="charger_offline",
        translation_placeholders={"charger_id": charger_id},
    )


def clear_charger_offline(hass: HomeAssistant, charger_id: str) -> None:
    async_delete_issue(hass, DOMAIN, _issue_id("charger_offline", charger_id))


# ── write_blocked ───────────────────────────────────────────────────────

def note_write_blocked(hass: HomeAssistant, entry_id: str) -> None:
    now = time.monotonic()
    last = _WRITE_BLOCKED_LAST.get(entry_id, 0.0)
    if now - last < _WRITE_BLOCKED_COOLDOWN_S:
        return
    _WRITE_BLOCKED_LAST[entry_id] = now
    async_create_issue(
        hass, DOMAIN, _issue_id("write_blocked", entry_id),
        is_fixable=False,
        severity=IssueSeverity.WARNING,
        translation_key="write_blocked",
        translation_placeholders={"entry_id": entry_id},
    )


def clear_write_blocked(hass: HomeAssistant, entry_id: str) -> None:
    _WRITE_BLOCKED_LAST.pop(entry_id, None)
    async_delete_issue(hass, DOMAIN, _issue_id("write_blocked", entry_id))
