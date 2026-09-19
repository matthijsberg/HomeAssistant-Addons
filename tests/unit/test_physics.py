"""
Unit Tests for Core Physics Module (models/physics.py)
======================================================
Verifies thermodynamic purity, Carnot calculations, boundary clamping,
and defrost penalties.
"""

import pytest
from datetime import datetime, timezone
from models.physics import (
    calculate_carnot_cop,
    dhw_cop,
    dhw_heat_delivered_kwh,
    dhw_step,
    calculate_dhw_cop,
    calculate_dhw_thermal_output_kw,
)
from layer2_calibration.dhw_thermal_model import DhwThermalModel


def test_calculate_carnot_cop_standard():
    """Verify Carnot COP at typical outdoor and flow temperatures."""
    # At 7°C outdoor and 35°C flow, delta_t = 28K
    # T_flow_K = 308.15K, Carnot COP = 308.15 / 28 = 11.005
    # Empirical COP = 0.48 * 11.005 = 5.28
    cop_7c = calculate_carnot_cop(outdoor_temp_c=7.0, flow_temp_c=35.0)
    assert 5.0 <= cop_7c <= 5.5


def test_calculate_carnot_cop_defrost_penalty():
    """Verify that freezing temperatures (-2°C to +4°C) incur the 0.85 defrost penalty."""
    cop_dry = calculate_carnot_cop(outdoor_temp_c=5.0, flow_temp_c=35.0)
    cop_defrost = calculate_carnot_cop(outdoor_temp_c=3.0, flow_temp_c=35.0)
    # The defrost penalty (0.85) should make 3.0°C visibly lower than 5.0°C beyond natural Carnot delta
    assert cop_defrost < cop_dry


def test_calculate_carnot_cop_bounds():
    """Verify that extreme cold or mild conditions are clamped to [2.2, 6.8]."""
    cop_extreme_cold = calculate_carnot_cop(outdoor_temp_c=-25.0, flow_temp_c=45.0)
    assert cop_extreme_cold == 2.2

    cop_extreme_mild = calculate_carnot_cop(outdoor_temp_c=25.0, flow_temp_c=28.0)
    assert cop_extreme_mild == 6.8


def test_dhw_cop_monotonic_decrease_with_tank_temp():
    """COP must decrease monotonically as tank temperature rises (higher condensing temp)."""
    t_out = 10.0
    tank_temps = [35.0, 40.0, 45.0, 50.0, 55.0, 60.0]
    cops = [dhw_cop(t, t_out) for t in tank_temps]

    for i in range(len(cops) - 1):
        assert cops[i] > cops[i + 1], f"COP should decrease: {cops[i]} > {cops[i+1]} at {tank_temps[i]} vs {tank_temps[i+1]}"

    # Check nominal points
    assert dhw_cop(50.0, 10.0) == 2.0
    assert dhw_cop(60.0, 10.0) == 1.4


def test_dhw_cop_monotonic_increase_with_outdoor_temp():
    """COP must increase monotonically as outdoor temperature rises (higher evaporating temp)."""
    t_tank = 50.0
    out_temps = [-5.0, 0.0, 5.0, 10.0, 15.0, 20.0]
    cops = [dhw_cop(t_tank, t) for t in out_temps]

    for i in range(len(cops) - 1):
        assert cops[i] < cops[i + 1], f"COP should increase: {cops[i]} < {cops[i+1]} at {out_temps[i]} vs {out_temps[i+1]}"


def test_dhw_cop_clamping():
    """Verify that extreme cold/hot or extreme tank temps are clamped to [COP_min, COP_max]."""
    # Extremely cold outdoor temp and hot tank -> clamped to min (1.4)
    cop_low = dhw_cop(t_tank_c=70.0, t_outdoor_c=-20.0)
    assert cop_low == 1.4

    # Mild outdoor temp and very cold tank -> clamped to max (3.2)
    cop_high = dhw_cop(t_tank_c=20.0, t_outdoor_c=35.0)
    assert cop_high == 3.2


