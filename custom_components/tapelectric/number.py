"""Number platform.

Two families:
  1. ChargeCurrentLimit — slider that calls SetChargingProfile. Per
     connector. Value persisted in entry.data so it survives reloads.
  2. AutoStop* — three HA-local thresholds (kWh / minutes / cost) per
     charger, NOT pushed anywhere. Blueprint automations pair these with
     the session_energy sensor and the charge_allowed switch to stop
     charging at a user-defined point. Persisted in entry.data.
"""
from __future__ import annotations

import logging

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfElectricCurrent, UnitOfEnergy, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import TapElectricClient, TapElectricError
from .const import (
    CONF_MAX_CHARGE_AMPS,
    CONF_MIN_CHARGE_AMPS,
    DATA_APPLIED_LIMITS,
    DATA_AUTO_STOP,
    DEFAULT_MAX_CHARGE_AMPS,
    DEFAULT_MIN_CHARGE_AMPS,
    DOMAIN,
    MANUFACTURER,
    PLUGGED_CONNECTOR_STATES,
)
from .coordinator import TapCoordinator

_LOGGER = logging.getLogger(__name__)


def _ensure_write_enabled(hass: HomeAssistant, entry: ConfigEntry) -> None:
    from . import ensure_write_enabled
    ensure_write_enabled(hass, entry)


def _connector_phases(conn: dict) -> int | None:
    ctype = (conn.get("currentType") or "").upper()
    if ctype == "THREE_PHASE":
        return 3
    if ctype == "SINGLE_PHASE":
        return 1
    phases = conn.get("phases")
    if isinstance(phases, int) and phases in (1, 3):
        return phases
    return None


def _limits_bag(entry: ConfigEntry) -> dict[str, float]:
    raw = entry.data.get(DATA_APPLIED_LIMITS) or {}
    if not isinstance(raw, dict):
        return {}
    return {str(k): float(v) for k, v in raw.items() if isinstance(v, (int, float))}


def _auto_stop_bag(entry: ConfigEntry) -> dict[str, dict[str, float]]:
    raw = entry.data.get(DATA_AUTO_STOP) or {}
    if not isinstance(raw, dict):
        return {}
    out: dict[str, dict[str, float]] = {}
    for cid, inner in raw.items():
        if not isinstance(inner, dict):
            continue
        out[str(cid)] = {
            k: float(v) for k, v in inner.items()
            if isinstance(v, (int, float))
        }
    return out


def _persist_auto_stop(
    hass: HomeAssistant, entry: ConfigEntry, charger_id: str,
    field_key: str, value: float,
) -> None:
    bag = _auto_stop_bag(entry)
    per = dict(bag.get(charger_id) or {})
    per[field_key] = float(value)
    bag[charger_id] = per
    hass.config_entries.async_update_entry(
        entry, data={**entry.data, DATA_AUTO_STOP: bag},
    )


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    bucket = hass.data[DOMAIN][entry.entry_id]
    coord: TapCoordinator = bucket["coordinator"]
    client: TapElectricClient = bucket["client"]

    min_amps = float(entry.data.get(CONF_MIN_CHARGE_AMPS) or DEFAULT_MIN_CHARGE_AMPS)
    fallback_max = float(
        entry.data.get(CONF_MAX_CHARGE_AMPS) or DEFAULT_MAX_CHARGE_AMPS
    )

    entities: list[NumberEntity] = []
    for c in coord.data.chargers:
        cid = c.get("id")
        if not cid:
            continue
        # Per-connector charge current slider.
        for conn in (c.get("connectors") or [{"id": 1}]):
            raw = conn.get("id") or 1
            try:
                conn_id = int(raw)
            except (TypeError, ValueError):
                conn_id = 1
            entities.append(
                ChargeCurrentLimit(
                    hass, entry, coord, client, cid, conn_id,
                    min_amps=min_amps, fallback_max=fallback_max,
                )
            )
        # Per-charger auto-stop thresholds (HA-local; off by default).
        entities.extend([
            AutoStopKWh(hass, entry, coord, cid),
            AutoStopMinutes(hass, entry, coord, cid),
            AutoStopCost(hass, entry, coord, cid),
        ])
    async_add_entities(entities)


# ── ChargeCurrentLimit ──────────────────────────────────────────────────

