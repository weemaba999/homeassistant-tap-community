"""Tap Electric webhook receiver for Home Assistant.

Verifies X-Tap-Signature using HMAC-SHA256 on `{timestamp}.{raw_body}`.
This part is derived directly from the official developer docs.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import time
from typing import Any

from aiohttp import web
from homeassistant.components import webhook
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import (
    DOMAIN,
    EVENT_SESSION_ENDED,
    EVENT_SESSION_STARTED,
    EVENT_SESSION_UPDATED,
    EVENT_TOKEN_AUTHORIZATION,
    WEBHOOK_MAX_AGE_SECONDS,
    WEBHOOK_SIGNATURE_HEADER,
    WEBHOOK_TIMESTAMP_HEADER,
)

_LOGGER = logging.getLogger(__name__)


def verify_signature(secret: str, timestamp: str, raw_body: str, provided: str) -> bool:
    """HMAC-SHA256 over `{timestamp}.{raw_body}` — upper-hex, constant-time."""
    if not secret or not timestamp or not raw_body or not provided:
        return False
    # Reject stale timestamps to prevent replay attacks.
    try:
        if abs(time.time() - int(timestamp)) > WEBHOOK_MAX_AGE_SECONDS:
            _LOGGER.warning("Webhook timestamp %s too old / in future", timestamp)
            return False
    except ValueError:
        return False
    payload = f"{timestamp}.{raw_body}".encode("utf-8")
    computed = hmac.new(
        secret.encode("utf-8"), payload, hashlib.sha256
    ).hexdigest().upper()
    return hmac.compare_digest(computed, provided.upper())


async def async_register_webhook(
    hass: HomeAssistant, entry: ConfigEntry, secret: str
) -> None:
    """Register a webhook that fires HA events per Tap event type."""
    webhook_id = entry.data.get("webhook_id") or entry.entry_id

    async def _handle(hass: HomeAssistant, webhook_id: str, request: web.Request) -> web.Response:
        # Read raw body BEFORE parsing — signature is over the exact bytes
        raw = await request.text()
        sig = request.headers.get(WEBHOOK_SIGNATURE_HEADER, "")
        ts = request.headers.get(WEBHOOK_TIMESTAMP_HEADER, "")

        if not verify_signature(secret, ts, raw, sig):
            _LOGGER.warning("Webhook signature verification failed")
            return web.Response(status=401, text="invalid signature")

        try:
            payload: dict[str, Any] = await request.json()
        except Exception:
            return web.Response(status=400, text="invalid json")

        event_type = payload.get("type", "unknown")
        event_data = payload.get("data", {})
        _LOGGER.debug("Received Tap webhook type=%s id=%s",
                      event_type, payload.get("id"))

        # Fire a generic HA event so automations can hook in.
        hass.bus.async_fire(
            f"{DOMAIN}_webhook",
            {"type": event_type, "data": event_data, "id": payload.get("id")},
        )

        # Also fire more specific events for the known types.
        if event_type in {
            EVENT_TOKEN_AUTHORIZATION,
            EVENT_SESSION_STARTED,
            EVENT_SESSION_UPDATED,
            EVENT_SESSION_ENDED,
        }:
            hass.bus.async_fire(f"{DOMAIN}_{event_type.lower()}", event_data)

        # Nudge the coordinator to refresh immediately.
        coord = hass.data.get(DOMAIN, {}).get(entry.entry_id, {}).get("coordinator")
        if coord is not None:
            await coord.async_request_refresh()

        return web.Response(status=200, text="ok")

    webhook.async_register(
        hass, DOMAIN, "Tap Electric", webhook_id, _handle,
    )
    _LOGGER.info(
        "Tap Electric webhook registered at /api/webhook/%s — paste this URL "
        "(with your external HA base) into the Tap dashboard.",
        webhook_id,
    )


async def async_unregister_webhook(hass: HomeAssistant, entry: ConfigEntry) -> None:
    webhook_id = entry.data.get("webhook_id") or entry.entry_id
    webhook.async_unregister(hass, webhook_id)
