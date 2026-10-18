"""Klimaplan mit Home Assistant."""

from datetime import datetime, timedelta
from unittest.mock import patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_mock_service

from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util

from custom_components.klimaplan.const import DOMAIN

ATTRS = {"hvac_modes": ["cool", "dry", "fan_only", "heat", "off"], "min_temp": 19, "max_temp": 30,
         "target_temp_step": 0.5, "fan_modes": ["off", "1", "2", "3", "4"], "temperature": 22,
         "current_temperature": 24, "friendly_name": "Klima Wohnzimmer"}
WZ = "climate.klima_wohnzimmer"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    yield


@pytest.fixture
def clock():
    tz = dt_util.get_default_time_zone()
    t0 = datetime(2026, 7, 13, 17, 0, tzinfo=tz)   # Montag 17:00, Plan ab 18:00
    c = {"t0": t0, "now": t0}
    with patch("custom_components.klimaplan.manager.dt_util.now", side_effect=lambda: c["now"]):
        yield c


class Env:
    def __init__(self, hass, entry, clock, calls):
        self.hass, self.entry, self.clock, self.calls = hass, entry, clock, calls

    @property
    def manager(self):
        return self.hass.data[DOMAIN][self.entry.entry_id]

    def klima(self, state=None, **attrs):
        old = self.hass.states.get(WZ)
        self.hass.states.async_set(WZ, state or old.state, {**dict(old.attributes), **attrs})

    def set(self, entity, state):
        self.hass.states.async_set(entity, state)

    async def at(self, minutes):
        self.clock["now"] = self.clock["t0"] + timedelta(minutes=minutes)
        await self.manager.async_apply()
        await self.hass.async_block_till_done()

    def c(self, kind):
        return [x for x in self.calls[kind] if x.data.get("entity_id") == WZ]


async def _setup(hass: HomeAssistant, clock, plan=True, outside="31") -> Env:
    await async_setup_component(hass, "http", {})
    await async_setup_component(hass, "websocket_api", {})
    hass.config.components.add("frontend")
    hass.states.async_set(WZ, "off", ATTRS)
    hass.states.async_set("sensor.aussen", outside, {"unit_of_measurement": "°C"})
    hass.states.async_set("person.patrick", "home")
    hass.states.async_set("person.franzi", "not_home")
    hass.states.async_set("switch.heizperiode", "off")
    hass.states.async_set("switch.lufter_auto_wohnzimmer", "off")
    calls = {k: async_mock_service(hass, "climate", k) for k in ("set_temperature", "set_hvac_mode", "set_fan_mode")}
    entry = MockConfigEntry(domain=DOMAIN, title="Klimaplan", unique_id=DOMAIN, options={
        "outside_entity": "sensor.aussen", "persons": ["person.patrick", "person.franzi"],
        "block_entity": "switch.heizperiode"})
    entry.add_to_hass(hass)
    with patch("custom_components.klimaplan.add_extra_js_url"):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    calls["switch_on"] = async_mock_service(hass, "switch", "turn_on")
    calls["switch_off"] = async_mock_service(hass, "switch", "turn_off")
    await hass.services.async_call(DOMAIN, "add_room", {
        "climate": WZ, "name": "Wohnzimmer", "fan_auto_switch": "switch.lufter_auto_wohnzimmer",
        "plan_enabled": plan, "protect": True}, blocking=True)
    await hass.async_block_till_done()
    await hass.async_block_till_done()
    return Env(hass, entry, clock, calls)


async def test_entities(hass, clock):
    env = await _setup(hass, clock)
    await env.at(0)
    assert hass.states.get("binary_sensor.klimaplan_hitzetag").state == "on"
    assert hass.states.get("binary_sensor.klimaplan_anwesend_heute").state == "on"
    assert hass.states.get("switch.klimaplan_plan_wohnzimmer").state == "on"
    assert hass.states.get("number.klimaplan_hitzeschutz_soll").state == "25.0"
    soll = hass.states.get("sensor.klimaplan_wohnzimmer_kuhl_soll")
    assert soll.state == "25.0" and soll.attributes["quelle"] == "Hitzeschutz"


