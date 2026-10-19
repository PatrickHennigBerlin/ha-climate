"""Klimaplan: Räume, Pläne, Hitzeschutz – entscheidet, wann ein Klimagerät kühlt."""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_ENTITY_ID, STATE_HOME, STATE_ON
from homeassistant.core import CALLBACK_TYPE, Event, HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_track_state_change_event, async_track_time_change
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import (
    CONF_BLOCK,
    CONF_FORECAST,
    CONF_MOTION,
    CONF_OUTSIDE,
    CONF_PERSONS,
    CONF_VACATION,
    SIGNAL_UPDATED,
    STORAGE_KEY,
    STORAGE_VERSION,
)
from .klima_start import async_start_climate
from .logic import (
    DAYS,
    DEFAULT_SETTINGS,
    PlanError,
    climate_setpoint,
    default_plan,
    hot_today,
    may_start,
    next_window,
    normalize_plan,
    normalize_settings,
    normalize_windows,
    presence_threshold,
    room_target,
    window_at,
)

_LOGGER = logging.getLogger(__name__)

COOL = "cool"
OFF = "off"
START_RETRY = timedelta(minutes=5)   # nach unbestätigtem Einschalten so lange warten
STARTUP_GRACE = timedelta(minutes=2)   # nach dem Start: KNX-Klimas zeigen „aus“, bis ihr Status gelesen ist
SETTLE = timedelta(minutes=2)   # so lange nach eigenem Schalten keine „Handänderung“ annehmen


def room_id_from_entity(entity_id: str) -> str:
    return re.sub(r"[^a-z0-9_]", "_", entity_id.split(".", 1)[-1].lower())


