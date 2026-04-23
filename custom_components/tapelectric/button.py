"""Button platform — single OCPP Reset button.

The reset *type* (Soft vs Hard) is chosen via the companion
select.reset_type entity. Hard reset remains destructive; the select
keeps it a two-step action (pick Hard, then press).
"""
from __future__ import annotations

import logging

from homeassistant.components.button import ButtonDeviceClass, ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import TapElectricClient, TapElectricError
from .const import DATA_RESET_TYPE, DOMAIN, MANUFACTURER
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

    entities: list[ButtonEntity] = []
    for c in coord.data.chargers:
        cid = c.get("id")
        if not cid:
            continue
        entities.append(ResetButton(hass, entry, coord, client, cid))
    async_add_entities(entities)


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

        c = coord.data.charger(charger_id) or {}
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, charger_id)},
            manufacturer=c.get("brand") or MANUFACTURER,
            name=c.get("name") or f"Tap Charger {charger_id[:8]}",
            model=c.get("model") or c.get("brand"),
            sw_version=c.get("firmwareVersion"),
            hw_version=c.get("serialNumber"),
        )

    def _selected_reset_type(self) -> str:
        bag = self._entry.data.get(DATA_RESET_TYPE) or {}
        value = bag.get(self._cid) if isinstance(bag, dict) else None
        return value if value in ("Soft", "Hard") else "Soft"

    async def async_press(self) -> None:
        _ensure_write_enabled(self._hass, self._entry)
        reset_type = self._selected_reset_type()
        try:
            # Uses dedicated /chargers/{id}/reset endpoint (no body needed).
            # The /ocpp passthrough endpoint currently returns 400 for all
            # tested payload shapes; when that's resolved, this can be
            # switched back to pass Soft/Hard via reset_charger().
            await self._client.reset_charger_direct(self._cid)
            _LOGGER.info(
                "Reset requested on %s (type %s ignored by direct endpoint)",
                self._cid, reset_type,
            )
        except TapElectricError as err:
            _LOGGER.error("Reset failed for %s: %s", self._cid, err)
            raise
        await self.coordinator.async_request_refresh()
