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

    prices = [0.30] * 40
    for i in range(30, 36):
        prices[i] = 0.50  # Huge peak later

    spec = BatterySpec(capacity_kwh=15.0)
    res = BatteryPolicy.optimize(
        residual_demand_kw=residual,
        import_prices=prices,
        export_prices=[0.05] * 40,
        spec=spec,
        initial_soc_pct=50.0,
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


def test_discharge_hurdle_follows_degradation_and_terminal_valuation():
    """Ontlaaddrempel volgt c_deg + lambda/eta bij een vlakke prijs versus piek."""
    spec = BatterySpec(capacity_kwh=15.0, degradation_cost_eur_kwh=0.078)
    # Bij vlakke prijs onder de drempel ontlaadt de batterij niet onnodig
    res_flat = BatteryPolicy.optimize(
        residual_demand_kw=[1.0] * 24,
        import_prices=[0.15] * 24,
        export_prices=[0.02] * 24,
        spec=spec,
        initial_soc_pct=50.0
    )
    assert res_flat.total_discharged_kwh == 0.0

    # Bij een piek die boven de drempel uitstijgt ontlaadt de batterij exact om vraag te dekken
    pr = [0.11] * 24
    pr[10:14] = [0.35, 0.35, 0.35, 0.35]
    res_peak = BatteryPolicy.optimize(
        residual_demand_kw=[1.0] * 24,
        import_prices=pr,
        export_prices=[0.02] * 24,
        spec=spec,
        initial_soc_pct=50.0
    )
    peak_powers = [res_peak.slots[i].power_kw for i in range(10, 14)]
    assert all(p == -1.0 for p in peak_powers)


def test_no_hamster_grid_charging_without_future_demand():
    """Geen hamsteren: zonder latere vraag wordt er niet netgeladen, ongeacht daltarief."""
    spec = BatterySpec(capacity_kwh=15.0, degradation_cost_eur_kwh=0.078)
    res_no_demand = BatteryPolicy.optimize(
        residual_demand_kw=[0.0] * 24,
        import_prices=[0.05] * 12 + [0.30] * 12,
        export_prices=[0.01] * 24,
        spec=spec,
        initial_soc_pct=15.0
    )
    assert res_no_demand.total_charged_grid_kwh == 0.0


def test_negative_price_absorbs_solar_and_keeps_lambda_non_negative():
    """Bij negatieve prijzen wordt zonne-energie volledig geabsorbeerd en blijft lambda >= 0."""
    spec = BatterySpec(capacity_kwh=15.0, degradation_cost_eur_kwh=0.078)
    res_neg = BatteryPolicy.optimize(
        residual_demand_kw=[-2.0] * 8 + [1.0] * 16,
        import_prices=[-0.05] * 8 + [0.25] * 16,
        export_prices=[-0.10] * 8 + [0.05] * 16,
        spec=spec,
        initial_soc_pct=15.0
    )
    # Zonne-overschot (2.0 kW * 8 slots * 0.25h = 4.0 kWh) moet 100% in de batterij zijn geladen
    assert res_neg.total_charged_solar_kwh == 4.0
    # Geen export wanneer terugleveren geld kost
    for slot in res_neg.slots[:8]:
        assert slot.ch_solar_kw == 2.0


def test_cost_impact_single_slot_manual_calculation():
    """Handmatige verificatie van cost_impact_eur voor één kwartier met netladen (geen dubbeltelling)."""
    spec = BatterySpec(capacity_kwh=15.0, degradation_cost_eur_kwh=0.078)
    # Setup met 1.0 kW residu, netladen 2.0 kW, p_in = 0.10 EUR/kWh, dt = 0.25h
    # Base cost: 1.0 kW * 0.10 * 0.25 = 0.025 EUR
    # P_imp = 1.0 kW + 2.0 kW = 3.0 kW
    # Actual cost: (0.10 * 3.0 kW - 0.0 + 0.0) * 0.25 = 0.075 EUR
    # Cost impact = actual_cost - base_cost = 0.075 - 0.025 = +0.050 EUR (extra inkoop voor laden)
    # Zonder de fix (met dubbeltelling) zou actual cost zijn: (0.10*3 + 0.10*2)*0.25 = 0.125 EUR (impact +0.100 EUR).
    pr = [0.10] * 10 + [0.45] * 14
    res = [1.0] * 24
    plan = BatteryPolicy.optimize(
        residual_demand_kw=res,
        import_prices=pr,
        export_prices=[0.02] * 24,
        spec=spec,
        initial_soc_pct=10.0
    )
    # Zoek een slot met pure netlading
    grid_slot = next(s for s in plan.slots if s.ch_grid_kw > 1.0)
    dt = 0.25
    expected_base_cost = (0.10 * 1.0) * dt
    expected_actual_cost = (0.10 * (1.0 + grid_slot.ch_grid_kw)) * dt
    expected_impact = round(expected_actual_cost - expected_base_cost, 4)
    assert round(grid_slot.cost_impact_eur, 4) == expected_impact


def test_flat_price_window_spreads_charging():
    """8+ kwartieren op gelijke lage prijs spreidt het laden uit (<= knee_kw + marge, geen 5->0->5 zigzag)."""
    spec = BatterySpec(capacity_kwh=15.0, degradation_cost_eur_kwh=0.078, charge_knee_kw=2.5)
    # 8 slots op 0.11 EUR, daarna piek met vraag van ~5 kWh (8 slots * 2.5 kW * 0.25h = 5 kWh)
    res_flat = BatteryPolicy.optimize(
        residual_demand_kw=[0.0] * 8 + [2.5] * 8,
        import_prices=[0.11] * 8 + [0.45] * 8,
        export_prices=[0.02] * 16,
        spec=spec,
        initial_soc_pct=10.0,
    )
    grid_ch = [s.ch_grid_kw for s in res_flat.slots[:8]]
    # Alle laadkwartieren moeten netjes rond de knee liggen (geen plotselinge 5 kW sprongen gevolgd door 0)
    assert max(grid_ch) <= spec.charge_knee_kw + 0.5
    assert min(grid_ch) >= 1.5


def test_short_deep_valley_still_uses_full_power():
    """1-2 kwartieren diep dal voor een dure piek benut wel het volledige omvormervermogen (5 kW)."""
    spec = BatterySpec(capacity_kwh=15.0, degradation_cost_eur_kwh=0.078)
    res_short = BatteryPolicy.optimize(
        residual_demand_kw=[0.0] * 2 + [3.0] * 14,
        import_prices=[0.05] * 2 + [0.45] * 14,
        export_prices=[0.02] * 16,
        spec=spec,
        initial_soc_pct=10.0,
    )
    short_ch = [s.ch_grid_kw for s in res_short.slots[:2]]
    assert short_ch[0] == pytest.approx(5.0, abs=0.05)
    assert short_ch[1] == pytest.approx(5.0, abs=0.05)


def test_expensive_tail_keeps_terminal_reserve():
    """Goedkoop dal op dag 1, dag 2 volledig duur -> eind-SoC > min_soc en houdt reserve over."""
    spec = BatterySpec(capacity_kwh=15.0, degradation_cost_eur_kwh=0.078)
    pr_tail = [0.11] * 32 + [0.35] * 64
    res_tail = BatteryPolicy.optimize(
        residual_demand_kw=[0.5] * 96,
        import_prices=pr_tail,
        export_prices=[0.02] * 96,
        spec=spec,
        initial_soc_pct=10.0,
    )
    end_soc = res_tail.slots[-1].soc_pct
    assert end_soc > spec.min_soc_pct + 10.0
    assert res_tail.total_charged_grid_kwh > 10.0


def test_early_negative_price_does_not_zero_lambda():
    """Negatief kwartier vroeg in de horizon trekt terminale lambda niet omlaag; accu houdt reserve."""
    spec = BatterySpec(capacity_kwh=15.0, degradation_cost_eur_kwh=0.078)
    pr_neg_early = [-0.05] * 4 + [0.25] * 28 + [0.35] * 64
    res_neg_early = BatteryPolicy.optimize(
        residual_demand_kw=[0.5] * 96,
        import_prices=pr_neg_early,
        export_prices=[-0.10] * 4 + [0.05] * 92,
        spec=spec,
        initial_soc_pct=10.0,
    )
    end_soc_neg = res_neg_early.slots[-1].soc_pct
    assert end_soc_neg > spec.min_soc_pct + 10.0


