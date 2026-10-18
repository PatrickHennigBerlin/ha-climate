"""Gemeinsame Basis für Klimaplan-Entitäten."""

from __future__ import annotations

from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import Entity

from .const import DOMAIN, SIGNAL_UPDATED, VERSION
from .manager import KlimaplanManager


class KlimaplanEntity(Entity):
    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, manager: KlimaplanManager) -> None:
        self.manager = manager
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, manager.entry.entry_id)},
            name="Klimaplan",
            manufacturer="Klimaplan",
            model="Kühlplan",
            sw_version=VERSION,
            entry_type=DeviceEntryType.SERVICE,
        )

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(async_dispatcher_connect(self.hass, SIGNAL_UPDATED, self._handle_update))

    @callback
    def _handle_update(self) -> None:
        self.async_write_ha_state()


class KlimaplanRoomEntity(KlimaplanEntity):
    def __init__(self, manager: KlimaplanManager, room_id: str, suffix: str, label: str) -> None:
        super().__init__(manager)
        self.room_id = room_id
        self._attr_unique_id = f"{manager.entry.entry_id}_{room_id}_{suffix}"
        name = manager.rooms[room_id].get("name", room_id)
        self._attr_name = label.format(name=name)

    @property
    def available(self) -> bool:
        return self.room_id in self.manager.rooms
