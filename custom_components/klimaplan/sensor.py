"""Je Raum: aktueller Kühl-Soll aus Plan oder Hitzeschutz."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .entity import KlimaplanRoomEntity
from .manager import KlimaplanManager

SOURCE = {"plan": "Zeitplan", "protect": "Hitzeschutz"}


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    manager: KlimaplanManager = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(KlimaplanRoomTarget(manager, rid) for rid in manager.rooms)


class KlimaplanRoomTarget(KlimaplanRoomEntity, SensorEntity):
    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_icon = "mdi:snowflake-thermometer"

    def __init__(self, manager: KlimaplanManager, room_id: str) -> None:
        super().__init__(manager, room_id, "soll", "{name} Kühl-Soll")

    @property
    def native_value(self) -> float | None:
        if self.room_id not in self.manager.rooms:
            return None
        t = self.manager.room_status(self.room_id)["target"]
        return t["temp"] if t else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        if self.room_id not in self.manager.rooms:
            return {}
        s = self.manager.room_status(self.room_id)
        t = s["target"] or {}
        return {"quelle": SOURCE.get(t.get("source"), "aus"), "bis": t.get("until"),
                "naechstes_fenster": (s["next_window"] or {}).get("at")}
