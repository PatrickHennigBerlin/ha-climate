"""Klimaplan: reine Logik ohne Home Assistant (testbar).

Begriffe
- Fenster: ein Zeitraum am Tag, in dem gekühlt werden darf ({start, end, temp, fan}).
- Hitzetag: Außentemperatur über der Grenze (mit Hysterese beim Abschalten).
- Anwesend heute: heute (seit 00:00) war jemand zu Hause oder mindestens N Bewegungsmelder haben
  ausgelöst – also kein Urlaub.
"""

from __future__ import annotations

import math
from datetime import date, datetime, time, timedelta
from typing import Any

DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
WEEKDAYS = DAYS[:5]
WEEKEND = DAYS[5:]

DEFAULT_SETTINGS: dict[str, Any] = {
    "enabled": True,          # Automatik insgesamt
    "plan_enabled": True,     # Zeitpläne
    "protect_enabled": True,  # Hitzeschutz
    "outside_limit": 30.0,    # Hitzetag ab (°C, „über“)
    "outside_hyst": 2.0,      # Hitzetag endet unter Grenze − Hysterese
    "start_delta": 2.0,       # Zeitplan: Klima startet erst bei Ist ≥ Plan-Soll + start_delta
    "protect_on": 28.0,       # Hitzeschutz: Klima startet ab dieser Innentemperatur
    "protect_temp": 25.0,     # Soll im Hitzeschutz
    "protect_fan": "auto",    # Lüfter im Hitzeschutz
    "presence_cutoff": "00:00",  # „anwesend heute“ zählt ab dieser Uhrzeit (Tagesbeginn)
    "motion_min": 2,          # „anwesend heute“ auch, wenn so viele verschiedene Melder ausgelöst haben (0 = aus)
}


class PlanError(ValueError):
    """Ungültiger Plan oder ungültige Einstellung."""


def _num(value: Any) -> float | None:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(v) else v


def to_min(text: str) -> int:
    try:
        h, m = (int(x) for x in str(text).strip().split(":")[:2])
    except (TypeError, ValueError) as err:
        raise PlanError(f"Ungültige Uhrzeit: {text!r}") from err
    if not (0 <= h <= 24 and 0 <= m < 60) or (h == 24 and m):
        raise PlanError(f"Ungültige Uhrzeit: {text!r}")
    return h * 60 + m


def fmt_min(m: int) -> str:
    return f"{m // 60:02d}:{m % 60:02d}"


