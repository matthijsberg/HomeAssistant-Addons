"""
Open HEMS: Unit Tests for In-Flight Run Continuity & Protection
===============================================================
Verifies that once a DHW run has started (either via smart grid actuation,
opportunistic merger, or autonomous start), subsequent replanning cycles
do NOT cancel, abort, or shift the run to later in the day.
"""

import pytest
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

from models.canonical import Quality, StandardizedState
from layer1_data_collection.sanitizer import TelemetrySanitizer
from layer3_scheduling.plan_store import PlanStore
from layer3_scheduling.central_planner import CentralPlanner
from layer3_scheduling.dhw_optimizer import solve, DhwOptimizerParams
from layer3_scheduling.dhw_specs import DhwTankSpec
from layer3_scheduling.tariff_provider import TariffProvider


def test_solve_respects_in_flight_slots_override():
    """Verify that solve() strictly enforces u=1 for initial in-flight slots even if later slots are cheaper."""
    now = datetime(2026, 9, 20, 12, 0, tzinfo=timezone.utc)
    slots = []
    # Create 8 slots: slot 0-3 are expensive (0.35 EUR/kWh), slots 4-7 are very cheap (0.05 EUR/kWh)
    for i in range(8):
        class DummySlot:
            pass
        s = DummySlot()
        s.dt = now + timedelta(minutes=15 * i)
        s.label = s.dt.strftime("%H:%M")
        s.outdoor_temp_c = 15.0
        s.solar_kw = 0.0
        s.unallocated_kw = 0.3
        s.price_all_in = 0.35 if i < 4 else 0.05
        s.is_hard_lockout = False
        slots.append(s)

    spec = DhwTankSpec(comfort_min_temp_c=40.0, target_setpoint_c=50.0)
    params = DhwOptimizerParams(comfort_margin_mode="p50", min_comfort_margin_c=0.0)

    # Without in-flight protection: optimizer would naturally prefer slots 4-7 (cheap)
    res_free = solve(slots, t0_c=44.0, spec=spec, params=params)
    assert 0 not in res_free.planned_slots, "Without in-flight protection, optimizer should wait for cheaper slots"

    # With in-flight protection (2 slots remaining): solver MUST enforce slots 0 and 1
    res_locked = solve(slots, t0_c=44.0, spec=spec, params=params, in_flight_slots=2, in_flight_target_c=50.0)
    assert 0 in res_locked.planned_slots, "Slot 0 must be locked in for in-flight run"
    assert 1 in res_locked.planned_slots, "Slot 1 must be locked in for in-flight run"


def test_central_planner_preserves_in_flight_run():
    """Verify that CentralPlanner.plan() maintains in-flight run commitment from PlanStore."""
    store = PlanStore.get_instance()
    store.register_in_flight_run(target_temp_c=50.0, mode="forced_on")

    now = datetime(2026, 9, 20, 12, 15, tzinfo=ZoneInfo("Europe/Amsterdam"))
    raw_prices = [{"dt": (now + timedelta(hours=i)).isoformat(), "price": 0.25} for i in range(24)]
    raw_solar = [{"dt": (now + timedelta(hours=i)).isoformat(), "solar_kw": 2.0} for i in range(24)]
    raw_weather = [{"dt": (now + timedelta(hours=i)).isoformat(), "temperature": 18.0} for i in range(24)]

    frame = TelemetrySanitizer.sanitize(
        now=now,
        raw_prices=raw_prices,
        raw_solar=raw_solar,
        raw_weather=raw_weather,
        raw_unallocated_matrix=[0.30] * 672,
        current_dhw_temp=44.0,
        last_hardware_reading_time=now,
        horizon_slots=96,
        step_mins=15
    )

    plan = CentralPlanner.plan(frame, current_dhw_temp=44.0)

    # In-flight run must be present on slot 0
    assert plan.slots[0].dhw_kw > 0.0, "Slot 0 must have active DHW power"
    assert plan.slots[0].mode_code in [StandardizedState.FORCED_ON, StandardizedState.MAX_ON]
    assert "nu" in plan.dhw_summary.run_start.lower()

    # When tank temperature reaches target, in-flight run is cleared
    plan_finished = CentralPlanner.plan(frame, current_dhw_temp=50.0)
    assert store.get_in_flight_run() is None, "In-flight run should be cleared once setpoint is attained"