def test_dhw_warming_rate_consistency():
    """
    WP6 Consistency Guardrail:
    Verify that simulated tank warming rate with 3.0 kW electrical power and COP ~2.0
    is within 25% of the empirical 12 to 15 °C/hour (benchmark 13.5 °C/h) observed in InfluxDB runs.
    """
    c_tank = 0.407  # kWh/K for 350L
    p_el = 3.0      # kW compressor draw
    t_start = 45.0
    t_out = 10.0
    ua = 2.5        # W/K
    spec = {
        "thermal_capacity_kwh_per_k": c_tank,
        "ua_w_per_k": ua,
        "heat_pump_power_kw": p_el,
        "target_temp_c": 60.0
    }
    t_curr = t_start
    for _ in range(4):
        t_curr = dhw_step(
            t_tank_c=t_curr,
            u=1.0,
            q_tap_kwh=0.0,
            t_outdoor_c=t_out,
            dt_h=0.25,
            spec=spec,
            t_max_c=60.0
        )
    warming_rate_per_hour = t_curr - t_start
    empirical_benchmark = 13.5
    relative_error = abs(warming_rate_per_hour - empirical_benchmark) / empirical_benchmark
    assert relative_error <= 0.25, (
        f"Simulated warming rate {warming_rate_per_hour:.2f} °C/h deviates by "
        f"{relative_error * 100:.1f}% from empirical benchmark {empirical_benchmark} °C/h (must be <= 25%)"
    )


def test_dhw_step_energy_conservation():
    """
    Verify First Law energy conservation:
    A heating run of n quarter-hours with zero standby loss (or UA=0) and zero tapping
    must yield an exact temperature rise of Delta_T = Q_delivered_total / C.
    """
    c_tank = 0.407  # kWh / K
    spec = {
        "thermal_capacity_kwh_per_k": c_tank,
        "ua_w_per_k": 0.0,  # Zero standing loss to test pure thermal accumulation
        "heat_pump_power_kw": 1.8,
        "target_temp_c": 60.0,
    }

    t_curr = 40.0
    total_q_delivered = 0.0
    n_slots = 4
    dt_h = 0.25

    for _ in range(n_slots):
        # Calculate expected heat delivered in this slot
        q_slot = dhw_heat_delivered_kwh(t_curr, t_outdoor_c=10.0, p_el_kw=1.8, dt_h=dt_h)
        total_q_delivered += q_slot
        t_next = dhw_step(
            t_tank_c=t_curr,
            u=1.0,
            q_tap_kwh=0.0,
            t_outdoor_c=10.0,
            dt_h=dt_h,
            spec=spec,
            t_max_c=60.0,
        )
        t_curr = t_next

    expected_delta_t = total_q_delivered / c_tank
    actual_delta_t = t_curr - 40.0
    assert abs(actual_delta_t - expected_delta_t) < 1e-6, (
        f"Energy balance violation: actual delta_T={actual_delta_t:.6f}, expected={expected_delta_t:.6f}"
    )


def test_simulate_trajectory_monotonic_idle_cooling():
    """When u = 0 for all slots, simulate_trajectory must produce a strictly monotonically non-increasing temperature trajectory."""
    model = DhwThermalModel()
    start_dt = datetime(2026, 9, 18, 12, 0, tzinfo=timezone.utc)
    res = model.simulate_trajectory(
        t_start_c=55.0,
        start_dt=start_dt,
        hours_ahead=24,
        heat_pump_schedule_slots=[],  # u = 0 for all slots
        target_temp_c=50.0,
    )

    temps = res["temperatures_c"]
    assert len(temps) == 96
    for i in range(len(temps) - 1):
        assert temps[i] >= temps[i + 1], f"Trajectory must not increase at slot {i}: {temps[i]} < {temps[i+1]}"
    assert temps[-1] < temps[0]
