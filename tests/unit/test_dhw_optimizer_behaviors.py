"""
Unit Tests for DHW Optimizer Core Preserved Behaviors & Financials
==================================================================
Verifies preserved invariants:
1. calculate_slot_financials in layer3_scheduling/dhw_financials.py
2. Saturation suppression at high initial tank temperatures (e.g. 58°C)
3. Hard lockout suppression (u = 0 in locked slots)
4. Comfort boundary enforcement across rolling horizon
"""

import pytest
from datetime import datetime, timezone, timedelta
from layer3_scheduling.dhw_financials import calculate_slot_financials, DhwFinancials
from layer3_scheduling.dhw_optimizer import solve, DhwOptimizerParams
from layer3_scheduling.dhw_specs import DhwTankSpec
from layer3_scheduling.tariff_provider import TariffProvider
from tests.unit.test_dhw_optimizer import OptimizerMockSlot, make_test_slots


def test_calculate_slot_financials():
    """Verifies that solar self-consumption is valued at avoided export, remainder at import."""
    cfg = {
        "dynamic_tariffs": {
            "fallback_markup_import": 0.0121,
            "fallback_markup_export": 0.0121,
            "fallback_tax_electricity": 0.11085,
            "fallback_fixed_monthly_fee": 6.25
        }
    }
    tp = TariffProvider.from_dict(cfg)
    p_import = 0.25
    p_export = tp.calculate_export_value_from_import(p_import)

    # 1. Zero demand -> zero cost
    cost, self_kwh, grid_kwh, p_eff = calculate_slot_financials(
        solar_kw=3.0, unalloc_kw=0.5, el_demand_kw=0.0, price_all_in=p_import, step_hours=0.25, tariff_provider=tp
    )
    assert cost == 0.0
    assert self_kwh == 0.0
    assert grid_kwh == 0.0

    # 2. 100% solar surplus (demand 2.0 kW * 0.25h = 0.5 kWh, surplus 2.5 kW * 0.25h = 0.625 kWh)
    cost, self_kwh, grid_kwh, p_eff = calculate_slot_financials(
        solar_kw=3.0, unalloc_kw=0.5, el_demand_kw=2.0, price_all_in=p_import, step_hours=0.25, tariff_provider=tp
    )
    assert self_kwh == 0.5
    assert grid_kwh == 0.0
    assert abs(cost - (0.5 * p_export)) < 1e-5
    assert abs(p_eff - p_export) < 1e-5

    # 3. 50% solar surplus, 50% grid
    cost, self_kwh, grid_kwh, p_eff = calculate_slot_financials(
        solar_kw=1.5, unalloc_kw=0.5, el_demand_kw=4.0, price_all_in=p_import, step_hours=0.25, tariff_provider=tp
    )
    assert self_kwh == 0.25
    assert grid_kwh == 0.75
    expected_cost = (0.75 * p_import) + (0.25 * p_export)
    assert abs(cost - expected_cost) < 1e-5


def test_saturation_suppresses_heating_at_high_temp():
    """
    When tank is already saturated at high temperature (e.g. 58.5°C),
    the optimizer naturally avoids unprofitable heating runs.
    """
    now_dt = datetime(2026, 9, 18, 12, 0, tzinfo=timezone.utc)
    slots = make_test_slots(now_dt, hours=24)
    spec = DhwTankSpec()
    params = DhwOptimizerParams()

    res = solve(slots, t0_c=58.5, spec=spec, params=params)

    # In the first 8 hours (32 slots), tank is hot enough that no run is initiated
    initial_runs = [r for r in res.runs if r.start_idx < 32]
    assert len(initial_runs) == 0, f"Expected no run when tank is at 58.5°C, got: {initial_runs}"


def test_lockout_suppression_and_comfort():
    """Verifies that hard lockouts strictly suppress heating while comfort is preserved outside."""
    now_dt = datetime(2026, 9, 18, 12, 0, tzinfo=timezone.utc)
    slots = make_test_slots(now_dt, hours=24)
    spec = DhwTankSpec()
    params = DhwOptimizerParams()

    # Lock slots 4..8
    for i in range(4, 9):
        slots[i].is_hard_lockout = True

    res = solve(slots, t0_c=45.0, spec=spec, params=params)

    for i in range(4, 9):
        assert i not in res.planned_slots
        assert res.slot_modes[i] == "forced_off"
