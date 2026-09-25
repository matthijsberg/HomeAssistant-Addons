"""
Unit Tests: Home Battery Optimization Policy (Priority 3)
==========================================================
Tests solar absorption, peak shaving, reserve holding, winter night valley
grid charging, profit threshold enforcement, and physical SoC bounds.
"""

import pytest
from pathlib import Path
import inspect
from layer3_scheduling.battery_policy import (
    BatteryPolicy,
    BatterySpec,
    BatteryPlanSummary,
)


def test_entity_isolation_in_battery_policy():
    """Invariant #2: Zero Home Assistant entity strings in scheduling logic."""
    import layer3_scheduling.battery_policy as bp
    content = Path(inspect.getfile(bp)).read_text(encoding="utf-8")
    for forbidden in ["sensor.", "climate.", "switch.", "input_boolean.", "binary_sensor."]:
        assert forbidden not in content, f"Forbidden entity prefix '{forbidden}' leaked into battery_policy!"


def test_solar_surplus_absorption():
    """In summer, excess solar charges the battery with CHARGE_SOLAR."""
    # 24 slots (6 hours): 12:00 to 18:00 with 3 kW solar surplus (-3.0 residual)
    residual = [-3.0] * 24
    prices = [0.25] * 24
    export_pr = [0.05] * 24

    spec = BatterySpec(capacity_kwh=15.0, max_charge_kw=5.0)
    res = BatteryPolicy.optimize(
        residual_demand_kw=residual,
        import_prices=prices,
        export_prices=export_pr,
        spec=spec,
        initial_soc_pct=20.0,
    )

    assert res.total_charged_solar_kwh > 5.0
    assert res.final_soc_pct > res.initial_soc_pct
    assert res.slots[0].mode_code == "CHARGE_SOLAR"
    assert res.slots[0].power_kw > 0.0


def test_peak_shaving_forced_lockout():
    """During forced peak lockouts, battery discharges to cover house load."""
    # 96 quarters: slot 68 to 76 (17:00 - 19:00) is peak lockout with 2 kW demand
    residual = [0.4] * 96
    for i in range(68, 76):
        residual[i] = 2.5  # House needs 2.5 kW

    prices = [0.25] * 96
    for i in range(68, 76):
        prices[i] = 0.48  # Expensive peak

    spec = BatterySpec(capacity_kwh=15.0)
    res = BatteryPolicy.optimize(
        residual_demand_kw=residual,
        import_prices=prices,
        export_prices=[0.05] * 96,
        spec=spec,
        initial_soc_pct=80.0,
        forced_off_indices=set(range(68, 76)),
    )

    # In slot 68, battery should discharge
    assert res.slots[68].mode_code == "DISCHARGE_PEAK"
    assert res.slots[68].power_kw < 0.0
    assert res.total_discharged_kwh > 2.0


def test_reserve_holding_ahead_of_expensive_peak():
    """Battery holds charge in moderate price slots if upcoming peak needs it."""
    residual = [0.5] * 40
    # Slot 30 to 36 has a high peak
    for i in range(30, 36):
        residual[i] = 3.0

    prices = [0.38] * 40
    for i in range(30, 36):
        prices[i] = 0.50  # Huge peak later

    spec = BatterySpec(capacity_kwh=15.0)
    # Low initial SoC (30% = ~4.5 kWh), barely enough to cover the 3 kW * 1.5h = 4.5 kWh peak
    res = BatteryPolicy.optimize(
        residual_demand_kw=residual,
        import_prices=prices,
        export_prices=[0.05] * 40,
        spec=spec,
        initial_soc_pct=30.0,
        forced_off_indices=set(range(30, 36)),
    )

    # In early slots, battery should hold reserve rather than emptying on 0.5 kW at €0.26
    modes = [s.mode_code for s in res.slots[:25]]
    assert "HOLD_RESERVE" in modes or "STANDBY" in modes
    # And during peak, it discharges
    assert res.slots[30].mode_code == "DISCHARGE_PEAK"


def test_winter_night_valley_grid_charging_on_high_spread():
    """Winter: battery charges from grid during cheap night valley to survive morning peak."""
    residual = [1.5] * 48  # 12 hours: night to morning, 1.5 kW heat pump demand
    prices = [0.30] * 48
    # Night valley (02:00 to 05:00 = slots 8 to 20): cheap €0.15
    for i in range(8, 20):
        prices[i] = 0.15
    # Morning peak (07:00 to 09:00 = slots 28 to 36): expensive €0.45
    for i in range(28, 36):
        prices[i] = 0.45

    spec = BatterySpec(capacity_kwh=15.0, min_cycle_margin_eur_kwh=0.08)
    res = BatteryPolicy.optimize(
        residual_demand_kw=residual,
        import_prices=prices,
        export_prices=[0.05] * 48,
        spec=spec,
        initial_soc_pct=15.0,  # Nearly empty
        forced_off_indices=set(range(28, 36)),
    )

    # Battery should schedule night grid pre-charging
    assert res.total_charged_grid_kwh > 2.0
    night_modes = [res.slots[i].mode_code for i in range(8, 20)]
    assert "CHARGE_GRID" in night_modes


def test_no_night_grid_charging_when_spread_too_small():
    """Battery does NOT charge from grid if price spread is too small to cover degradation."""
    residual = [1.5] * 48
    prices = [0.28] * 48
    # Small night valley: €0.24 (spread is only 4 ct, whereas degradation is 7.8 ct + 13% loss)
    for i in range(8, 20):
        prices[i] = 0.24
    for i in range(28, 36):
        prices[i] = 0.28

    spec = BatterySpec(capacity_kwh=15.0, min_cycle_margin_eur_kwh=0.08)
    res = BatteryPolicy.optimize(
        residual_demand_kw=residual,
        import_prices=prices,
        export_prices=[0.05] * 48,
        spec=spec,
        initial_soc_pct=15.0,
    )

    # Zero grid charging allowed
    assert res.total_charged_grid_kwh == 0.0
    for s in res.slots:
        assert s.mode_code != "CHARGE_GRID"


