"""Schalter: Automatik, Zeitpläne, Hitzeschutz (global) sowie je Raum Plan und Hitzeschutz."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .entity import KlimaplanEntity, KlimaplanRoomEntity
from .manager import KlimaplanManager

GLOBAL = [
    ("enabled", "Automatik", "mdi:robot-outline"),
    ("plan_enabled", "Zeitpläne", "mdi:calendar-clock"),
    ("protect_enabled", "Hitzeschutz", "mdi:sun-thermometer-outline"),
    ("plan_needs_home", "Zeitpläne nur wenn jemand zu Hause", "mdi:home-clock-outline"),
]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    manager: KlimaplanManager = hass.data[DOMAIN][entry.entry_id]
    ents: list[SwitchEntity] = [KlimaplanSettingSwitch(manager, *g) for g in GLOBAL]
    for rid in manager.rooms:
        ents.append(KlimaplanRoomSwitch(manager, rid, "plan_enabled", "plan", "Plan {name}", "mdi:calendar-clock"))
        ents.append(KlimaplanRoomSwitch(manager, rid, "protect", "hitzeschutz", "Hitzeschutz {name}",
                                        "mdi:sun-thermometer-outline"))
    async_add_entities(ents)


class KlimaplanSettingSwitch(KlimaplanEntity, SwitchEntity):
    def __init__(self, manager: KlimaplanManager, key: str, name: str, icon: str) -> None:
        super().__init__(manager)
        self._key = key
        self._attr_name = name
        self._attr_icon = icon
        self._attr_unique_id = f"{manager.entry.entry_id}_{key}"

    @property
    def is_on(self) -> bool:
        return bool(self.manager.settings[self._key])

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.manager.async_set_settings({self._key: True})

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.manager.async_set_settings({self._key: False})


class KlimaplanRoomSwitch(KlimaplanRoomEntity, SwitchEntity):
    def __init__(self, manager, room_id, key, suffix, label, icon) -> None:
        super().__init__(manager, room_id, suffix, label)
        self._key = key
        self._attr_icon = icon

    @property
    def is_on(self) -> bool | None:
        room = self.manager.rooms.get(self.room_id)
        return None if room is None else bool(room.get(self._key, self._key == "protect"))

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.manager.async_update_room(self.room_id, **{self._key: True})

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.manager.async_update_room(self.room_id, **{self._key: False})
