"""Select platform for charger-local choices."""
from __future__ import annotations

import logging

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .charging_cards import charging_cards, selected_card
from .const import (
    CONF_ADVANCED_MODE,
    DATA_RESET_TYPE,
    DATA_SELECTED_CHARGING_CARD_IDS,
    DOMAIN,
    MANUFACTURER,
)
from .coordinator import TapCoordinator

_LOGGER = logging.getLogger(__name__)

_OPTIONS = ["Soft", "Hard"]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coord: TapCoordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    has_cards = bool(charging_cards(entry.options))
    advanced = bool(entry.data.get(CONF_ADVANCED_MODE))

    entities: list[SelectEntity] = []
    for c in coord.data.chargers:
        cid = c.get("id")
        if not cid:
            continue
        entities.append(ResetTypeSelect(hass, entry, coord, cid))
        if advanced and has_cards:
            entities.append(ChargingCardSelect(hass, entry, coord, cid))
    async_add_entities(entities)


def _device_info_for(coord: TapCoordinator, charger_id: str) -> DeviceInfo:
    charger = coord.data.charger(charger_id) or {}
    return DeviceInfo(
        identifiers={(DOMAIN, charger_id)},
        manufacturer=charger.get("brand") or MANUFACTURER,
        name=charger.get("name") or f"Tap Charger {charger_id[:8]}",
        model=charger.get("model") or charger.get("brand"),
        sw_version=charger.get("firmwareVersion"),
        hw_version=charger.get("serialNumber"),
    )


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

        self._attr_device_info = _device_info_for(coord, charger_id)

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


class ChargingCardSelect(CoordinatorEntity[TapCoordinator], SelectEntity):
    """Choose the saved card used by Remote Start for one charger."""

    _attr_has_entity_name = True
    _attr_icon = "mdi:credit-card-wireless"
    _attr_translation_key = "charging_card"

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
        self._attr_unique_id = f"{charger_id}_charging_card"
        self._attr_device_info = _device_info_for(coord, charger_id)

    @property
    def options(self) -> list[str]:
        return [card["label"] for card in charging_cards(self._entry.options)]

    @property
    def current_option(self) -> str | None:
        card = selected_card(self._entry.data, self._entry.options, self._cid)
        return card["label"] if card else None

    async def async_select_option(self, option: str) -> None:
        card = next(
            (
                candidate
                for candidate in charging_cards(self._entry.options)
                if candidate["label"] == option
            ),
            None,
        )
        if card is None:
            raise ValueError(f"Unknown charging card: {option}")

        raw_selections = self._entry.data.get(
            DATA_SELECTED_CHARGING_CARD_IDS,
        )
        selections = (
            dict(raw_selections) if isinstance(raw_selections, dict) else {}
        )
        selections[self._cid] = card["id"]
        self._hass.config_entries.async_update_entry(
            self._entry,
            data={
                **self._entry.data,
                DATA_SELECTED_CHARGING_CARD_IDS: selections,
            },
        )
        self.async_write_ha_state()
