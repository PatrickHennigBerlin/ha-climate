"""Hitzetag, anwesend heute, je Raum: Klimaplan kühlt."""

from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .entity import KlimaplanEntity, KlimaplanRoomEntity
from .manager import KlimaplanManager


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    manager: KlimaplanManager = hass.data[DOMAIN][entry.entry_id]
    ents: list[BinarySensorEntity] = [KlimaplanHot(manager), KlimaplanPresent(manager)]
    ents += [KlimaplanRoomActive(manager, rid) for rid in manager.rooms]
    async_add_entities(ents)


class KlimaplanHot(KlimaplanEntity, BinarySensorEntity):
    _attr_name = "Hitzetag"
    _attr_icon = "mdi:weather-sunny-alert"
    _attr_device_class = BinarySensorDeviceClass.HEAT

    def __init__(self, manager: KlimaplanManager) -> None:
        super().__init__(manager)
        self._attr_unique_id = f"{manager.entry.entry_id}_hitzetag"

    @property
    def is_on(self) -> bool:
        return self.manager.hot

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        s = self.manager.settings
        return {"aussen": self.manager.outside_value(), "grenze": s["outside_limit"],
                "aus_unter": s["outside_limit"] - s["outside_hyst"]}


class KlimaplanPresent(KlimaplanEntity, BinarySensorEntity):
    _attr_name = "Anwesend heute"
    _attr_icon = "mdi:home-account"
    _attr_device_class = BinarySensorDeviceClass.PRESENCE

    def __init__(self, manager: KlimaplanManager) -> None:
        super().__init__(manager)
        self._attr_unique_id = f"{manager.entry.entry_id}_anwesend_heute"

    @property
    def is_on(self) -> bool:
        return self.manager.present_today(dt_util.now())

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"zuletzt_zuhause": self.manager.last_home, "jemand_zuhause": self.manager.anyone_home(),
                "tagesbeginn": self.manager.settings["presence_cutoff"],
                "melder_heute": self.manager.motion_today(dt_util.now()),
                "melder_noetig": self.manager.settings.get("motion_min")}


class KlimaplanRoomActive(KlimaplanRoomEntity, BinarySensorEntity):
    _attr_icon = "mdi:snowflake"
    _attr_device_class = BinarySensorDeviceClass.COLD

    def __init__(self, manager: KlimaplanManager, room_id: str) -> None:
        super().__init__(manager, room_id, "kuehlt", "{name} kühlt")

    @property
    def is_on(self) -> bool | None:
        st = self.manager.state.get(self.room_id) or {}
        return bool(st.get("owned"))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        if self.room_id not in self.manager.rooms:
            return {}
        s = self.manager.room_status(self.room_id)
        t = s["target"] or {}
        return {"klima": s["climate"], "soll": t.get("temp"), "quelle": t.get("source"),
                "bis": t.get("until"), "pausiert": s["paused"], "wartet": s["waiting"]}
