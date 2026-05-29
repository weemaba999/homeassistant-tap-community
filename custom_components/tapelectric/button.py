"""Button platform — OCPP Reset + Remote Start/Stop charging.

Reset uses the public-API direct endpoint; type (Soft vs Hard) is picked
from the companion select.reset_type entity (kept as a two-step action
for the destructive Hard option).

Remote Start / Remote Stop only show up in advanced mode. They use the
management-side chargerManagement/ocppMessages endpoint discovered from
web.tapelectric.app's own traffic — the public-API SetChargingProfile
path was broken in v1.0.0.
"""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.button import ButtonDeviceClass, ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import TapElectricClient, TapElectricError
from .api_management import (
    TapManagementAuthError,
    TapManagementClient,
    TapManagementError,
)
from .const import (
    CONF_ADVANCED_MODE,
    CONF_DEFAULT_ID_TAG,
    DATA_DEFAULT_OUTLET_IDS,
    DATA_RESET_TYPE,
    DOMAIN,
    MANUFACTURER,
)
from .coordinator import TapCoordinator

_LOGGER = logging.getLogger(__name__)


def _ensure_write_enabled(hass: HomeAssistant, entry: ConfigEntry) -> None:
    from . import ensure_write_enabled
    ensure_write_enabled(hass, entry)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    bucket = hass.data[DOMAIN][entry.entry_id]
    coord: TapCoordinator = bucket["coordinator"]
    client: TapElectricClient = bucket["client"]
    mgmt: TapManagementClient | None = bucket.get("mgmt")

    advanced = bool(entry.data.get(CONF_ADVANCED_MODE)) and mgmt is not None

    entities: list[ButtonEntity] = []
    for c in coord.data.chargers:
        cid = c.get("id")
        if not cid:
            continue
        entities.append(ResetButton(hass, entry, coord, client, cid))
        if advanced:
            entities.append(
                StopChargingButton(hass, entry, coord, mgmt, cid),
            )
            entities.append(
                StartChargingButton(hass, entry, coord, mgmt, cid),
            )
    async_add_entities(entities)


def _device_info_for(coord: TapCoordinator, charger_id: str) -> DeviceInfo:
    c = coord.data.charger(charger_id) or {}
    return DeviceInfo(
        identifiers={(DOMAIN, charger_id)},
        manufacturer=c.get("brand") or MANUFACTURER,
        name=c.get("name") or f"Tap Charger {charger_id[:8]}",
        model=c.get("model") or c.get("brand"),
        sw_version=c.get("firmwareVersion"),
        hw_version=c.get("serialNumber"),
    )


class ResetButton(CoordinatorEntity[TapCoordinator], ButtonEntity):
    _attr_has_entity_name = True
    _attr_device_class = ButtonDeviceClass.RESTART

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        coord: TapCoordinator,
        client: TapElectricClient,
        charger_id: str,
    ) -> None:
        super().__init__(coord)
        self._hass = hass
        self._entry = entry
        self._client = client
        self._cid = charger_id
        self._attr_unique_id = f"{charger_id}_reset"
        self._attr_name = "Reset"
        self._attr_device_info = _device_info_for(coord, charger_id)

    def _selected_reset_type(self) -> str:
        bag = self._entry.data.get(DATA_RESET_TYPE) or {}
        value = bag.get(self._cid) if isinstance(bag, dict) else None
        return value if value in ("Soft", "Hard") else "Soft"

    async def async_press(self) -> None:
        _ensure_write_enabled(self._hass, self._entry)
        reset_type = self._selected_reset_type()
        try:
            await self._client.reset_charger(self._cid, reset_type)
            _LOGGER.info(
                "Reset requested on %s (type %s, via /ocpp passthrough)",
                self._cid, reset_type,
            )
        except TapElectricError as err:
            _LOGGER.error("Reset failed for %s: %s", self._cid, err)
            raise
        await self.coordinator.async_request_refresh()


class _AdvancedButtonBase(CoordinatorEntity[TapCoordinator], ButtonEntity):
    """Shared scaffolding for the advanced-mode start/stop buttons."""

    _attr_has_entity_name = True

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        coord: TapCoordinator,
        mgmt: TapManagementClient,
        charger_id: str,
    ) -> None:
        super().__init__(coord)
        self._hass = hass
        self._entry = entry
        self._mgmt = mgmt
        self._cid = charger_id
        self._attr_device_info = _device_info_for(coord, charger_id)


