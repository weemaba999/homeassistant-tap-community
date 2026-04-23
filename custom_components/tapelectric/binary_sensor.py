"""Binary sensor platform for Tap Electric.

Four sensors per charger/connector combination:
  online          — integration can see the charger as reachable
  plug_connected  — cable is physically in the car
  charging        — power is actually flowing
  fault           — the charger or the plug is in a fault state
"""
from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    DOMAIN,
    FAULTED_CHARGER_STATES,
    FAULTED_CONNECTOR_STATES,
    MANUFACTURER,
    PLUGGED_CONNECTOR_STATES,
)
from .coordinator import TapCoordinator

async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coord: TapCoordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]

    entities: list[BinarySensorEntity] = []
    for charger in coord.data.chargers:
        cid = charger.get("id")
        if not cid:
            continue
        entities.append(OnlineBinarySensor(coord, cid))
        entities.append(FaultBinarySensor(coord, cid))
        for conn in (charger.get("connectors") or [{"id": 1}]):
            raw = conn.get("id") or 1
            try:
                conn_id = int(raw)
            except (TypeError, ValueError):
                conn_id = 1
            entities.append(PlugConnectedBinarySensor(coord, cid, conn_id))
            entities.append(ChargingBinarySensor(coord, cid, conn_id))
    async_add_entities(entities)


# ── Base ────────────────────────────────────────────────────────────────

class _TapBinaryBase(CoordinatorEntity[TapCoordinator], BinarySensorEntity):
    _attr_has_entity_name = True

    def __init__(self, coord: TapCoordinator, charger_id: str) -> None:
        super().__init__(coord)
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


class _ConnectorBase(_TapBinaryBase):
    def __init__(
        self, coord: TapCoordinator, charger_id: str, connector_id: int,
    ) -> None:
        super().__init__(coord, charger_id)
        self._connector_id = connector_id

    def _connector(self) -> dict | None:
        return self.coordinator.data.connector(self._cid, self._connector_id)


# ── Sensors ─────────────────────────────────────────────────────────────

class OnlineBinarySensor(_TapBinaryBase):
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY

    def __init__(self, coord: TapCoordinator, charger_id: str) -> None:
        super().__init__(coord, charger_id)
        self._attr_unique_id = f"{charger_id}_online"
        self._attr_name = "Online"

    def _connector_statuses(self) -> list[str | None]:
        c = self.coordinator.data.charger(self._cid) or {}
        return [conn.get("status") for conn in (c.get("connectors") or [])]

    @property
    def is_on(self) -> bool:
        """Online if any connector reports a 'real' state.

        `charger.status` is unreliable — it can stick at UNAVAILABLE
        while the charger is online and every connector says AVAILABLE.
        A connector reporting anything other than UNAVAILABLE/FAULTED
        is proof that Tap's cloud is in live contact with the unit.
        """
        return any(
            s and s not in ("UNAVAILABLE", "FAULTED")
            for s in self._connector_statuses()
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        c = self.coordinator.data.charger(self._cid) or {}
        return {
            "charger_api_status": c.get("status"),
            "connector_statuses": self._connector_statuses(),
        }


class FaultBinarySensor(_TapBinaryBase):
    _attr_device_class = BinarySensorDeviceClass.PROBLEM

    def __init__(self, coord: TapCoordinator, charger_id: str) -> None:
        super().__init__(coord, charger_id)
        self._attr_unique_id = f"{charger_id}_fault"
        self._attr_name = "Fault"

    @property
    def is_on(self) -> bool:
        c = self.coordinator.data.charger(self._cid) or {}
        if (c.get("status") or "").upper() in FAULTED_CHARGER_STATES:
            return True
        for conn in (c.get("connectors") or []):
            if (conn.get("status") or "").upper() in FAULTED_CONNECTOR_STATES:
                return True
        return False

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        c = self.coordinator.data.charger(self._cid) or {}
        faulted_connectors = [
            conn.get("id") for conn in (c.get("connectors") or [])
            if (conn.get("status") or "").upper() in FAULTED_CONNECTOR_STATES
        ]
        return {
            "charger_status": c.get("status"),
            "faulted_connectors": faulted_connectors,
        }


class PlugConnectedBinarySensor(_ConnectorBase):
    _attr_device_class = BinarySensorDeviceClass.PLUG

    def __init__(
        self, coord: TapCoordinator, charger_id: str, connector_id: int,
    ) -> None:
        super().__init__(coord, charger_id, connector_id)
        self._attr_unique_id = f"{charger_id}_{connector_id}_plug_connected"
        self._attr_name = (
            "Plug connected"
            if connector_id == 1
            else f"Plug connected connector {connector_id}"
        )

    @property
    def is_on(self) -> bool:
        conn = self._connector() or {}
        return conn.get("status") in PLUGGED_CONNECTOR_STATES


class ChargingBinarySensor(_ConnectorBase):
    _attr_device_class = BinarySensorDeviceClass.POWER

    def __init__(
        self, coord: TapCoordinator, charger_id: str, connector_id: int,
    ) -> None:
        super().__init__(coord, charger_id, connector_id)
        self._attr_unique_id = f"{charger_id}_{connector_id}_charging"
        self._attr_name = (
            "Charging"
            if connector_id == 1
            else f"Charging connector {connector_id}"
        )

    @property
    def is_on(self) -> bool:
        # Advanced mode: the management API knows about a live session
        # even when the OCPP connector status has lagged behind. Prefer
        # it when available (non-None). None means "mgmt is off or the
        # current tick is degraded" → fall back to connector status.
        from_mgmt = self.coordinator.data.is_charging_active(self._cid)
        if from_mgmt is not None:
            return from_mgmt
        conn = self._connector() or {}
        return conn.get("status") == "CHARGING"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        conn = self._connector() or {}
        from_mgmt = self.coordinator.data.is_charging_active(self._cid)
        return {
            "source":           "management" if from_mgmt is not None else "public",
            "connector_status": conn.get("status"),
        }
