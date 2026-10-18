"""Klimaplan – Kühlpläne und Hitzeschutz für Klimageräte."""

from __future__ import annotations

import logging
from pathlib import Path

import voluptuous as vol

from homeassistant.components.frontend import add_extra_js_url
from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
import homeassistant.helpers.config_validation as cv

from .const import CARD_URL, DOMAIN, VERSION
from .logic import DAYS, PlanError
from .manager import KlimaplanManager, room_id_from_entity
from .websocket import async_register_websocket

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.SWITCH, Platform.NUMBER, Platform.BINARY_SENSOR, Platform.SENSOR]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


def get_manager(hass: HomeAssistant) -> KlimaplanManager:
    entries = hass.data.get(DOMAIN, {})
    if not entries:
        raise HomeAssistantError("Klimaplan ist nicht eingerichtet")
    return next(iter(entries.values()))


def _resolve_room(manager: KlimaplanManager, value: str) -> str:
    if value in manager.rooms:
        return value
    rid = room_id_from_entity(value)
    if rid in manager.rooms:
        return rid
    for room_id, room in manager.rooms.items():
        if room["climate"] == value or room.get("name", "").lower() == value.lower():
            return room_id
    raise ServiceValidationError(f"Unbekannter Raum: {value}")


def _lovelace_resources(hass: HomeAssistant):
    """Ressourcen-Sammlung im Storage-Modus, sonst None."""
    try:
        from homeassistant.components.lovelace.const import LOVELACE_DATA
    except ImportError:  # sehr alte HA-Version
        return None
    data = hass.data.get(LOVELACE_DATA)
    resources = getattr(data, "resources", None)
    if resources is None or getattr(data, "resource_mode", "storage") != "storage":
        return None
    if not hasattr(resources, "async_create_item"):
        return None
    return resources


async def _async_register_resource(hass: HomeAssistant, card_url: str) -> bool:
    """Karte als Lovelace-Ressource eintragen bzw. auf die aktuelle Version setzen."""
    resources = _lovelace_resources(hass)
    if resources is None:
        return False
    try:
        await resources.async_get_info()  # stellt sicher, dass die Sammlung geladen ist
        ours = [
            item for item in resources.async_items()
            if str(item.get("url", "")).split("?", 1)[0] == CARD_URL
        ]
        if not ours:
            await resources.async_create_item({"res_type": "module", "url": card_url})
        else:
            if ours[0].get("url") != card_url:
                await resources.async_update_item(ours[0]["id"], {"res_type": "module", "url": card_url})
            for extra in ours[1:]:
                await resources.async_delete_item(extra["id"])
    except Exception as err:  # noqa: BLE001 – im Zweifel der alte Weg
        _LOGGER.warning("Klimaplan: Karte nicht als Dashboard-Ressource eintragbar (%s)", err)
        return False
    return True


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Integration gelöscht: Dashboard-Ressource wieder entfernen."""
    resources = _lovelace_resources(hass)
    if resources is None:
        return
    try:
        await resources.async_get_info()
        for item in list(resources.async_items()):
            if str(item.get("url", "")).split("?", 1)[0] == CARD_URL:
                await resources.async_delete_item(item["id"])
    except Exception as err:  # noqa: BLE001
        _LOGGER.warning("Klimaplan: Dashboard-Ressource nicht entfernbar (%s)", err)


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    hass.data.setdefault(DOMAIN, {})
    path = Path(__file__).parent / "www" / "klimaplan-card.js"
    await hass.http.async_register_static_paths([StaticPathConfig(CARD_URL, str(path), True)])
    card_url = f"{CARD_URL}?v={VERSION}"
    if not await _async_register_resource(hass, card_url):
        add_extra_js_url(hass, card_url)
    async_register_websocket(hass)
    _register_services(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    manager = KlimaplanManager(hass, entry)
    await manager.async_load()
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = manager
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    await manager.async_start()
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    return True


async def _async_options_updated(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    manager: KlimaplanManager = hass.data[DOMAIN][entry.entry_id]
    manager.async_stop()
    await manager.async_flush()
    ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if ok:
        hass.data[DOMAIN].pop(entry.entry_id)
    return ok


def _register_services(hass: HomeAssistant) -> None:
    def _guard(func):
        async def inner(call: ServiceCall) -> None:
            try:
                await func(call)
            except (PlanError, KeyError) as err:
                raise ServiceValidationError(str(err)) from err
        return inner

    @_guard
    async def add_room(call: ServiceCall) -> None:
        await get_manager(hass).async_add_room(
            call.data["climate"], call.data.get("name"), call.data.get("fan_auto_switch"),
            call.data.get("plan_enabled", False), call.data.get("protect", True))

    @_guard
    async def update_room(call: ServiceCall) -> None:
        manager = get_manager(hass)
        fields = {k: v for k, v in call.data.items() if k != "room"}
        await manager.async_update_room(_resolve_room(manager, call.data["room"]), **fields)

    @_guard
    async def set_day(call: ServiceCall) -> None:
        manager = get_manager(hass)
        await manager.async_set_day(_resolve_room(manager, call.data["room"]), call.data["day"],
                                    call.data["windows"], call.data.get("copy_to", []))

    @_guard
    async def set_settings(call: ServiceCall) -> None:
        await get_manager(hass).async_set_settings(dict(call.data))

    window = vol.Schema({vol.Required("start"): cv.string, vol.Required("end"): cv.string,
                         vol.Required("temp"): vol.Coerce(float), vol.Optional("fan"): vol.Any(cv.string, None)})
    hass.services.async_register(DOMAIN, "add_room", add_room, schema=vol.Schema({
        vol.Required("climate"): cv.entity_id,
        vol.Optional("name"): cv.string,
        vol.Optional("fan_auto_switch"): cv.entity_id,
        vol.Optional("plan_enabled", default=False): cv.boolean,
        vol.Optional("protect", default=True): cv.boolean,
    }))
    hass.services.async_register(DOMAIN, "update_room", update_room, schema=vol.Schema({
        vol.Required("room"): cv.string,
        vol.Optional("name"): cv.string,
        vol.Optional("fan_auto_switch"): vol.Any(cv.entity_id, ""),
        vol.Optional("plan_enabled"): cv.boolean,
        vol.Optional("protect"): cv.boolean,
    }))
    hass.services.async_register(DOMAIN, "set_day", set_day, schema=vol.Schema({
        vol.Required("room"): cv.string,
        vol.Required("day"): vol.In(DAYS),
        vol.Required("windows"): [window],
        vol.Optional("copy_to", default=[]): [vol.In(DAYS)],
    }))
    hass.services.async_register(DOMAIN, "set_settings", set_settings, schema=vol.Schema({
        vol.Optional("enabled"): cv.boolean,
        vol.Optional("plan_enabled"): cv.boolean,
        vol.Optional("protect_enabled"): cv.boolean,
        vol.Optional("outside_limit"): vol.Coerce(float),
        vol.Optional("outside_hyst"): vol.Coerce(float),
        vol.Optional("start_delta"): vol.Coerce(float),
        vol.Optional("protect_temp"): vol.Coerce(float),
        vol.Optional("protect_fan"): vol.Any(cv.string, None),
        vol.Optional("presence_cutoff"): cv.string,
    }))