async def test_protect_starts_only_at_delta(hass, clock):
    env = await _setup(hass, clock)
    env.klima(current_temperature=26.5)      # Hitzeschutz 25 -> erst ab 27
    await env.at(0)
    assert not env.c("set_temperature")
    env.klima(current_temperature=27)
    await env.at(1)
    on = env.c("set_temperature")
    assert on and on[-1].data == {"entity_id": WZ, "hvac_mode": "cool", "temperature": 25.0}
    assert env.calls["switch_on"][-1].data["entity_id"] == "switch.lufter_auto_wohnzimmer"
    assert hass.states.get("binary_sensor.klimaplan_wohnzimmer_kuhlt").state == "on"

    # Plan ab 18 Uhr: Soll 23
    env.klima("cool", temperature=25)
    await env.at(61)
    assert env.c("set_temperature")[-1].data == {"entity_id": WZ, "temperature": 23.0}
    # Hitzetag vorbei (unter 28) -> aus
    env.set("sensor.aussen", "28.5")
    await env.at(70)
    assert not env.c("set_hvac_mode")
    env.set("sensor.aussen", "27.9")
    await env.at(71)
    assert env.c("set_hvac_mode")[-1].data == {"entity_id": WZ, "hvac_mode": "off"}
    assert env.manager.state["klima_wohnzimmer"]["owned"] is False


async def test_not_hot_no_start(hass, clock):
    env = await _setup(hass, clock, outside="30")
    env.klima(current_temperature=29)
    await env.at(70)
    assert not env.c("set_temperature")


async def test_both_away_plan_falls_back_and_vacation(hass, clock):
    env = await _setup(hass, clock)
    env.klima(current_temperature=25)        # Plan 23 -> ab 25
    await env.at(65)
    assert env.c("set_temperature")[-1].data["temperature"] == 23.0
    env.klima("cool", temperature=23)
    # beide weg: Plan aus, Hitzeschutz übernimmt (Soll 25), Gerät bleibt an
    env.set("person.patrick", "not_home")
    await env.at(80)
    assert env.c("set_temperature")[-1].data == {"entity_id": WZ, "temperature": 25.0}
    assert not env.c("set_hvac_mode")


async def test_both_away_without_protect_switches_off(hass, clock):
    env = await _setup(hass, clock)
    await hass.services.async_call(DOMAIN, "update_room", {"room": "Wohnzimmer", "protect": False}, blocking=True)
    env.klima(current_temperature=25)
    await env.at(65)
    assert env.c("set_temperature")
    env.klima("cool", temperature=23)
    env.set("person.patrick", "not_home")
    await env.at(80)
    assert env.c("set_hvac_mode")[-1].data == {"entity_id": WZ, "hvac_mode": "off"}


async def test_nobody_today_no_cooling(hass, clock):
    env = await _setup(hass, clock)
    env.set("person.patrick", "not_home")
    env.manager.last_home = (clock["t0"] - timedelta(hours=20)).isoformat()   # gestern
    env.klima(current_temperature=30)
    await env.at(0)
    assert not env.c("set_temperature")
    assert hass.states.get("binary_sensor.klimaplan_anwesend_heute").state == "off"
    # heute früh noch da gewesen -> Hitzeschutz
    env.manager.last_home = (clock["t0"] - timedelta(hours=10)).isoformat()   # 07:00
    await env.at(1)
    assert env.c("set_temperature")[-1].data["temperature"] == 25.0


async def test_manual_off_pauses_until_source_changes(hass, clock):
    env = await _setup(hass, clock)
    env.klima(current_temperature=28)
    await env.at(0)
    assert len(env.c("set_temperature")) == 1
    env.klima("off")                         # von Hand (oder „Außer Haus“) aus
    await env.at(1)                          # innerhalb der Beruhigungszeit: noch nichts
    await env.at(5)
    assert env.manager.state["klima_wohnzimmer"]["paused"] == "protect"
    await env.at(10)
    assert len(env.c("set_temperature")) == 1   # bleibt aus
    await env.at(61)                         # Planfenster beginnt -> neue Quelle -> wieder an
    assert env.c("set_temperature")[-1].data == {"entity_id": WZ, "hvac_mode": "cool", "temperature": 23.0}