def test_soc_physical_bounds_enforced():
    """SoC never breaches min_soc_pct (10%) or max_soc_pct (95%)."""
    residual = [-10.0] * 48  # Massive solar overload
    prices = [0.10] * 48

    spec = BatterySpec(capacity_kwh=15.0, min_soc_pct=10.0, max_soc_pct=95.0, max_charge_kw=5.0)
    res = BatteryPolicy.optimize(
        residual_demand_kw=residual,
        import_prices=prices,
        export_prices=[0.05] * 48,
        spec=spec,
        initial_soc_pct=90.0,
    )

    assert res.max_projected_soc_pct <= 95.0
    for s in res.slots:
        assert s.soc_pct <= 95.0
        assert s.soc_pct >= 10.0
        # abs(): zonder deze was een ontlading van -5,361 kW door de limiet geglipt
        assert s.power_kw <= spec.max_charge_kw
        assert -s.power_kw <= spec.max_discharge_kw


def test_discharge_power_respects_inverter_ac_limit():
    """Ontlaadvermogen is AC en mag de omvormerlimiet nooit overschrijden."""
    spec = BatterySpec(capacity_kwh=15.0, max_discharge_kw=5.0)
    res = BatteryPolicy.optimize(
        residual_demand_kw=[5.0] * 8,
        import_prices=[0.40] * 8,
        export_prices=[0.05] * 8,
        spec=spec,
        initial_soc_pct=90.0,
    )
    for s in res.slots:
        assert abs(s.power_kw) <= spec.max_discharge_kw + 1e-9, (
            f"slot {s.slot_idx} ontlaadt {s.power_kw} kW boven de limiet {spec.max_discharge_kw}"
        )


def test_energy_conservation_on_discharge():
    """SoC-daling is precies de geleverde AC-energie gedeeld door het eenrichtingsrendement."""
    spec = BatterySpec(capacity_kwh=15.0, max_discharge_kw=5.0)
    step = 0.25
    soc0 = spec.capacity_kwh * 0.90
    res = BatteryPolicy.optimize(
        residual_demand_kw=[4.0] * 8,
        import_prices=[0.40] * 8,
        export_prices=[0.05] * 8,
        spec=spec,
        initial_soc_pct=90.0,
        step_hours=step,
    )
    eta = spec.roundtrip_efficiency ** 0.5
    ac_delivered = sum(abs(s.power_kw) for s in res.slots if s.power_kw < 0) * step
    soc_drop = soc0 - res.slots[-1].soc_kwh
    # Tolerantie volgt uit round(act_kw, 3) en round(soc_kwh, 3) in de dispatch:
    # maximaal 8 slots x 0,0005 kW x 0,25 h ~= 1e-3 kWh.
    assert abs(soc_drop - ac_delivered / eta) < 2e-3, (
        f"SoC-daling {soc_drop:.4f} kWh wijkt af van verwacht {ac_delivered / eta:.4f} kWh"
    )


def test_missing_soc_yields_inactive_plan():
    """Zonder gemeten SoC wordt niets gepland en niets beweerd (Invariant 3)."""
    res = BatteryPolicy.optimize(
        residual_demand_kw=[3.0] * 16,
        import_prices=[0.40] * 16,
        export_prices=[0.05] * 16,
        spec=BatterySpec(),
        initial_soc_pct=None,
    )
    assert res.is_active is False
    assert res.inactive_reason
    assert res.net_financial_saving_eur == 0.0
    assert len(res.slots) == 16
    assert all(s.power_kw == 0.0 and s.mode_code == "STANDBY" for s in res.slots)


def test_net_saving_includes_grid_charging_cost():
    """De gerapporteerde besparing is netto: laadkosten gaan eraf."""
    spec = BatterySpec(capacity_kwh=15.0, min_cycle_margin_eur_kwh=0.05)
    residual = [0.3] * 40 + [4.0] * 16 + [0.3] * 40
    prices = [0.10] * 40 + [0.45] * 16 + [0.10] * 40
    res = BatteryPolicy.optimize(
        residual_demand_kw=residual,
        import_prices=prices,
        export_prices=[0.05] * 96,
        spec=spec,
        initial_soc_pct=20.0,
    )
    assert res.total_charged_grid_kwh > 0.0, "scenario moet netladen uitlokken"
    expected = -sum(s.cost_impact_eur for s in res.slots)
    assert abs(res.net_financial_saving_eur - expected) < 0.01, (
        f"gerapporteerd {res.net_financial_saving_eur} vs som slotimpact {expected:.2f}"
    )


def test_no_discharge_when_avoided_import_below_degradation_floor():
    """Ontladen moet degradatie plus exportwaarde verslaan, ook zonder komende piek."""
    spec = BatterySpec(capacity_kwh=15.0, degradation_cost_eur_kwh=0.078)
    res = BatteryPolicy.optimize(
        residual_demand_kw=[0.5] * 32,
        import_prices=[0.10] * 32,      # 0.10 - 0.078 = 0.022 < export 0.05
        export_prices=[0.05] * 32,
        spec=spec,
        initial_soc_pct=90.0,
    )
    assert res.total_discharged_kwh == 0.0
    assert all(s.mode_code != "DISCHARGE_PEAK" for s in res.slots)
