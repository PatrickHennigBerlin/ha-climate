"""WebSocket-API für die Klimaplan-Karte."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect

from .const import DOMAIN, SIGNAL_UPDATED
from .logic import DAYS, PlanError


def _manager(hass: HomeAssistant):
    entries = hass.data.get(DOMAIN, {})
    return next(iter(entries.values())) if entries else None


def _wrap(handler):
    async def _inner(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
        manager = _manager(hass)
        if manager is None:
            connection.send_error(msg["id"], "not_setup", "Klimaplan ist nicht eingerichtet")
            return
        try:
            await handler(hass, manager, msg)
        except (PlanError, KeyError, ValueError) as err:
            connection.send_error(msg["id"], "invalid", str(err))
            return
        connection.send_result(msg["id"], manager.full_state())
    return _inner


@websocket_api.websocket_command({vol.Required("type"): "klimaplan/get"})
@websocket_api.async_response
@_wrap
async def ws_get(hass, manager, msg):
    return None


@websocket_api.websocket_command({vol.Required("type"): "klimaplan/subscribe"})
@callback
def ws_subscribe(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    @callback
    def _forward() -> None:
        manager = _manager(hass)
        if manager is not None:
            connection.send_message(websocket_api.event_message(msg["id"], manager.full_state()))

    connection.subscriptions[msg["id"]] = async_dispatcher_connect(hass, SIGNAL_UPDATED, _forward)
    connection.send_result(msg["id"])
    _forward()


@websocket_api.websocket_command({
    vol.Required("type"): "klimaplan/set_day",
    vol.Required("room_id"): str,
    vol.Required("day"): vol.In(DAYS),
    vol.Required("windows"): list,
    vol.Optional("copy_to", default=[]): [vol.In(DAYS)],
})
@websocket_api.async_response
@_wrap
async def ws_set_day(hass, manager, msg):
    await manager.async_set_day(msg["room_id"], msg["day"], msg["windows"], msg["copy_to"])


@websocket_api.websocket_command({
    vol.Required("type"): "klimaplan/set_settings",
    vol.Optional("enabled"): bool,
    vol.Optional("plan_enabled"): bool,
    vol.Optional("protect_enabled"): bool,
    vol.Optional("outside_limit"): vol.Coerce(float),
    vol.Optional("outside_hyst"): vol.Coerce(float),
    vol.Optional("start_delta"): vol.Coerce(float),
    vol.Optional("protect_temp"): vol.Coerce(float),
    vol.Optional("protect_fan"): vol.Any(str, None),
    vol.Optional("presence_cutoff"): str,
})
@websocket_api.require_admin
@websocket_api.async_response
@_wrap
async def ws_set_settings(hass, manager, msg):
    await manager.async_set_settings({k: v for k, v in msg.items() if k not in ("type", "id")})


@websocket_api.websocket_command({
    vol.Required("type"): "klimaplan/update_room",
    vol.Required("room_id"): str,
    vol.Optional("plan_enabled"): bool,
    vol.Optional("protect"): bool,
})
@websocket_api.async_response
@_wrap
async def ws_update_room(hass, manager, msg):
    await manager.async_update_room(msg["room_id"], plan_enabled=msg.get("plan_enabled"), protect=msg.get("protect"))


@websocket_api.websocket_command({
    vol.Required("type"): "klimaplan/set_fan",
    vol.Required("room_id"): str,
    vol.Required("fan"): str,
})
@websocket_api.async_response
@_wrap
async def ws_set_fan(hass, manager, msg):
    await manager.async_set_fan(msg["room_id"], msg["fan"])


@callback
def async_register_websocket(hass: HomeAssistant) -> None:
    for handler in (ws_get, ws_subscribe, ws_set_day, ws_set_settings, ws_update_room, ws_set_fan):
        websocket_api.async_register_command(hass, handler)
