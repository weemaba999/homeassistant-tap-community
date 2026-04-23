"""Select platform — holds the reset type for the Reset button."""
from __future__ import annotations

import logging

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DATA_RESET_TYPE, DOMAIN, MANUFACTURER
from .coordinator import TapCoordinator

_LOGGER = logging.getLogger(__name__)

_OPTIONS = ["Soft", "Hard"]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coord: TapCoordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]

    entities: list[SelectEntity] = []
    for c in coord.data.chargers:
        cid = c.get("id")
        if not cid:
            continue
        entities.append(ResetTypeSelect(hass, entry, coord, cid))
    async_add_entities(entities)


class ResetTypeSelect(CoordinatorEntity[TapCoordinator], SelectEntity):
    _attr_has_entity_name = True
    _attr_icon = "mdi:power-cycle"
    _attr_options = _OPTIONS
    # Niche control — power users know what they want; default hidden.
    _attr_entity_registry_enabled_default = False

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
        self._attr_unique_id = f"{charger_id}_reset_type"
        self._attr_name = "Reset type"

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
    def current_option(self) -> str:
        # Stored in entry.data (not entry.options) so flipping the
        # dropdown doesn't trigger the reload listener wired to options.
        bag = self._entry.data.get(DATA_RESET_TYPE) or {}
        value = bag.get(self._cid) if isinstance(bag, dict) else None
        return value if value in _OPTIONS else "Soft"

    async def async_select_option(self, option: str) -> None:
        if option not in _OPTIONS:
            raise ValueError(f"Unknown reset type: {option}")
        bag = dict(self._entry.data.get(DATA_RESET_TYPE) or {})
        bag[self._cid] = option
        self._hass.config_entries.async_update_entry(
            self._entry,
            data={**self._entry.data, DATA_RESET_TYPE: bag},
        )
        self.async_write_ha_state()
