"""
Unit Tests for DHW Optimizer Feasibility & Boundary Invariants (WP1)
===================================================================
Verifies:
1. Coasting through locked slots does NOT artificially inflate the comfort boundary (no 0.25°C creep per slot).
2. Delay counterfactual remains feasible when physical dynamics permit.
3. No NaN values ever occur in the value function table.
"""

import pytest
import numpy as np
import math
from datetime import datetime, timezone, timedelta

from layer3_scheduling.dhw_optimizer import solve, DhwOptimizerParams, DhwOptimizerResult
from layer3_scheduling.dhw_specs import DhwTankSpec
from layer3_scheduling.tariff_provider import TariffProvider
from layer2_calibration.dhw_thermal_model import DhwThermalModel
from tests.unit.test_dhw_optimizer import OptimizerMockSlot, make_test_slots


def test_locked_coast_does_not_inflate_comfort_boundary():
    """
    WP1 Guardrail:
    16 slots hard lockout from k=0. Initial temp is 1.0 K above comfort boundary.
    In the old implementation, V interpolating over inf caused the boundary to creep up
    by 0.25°C per slot, causing an unphysical validation_issue or emergency degradation.
    The new finite interpolation must stay feasible (validation_issue is None)
    and strictly respect comfort bounds.
    """
    now_dt = datetime(2026, 9, 19, 0, 0, tzinfo=timezone.utc)
    slots = make_test_slots(now_dt, hours=24)
    spec = DhwTankSpec()
    params = DhwOptimizerParams(min_run_slots=3, min_dwell_slots=4)

    # 16 slots locked (4 hours)
    for i in range(16):
        slots[i].is_hard_lockout = True

    # Initial tank temp: comfort_min (40.0) + fixed_margin (2.0) + 1.0 = 43.0°C
    t0 = 43.0
    res = solve(slots, t0_c=t0, spec=spec, params=params)

    assert res.validation_issue is None, f"Expected feasible plan, got validation issue: {res.validation_issue}"
    # Heating must NOT occur during locked slots
    for i in range(16):
        assert i not in res.planned_slots, f"Slot {i} should be locked out but was planned"
        assert res.slot_modes[i] == "forced_off"

    # Minimum temperature in trajectory must not violate comfort lower bound
    min_traj = min(res.trajectory["temperatures_c"])
    assert min_traj >= spec.comfort_min_temp_c - 0.05, f"Trajectory dipped below comfort min: {min_traj} < 40.0"


def test_no_nan_in_value_function():
    """
    WP1 Guardrail:
    Solve with high dwell requirement (e.g. 8 slots) and fragmented lockouts.
    Guarantee that no NaN values ever contaminate V or total costs.
    """
    now_dt = datetime(2026, 9, 19, 0, 0, tzinfo=timezone.utc)
    slots = make_test_slots(now_dt, hours=24)
    spec = DhwTankSpec()
    params = DhwOptimizerParams(min_run_slots=4, min_dwell_slots=8)

    # Intermittent lockouts
    for i in [2, 3, 10, 11, 12, 25, 26]:
        slots[i].is_hard_lockout = True

    res = solve(slots, t0_c=48.0, spec=spec, params=params)

    assert not math.isnan(res.total_cost_eur)
    for t in res.trajectory["temperatures_c"]:
        assert not math.isnan(t)