def normalize_windows(windows: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    out = []
    for w in windows or []:
        start, end = to_min(w.get("start", "")), to_min(w.get("end", ""))
        if end <= start:
            raise PlanError(f"Fenster {w.get('start')}–{w.get('end')}: Ende muss nach dem Anfang liegen")
        temp = _num(w.get("temp"))
        if temp is None or not 16 <= temp <= 30:
            raise PlanError("Soll im Fenster: 16 bis 30 °C")
        fan = w.get("fan")
        out.append({"start": fmt_min(start), "end": fmt_min(end), "temp": round(temp * 2) / 2,
                    "fan": str(fan) if fan not in (None, "") else None})
    out.sort(key=lambda w: to_min(w["start"]))
    for a, b in zip(out, out[1:]):
        if to_min(b["start"]) < to_min(a["end"]):
            raise PlanError(f"Fenster überlappen: {a['start']}–{a['end']} und {b['start']}–{b['end']}")
    return out


def normalize_plan(plan: dict[str, Any] | None) -> dict[str, list[dict[str, Any]]]:
    plan = plan or {}
    return {d: normalize_windows(plan.get(d)) for d in DAYS}


def default_plan(temp: float = 23.0, fan: str | None = "auto") -> dict[str, list[dict[str, Any]]]:
    week = [{"start": "18:00", "end": "21:00", "temp": temp, "fan": fan}]
    weekend = [{"start": "10:00", "end": "21:00", "temp": temp, "fan": fan}]
    return {d: [dict(w) for w in (week if d in WEEKDAYS else weekend)] for d in DAYS}


def day_key(d: date | datetime) -> str:
    return DAYS[d.weekday()]


def window_at(plan: dict[str, list[dict[str, Any]]], now: datetime) -> dict[str, Any] | None:
    m = now.hour * 60 + now.minute
    for w in plan.get(day_key(now), []):
        if to_min(w["start"]) <= m < to_min(w["end"]):
            return w
    return None


def next_window(plan: dict[str, list[dict[str, Any]]], now: datetime) -> tuple[datetime, dict[str, Any]] | None:
    """Nächster Fensterbeginn nach jetzt (bis 7 Tage voraus)."""
    base = now.replace(hour=0, minute=0, second=0, microsecond=0)
    m_now = now.hour * 60 + now.minute
    for offset in range(8):
        day = base + timedelta(days=offset)
        for w in plan.get(day_key(day), []):
            start = to_min(w["start"])
            if offset == 0 and start <= m_now:
                continue
            return day + timedelta(minutes=start), w
    return None


def hot_now(was_hot: bool, outside: Any, limit: float, hyst: float) -> bool:
    """Hitzetag mit Hysterese. Ohne Außenwert: kein Hitzetag."""
    v = _num(outside)
    if v is None:
        return False
    return v > (limit - hyst if was_hot else limit)


def presence_threshold(now: datetime, cutoff: str) -> datetime:
    """Ab wann zählt „heute anwesend“: letzte Stichzeit vor jetzt."""
    m = to_min(cutoff)
    t = datetime.combine(now.date(), time(m // 60, m % 60), tzinfo=now.tzinfo)
    return t if now >= t else t - timedelta(days=1)


def room_target(*, room: dict[str, Any], settings: dict[str, Any], now: datetime, hot: bool,
                present_today: bool, anyone_home: bool, blocked: bool) -> dict[str, Any] | None:
    """Soll für einen Raum oder None. Bei Plan und Hitzeschutz zugleich gilt der niedrigere Soll.

    -> {"temp", "fan", "source", "key", "until"}  (key wechselt mit jedem neuen Fenster)
    """
    if blocked or not settings["enabled"] or not hot or not present_today:
        return None
    candidates = []
    if settings["plan_enabled"] and room.get("plan_enabled") and anyone_home:
        w = window_at(room.get("plan") or {}, now)
        if w is not None:
            candidates.append({"temp": w["temp"], "fan": w.get("fan"), "source": "plan",
                               "key": f"plan:{day_key(now)}:{w['start']}", "until": w["end"],
                               "start_at": w["temp"] + float(settings["start_delta"])})
    if settings["protect_enabled"] and room.get("protect", True):
        candidates.append({"temp": float(settings["protect_temp"]), "fan": settings.get("protect_fan"),
                           "source": "protect", "key": "protect", "until": None,
                           "start_at": float(settings["protect_on"])})
    if not candidates:
        return None
    best = dict(min(candidates, key=lambda c: (c["temp"], c["source"] != "plan")))
    # Einschalten, sobald eine der Regeln ihre Schwelle erreicht
    best["start_at"] = min(c["start_at"] for c in candidates)
    return best


def may_start(current: Any, start_at: float) -> bool:
    """Ist hat die Einschaltschwelle erreicht."""
    c = _num(current)
    return c is not None and c >= float(start_at) - 1e-9


def climate_setpoint(target: float, attrs: dict[str, Any]) -> float:
    value = float(target)
    step = _num(attrs.get("target_temp_step")) or 0.5
    value = round(value / step) * step
    low, high = _num(attrs.get("min_temp")), _num(attrs.get("max_temp"))
    if low is not None:
        value = max(value, low)
    if high is not None:
        value = min(value, high)
    return round(value, 2)


def normalize_settings(cfg: dict[str, Any] | None) -> dict[str, Any]:
    out = {**DEFAULT_SETTINGS, **(cfg or {})}
    try:
        for k in ("outside_limit", "outside_hyst", "start_delta", "protect_temp", "protect_on"):
            out[k] = round(float(out[k]) * 2) / 2
        out["motion_min"] = int(out["motion_min"])
    except (TypeError, ValueError) as err:
        raise PlanError("Klimaplan: Werte müssen Zahlen sein") from err
    for k in ("enabled", "plan_enabled", "protect_enabled"):
        out[k] = bool(out[k])
    to_min(out["presence_cutoff"])
    out["presence_cutoff"] = fmt_min(to_min(out["presence_cutoff"]))
    if not 15 <= out["outside_limit"] <= 45:
        raise PlanError("Außengrenze: 15 bis 45 °C")
    if not 0 <= out["outside_hyst"] <= 10:
        raise PlanError("Hysterese: 0 bis 10 K")
    if not 0 <= out["start_delta"] <= 10:
        raise PlanError("Einschaltabstand: 0 bis 10 K")
    if not 16 <= out["protect_temp"] <= 30:
        raise PlanError("Hitzeschutz-Soll: 16 bis 30 °C")
    if not 18 <= out["protect_on"] <= 40:
        raise PlanError("Hitzeschutz ab: 18 bis 40 °C")
    if out["protect_on"] < out["protect_temp"]:
        raise PlanError("Hitzeschutz ab muss mindestens so hoch sein wie der Hitzeschutz-Soll")
    if not 0 <= out["motion_min"] <= 10:
        raise PlanError("Bewegungsmelder: 0 bis 10")
    out["protect_fan"] = out.get("protect_fan") or None
    return out
