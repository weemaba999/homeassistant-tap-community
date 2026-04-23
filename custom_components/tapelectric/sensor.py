"""Sensor platform for Tap Electric.

Registration philosophy: every candidate entity is registered at setup
time. Availability is driven by `measurand_freshness` against the
options-configurable `stale_threshold_minutes`, so users don't lose
history when their charger's firmware temporarily stops emitting a
measurand (OCPP MeterValuesSample config change, reboot, etc.). Niche
measurands default to disabled in the entity registry to keep the
device page readable.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    PERCENTAGE,
    UnitOfElectricCurrent,
    UnitOfElectricPotential,
    UnitOfEnergy,
    UnitOfFrequency,
    UnitOfPower,
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    DEFAULT_ENABLED,
    DOMAIN,
    MANUFACTURER,
    MEASURANDS,
    OPT_ROUND_ENERGY_DECIMALS,
    OPT_ROUND_POWER_DECIMALS,
    PHASE_AWARE_MEASURANDS,
    PHASES,
)
from .coordinator import TapCoordinator


# ── Measurand → HA typing maps ──────────────────────────────────────────
# Kept explicit (rather than inferred from substring) so a typo in the
# measurand constant never silently picks a wrong device_class.

_ENERGY_REGISTERS = frozenset({
    "Energy.Active.Import.Register",
    "Energy",
    "Energy.Active.Export.Register",
    "Energy.Reactive.Import.Register",
})
_ENERGY_INTERVALS = frozenset({
    "Energy.Active.Import.Interval",
})
_POWER_MEASURANDS = frozenset({
    "Power.Active.Import",
    "Power.Active.Export",
    "Power.Offered",
    "Power.Reactive.Import",
})
_CURRENT_MEASURANDS = frozenset({
    "Current.Import",
    "Current.Export",
    "Current.Offered",
})

_DEVICE_CLASS: dict[str, SensorDeviceClass] = {
    **{m: SensorDeviceClass.ENERGY for m in _ENERGY_REGISTERS | _ENERGY_INTERVALS},
    **{m: SensorDeviceClass.POWER for m in _POWER_MEASURANDS},
    **{m: SensorDeviceClass.CURRENT for m in _CURRENT_MEASURANDS},
    "Voltage":     SensorDeviceClass.VOLTAGE,
    "Frequency":   SensorDeviceClass.FREQUENCY,
    "SoC":         SensorDeviceClass.BATTERY,
    "Temperature": SensorDeviceClass.TEMPERATURE,
    "Power.Factor": SensorDeviceClass.POWER_FACTOR,
}

_UNIT: dict[str, str] = {
    **{m: UnitOfEnergy.KILO_WATT_HOUR for m in _ENERGY_REGISTERS | _ENERGY_INTERVALS},
    **{m: UnitOfPower.KILO_WATT for m in _POWER_MEASURANDS},
    **{m: UnitOfElectricCurrent.AMPERE for m in _CURRENT_MEASURANDS},
    "Voltage":     UnitOfElectricPotential.VOLT,
    "Frequency":   UnitOfFrequency.HERTZ,
    "SoC":         PERCENTAGE,
    "Temperature": UnitOfTemperature.CELSIUS,
    "Power.Factor": None,   # dimensionless
}


def _state_class_for(measurand: str) -> SensorStateClass:
    if measurand in _ENERGY_REGISTERS:
        return SensorStateClass.TOTAL_INCREASING
    return SensorStateClass.MEASUREMENT


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coord: TapCoordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]

    entities: list[SensorEntity] = []
    for charger in coord.data.chargers:
        cid = charger.get("id")
        if not cid:
            continue

        # ── Always-on sensors (preserve v2 unique_ids) ─────────────────
        entities += [
            ChargerStatusSensor(coord, cid),
            ConnectorStatusSensor(coord, cid),
            SessionEnergySensor(coord, cid),
            SessionDurationSensor(coord, cid),
            LastSessionEnergySensor(coord, cid),
        ]

        # ── Measurand-backed sensors: always register, avail. gated ────
        for ocpp_name, internal_key, _enabled in MEASURANDS:
            if ocpp_name in PHASE_AWARE_MEASURANDS:
                for phase in PHASES:
                    entities.append(
                        MeasurandSensor(coord, cid, ocpp_name, internal_key, phase)
                    )
            else:
                entities.append(
                    MeasurandSensor(coord, cid, ocpp_name, internal_key, None)
                )

        # ── Charger metadata + tariff (both opt-in) ────────────────────
        entities.append(ChargerInfoSensor(coord, cid))
        entities.append(TariffSensor(coord, cid))

    async_add_entities(entities)


# ── Base ────────────────────────────────────────────────────────────────

class _TapBase(CoordinatorEntity[TapCoordinator], SensorEntity):
    _attr_has_entity_name = True

    def __init__(self, coord: TapCoordinator, charger_id: str) -> None:
        super().__init__(coord)
        self._cid = charger_id
        c = coord.data.charger(charger_id) or {}

        # Human name fallback chain:
        # charger.name → "{brand} {serialNumber}" → session.location.name
        # → "Tap Charger {id[:8]}"
        name = c.get("name")
        if not name:
            brand = c.get("brand")
            serial = c.get("serialNumber")
            if brand and serial:
                name = f"{brand} {serial}"
        if not name:
            for s in coord.data.recent_sessions:
                if (s.get("charger") or {}).get("id") == charger_id:
                    loc = (s.get("location") or {}).get("name")
                    if loc:
                        name = loc
                        break
        if not name:
            name = f"Tap Charger {charger_id[:8]}"

        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, charger_id)},
            manufacturer=c.get("brand") or MANUFACTURER,
            name=name,
            model=c.get("model") or c.get("brand"),
            sw_version=c.get("firmwareVersion"),
            hw_version=c.get("serialNumber"),
        )


# ── Always-on sensors (v2 unique_ids preserved) ─────────────────────────

class ChargerStatusSensor(_TapBase):
    _attr_icon = "mdi:ev-station"
    _attr_translation_key = "charger_status"

    def __init__(self, coord: TapCoordinator, charger_id: str) -> None:
        super().__init__(coord, charger_id)
        self._attr_unique_id = f"{charger_id}_status"
        self._attr_name = "Status"

    @property
    def native_value(self) -> str | None:
        """Composite status — connector truth beats stale charger.status.

        Observed on Tap's API: `charger.status` can be UNAVAILABLE while
        the connector reports AVAILABLE and the charger is actually
        online (web UI and physical LEDs confirm). Connector status is
        the live signal. We only fall back to charger.status when every
        connector is UNAVAILABLE or missing.
        """
        c = self.coordinator.data.charger(self._cid)
        if not c:
            return None
        connector_statuses = [
            conn.get("status") for conn in (c.get("connectors") or [])
        ]
        non_unavailable = [
            s for s in connector_statuses if s and s != "UNAVAILABLE"
        ]
        if non_unavailable:
            priority = (
                "CHARGING", "SUSPENDEDEV", "SUSPENDEDEVSE",
                "PREPARING", "FINISHING", "FAULTED", "AVAILABLE",
            )
            for preferred in priority:
                if preferred in non_unavailable:
                    return preferred
            return non_unavailable[0]
        # All connectors UNAVAILABLE or no connectors → last-resort
        # charger-level value (typically also UNAVAILABLE).
        return c.get("status")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return self.coordinator.data.charger(self._cid) or {}


class ConnectorStatusSensor(_TapBase):
    """OCPP connector status — leading signal for 'is actually charging'."""
    _attr_icon = "mdi:ev-plug-type2"

    def __init__(self, coord: TapCoordinator, charger_id: str) -> None:
        super().__init__(coord, charger_id)
        self._attr_unique_id = f"{charger_id}_connector_status"
        self._attr_name = "Connector status"

    @property
    def native_value(self) -> str | None:
        connectors = self.coordinator.data.connectors(self._cid)
        if not connectors:
            return None
        statuses = [conn.get("status") for conn in connectors]
        # Aggregate: most-interesting-wins so multi-connector chargers
        # surface the busy side.
        for preferred in ("CHARGING", "SUSPENDEDEV", "SUSPENDEDEVSE",
                          "PREPARING", "FINISHING", "FAULTED",
                          "UNAVAILABLE", "AVAILABLE"):
            if preferred in statuses:
                return preferred
        return statuses[0]


class SessionEnergySensor(_TapBase):
    """Live session energy.

    Tap's /charger-sessions list endpoint reports `wh: 0` for active
    sessions until the session closes — the web UI gets live values
    from somewhere else. session-meter-data (which we already poll for
    an active session) carries the real OCPP Energy readings, so we
    read from there first and fall back to session.wh only when no
    meter data is available yet (very new session, or a firmware that
    doesn't emit an Energy measurand live).
    """
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR

    # Try the canonical OCPP register first, then the EVBox short form.
    _ENERGY_MEASURANDS = ("Energy.Active.Import.Register", "Energy")

    def __init__(self, coord: TapCoordinator, charger_id: str) -> None:
        super().__init__(coord, charger_id)
        self._attr_unique_id = f"{charger_id}_session_energy"
        self._attr_name = "Session energy"

    def _latest_energy_reading(self) -> dict | None:
        """Return the freshest Energy.* meter row we have for this charger."""
        newest: dict | None = None
        for measurand in self._ENERGY_MEASURANDS:
            m = self.coordinator.data.latest_meter(self._cid, measurand, None)
            if not m:
                continue
            if newest is None or (m.get("measuredAt") or "") > (newest.get("measuredAt") or ""):
                newest = m
        return newest

    @staticmethod
    def _to_kwh(raw: Any, unit: str | None) -> float | None:
        try:
            value = float(raw)
        except (TypeError, ValueError):
            return None
        u = (unit or "").upper()
        if u == "KWH":
            return round(value, 3)
        # Default and documented Tap unit is Wh.
        return round(value / 1000, 3)

    @property
    def native_value(self) -> float | None:
        s = self.coordinator.data.active_for(self._cid)
        if not s:
            return 0.0

        m = self._latest_energy_reading()
        if m is not None:
            kwh = self._to_kwh(m.get("value"), m.get("unit"))
            if kwh is not None:
                return kwh

        # No meter data yet (fresh session / silent firmware) — use
        # the list-endpoint value, which is stale during live sessions
        # but correct once the session closes.
        wh = s.get("wh")
        return round(wh / 1000, 3) if isinstance(wh, (int, float)) else 0.0

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        s = self.coordinator.data.active_for(self._cid) or {}
        m = self._latest_energy_reading() or {}
        # For diagnostics: what measurands did we actually receive this
        # poll? Useful for troubleshooting silent firmwares.
        meter_map = self.coordinator.data.meter_by_charger.get(self._cid, {})
        measurands_seen = sorted({k[0] for k in meter_map.keys() if k[0]})
        return {
            "session_id":         s.get("id"),
            "started_at":         s.get("startedAt"),
            "connector_id":       (s.get("charger") or {}).get("connectorId"),
            "location_id":        (s.get("location") or {}).get("id"),
            "list_endpoint_wh":   s.get("wh"),
            "latest_measurand":   m.get("measurand"),
            "latest_measured_at": m.get("measuredAt"),
            "latest_raw_value":   m.get("value"),
            "latest_raw_unit":    m.get("unit"),
            "measurands_seen":    measurands_seen,
        }


class SessionDurationSensor(_TapBase):
    _attr_icon = "mdi:timer-outline"
    _attr_native_unit_of_measurement = "min"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coord: TapCoordinator, charger_id: str) -> None:
        super().__init__(coord, charger_id)
        self._attr_unique_id = f"{charger_id}_session_duration"
        self._attr_name = "Session duration"

    @property
    def native_value(self) -> int | None:
        s = self.coordinator.data.active_for(self._cid)
        if not s or not s.get("startedAt"):
            return 0
        try:
            started = datetime.fromisoformat(
                s["startedAt"].replace("Z", "+00:00")
            )
        except ValueError:
            return None
        delta = datetime.now(timezone.utc) - started
        return max(0, int(delta.total_seconds() // 60))


class LastSessionEnergySensor(_TapBase):
    """Energy of the most recent COMPLETED session (endedAt set)."""
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.TOTAL
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_icon = "mdi:history"

    def __init__(self, coord: TapCoordinator, charger_id: str) -> None:
        super().__init__(coord, charger_id)
        self._attr_unique_id = f"{charger_id}_last_session_energy"
        self._attr_name = "Last session energy"

    def _last(self) -> dict | None:
        candidates = [
            s for s in self.coordinator.data.recent_sessions
            if s.get("endedAt") is not None
            and (s.get("charger") or {}).get("id") == self._cid
        ]
        if not candidates:
            return None
        candidates.sort(key=lambda s: s.get("endedAt") or "", reverse=True)
        return candidates[0]

    @property
    def native_value(self) -> float | None:
        s = self._last()
        if not s:
            return None
        wh = s.get("wh")
        return round(wh / 1000, 3) if isinstance(wh, (int, float)) else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        s = self._last() or {}
        return {
            "session_id": s.get("id"),
            "started_at": s.get("startedAt"),
            "ended_at": s.get("endedAt"),
            "connector_id": (s.get("charger") or {}).get("connectorId"),
            "location_id": (s.get("location") or {}).get("id"),
        }


# ── MeasurandSensor (one class, data-driven) ────────────────────────────

class MeasurandSensor(_TapBase):
    """One HA sensor per (charger, OCPP measurand, phase?) combination.

    Always registered. `available` flips to False when the freshest value
    is older than the coordinator's `stale_threshold()` — prevents a
    2-hour-old voltage reading from looking live after the cable is
    unplugged. Threshold is user-tunable via the Options flow.
    """

    def __init__(
        self,
        coord: TapCoordinator,
        charger_id: str,
        measurand: str,
        internal_key: str,
        phase: str | None,
    ) -> None:
        super().__init__(coord, charger_id)
        self._measurand = measurand
        self._phase = phase
        suffix = f"_{phase.lower()}" if phase else ""
        self._attr_unique_id = f"{charger_id}_{internal_key}{suffix}"

        # Human label: "Current L1", "Power active import", "SoC", …
        label = internal_key.replace("_", " ").capitalize()
        self._attr_name = f"{label}{' ' + phase if phase else ''}".strip()

        self._attr_device_class = _DEVICE_CLASS.get(measurand)
        self._attr_native_unit_of_measurement = _UNIT.get(measurand)
        self._attr_state_class = _state_class_for(measurand)
        self._attr_entity_registry_enabled_default = DEFAULT_ENABLED.get(
            measurand, True
        )

    # ── value conversions ──────────────────────────────────────────────
    def _convert(self, value: float, unit: str | None) -> float:
        """Normalise the raw meter value to the entity's declared unit."""
        u = (unit or "").upper()
        # Energy: normalise to kWh
        if self._measurand in _ENERGY_REGISTERS | _ENERGY_INTERVALS:
            if u in ("WH", "W.H"):
                return value / 1000
            if u in ("KWH", ""):
                return value
            # Tap has occasionally been observed reporting bare "J" — drop
            # the sample by returning it unchanged; the user will notice.
            return value
        # Power: normalise to kW
        if self._measurand in _POWER_MEASURANDS:
            if u == "W":
                return value / 1000
            return value
        # Temperature: OCPP default is °C. If firmware sends F or K, we
        # don't convert — this is exceedingly rare and non-lossy.
        return value

    def _decimals(self) -> int:
        opts = {**self.coordinator.entry.options}
        if self._measurand in _ENERGY_REGISTERS | _ENERGY_INTERVALS:
            return int(opts.get(OPT_ROUND_ENERGY_DECIMALS, 3))
        if self._measurand in _POWER_MEASURANDS:
            return int(opts.get(OPT_ROUND_POWER_DECIMALS, 2))
        return 3

    @property
    def native_value(self) -> float | None:
        m = self.coordinator.data.latest_meter(
            self._cid, self._measurand, self._phase
        )
        if m:
            try:
                raw = float(m.get("value"))
            except (TypeError, ValueError):
                raw = None
            if raw is not None:
                return round(self._convert(raw, m.get("unit")), self._decimals())

        # Fallback: for energy-register measurands with no live meter
        # reading, use the /charger-sessions `wh` for the active session.
        # It's stale during a live session (Tap API quirk) but it's a
        # non-zero starting point; when meter data arrives this branch
        # stops being hit.
        if self._phase is None and self._measurand in _ENERGY_REGISTERS:
            s = self.coordinator.data.active_for(self._cid)
            if s:
                wh = s.get("wh")
                if isinstance(wh, (int, float)):
                    return round(wh / 1000, self._decimals())
        return None

    @property
    def available(self) -> bool:
        if not super().available:
            return False
        # Energy register fallback via session.wh keeps the entity live
        # while the OCPP meter stream hasn't started.
        if (
            self._phase is None
            and self._measurand in _ENERGY_REGISTERS
            and self.coordinator.data.active_for(self._cid)
        ):
            return True
        ts = self.coordinator.data.measurand_freshness(
            self._cid, self._measurand, self._phase
        )
        if ts is None:
            return False
        return (datetime.now(timezone.utc) - ts) <= self.coordinator.stale_threshold()

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        m = self.coordinator.data.latest_meter(
            self._cid, self._measurand, self._phase
        )
        if not m:
            return {}
        return {
            "raw_unit": m.get("unit"),
            "measured_at": m.get("measuredAt"),
            "ocpp_measurand": self._measurand,
            "phase": self._phase,
        }


# ── Charger metadata + tariff (diagnostic-ish, off by default) ─────────

class ChargerInfoSensor(_TapBase):
    """Static-ish charger metadata: firmware in state, rest in attributes."""
    _attr_icon = "mdi:information-outline"
    _attr_entity_registry_enabled_default = False

    def __init__(self, coord: TapCoordinator, charger_id: str) -> None:
        super().__init__(coord, charger_id)
        self._attr_unique_id = f"{charger_id}_info"
        self._attr_name = "Info"

    @property
    def native_value(self) -> str | None:
        c = self.coordinator.data.charger(self._cid) or {}
        return c.get("firmwareVersion") or c.get("serverPartition")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        c = self.coordinator.data.charger(self._cid) or {}
        return {
            k: v for k, v in {
                "brand":        c.get("brand"),
                "model":        c.get("model"),
                "serial":       c.get("serialNumber"),
                "firmware":     c.get("firmwareVersion"),
                "access_mode":  c.get("accessMode"),
                "display_mode": c.get("displayMode"),
                "location_id":  c.get("locationId"),
                "partition":    c.get("serverPartition"),
                "updated_at":   c.get("updatedAt"),
            }.items() if v is not None
        }


class TariffSensor(_TapBase):
    """Currently-active tariff for this charger, if configured."""
    _attr_icon = "mdi:cash-multiple"
    _attr_entity_registry_enabled_default = False

    def __init__(self, coord: TapCoordinator, charger_id: str) -> None:
        super().__init__(coord, charger_id)
        self._attr_unique_id = f"{charger_id}_tariff"
        self._attr_name = "Active tariff"

    def _active_tariff(self) -> dict | None:
        # Schema unverified: match by chargerId or locationId, fall back
        # to the first available tariff for single-location setups.
        tariffs = self.coordinator.data.tariffs
        charger = self.coordinator.data.charger(self._cid) or {}
        loc_id = charger.get("locationId")
        for t in tariffs:
            if t.get("chargerId") == self._cid:
                return t
            if loc_id and t.get("locationId") == loc_id:
                return t
        return tariffs[0] if tariffs else None

    @property
    def native_value(self) -> str | None:
        t = self._active_tariff()
        if not t:
            return None
        return t.get("name") or t.get("description") or "configured"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        t = self._active_tariff() or {}
        return {k: v for k, v in t.items() if k != "id"}
