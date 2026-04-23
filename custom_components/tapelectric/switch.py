"""Switch platform — pause/resume charging via OCPP SetChargingProfile.

Semantics:
  on  → SetChargingProfile with connector.maxAmperage (fallback: config)
  off → SetChargingProfile with 0A (charger → SUSPENDEDEVSE)

This does NOT start/stop the transaction — the driver-initiated session
continues. It just gates the actual power draw within the session.
"""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import TapElectricClient, TapElectricError
from .const import (
    CHARGING_LIMIT_DEFAULT_A,
    CONF_MAX_CHARGE_AMPS,
    DOMAIN,
    MANUFACTURER,
)
from .coordinator import TapCoordinator

# Late import to avoid circulars at platform-import time.
def _ensure_write_enabled(hass, entry) -> None:
    from . import ensure_write_enabled
    ensure_write_enabled(hass, entry)

_LOGGER = logging.getLogger(__name__)


def _connector_phases(conn: dict) -> int | None:
    """Derive numberPhases from whatever shape the firmware exposes.

    Not all firmwares emit a phase count; returning None tells the charger
    "decide yourself" which matches the conservative OCPP default.
    """
    ctype = (conn.get("currentType") or "").upper()
    if ctype == "THREE_PHASE":
        return 3
    if ctype == "SINGLE_PHASE":
        return 1
    phases = conn.get("phases")
    if isinstance(phases, int) and phases in (1, 3):
        return phases
    return None


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    bucket = hass.data[DOMAIN][entry.entry_id]
    coord: TapCoordinator = bucket["coordinator"]
    client: TapElectricClient = bucket["client"]
    fallback_amps = float(
        entry.data.get(CONF_MAX_CHARGE_AMPS) or CHARGING_LIMIT_DEFAULT_A
    )

    entities: list[SwitchEntity] = []
    for c in coord.data.chargers:
        cid = c.get("id")
        if not cid:
            continue
        for conn in (c.get("connectors") or [{"id": 1}]):
            raw_cid = conn.get("id") or 1
            try:
                connector_id_int = int(raw_cid)
            except (TypeError, ValueError):
                connector_id_int = 1
            entities.append(
                ChargeAllowedSwitch(
                    hass, entry, coord, client, cid,
                    connector_id_int, fallback_amps,
                )
            )
    async_add_entities(entities)


class ChargeAllowedSwitch(CoordinatorEntity[TapCoordinator], SwitchEntity):
    _attr_has_entity_name = True
    _attr_icon = "mdi:ev-plug-type2"

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        coord: TapCoordinator,
        client: TapElectricClient,
        charger_id: str,
        connector_id: int,
        fallback_amps: float,
    ) -> None:
        super().__init__(coord)
        self._hass = hass
        self._entry = entry
        self._client = client
        self._cid = charger_id
        self._connector_id = connector_id
        self._fallback_amps = fallback_amps
        self._attr_unique_id = f"{charger_id}_{connector_id}_charge_allowed"
        self._attr_name = (
            "Charging allowed"
            if connector_id == 1
            else f"Charging allowed connector {connector_id}"
        )
        c = coord.data.charger(charger_id) or {}
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, charger_id)},
            manufacturer=c.get("brand") or MANUFACTURER,
            name=c.get("name") or f"Tap Charger {charger_id[:8]}",
            model=c.get("model") or c.get("brand"),
            sw_version=c.get("firmwareVersion"),
            hw_version=c.get("serialNumber"),
        )

    # ── helpers ────────────────────────────────────────────────────────

    def _connector(self) -> dict | None:
        return self.coordinator.data.connector(self._cid, self._connector_id)

    def _resume_limit_amps(self) -> float:
        """Prefer connector.maxAmperage, fall back to the config value.

        This way, a 32 A charger resumes at 32 A instead of the hard-coded
        16 A default; a fleet-specific value configured at setup still
        wins if the firmware omits maxAmperage.
        """
        conn = self._connector() or {}
        raw = conn.get("maxAmperage")
        try:
            return float(raw) if raw is not None else self._fallback_amps
        except (TypeError, ValueError):
            return self._fallback_amps

    # ── state ──────────────────────────────────────────────────────────

    @property
    def is_on(self) -> bool:
        conn = self._connector() or {}
        return conn.get("status") in {"CHARGING", "SUSPENDEDEV"}

    @property
    def available(self) -> bool:
        conn = self._connector() or {}
        return conn.get("status") not in {"UNAVAILABLE", "FAULTED", None}

    # ── commands ───────────────────────────────────────────────────────

    async def async_turn_on(self, **kwargs: Any) -> None:
        _ensure_write_enabled(self._hass, self._entry)
        conn = self._connector() or {}
        amps = self._resume_limit_amps()
        phases = _connector_phases(conn)
        try:
            await self._client.resume_charging(
                self._cid,
                limit_amps=amps,
                connector_id=self._connector_id,
                number_phases=phases,
            )
            _LOGGER.info(
                "Resume charging on %s/c%s at %sA (phases=%s)",
                self._cid, self._connector_id, amps, phases,
            )
        except TapElectricError as err:
            _LOGGER.error("Resume failed for %s: %s", self._cid, err)
            raise
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs: Any) -> None:
        _ensure_write_enabled(self._hass, self._entry)
        try:
            await self._client.pause_charging(
                self._cid, connector_id=self._connector_id,
            )
            _LOGGER.info("Pause charging on %s/c%s", self._cid, self._connector_id)
        except TapElectricError as err:
            _LOGGER.error("Pause failed for %s: %s", self._cid, err)
            raise
        await self.coordinator.async_request_refresh()