async def test_manual_setpoint_kept(hass, clock):
    env = await _setup(hass, clock)
    env.klima(current_temperature=28)
    await env.at(0)
    env.klima("cool", temperature=24)        # am Pad auf 24 gestellt
    await env.at(5)
    await env.at(6)
    assert len(env.c("set_temperature")) == 1
    await env.at(61)                         # neues Fenster -> Plan-Soll gilt wieder
    assert env.c("set_temperature")[-1].data == {"entity_id": WZ, "temperature": 23.0}


async def test_running_unit_not_taken_over_and_block(hass, clock):
    env = await _setup(hass, clock)
    env.klima("heat", current_temperature=28)
    await env.at(0)
    assert not env.c("set_temperature")
    env.klima("off")
    env.set("switch.heizperiode", "on")
    await env.at(1)
    assert not env.c("set_temperature")
    assert env.manager.blocked()


async def test_fan_level_turns_auto_off(hass, clock):
    env = await _setup(hass, clock)
    env.set("switch.lufter_auto_wohnzimmer", "on")
    await env.manager.async_set_fan("klima_wohnzimmer", "3")
    await hass.async_block_till_done()
    assert env.calls["switch_off"][-1].data["entity_id"] == "switch.lufter_auto_wohnzimmer"
    assert env.c("set_fan_mode")[-1].data["fan_mode"] == "3"


async def test_set_day_and_settings(hass, clock):
    env = await _setup(hass, clock)
    await hass.services.async_call(DOMAIN, "set_day", {
        "room": "Wohnzimmer", "day": "mon", "windows": [{"start": "16:00", "end": "22:00", "temp": 22, "fan": "2"}],
        "copy_to": ["tue"]}, blocking=True)
    plan = env.manager.rooms["klima_wohnzimmer"]["plan"]
    assert plan["tue"][0]["start"] == "16:00" and plan["wed"][0]["start"] == "18:00"
    await hass.services.async_call(DOMAIN, "set_settings", {"protect_temp": 26}, blocking=True)
    assert env.manager.settings["protect_temp"] == 26
    await env.at(0)
    assert hass.states.get("sensor.klimaplan_wohnzimmer_kuhl_soll").state == "22.0"


async def test_websocket(hass, hass_ws_client, clock):
    env = await _setup(hass, clock)
    ws = await hass_ws_client(hass)
    await ws.send_json({"id": 1, "type": "klimaplan/get"})
    msg = await ws.receive_json()
    assert msg["success"] and msg["result"]["hot"] is True
    assert msg["result"]["rooms"]["klima_wohnzimmer"]["status"]["target"]["source"] == "protect"
    await ws.send_json({"id": 2, "type": "klimaplan/set_day", "room_id": "klima_wohnzimmer", "day": "sat",
                        "windows": [{"start": "09:00", "end": "08:00", "temp": 22}]})
    msg = await ws.receive_json()
    assert not msg["success"]
    await ws.send_json({"id": 3, "type": "klimaplan/update_room", "room_id": "klima_wohnzimmer", "plan_enabled": False})
    msg = await ws.receive_json()
    assert msg["success"] and msg["result"]["rooms"]["klima_wohnzimmer"]["plan_enabled"] is False


async def test_plan_needs_outside_and_delta(hass, clock):
    env = await _setup(hass, clock, outside="29")
    await hass.services.async_call(DOMAIN, "update_room", {"room": "Wohnzimmer", "protect": False}, blocking=True)
    env.klima(current_temperature=26)
    await env.at(65)                         # im Fenster, aber außen nicht über 30
    assert not env.c("set_temperature")
    env.klima(current_temperature=24.5)      # Plan 23: erst ab 25
    env.set("sensor.aussen", "30.5")
    await env.at(66)
    assert not env.c("set_temperature")
    env.klima(current_temperature=25)
    await env.at(67)
    assert env.c("set_temperature")[-1].data == {"entity_id": WZ, "hvac_mode": "cool", "temperature": 23.0}


async def test_present_today_while_at_work(hass, clock):
    env = await _setup(hass, clock)
    await env.at(-600)                       # 07:00 zu Hause
    env.set("person.patrick", "not_home")    # zur Arbeit
    env.klima(current_temperature=27)
    await env.at(-300)                       # 12:00, keiner da -> Hitzeschutz läuft trotzdem
    assert env.c("set_temperature")[-1].data["temperature"] == 25.0