class ChargeCurrentLimit(CoordinatorEntity[TapCoordinator], NumberEntity):
    _attr_has_entity_name = True
    _attr_mode = NumberMode.SLIDER
    _attr_icon = "mdi:current-ac"
    _attr_native_unit_of_measurement = UnitOfElectricCurrent.AMPERE
    _attr_native_step = 1.0

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        coord: TapCoordinator,
        client: TapElectricClient,
        charger_id: str,
        connector_id: int,
        *,
        min_amps: float,
        fallback_max: float,
    ) -> None:
        super().__init__(coord)
        self._hass = hass
        self._entry = entry
        self._client = client
        self._cid = charger_id
        self._connector_id = connector_id
        self._min_amps = min_amps
        self._fallback_max = fallback_max

        self._attr_unique_id = f"{charger_id}_{connector_id}_charge_current_limit"
        self._attr_name = (
            "Charge current limit"
            if connector_id == 1
            else f"Charge current limit connector {connector_id}"
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

    def _connector(self) -> dict | None:
        return self.coordinator.data.connector(self._cid, self._connector_id)

    def _bag_key(self) -> str:
        return f"{self._cid}:{self._connector_id}"

    def _max_from_connector(self) -> float:
        conn = self._connector() or {}
        raw = conn.get("maxAmperage")
        try:
            return float(raw) if raw is not None else self._fallback_max
        except (TypeError, ValueError):
            return self._fallback_max

    @property
    def native_min_value(self) -> float:
        return self._min_amps

    @property
    def native_max_value(self) -> float:
        return self._max_from_connector()

    @property
    def native_value(self) -> float | None:
        bag = _limits_bag(self._entry)
        key = self._bag_key()
        if key in bag:
            return bag[key]
        return self._max_from_connector()

    @property
    def available(self) -> bool:
        conn = self._connector() or {}
        return conn.get("status") in PLUGGED_CONNECTOR_STATES

    async def async_set_native_value(self, value: float) -> None:
        _ensure_write_enabled(self._hass, self._entry)
        conn = self._connector() or {}
        phases = _connector_phases(conn)
        try:
            await self._client.set_charging_limit(
                self._cid,
                limit_amps=float(value),
                connector_id=self._connector_id,
                number_phases=phases,
            )
        except TapElectricError as err:
            _LOGGER.error(
                "Set charge current limit failed for %s/c%s: %s",
                self._cid, self._connector_id, err,
            )
            raise

        bag = _limits_bag(self._entry)
        bag[self._bag_key()] = float(value)
        self._hass.config_entries.async_update_entry(
            self._entry,
            data={**self._entry.data, DATA_APPLIED_LIMITS: bag},
        )
        _LOGGER.info(
            "Applied charge current limit %sA on %s/c%s (phases=%s)",
            value, self._cid, self._connector_id, phases,
        )
        await self.coordinator.async_request_refresh()


# ── Auto-stop thresholds (HA-local; no server-side enforcement) ─────────

class _AutoStopBase(CoordinatorEntity[TapCoordinator], NumberEntity):
    """Base for HA-local threshold numbers.

    These do not call any Tap endpoint. The value is persisted in the
    config entry and read by user-supplied blueprint automations.
    """

    _attr_has_entity_name = True
    _attr_mode = NumberMode.BOX
    _attr_entity_registry_enabled_default = False

    _field_key: str = ""
    _default: float = 0.0

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        coord: TapCoordinator,
        charger_id: str,
    ) -> None:
        super().__init__(coord)
        self._hass = hass
        self._entry = entry
        self._cid = charger_id

        c = coord.data.charger(charger_id) or {}
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, charger_id)},
            manufacturer=c.get("brand") or MANUFACTURER,
            name=c.get("name") or f"Tap Charger {charger_id[:8]}",
            model=c.get("model") or c.get("brand"),
            sw_version=c.get("firmwareVersion"),
            hw_version=c.get("serialNumber"),
        )

    @property
    def native_value(self) -> float:
        per = _auto_stop_bag(self._entry).get(self._cid) or {}
        return float(per.get(self._field_key, self._default))

    async def async_set_native_value(self, value: float) -> None:
        _persist_auto_stop(
            self._hass, self._entry, self._cid, self._field_key, float(value),
        )
        self.async_write_ha_state()


class AutoStopKWh(_AutoStopBase):
    _field_key = "kwh"
    _default = 0.0
    _attr_native_min_value = 0.0
    _attr_native_max_value = 200.0
    _attr_native_step = 0.5
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_icon = "mdi:lightning-bolt"

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._attr_unique_id = f"{self._cid}_auto_stop_kwh"
        self._attr_name = "Auto-stop kWh"


class AutoStopMinutes(_AutoStopBase):
    _field_key = "minutes"
    _default = 0.0
    _attr_native_min_value = 0.0
    _attr_native_max_value = 1440.0
    _attr_native_step = 5.0
    _attr_native_unit_of_measurement = UnitOfTime.MINUTES
    _attr_icon = "mdi:timer"

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._attr_unique_id = f"{self._cid}_auto_stop_minutes"
        self._attr_name = "Auto-stop minutes"


class AutoStopCost(_AutoStopBase):
    _field_key = "cost"
    _default = 0.0
    _attr_native_min_value = 0.0
    _attr_native_max_value = 100.0
    _attr_native_step = 0.5
    _attr_native_unit_of_measurement = "EUR"
    _attr_icon = "mdi:cash"

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._attr_unique_id = f"{self._cid}_auto_stop_cost"
        self._attr_name = "Auto-stop cost"
