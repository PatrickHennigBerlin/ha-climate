"""Einrichtung über die Oberfläche."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import CONF_BLOCK, CONF_MOTION, CONF_OUTSIDE, CONF_PERSONS, CONF_VACATION, DOMAIN


def _opt(key: str, defaults: dict[str, Any]):
    value = defaults.get(key)
    return vol.Optional(key, description={"suggested_value": value} if value else None)


def _schema(defaults: dict[str, Any]) -> vol.Schema:
    return vol.Schema({
        _opt(CONF_OUTSIDE, defaults): selector.EntitySelector(
            selector.EntitySelectorConfig(domain=["sensor"], device_class="temperature")),
        _opt(CONF_PERSONS, defaults): selector.EntitySelector(
            selector.EntitySelectorConfig(domain=["person", "device_tracker"], multiple=True)),
        _opt(CONF_MOTION, defaults): selector.EntitySelector(
            selector.EntitySelectorConfig(domain=["binary_sensor"], multiple=True)),
        _opt(CONF_VACATION, defaults): selector.EntitySelector(
            selector.EntitySelectorConfig(domain=["input_boolean", "switch", "binary_sensor"])),
        _opt(CONF_BLOCK, defaults): selector.EntitySelector(
            selector.EntitySelectorConfig(domain=["input_boolean", "switch", "binary_sensor"])),
    })


class KlimaplanConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured()
        if user_input is not None:
            return self.async_create_entry(title="Klimaplan", data={}, options=user_input)
        return self.async_show_form(step_id="user", data_schema=_schema({}))

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return KlimaplanOptionsFlow()


class KlimaplanOptionsFlow(OptionsFlow):
    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data=user_input)
        return self.async_show_form(step_id="init", data_schema=_schema(dict(self.config_entry.options)))
