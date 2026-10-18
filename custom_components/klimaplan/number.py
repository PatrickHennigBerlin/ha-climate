"""Einstellungen als Zahlen (für das Dashboard)."""

from __future__ import annotations

from homeassistant.components.number import NumberDeviceClass, NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .entity import KlimaplanEntity
from .manager import KlimaplanManager
from .logic import PlanError

NUMBERS = [
    ("outside_limit", "Außengrenze", "mdi:sun-thermometer", 15, 45, 0.5, UnitOfTemperature.CELSIUS),
    ("outside_hyst", "Außen-Hysterese", "mdi:arrow-collapse-down", 0, 10, 0.5, UnitOfTemperature.KELVIN),
    ("start_delta", "Einschaltabstand", "mdi:thermometer-chevron-up", 0, 10, 0.5, UnitOfTemperature.KELVIN),
    ("protect_on", "Hitzeschutz ab innen", "mdi:thermometer-alert", 18, 40, 0.5, UnitOfTemperature.CELSIUS),
    ("protect_temp", "Hitzeschutz-Soll", "mdi:thermometer-low", 16, 30, 0.5, UnitOfTemperature.CELSIUS),
    ("motion_min", "Bewegungsmelder heute", "mdi:motion-sensor", 0, 10, 1, None),
]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    manager: KlimaplanManager = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(KlimaplanNumber(manager, *n) for n in NUMBERS)


class KlimaplanNumber(KlimaplanEntity, NumberEntity):
    _attr_mode = NumberMode.BOX
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, manager, key, name, icon, low, high, step, unit) -> None:
        super().__init__(manager)
        self._key = key
        self._attr_name = name
        self._attr_icon = icon
        self._attr_native_min_value = low
        self._attr_native_max_value = high
        self._attr_native_step = step
        self._attr_native_unit_of_measurement = unit
        if unit == UnitOfTemperature.CELSIUS:
            self._attr_device_class = NumberDeviceClass.TEMPERATURE
        self._attr_unique_id = f"{manager.entry.entry_id}_{key}"

    @property
    def native_value(self) -> float:
        return self.manager.settings[self._key]

    async def async_set_native_value(self, value: float) -> None:
        try:
            await self.manager.async_set_settings({self._key: value})
        except PlanError as err:
            raise ServiceValidationError(str(err)) from err