class KlimaplanManager:
    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        self._store: Store = Store(hass, STORAGE_VERSION, f"{STORAGE_KEY}.{entry.entry_id}")
        self.rooms: dict[str, dict[str, Any]] = {}
        self.settings: dict[str, Any] = dict(DEFAULT_SETTINGS)
        self.state: dict[str, dict[str, Any]] = {}   # je Raum {owned, since, temp, fan, paused, manual_temp}
        self._starting: set[str] = set()   # Klima wird gerade eingeschaltet (klima_start)
        self._loaded_at: datetime = dt_util.now()
        self._start_failed: dict[str, datetime] = {}   # Einschalten nicht bestätigt -> START_RETRY warten
        self.last_home: str | None = None            # ISO-Zeit, zuletzt jemand zu Hause
        self.hot: bool = False
        self.hot_day: str | None = None              # Tag, für den measured_max/forecast_max gelten
        self.measured_max: float | None = None       # heute gemessenes Außen-Maximum
        self.forecast_max: float | None = None       # Vorhersage-Höchstwert für heute
        self.motion_day: str | None = None           # Datum, für das motion_seen gilt
        self.motion_seen: list[str] = []             # Melder, die heute ausgelöst haben
        self._unsubs: list[CALLBACK_TYPE] = []

    # ------------------------------------------------------------------ Optionen
    @property
    def outside_entity(self) -> str | None:
        return self.entry.options.get(CONF_OUTSIDE) or None

    @property
    def persons(self) -> list[str]:
        return list(self.entry.options.get(CONF_PERSONS) or [])

    @property
    def vacation_entity(self) -> str | None:
        return self.entry.options.get(CONF_VACATION) or None

    @property
    def block_entity(self) -> str | None:
        return self.entry.options.get(CONF_BLOCK) or None

    @property
    def forecast_entity(self) -> str | None:
        return self.entry.options.get(CONF_FORECAST) or None

    @property
    def motion_entities(self) -> list[str]:
        return list(self.entry.options.get(CONF_MOTION) or [])

    def _day_start(self, now: datetime) -> datetime:
        return presence_threshold(now, self.settings["presence_cutoff"])

    def motion_today(self, now: datetime) -> list[str]:
        return list(self.motion_seen) if self.motion_day == self._day_start(now).isoformat() else []

    @callback
    def _note_motion(self, entity_id: str, now: datetime) -> bool:
        day = self._day_start(now).isoformat()
        if self.motion_day != day:
            self.motion_day, self.motion_seen = day, []
        if entity_id in self.motion_seen:
            return False
        self.motion_seen.append(entity_id)
        return True

    def _is_on(self, entity_id: str | None) -> bool:
        if not entity_id:
            return False
        st = self.hass.states.get(entity_id)
        return st is not None and st.state == STATE_ON

    def outside_value(self) -> float | None:
        ent = self.outside_entity
        st = self.hass.states.get(ent) if ent else None
        try:
            return float(st.state) if st is not None else None
        except ValueError:
            return None

    def anyone_home(self) -> bool:
        persons = self.persons
        if not persons:
            return True
        return any((st := self.hass.states.get(p)) is not None and st.state == STATE_HOME for p in persons)

    def present_today(self, now: datetime) -> bool:
        """Heute war schon jemand da (kein Urlaub): Person zu Hause oder genug Melder ausgelöst."""
        if self._is_on(self.vacation_entity):
            return False
        motion_min = int(self.settings.get("motion_min") or 0)
        use_motion = bool(self.motion_entities) and motion_min > 0
        if not self.persons and not use_motion:
            return True
        if self.persons and self.anyone_home():
            return True
        last = dt_util.parse_datetime(self.last_home) if self.last_home else None
        if last is not None and last >= self._day_start(now):
            return True
        return use_motion and len(self.motion_today(now)) >= motion_min

    def blocked(self) -> bool:
        return self._is_on(self.block_entity)

    # ------------------------------------------------------------------ Speicher
    async def async_load(self) -> None:
        self._loaded_at = dt_util.now()
        data = await self._store.async_load() or {}
        self.rooms = data.get("rooms", {})
        for room in self.rooms.values():
            room["plan"] = normalize_plan(room.get("plan"))
        try:
            self.settings = normalize_settings(data.get("settings"))
        except PlanError as err:
            _LOGGER.warning("Klimaplan: Einstellungen zurückgesetzt (%s)", err)
            self.settings = dict(DEFAULT_SETTINGS)
        self.state = {rid: st for rid, st in (data.get("state") or {}).items() if rid in self.rooms}
        self.last_home = data.get("last_home")
        self.hot = bool(data.get("hot"))
        self.hot_day = data.get("hot_day")
        self.measured_max = data.get("measured_max")
        self.forecast_max = data.get("forecast_max")
        self.motion_day = data.get("motion_day")
        self.motion_seen = list(data.get("motion_seen") or [])

    def as_dict(self) -> dict[str, Any]:
        return {"rooms": self.rooms, "settings": self.settings, "state": self.state,
                "last_home": self.last_home, "hot": self.hot, "hot_day": self.hot_day,
                "measured_max": self.measured_max, "forecast_max": self.forecast_max,
                "motion_day": self.motion_day, "motion_seen": self.motion_seen}

    @callback
    def _save(self) -> None:
        self._store.async_delay_save(self.as_dict, 1)

    async def async_flush(self) -> None:
        await self._store.async_save(self.as_dict())

    # ------------------------------------------------------------------ Start/Stop
    async def async_start(self) -> None:
        self._unsubs.append(async_track_time_change(self.hass, self._tick, second=15))
        if self.forecast_entity:
            self._unsubs.append(async_track_time_change(self.hass, self._forecast_tick, minute=[2, 32], second=0))
            await self.async_update_forecast()
        watch = [e for e in [self.outside_entity, self.vacation_entity, self.block_entity, *self.persons] if e]
        if watch:
            self._unsubs.append(async_track_state_change_event(self.hass, watch, self._changed_event))
        if self.motion_entities:
            now = dt_util.now()
            for ent in self.motion_entities:
                if self._is_on(ent):
                    self._note_motion(ent, now)
            self._unsubs.append(async_track_state_change_event(self.hass, self.motion_entities, self._motion_event))
        await self.async_apply()

    @callback
    def async_stop(self) -> None:
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()

    async def _tick(self, now: datetime) -> None:
        await self.async_apply()

    async def _forecast_tick(self, now: datetime) -> None:
        await self.async_update_forecast()
        await self.async_apply()

    async def async_update_forecast(self) -> None:
        """Tageshöchstwert für heute aus weather.get_forecasts (daily) holen."""
        ent = self.forecast_entity
        if not ent:
            return
        try:
            res = await self.hass.services.async_call("weather", "get_forecasts", {ATTR_ENTITY_ID: ent, "type": "daily"},
                                                      blocking=True, return_response=True)
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("Klimaplan: Vorhersage von %s nicht lesbar: %s", ent, err)
            return
        today = dt_util.now().date()
        best = None
        for f in ((res or {}).get(ent) or {}).get("forecast") or []:
            when = dt_util.parse_datetime(str(f.get("datetime", "")))
            if when is not None and dt_util.as_local(when).date() == today and f.get("temperature") is not None:
                best = float(f["temperature"]) if best is None else max(best, float(f["temperature"]))
        self._roll_day(dt_util.now())
        self.forecast_max = best
        self._save()

    def _roll_day(self, now: datetime) -> None:
        day = now.date().isoformat()
        if self.hot_day != day:
            self.hot_day, self.measured_max, self.forecast_max = day, None, None

    async def _changed_event(self, event: Event) -> None:
        await self.async_apply()

    async def _motion_event(self, event: Event) -> None:
        new = event.data.get("new_state")
        if new is None or new.state != STATE_ON:
            return
        if self._note_motion(event.data["entity_id"], dt_util.now()):
            self._save()
            await self.async_apply()

    # ------------------------------------------------------------------ Status
    def room_status(self, room_id: str, now: datetime | None = None) -> dict[str, Any]:
        now = now or dt_util.now()
        room = self.rooms[room_id]
        klima = self.hass.states.get(room["climate"])
        target = self._target(room, now)
        st = self.state.get(room_id) or {}
        nxt = next_window(room.get("plan") or {}, now) if room.get("plan_enabled") else None
        win = window_at(room.get("plan") or {}, now) if room.get("plan_enabled") else None
        return {
            "room_id": room_id,
            "name": room.get("name", room_id),
            "climate": room["climate"],
            "hvac": klima.state if klima else None,
            "current_temp": klima.attributes.get("current_temperature") if klima else None,
            "climate_temp": klima.attributes.get("temperature") if klima else None,
            "target": target,
            "window": win,
            "next_window": {"at": nxt[0].isoformat(), **nxt[1]} if nxt else None,
            "active": bool(st.get("owned")),
            "since": st.get("since"),
            "paused": bool(st.get("paused")),
            "heizplan": self.heizplan_owner(room["climate"]),
            "waiting": bool(target) and not st.get("owned") and not st.get("paused")
                       and not may_start(klima.attributes.get("current_temperature") if klima else None,
                                         target["start_at"]),
        }

    def full_state(self) -> dict[str, Any]:
        now = dt_util.now()
        return {
            "settings": self.settings,
            "options": {"outside_entity": self.outside_entity, "persons": self.persons,
                        "vacation_entity": self.vacation_entity, "block_entity": self.block_entity,
                        "forecast_entity": self.forecast_entity,
                        "motion_entities": self.motion_entities},
            "outside": self.outside_value(),
            "hot": self.hot,
            "measured_max": self.measured_max,
            "forecast_max": self.forecast_max,
            "present_today": self.present_today(now),
            "motion_today": self.motion_today(now),
            "anyone_home": self.anyone_home(),
            "blocked": self.blocked(),
            "rooms": {rid: {**room, "status": self.room_status(rid, now)} for rid, room in self.rooms.items()},
            "now": now.isoformat(),
        }

    def _target(self, room: dict[str, Any], now: datetime) -> dict[str, Any] | None:
        return room_target(room=room, settings=self.settings, now=now, hot=self.hot,
                           present_today=self.present_today(now), anyone_home=self.anyone_home(),
                           blocked=self.blocked())

    # ------------------------------------------------------------------ Steuern
    async def _call(self, domain: str, service: str, data: dict[str, Any]) -> bool:
        try:
            await self.hass.services.async_call(domain, service, data, blocking=True)
            return True
        except Exception as err:  # noqa: BLE001 – ein Raum soll andere nicht stoppen
            _LOGGER.error("Klimaplan: %s.%s %s fehlgeschlagen: %s", domain, service, data, err)
            return False

    async def async_set_fan(self, room_id: str, fan: str | None) -> None:
        """Lüfter setzen: 'auto' über fan_mode auto oder Auto-Schalter, sonst Stufe."""
        if not fan:
            return
        room = self.rooms[room_id]
        ent = room["climate"]
        klima = self.hass.states.get(ent)
        modes = list(klima.attributes.get("fan_modes") or []) if klima else []
        auto_switch = room.get("fan_auto_switch")
        if fan == "auto":
            if "auto" in modes:
                await self._call("climate", "set_fan_mode", {ATTR_ENTITY_ID: ent, "fan_mode": "auto"})
            elif auto_switch:
                await self._call(auto_switch.split(".", 1)[0], "turn_on", {ATTR_ENTITY_ID: auto_switch})
            return
        if auto_switch and self._is_on(auto_switch):
            await self._call(auto_switch.split(".", 1)[0], "turn_off", {ATTR_ENTITY_ID: auto_switch})
        if fan in modes:
            await self._call("climate", "set_fan_mode", {ATTR_ENTITY_ID: ent, "fan_mode": fan})

    async def async_apply(self) -> None:
        now = dt_util.now()
        changed = False
        if self.persons and self.anyone_home():
            self.last_home = now.isoformat()
        self._roll_day(now)
        out = self.outside_value()
        if out is not None and (self.measured_max is None or out > self.measured_max):
            self.measured_max = out
            changed = True
        fc = self.forecast_max if self.settings.get("use_forecast") else None
        hot = hot_today(self.measured_max, fc, self.settings["outside_limit"])
        if hot != self.hot:
            self.hot = hot
            changed = True
        for room_id, room in list(self.rooms.items()):
            try:
                changed |= await self._apply_room(room_id, room, now)
            except (KeyError, PlanError) as err:
                _LOGGER.error("Klimaplan: Raum %s fehlerhaft: %s", room_id, err)
        if changed:
            self._save()
        async_dispatcher_send(self.hass, SIGNAL_UPDATED)

    def heizplan_owner(self, climate: str) -> str | None:
        """Steuert die Integration Heizplan dieses Klimagerät gerade (Zuheizen oder Boost)?

        Erkennung über deren Entitäten binary_sensor.heizplan_* mit Attribut klima = climate.
        -> "Boost" / "Zuheizen" oder None.
        """
        for st in self.hass.states.async_all("binary_sensor"):
            if not st.entity_id.startswith("binary_sensor.heizplan_") or st.state != STATE_ON:
                continue
            if st.attributes.get("klima") == climate:
                return st.attributes.get("quelle") or ("Boost" if st.attributes.get("boost") else "Zuheizen")
        return None

    async def _apply_room(self, room_id: str, room: dict[str, Any], now: datetime) -> bool:
        if room_id in self._starting:
            return False   # Klima wird gerade eingeschaltet (wartet auf Bestätigung)
        ent = room["climate"]
        klima = self.hass.states.get(ent)
        hvac = klima.state if klima is not None else None
        if hvac in (None, "unavailable", "unknown"):
            return False
        st = self.state.setdefault(room_id, {"owned": False, "since": None, "temp": None, "fan": None,
                                             "paused": None, "manual_temp": False})
        if self.heizplan_owner(ent):
            # Heizplan heizt mit dieser Klima: nichts tun, nicht als Handänderung werten
            if st.get("owned"):
                st.update(owned=False, since=None, temp=None, fan=None, manual_temp=False)
                _LOGGER.info("Klimaplan: %s an Heizplan übergeben", ent)
                return True
            return False
        target = self._target(room, now)
        key = target["key"] if target else None
        changed = False

        # Pause endet, sobald sich die Quelle ändert (neues Fenster, Hitzeschutz vorbei …)
        if st.get("paused") and st["paused"] != key:
            st["paused"] = None
            changed = True

        started = dt_util.parse_datetime(st["since"]) if st.get("since") else None
        settled = started is None or now - started >= SETTLE

        if st["owned"]:
            if hvac != COOL and now - self._loaded_at < STARTUP_GRACE:
                return changed   # direkt nach dem Start meldet die Klima ggf. noch „aus“ (Status nicht gelesen)
            if hvac != COOL and settled:
                # von Hand aus- oder umgestellt (auch durch „Außer Haus“/„Schlafen gehen“): in Ruhe lassen
                st.update(owned=False, since=None, temp=None, fan=None, manual_temp=False, paused=key)
                _LOGGER.info("Klimaplan: %s von Hand geändert – Pause bis zum nächsten Wechsel", ent)
                return True
            if target is None:
                if hvac == COOL:
                    await self._call("climate", "set_hvac_mode", {ATTR_ENTITY_ID: ent, "hvac_mode": OFF})
                    _LOGGER.info("Klimaplan: %s aus", ent)
                st.update(owned=False, since=None, temp=None, fan=None, manual_temp=False)
                return True
            value = climate_setpoint(target["temp"], dict(klima.attributes))
            current_set = klima.attributes.get("temperature")
            temp_at = dt_util.parse_datetime(st["temp_at"]) if st.get("temp_at") else started
            temp_settled = temp_at is None or now - temp_at >= SETTLE
            if temp_settled and st.get("temp") is not None and current_set is not None \
                    and abs(float(current_set) - float(st["temp"])) > 0.01 and not st.get("manual_temp"):
                st["manual_temp"] = True   # Soll am Gerät/Pad geändert: bis zum nächsten Wechsel behalten
                changed = True
            if st.get("key") != key:
                st["manual_temp"] = False
                st["key"] = key
                changed = True
            if not st.get("manual_temp") and st.get("temp") != value:
                if await self._call("climate", "set_temperature", {ATTR_ENTITY_ID: ent, "temperature": value}):
                    st["temp"] = value
                    st["temp_at"] = now.isoformat()
                    changed = True
            if target.get("fan") and st.get("fan") != target["fan"]:
                await self.async_set_fan(room_id, target["fan"])
                st["fan"] = target["fan"]
                changed = True
            return changed

        if target is None or st.get("paused") == key:
            return changed
        if hvac != OFF:
            return changed   # läuft schon (von Hand oder Zuheizen) – nicht übernehmen
        if not may_start(klima.attributes.get("current_temperature"), target["start_at"]):
            return changed
        failed = self._start_failed.get(room_id)
        if failed is not None and now - failed < START_RETRY:
            return changed
        value = climate_setpoint(target["temp"], dict(klima.attributes))
        # Zuerst EIN und auf die Bestätigung des Geräts warten, dann Modus und Soll (klima_start).
        # Läuft im Hintergrund; solange bleibt der Raum hier unangetastet.
        self._starting.add(room_id)
        self.hass.async_create_task(self._start(room_id, ent, value, key, target), f"klimaplan_start_{room_id}")
        return changed

    async def _start(self, room_id: str, ent: str, value: float, key: Any, target: dict[str, Any]) -> None:
        try:
            # Lüfter: Auto, wenn die Regel nichts anderes sagt; der Auto-Schalter gehört zur Einschaltfolge
            fan = target.get("fan") or "auto"
            klima = self.hass.states.get(ent)
            modes = list(klima.attributes.get("fan_modes") or []) if klima else []
            auto_switch = self.rooms.get(room_id, {}).get("fan_auto_switch")
            fan_in_start = auto_switch if fan == "auto" and "auto" not in modes and auto_switch else None
            ok = await async_start_climate(self.hass, ent, COOL, value, self._call, fan_auto_switch=fan_in_start)
            st = self.state.setdefault(room_id, {"owned": False, "since": None, "temp": None, "fan": None,
                                                 "paused": None, "manual_temp": False})
            if ok:
                self._start_failed.pop(room_id, None)
                now = dt_util.now().isoformat()
                st.update(owned=True, since=now, temp=value, temp_at=now, fan=None,
                          paused=None, manual_temp=False, key=key)
                _LOGGER.info("Klimaplan: %s kühlt (%s °C, %s)", ent, value, target["source"])
                if not fan_in_start:
                    await self.async_set_fan(room_id, fan)
                st["fan"] = fan
            else:
                self._start_failed[room_id] = dt_util.now()
                _LOGGER.warning("Klimaplan: %s hat das Einschalten nicht bestätigt, neuer Versuch in %s min",
                                ent, int(START_RETRY.total_seconds() // 60))
        finally:
            self._starting.discard(room_id)
            self._save()
            async_dispatcher_send(self.hass, SIGNAL_UPDATED)

    # ------------------------------------------------------------------ Änderungen
    async def _changed(self, reload: bool = False) -> None:
        self._save()
        await self.async_apply()
        if reload:
            await self.async_flush()
            self.hass.async_create_task(self.hass.config_entries.async_reload(self.entry.entry_id))

    async def async_add_room(self, climate: str, name: str | None = None, fan_auto_switch: str | None = None,
                             plan_enabled: bool = False, protect: bool = True) -> str:
        if not climate.startswith("climate."):
            raise PlanError("Klimagerät muss climate.* sein")
        room_id = room_id_from_entity(climate)
        if room_id in self.rooms:
            raise PlanError(f"Raum {room_id} gibt es schon")
        state = self.hass.states.get(climate)
        self.rooms[room_id] = {
            "name": name or (state.name if state else room_id),
            "climate": climate,
            "fan_auto_switch": fan_auto_switch or None,
            "plan_enabled": bool(plan_enabled),
            "protect": bool(protect),
            "plan": default_plan(),
        }
        await self._changed(reload=True)
        return room_id

    async def async_update_room(self, room_id: str, **fields: Any) -> None:
        room = self.rooms[room_id]
        reload = False
        if fields.get("name") is not None:
            room["name"] = fields["name"]
            reload = True
        if fields.get("fan_auto_switch") is not None:
            room["fan_auto_switch"] = fields["fan_auto_switch"] or None
        for key in ("plan_enabled", "protect"):
            if fields.get(key) is not None:
                room[key] = bool(fields[key])
        await self._changed(reload=reload)

    async def async_remove_room(self, room_id: str) -> None:
        self.rooms.pop(room_id)
        self.state.pop(room_id, None)
        await self._changed(reload=True)

    async def async_set_day(self, room_id: str, day: str, windows: list, copy_to: list[str] | None = None) -> None:
        if day not in DAYS:
            raise PlanError(f"Unbekannter Tag {day}")
        norm = normalize_windows(windows)
        plan = self.rooms[room_id]["plan"]
        for d in [day, *(copy_to or [])]:
            if d in DAYS:
                plan[d] = [dict(w) for w in norm]
        await self._changed()

    async def async_set_plan(self, room_id: str, plan: dict[str, Any]) -> None:
        self.rooms[room_id]["plan"] = normalize_plan(plan)
        await self._changed()

    async def async_set_settings(self, cfg: dict[str, Any]) -> None:
        self.settings = normalize_settings({**self.settings, **cfg})
        await self._changed()