class StopChargingButton(_AdvancedButtonBase):
    """Stop the currently active charging session.

    Available only while the coordinator knows an active session (from the
    management API). The OCPP RemoteStop request goes through Tap's
    chargerManagement endpoint; some charger firmwares (EVBox Elvi)
    refuse to honour it — that's logged as a warning, not an exception.
    """

    _attr_icon = "mdi:stop-circle"

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        coord: TapCoordinator,
        mgmt: TapManagementClient,
        charger_id: str,
    ) -> None:
        super().__init__(hass, entry, coord, mgmt, charger_id)
        self._attr_unique_id = f"{charger_id}_stop_charging"
        self._attr_name = "Stop charging"

    def _active_transaction_id(self) -> int | None:
        s = self.coordinator.data.mgmt_active(self._cid)
        if s is None:
            return None
        tid = s.transaction_id
        try:
            return int(tid) if tid is not None else None
        except (TypeError, ValueError):
            return None

    @property
    def available(self) -> bool:
        return self._active_transaction_id() is not None

    async def async_press(self) -> None:
        _ensure_write_enabled(self._hass, self._entry)
        tid = self._active_transaction_id()
        if tid is None:
            _LOGGER.warning(
                "Stop charging pressed but no active transaction on %s — "
                "this button should have been unavailable.", self._cid,
            )
            return
        try:
            result = await self._mgmt.remote_stop_transaction(self._cid, tid)
        except TapManagementAuthError as err:
            _LOGGER.error(
                "Stop charging unauthorised for %s: %s. Re-authenticate "
                "advanced mode under Options.", self._cid, err,
            )
            return
        if result is None:
            _LOGGER.warning(
                "Stop charging on %s returned no result (network error).",
                self._cid,
            )
            return
        status = result.get("status")
        if status == "Rejected":
            _LOGGER.warning(
                "Charger firmware rejected RemoteStop on transaction %s. "
                "This is typical for EVBox Elvi and some other models — "
                "see README known limitations.", tid,
            )
        else:
            _LOGGER.info(
                "Stop charging accepted by Tap API for %s (transaction %s)",
                self._cid, tid,
            )
        await self.coordinator.async_request_refresh()


class StartChargingButton(_AdvancedButtonBase):
    """Start a charging session via OCPP RemoteStartTransaction.

    Available only when:
      - no active session is reported by the management API, AND
      - a default RFID id_tag is configured, AND
      - an outlet_id (ou_*) is known for this charger.

    The outlet_id is captured from probe_har.py at install time; users
    without a HAR can also configure it explicitly via Options.
    """

    _attr_icon = "mdi:play-circle"

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        coord: TapCoordinator,
        mgmt: TapManagementClient,
        charger_id: str,
    ) -> None:
        super().__init__(hass, entry, coord, mgmt, charger_id)
        self._attr_unique_id = f"{charger_id}_start_charging"
        self._attr_name = "Start charging"

    def _id_tag(self) -> str | None:
        raw = self._entry.data.get(CONF_DEFAULT_ID_TAG)
        return raw.strip() if isinstance(raw, str) and raw.strip() else None

    def _outlet_id(self) -> str | None:
        bag = self._entry.data.get(DATA_DEFAULT_OUTLET_IDS) or {}
        if not isinstance(bag, dict):
            return None
        value = bag.get(self._cid)
        return value if isinstance(value, str) and value else None

    def _session_active(self) -> bool:
        # Prefer management-API truth; if that's stale fall back to the
        # connector-level plugged state so we don't blindly start a
        # session when the user's car is already charging.
        s = self.coordinator.data.mgmt_active(self._cid)
        if s is not None:
            return True
        return self.coordinator.data.is_plugged(self._cid)

    @property
    def available(self) -> bool:
        if self._id_tag() is None:
            return False
        if self._outlet_id() is None:
            return False
        return not self._session_active()

    async def async_press(self) -> None:
        _ensure_write_enabled(self._hass, self._entry)
        id_tag = self._id_tag()
        outlet_id = self._outlet_id()
        if id_tag is None or outlet_id is None:
            _LOGGER.warning(
                "Start charging pressed but id_tag/outlet_id missing — "
                "configure both under Options → Advanced mode for %s.",
                self._cid,
            )
            return
        try:
            result = await self._mgmt.remote_start_transaction(
                self._cid, outlet_id=outlet_id, id_tag=id_tag,
            )
        except TapManagementAuthError as err:
            _LOGGER.error(
                "Start charging unauthorised for %s: %s. Re-authenticate "
                "advanced mode under Options.", self._cid, err,
            )
            return
        except TapManagementError as err:
            _LOGGER.error(
                "Start charging refused by API for %s: %s", self._cid, err,
            )
            return
        if result is None:
            _LOGGER.warning(
                "Start charging on %s returned no result (network error).",
                self._cid,
            )
            return
        status = result.get("status")
        if status == "Rejected":
            _LOGGER.warning(
                "Charger firmware rejected RemoteStart on %s. EVBox Elvi "
                "and some other models refuse remote start — see README "
                "known limitations.", self._cid,
            )
        else:
            _LOGGER.info(
                "Start charging accepted by Tap API for %s (outlet %s)",
                self._cid, outlet_id,
            )
        await self.coordinator.async_request_refresh()
