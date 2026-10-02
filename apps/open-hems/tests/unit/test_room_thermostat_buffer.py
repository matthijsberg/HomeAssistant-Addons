"""
Tests for Room Thermostat Floor Buffer Controller
=================================================
Verifies:
1. +1.0°C elevation above current room temperature.
2. Rate-limiting: max 2 changes/hour (1 up, 1 down).
3. Rate-limiting: max 6 changes/24h (3 complete cycles).
4. Minimum run duration: 45 minutes hold time before de-actuation.
5. Watchdog reset: lingering elevated setpoint restored to baseline.
"""

import pytest
from datetime import datetime, timedelta, timezone
from layer4_control.room_thermostat_buffer import RoomThermostatBufferController


def test_elevation_and_baseline_capture():
    ctrl = RoomThermostatBufferController(default_baseline_c=20.5)
    t0 = datetime(2026, 9, 21, 12, 0, tzinfo=timezone.utc)

    # Current room temp 21.2°C, baseline 20.5°C -> target should be 22.2°C (+1.0°C)
    target = ctrl.request_preheat(current_room_temp_c=21.2, current_setpoint_c=20.5, now=t0)
    assert target == 22.2
    assert ctrl.is_buffering is True
    assert ctrl.baseline_temp_c == 20.5


def test_minimum_run_duration_45m():
    ctrl = RoomThermostatBufferController(default_baseline_c=20.0)
    t0 = datetime(2026, 9, 21, 10, 0, tzinfo=timezone.utc)
    target = ctrl.request_preheat(current_room_temp_c=19.5, current_setpoint_c=20.0, now=t0)
    assert target == 21.0

    # Try release after 20 minutes -> should be blocked (< 45 mins)
    t_early = t0 + timedelta(minutes=20)
    res_early = ctrl.request_release(now=t_early)
    assert res_early is None
    assert ctrl.is_buffering is True

    # Try release after 50 minutes -> allowed, returns baseline 20.0°C
    t_ok = t0 + timedelta(minutes=50)
    res_ok = ctrl.request_release(now=t_ok)
    assert res_ok == 20.0
    assert ctrl.is_buffering is False


def test_rate_limit_max_2_per_hour():
    ctrl = RoomThermostatBufferController(default_baseline_c=20.0)
    t0 = datetime(2026, 9, 21, 8, 0, tzinfo=timezone.utc)

    # Change 1 (UP)
    assert ctrl.request_preheat(current_room_temp_c=20.0, current_setpoint_c=20.0, now=t0) == 21.0
    # Change 2 (DOWN at t0 + 50m)
    t1 = t0 + timedelta(minutes=50)
    assert ctrl.request_release(now=t1) == 20.0

    # Attempt Change 3 (UP again at t0 + 55m, which is within the 1-hour rolling window from t0)
    t2 = t0 + timedelta(minutes=55)
    assert ctrl.request_preheat(current_room_temp_c=20.0, current_setpoint_c=20.0, now=t2) is None

    # At t0 + 65m, the first change (t0) is older than 1 hour -> allowed again
    t3 = t0 + timedelta(minutes=65)
    assert ctrl.request_preheat(current_room_temp_c=20.0, current_setpoint_c=20.0, now=t3) == 21.0


def test_rate_limit_max_6_per_24h():
    ctrl = RoomThermostatBufferController(default_baseline_c=20.0)
    base_t = datetime(2026, 9, 21, 0, 0, tzinfo=timezone.utc)

    # 3 full cycles spaced across 6 hours:
    # Cycle 1: up at 0h, down at 1h (2 changes)
    assert ctrl.request_preheat(20.0, 20.0, now=base_t + timedelta(hours=0)) == 21.0
    assert ctrl.request_release(now=base_t + timedelta(hours=1)) == 20.0

    # Cycle 2: up at 2h, down at 3h (4 changes)
    assert ctrl.request_preheat(20.0, 20.0, now=base_t + timedelta(hours=2)) == 21.0
    assert ctrl.request_release(now=base_t + timedelta(hours=3)) == 20.0

    # Cycle 3: up at 4h, down at 5h (6 changes)
    assert ctrl.request_preheat(20.0, 20.0, now=base_t + timedelta(hours=4)) == 21.0
    assert ctrl.request_release(now=base_t + timedelta(hours=5)) == 20.0

    # Attempt Cycle 4: up at 6h -> should be blocked (6 changes in 24h)
    assert ctrl.request_preheat(20.0, 20.0, now=base_t + timedelta(hours=6)) is None


def test_watchdog_restores_lingering_setpoint():
    ctrl = RoomThermostatBufferController(default_baseline_c=20.5)
    t0 = datetime(2026, 9, 21, 14, 0, tzinfo=timezone.utc)

    # No buffer in flight, but setpoint in HA is 22.0°C (lingering elevated)
    assert ctrl.is_buffering is False
    restored = ctrl.watchdog_check(current_setpoint_c=22.0, now=t0)
    assert restored == 20.5
