"""Reine Logik von Klimaplan."""

from datetime import datetime, timezone

import pytest

from custom_components.klimaplan.logic import (
    DEFAULT_SETTINGS,
    PlanError,
    climate_setpoint,
    default_plan,
    hot_now,
    may_start,
    next_window,
    normalize_settings,
    normalize_windows,
    presence_threshold,
    room_target,
    window_at,
)

TZ = timezone.utc
MON_19 = datetime(2026, 7, 13, 19, 0, tzinfo=TZ)   # Montag
SAT_11 = datetime(2026, 7, 18, 11, 0, tzinfo=TZ)   # Samstag


def test_default_plan_windows():
    plan = default_plan()
    assert window_at(plan, MON_19)["temp"] == 23
    assert window_at(plan, MON_19.replace(hour=17)) is None
    assert window_at(plan, MON_19.replace(hour=21)) is None
    assert window_at(plan, SAT_11)["start"] == "10:00"
    at, w = next_window(plan, MON_19.replace(hour=12))
    assert at.hour == 18 and w["fan"] == "auto"
    at, _ = next_window(plan, datetime(2026, 7, 17, 22, 0, tzinfo=TZ))   # Fr abends -> Sa 10:00
    assert at.weekday() == 5 and at.hour == 10


def test_normalize_windows_errors():
    assert normalize_windows([{"start": "9:00", "end": "10:00", "temp": 22.3}])[0]["temp"] == 22.5
    for bad in ([{"start": "10:00", "end": "09:00", "temp": 22}],
                [{"start": "10:00", "end": "12:00", "temp": 40}],
                [{"start": "10:00", "end": "12:00", "temp": 22}, {"start": "11:00", "end": "13:00", "temp": 22}]):
        with pytest.raises(PlanError):
            normalize_windows(bad)


def test_hot_hysteresis():
    assert hot_now(False, "30.5", 30, 2) is True
    assert hot_now(False, "30", 30, 2) is False        # „über“ 30
    assert hot_now(True, "28.5", 30, 2) is True        # bleibt heiß bis unter 28
    assert hot_now(True, "28", 30, 2) is False
    assert hot_now(True, "unavailable", 30, 2) is False


def test_presence_threshold():
    t = presence_threshold(MON_19, "05:00")
    assert (t.day, t.hour) == (13, 5)
    t = presence_threshold(MON_19.replace(hour=3), "05:00")
    assert (t.day, t.hour) == (12, 5)


def _room(**kw):
    return {"plan_enabled": True, "protect": True, "plan": default_plan(), **kw}


def _target(now=MON_19, **kw):
    args = {"room": _room(), "settings": DEFAULT_SETTINGS, "now": now, "hot": True,
            "present_today": True, "anyone_home": True, "blocked": False, **kw}
    return room_target(**args)


def test_room_target():
    t = _target()
    assert (t["temp"], t["source"], t["until"], t["fan"]) == (23, "plan", "21:00", "auto")
    # außerhalb des Fensters: Hitzeschutz 25
    assert _target(now=MON_19.replace(hour=12))["source"] == "protect"
    # beide weg: kein Plan, nur Hitzeschutz
    assert _target(anyone_home=False)["temp"] == 25
    # Raum ohne Plan
    assert _target(room=_room(plan_enabled=False))["source"] == "protect"
    # ohne Hitzeschutz und außerhalb des Plans: nichts
    assert _target(room=_room(protect=False), now=MON_19.replace(hour=12)) is None
    for kw in ({"hot": False}, {"present_today": False}, {"blocked": True},
               {"settings": {**DEFAULT_SETTINGS, "enabled": False}}):
        assert _target(**kw) is None
    # Planfenster wärmer als Hitzeschutz -> der niedrigere gilt
    room = _room(plan={"mon": [{"start": "18:00", "end": "21:00", "temp": 26, "fan": "2"}]})
    assert _target(room=room)["source"] == "protect"


def test_start_and_setpoint():
    assert may_start(25, 23, 2) and not may_start(24.9, 23, 2) and not may_start(None, 23, 2)
    attrs = {"min_temp": 19, "max_temp": 30, "target_temp_step": 0.5}
    assert climate_setpoint(23.2, attrs) == 23 and climate_setpoint(17, attrs) == 19


def test_settings():
    assert normalize_settings({})["outside_limit"] == 30
    assert normalize_settings({"presence_cutoff": "6:00"})["presence_cutoff"] == "06:00"
    for bad in ({"outside_limit": 60}, {"protect_temp": 10}, {"start_delta": "x"}, {"presence_cutoff": "25:00"}):
        with pytest.raises(PlanError):
            normalize_settings(bad)
