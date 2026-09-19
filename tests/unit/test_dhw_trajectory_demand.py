"""
Unit Tests for DHW Trajectory Demand (WP2)
==========================================
Verifies:
1. dhw_optimizer.solve() returns demand_kwh_th and demand_p95_kwh_th in trajectory.
2. adapt_optimizer_to_dhw_summary preserves demand_kwh_th.
3. Total demand across 24h horizon is strictly positive for active profiles.
"""

import pytest
from datetime import datetime, timezone
from layer3_scheduling.dhw_optimizer import solve, DhwOptimizerParams
from layer3_scheduling.dhw_specs import DhwTankSpec
from layer3_scheduling.dhw_plan_adapter import adapt_optimizer_to_dhw_summary
from layer2_calibration.dhw_thermal_model import DhwThermalModel
from tests.unit.test_dhw_optimizer import make_test_slots


def test_dhw_status_trajectory_contains_demand():
    """Verify that the optimizer trajectory includes quarter-hourly demand arrays."""
    now_dt = datetime(2026, 9, 19, 0, 0, tzinfo=timezone.utc)
    slots = make_test_slots(now_dt, hours=24)
    spec = DhwTankSpec()
    params = DhwOptimizerParams()
    dhw_model = DhwThermalModel()

    res = solve(slots, t0_c=45.0, spec=spec, params=params, dhw_model=dhw_model)

    assert "demand_kwh_th" in res.trajectory
    assert "demand_p95_kwh_th" in res.trajectory

    demands = res.trajectory["demand_kwh_th"]
    assert len(demands) == len(slots)
    # Total daily thermal tap demand must be positive (typical day 3.0 to 7.0 kWh_th)
    total_demand = sum(demands)
    assert total_demand > 1.0, f"Expected positive daily demand, got {total_demand}"

    summary = adapt_optimizer_to_dhw_summary(
        opt_result=res,
        slots=slots,
        t0_c=45.0,
        dhw_model=dhw_model,
        spec=spec,
        params=params
    )

    assert summary.decision_details is not None
    details_traj = summary.decision_details["trajectory"]
    assert "demand_kwh_th" in details_traj
    assert sum(details_traj["demand_kwh_th"]) == total_demand
