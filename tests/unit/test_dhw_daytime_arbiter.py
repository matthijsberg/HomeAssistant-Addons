"""
Unit Tests for DHW Daytime Dispatch & Economic Arbitrage Arbiter
================================================================
Verifies all 4 pure functions, thermodynamics, COP factors, and 24-hour cost trees.
"""

import pytest
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from dataclasses import dataclass
from typing import List

from layer3_scheduling.dhw_daytime_arbiter import DhwDaytimeArbiter, EvaluatedPath, DaytimeArbitrationResult
from layer2_calibration.dhw_thermal_model import DhwThermalModel
from layer3_scheduling.dhw_specs import DhwTankSpec


AMS_TZ = ZoneInfo("Europe/Amsterdam")


@dataclass
class MockSlot:
    slot_idx: int
    dt: datetime
    label: str
    price_all_in: float
    solar_kw: float
    unallocated_kw: float
    outdoor_temp_c: float = 12.0


def make_test_slots(start_dt: datetime, hours: int = 24, base_price: float = 0.28, solar_peak: float = 2.5) -> List[MockSlot]:
    slots = []
    total_quarters = hours * 4
    for i in range(total_quarters):
        s_dt = start_dt + timedelta(minutes=15 * i)
        lbl = s_dt.strftime("%H:%M")

        # Solar peak between 11:00 and 15:00
        solar = 0.0
        if 11 <= s_dt.hour <= 15:
            solar = solar_peak * (1.0 - abs(s_dt.hour + s_dt.minute/60.0 - 13.0) / 3.0)

        # Cheaper midday prices, higher evening prices
        price = base_price
        if 12 <= s_dt.hour <= 15:
            price = base_price - 0.10
        elif 18 <= s_dt.hour <= 21:
            price = base_price + 0.15

        slots.append(
            MockSlot(
                slot_idx=i,
                dt=s_dt,
                label=lbl,
                price_all_in=round(price, 4),
                solar_kw=round(max(0.0, solar), 2),
                unallocated_kw=0.45,
                outdoor_temp_c=12.0
            )
        )
    return slots


def test_calculate_slot_financials():
    # 1. Abundant solar surplus
    # Demand 1.8 kW for 15m (0.45 kWh). Solar 3.0 kW, unalloc 0.5 kW -> 2.5 kW surplus (0.625 kWh).
    # All demand covered by solar -> valued at export price (avoided feed-in)
    cost, self_kwh, grid_kwh, p_eff = DhwDaytimeArbiter.calculate_slot_financials(
        solar_kw=3.0,
        unalloc_kw=0.5,
        el_demand_kw=1.8,
        price_all_in=0.30,
        step_hours=0.25
    )
    assert grid_kwh == 0.0
    assert self_kwh == 0.45
    assert cost < 0.06
    assert p_eff < 0.15

    # 2. Zero solar: 100% grid import
    cost_g, self_g, grid_g, p_eff_g = DhwDaytimeArbiter.calculate_slot_financials(
        solar_kw=0.0,
        unalloc_kw=0.5,
        el_demand_kw=1.8,
        price_all_in=0.30,
        step_hours=0.25
    )
    assert self_g == 0.0
    assert grid_g == 0.45
    assert round(cost_g, 3) == round(0.45 * 0.30, 3)
    assert round(p_eff_g, 3) == 0.30


def test_evaluate_window_cost_cop_scaling():
    now_dt = datetime(2026, 9, 15, 11, 0, tzinfo=AMS_TZ)
    slots = make_test_slots(now_dt, hours=8)

    # 4 slots = 1 hour, need 4.07 kWh_th (10K rise)
    # Standard 50C run
    cost_50, el_50, cop_50 = DhwDaytimeArbiter.evaluate_window_cost(
        slots=slots,
        start_idx=4,
        n_req_slots=4,
        th_need_kwh=4.07,
        is_boost_60=False
    )

    # High temp 60C boost run (same thermal demand)
    cost_60, el_60, cop_60 = DhwDaytimeArbiter.evaluate_window_cost(
        slots=slots,
        start_idx=4,
        n_req_slots=4,
        th_need_kwh=4.07,
        is_boost_60=True
    )

    # COP_60 must be ~25% lower than COP_50
    assert cop_60 < cop_50
    assert round(cop_60 / cop_50, 2) == 0.75
    # Electricity required for 60C must be higher due to lower COP
    assert el_60 > el_50
    assert cost_60 > cost_50


def test_find_optimal_heating_window_lockout_avoidance():
    now_dt = datetime(2026, 9, 15, 11, 0, tzinfo=AMS_TZ)
    slots = make_test_slots(now_dt, hours=8)

    # Put a hard peak lockout on slots 8 to 11 (13:00 - 14:00)
    slot_lockout_map = {idx: {"is_hard_lockout": True} for idx in range(8, 12)}

    best_win = DhwDaytimeArbiter.find_optimal_heating_window(
        slots=slots,
        search_start_idx=0,
        search_end_idx=20,
        n_req_slots=4,
        th_need_kwh=4.07,
        slot_lockout_map=slot_lockout_map
    )

    assert best_win is not None
    # None of the chosen slots may intersect slots 8 to 11
    chosen_slots = set(range(best_win["start_idx"], best_win["end_idx"]))
    assert chosen_slots.isdisjoint(set(range(8, 12)))


def test_simulate_path_and_evaluate_night_logic():
    now_dt = datetime(2026, 9, 15, 11, 0, tzinfo=AMS_TZ)
    slots = make_test_slots(now_dt, hours=24)
    model = DhwThermalModel()

    # Scenario A: Tank is heated to 60°C at 12:00 -> morning dip should remain safe (>= 40°C)
    day_win = {"start_idx": 4, "end_idx": 8, "cost_eur": 0.25, "el_kwh": 1.8}
    res_safe = DhwDaytimeArbiter.simulate_path_and_evaluate_night(
        slots=slots,
        current_dhw_temp=48.0,
        day_target_c=60.0,
        day_window=day_win,
        dynamic_peaks=[],
        slot_lockout_map={},
        dhw_model=model,
        now_dt=now_dt
    )
    # With 60C heating, morning dip should be >= 40.0C and no night run required
    assert res_safe["morning_dip_c"] >= 40.0
    assert res_safe["night_run_required"] is False
    assert res_safe["night_cost_eur"] == 0.0
    assert res_safe["total_24h_cost_eur"] == 0.25


def test_daytime_arbitrage_situation_1_comfort_risk():
    now_dt = datetime(2026, 9, 15, 11, 0, tzinfo=AMS_TZ)
    # Cold tank at 38°C -> evening dip drops < 40°C -> Situation 1
    slots = make_test_slots(now_dt, hours=24, base_price=0.28, solar_peak=3.5)
    model = DhwThermalModel()

    arb_res = DhwDaytimeArbiter.evaluate_daytime_arbitrage(
        slots=slots,
        current_dhw_temp=38.0,
        dynamic_peaks=[],
        dhw_model=model,
        now_dt=now_dt
    )

    assert arb_res.situation == "SITUATION_1_EVENING_COMFORT_RISK"
    assert len(arb_res.evaluated_paths) == 2
    assert arb_res.evaluated_paths[0].path_id == "PAD_A1_DAY_50"
    assert arb_res.evaluated_paths[1].path_id == "PAD_A2_DAY_60"
    assert arb_res.planned_mode in ["forced_on", "forced_solar_boost_60"]
    assert len(arb_res.planned_slots) > 0


def test_daytime_arbitrage_situation_2_comfort_safe():
    now_dt = datetime(2026, 9, 15, 11, 0, tzinfo=AMS_TZ)
    # Warm tank at 55°C -> evening dip >= 40°C even under P95 heavy usage -> Situation 2
    # Zero solar and flat/higher midday price so buffering to 60C is not economical
    slots = []
    for i in range(96):
        s_dt = now_dt + timedelta(minutes=15 * i)
        # Night price cheap (0.22), day price normal (0.28), zero solar
        p = 0.22 if (s_dt.hour < 6 or s_dt.hour >= 23) else 0.28
        slots.append(
            MockSlot(
                slot_idx=i,
                dt=s_dt,
                label=s_dt.strftime("%H:%M"),
                price_all_in=p,
                solar_kw=0.0,
                unallocated_kw=0.45,
                outdoor_temp_c=12.0
            )
        )
    model = DhwThermalModel()

    arb_res = DhwDaytimeArbiter.evaluate_daytime_arbitrage(
        slots=slots,
        current_dhw_temp=55.0,
        dynamic_peaks=[],
        dhw_model=model,
        now_dt=now_dt
    )

    assert arb_res.situation == "SITUATION_2_COMFORT_SAFE"
    assert len(arb_res.evaluated_paths) == 2
    assert arb_res.evaluated_paths[0].path_id == "PAD_B1_STANDBY"
    assert arb_res.evaluated_paths[1].path_id == "PAD_B2_BUFFER_60"
    # When solar is zero and night is cheaper, Pad B1 (Standby) must win
    assert arb_res.selected_path.path_id == "PAD_B1_STANDBY"
    assert arb_res.planned_mode == "normal"


def test_daytime_arbitrage_saturation_lockout_at_59c():
    """Verify that when tank is at 59C (or >=53C), 60C buffering is strictly locked out with clear explanation."""
    now_dt = datetime(2026, 9, 15, 12, 0, tzinfo=AMS_TZ)
    # Even with abundant solar surplus (5.0 kW) and cheap prices:
    slots = make_test_slots(now_dt, hours=24, base_price=0.20, solar_peak=5.0)
    model = DhwThermalModel()

    arb_res = DhwDaytimeArbiter.evaluate_daytime_arbitrage(
        slots=slots,
        current_dhw_temp=59.0,
        dynamic_peaks=[],
        dhw_model=model,
        now_dt=now_dt
    )

    assert arb_res.situation == "SITUATION_2_COMFORT_SAFE"
    assert arb_res.planned_mode == "normal"
    assert len(arb_res.planned_slots) == 0
    assert "Vat Al Verzadigd" in arb_res.planned_mode_label
    assert "drempel" in arb_res.explanation
    assert "59.0°C" in arb_res.explanation


def test_compute_historical_draw_offs_no_phantom_demand():
    """Verify that during heat pump heating, stratification charging does not register as phantom tap demand."""
    timestamps = [
        "2026-09-17T03:00:00Z",
        "2026-09-17T03:15:00Z",
        "2026-09-17T03:30:00Z",
        "2026-09-17T03:45:00Z",
        "2026-09-17T04:00:00Z"
    ]
    # Heat pump runs at 2.5 kW el (0.625 kWh per quarter), tank rises from 43C to 50C
    temp_map = {
        "2026-09-17T03:00:00Z": 43.0,
        "2026-09-17T03:15:00Z": 46.0,
        "2026-09-17T03:30:00Z": 50.0,
        "2026-09-17T03:45:00Z": 49.9,
        "2026-09-17T04:00:00Z": 46.0  # Real shower! (dropped 3.9C)
    }
    el_map = {
        "2026-09-17T03:00:00Z": 0.625,
        "2026-09-17T03:15:00Z": 0.625,
        "2026-09-17T03:30:00Z": 0.625,
        "2026-09-17T03:45:00Z": 0.0,
        "2026-09-17T04:00:00Z": 0.0
    }

    demands = DhwThermalModel.compute_historical_draw_offs(
        sorted_timestamps=timestamps,
        temperature_map=temp_map,
        heatpump_el_kwh_map=el_map,
        interval_h=0.25
    )

    # During heating (03:00 to 03:30), tank is rising -> 0 phantom tap water
    assert demands[0] == 0.0
    assert demands[1] == 0.0
    assert demands[2] == 0.0
    # Idle with minor standby drop -> 0 tap
    assert demands[3] == 0.0
    # Shower (dropped 3.9C) -> ~1.5 kWh genuine tap water!
    assert demands[4] > 1.4


def test_rolling_24h_night_start_plans_tomorrow_daytime():
    """
    Verify that when the planner runs late at night (e.g. 23:30),
    and the tank is warm enough to bridge the morning peak,
    the arbiter dynamically schedules the daytime heating run tomorrow midday.
    """
    now_dt = datetime(2026, 9, 17, 23, 30, tzinfo=AMS_TZ)
    # 24h rolling slots starting at 23:30 today
    slots = []
    for i in range(96):
        s_dt = now_dt + timedelta(minutes=15 * i)
        # Tomorrow solar peak 11:00-14:00
        solar = 2.4 if (s_dt.date() > now_dt.date() and 11 <= s_dt.hour <= 14) else 0.0
        # Morning peak 06:30-09:15, evening peak 18:30-21:30
        p = 0.20
        if 11 <= s_dt.hour <= 14:
            p = 0.11
        elif 6 <= s_dt.hour <= 9 or 18 <= s_dt.hour <= 21:
            p = 0.35
        slots.append(
            MockSlot(
                slot_idx=i,
                dt=s_dt,
                label=s_dt.strftime("%H:%M"),
                price_all_in=p,
                solar_kw=solar,
                unallocated_kw=0.45,
                outdoor_temp_c=12.0
            )
        )

    dynamic_peaks = [
        {"name": "Ochtendspits", "start_idx": 28, "end_idx": 39, "is_hard_lockout": True},
        {"name": "Avondspits", "start_idx": 76, "end_idx": 88, "is_hard_lockout": True}
    ]

    model = DhwThermalModel()
    arb_res = DhwDaytimeArbiter.evaluate_daytime_arbitrage(
        slots=slots,
        current_dhw_temp=45.8,
        dynamic_peaks=dynamic_peaks,
        dhw_model=model,
        now_dt=now_dt
    )

    # Must detect evening comfort risk on tomorrow evening and plan daytime run
    assert arb_res.situation == "SITUATION_1_EVENING_COMFORT_RISK"
    assert arb_res.planned_mode in ["forced_on", "forced_solar_boost_60"]
    assert len(arb_res.planned_slots) > 0
    # Must be scheduled within the dynamic midday valley (between morning and evening peaks)
    for s_idx in arb_res.planned_slots:
        assert 39 <= s_idx < 76, f"Slot {s_idx} outside dynamic daytime valley"


def test_dhw_boost_60_slot_calculation_and_trajectory_alignment():
    """
    Regression test for DHW planning formula vs physics simulation mismatch.
    Scenario: Tank at 50.1°C, target 60.0°C (delta T = 9.9K).
    Verifies that:
    1. calculate_required_slots allocates 4 slots (not the under-dimensioned 3 slots).
    2. simulate_trajectory() with the planned slots finishes within 0.3°C of target_temp (>= 59.7°C).
    """
    from layer3_scheduling.dhw_specs import DhwTankSpec

    spec = DhwTankSpec()
    dhw_model = DhwThermalModel()

    n_slots_60 = DhwDaytimeArbiter.calculate_required_slots(
        current_temp=50.1,
        target_temp=60.0,
        spec=spec,
        step_hours=0.25,
        min_slots=3,
        max_slots=8,
        min_delta_c=2.0
    )
    assert n_slots_60 == 4, f"Expected 4 slots for 50.1°C -> 60.0°C boost, got {n_slots_60}"

    # Run simulation with the 4 planned slots [0, 1, 2, 3]
    now_dt = datetime(2026, 9, 18, 12, 0, tzinfo=AMS_TZ)
    sim = dhw_model.simulate_trajectory(
        t_start_c=50.1,
        start_dt=now_dt,
        hours_ahead=4,
        heat_pump_schedule_slots=list(range(n_slots_60)),
        target_temp_c=60.0,
        heat_pump_power_kw=spec.solar_boost_electric_kw
    )

    end_temp = sim["temperatures_c"][n_slots_60]
    # Verify tank reaches within 0.3°C of 60.0°C
    assert end_temp >= 59.7, f"Tank ended at {end_temp}°C, failing to reach 60.0°C target within 0.3°C tolerance"
    assert end_temp <= 60.0, f"Tank overheated beyond target: {end_temp}°C"


def test_dhw_horizon_adaptive_safety_margin_scaling():
    """
    Verifies that the DHW safety margin scales dynamically with hours_to_anchor:
    - Korte horizon (<= 4h): min_margin_c (0.5°C)
    - Middellange horizon (12h): 1.25°C
    - Lange horizon (>= 20h): max_margin_c (2.0°C)
    - Verifies compute_optimal_horizon_target_temp behavior for short vs long anchor.
    """
    # 1. Direct helper checks
    assert DhwDaytimeArbiter.calculate_horizon_safety_margin_c(2.0) == 0.5
    assert DhwDaytimeArbiter.calculate_horizon_safety_margin_c(4.0) == 0.5
    assert DhwDaytimeArbiter.calculate_horizon_safety_margin_c(12.0) == 1.25
    assert DhwDaytimeArbiter.calculate_horizon_safety_margin_c(20.0) == 2.0
    assert DhwDaytimeArbiter.calculate_horizon_safety_margin_c(28.0) == 2.0

    # 2. Backwards horizon solver comparison: short horizon vs long horizon
    spec = DhwTankSpec()
    dhw_model = DhwThermalModel()
    start_dt = datetime(2026, 9, 18, 12, 0, tzinfo=AMS_TZ)

    # Construct slots where anchor is forced at slot 16 (4 hours ahead)
    slots_short = make_test_slots(start_dt, hours=24, solar_peak=0.0)
    for i in range(16, 20):
        slots_short[i].solar_kw = 2.5
        slots_short[i].dt = slots_short[i].dt + timedelta(days=1)  # tomorrow

    # Construct slots where anchor is forced at slot 80 (20 hours ahead)
    slots_long = make_test_slots(start_dt, hours=24, solar_peak=0.0)
    for i in range(80, 84):
        slots_long[i].solar_kw = 2.5
        slots_long[i].dt = slots_long[i].dt + timedelta(days=1)  # tomorrow

    t_opt_short, anchor_short, h_short, q_need_s, _, _ = DhwDaytimeArbiter.compute_optimal_horizon_target_temp(
        slots_short, current_dhw_temp=48.0, dhw_model=dhw_model, now_dt=start_dt, tank_spec=spec
    )
    t_opt_long, anchor_long, h_long, q_need_l, _, _ = DhwDaytimeArbiter.compute_optimal_horizon_target_temp(
        slots_long, current_dhw_temp=48.0, dhw_model=dhw_model, now_dt=start_dt, tank_spec=spec
    )

    # Short horizon (4h) has 0.5°C margin, long horizon (20h) has 2.0°C margin
    assert h_short == 4.0
    assert h_long == 20.0
    assert t_opt_long >= t_opt_short
    assert 50.0 <= t_opt_short <= 60.0
    assert 50.0 <= t_opt_long <= 60.0


def test_cop_consistency_between_evaluate_window_cost_and_required_slots():
    """
    Verifies that evaluate_window_cost() and calculate_required_slots() derive
    from the single source of truth in models/physics.py::calculate_dhw_cop.
    """
    from models.physics import calculate_dhw_cop
    spec = DhwTankSpec()

    temps = [-5.0, 0.0, 4.0, 10.0, 15.0, 22.0]
    for t_out in temps:
        # Check 50°C setpoint
        cop_canon_50 = calculate_dhw_cop(target_temp_c=50.0, outdoor_temp_c=t_out)
        assert spec.get_cop(50.0, outdoor_temp_c=t_out) == cop_canon_50

        # Check 60°C setpoint
        cop_canon_60 = calculate_dhw_cop(target_temp_c=60.0, outdoor_temp_c=t_out)
        assert spec.get_cop(60.0, outdoor_temp_c=t_out) == cop_canon_60

        # Check evaluate_window_cost COP return value
        dummy_slot = MockSlot(
            slot_idx=0,
            dt=datetime(2026, 9, 18, 12, 0, tzinfo=AMS_TZ),
            label="12:00",
            price_all_in=0.20,
            solar_kw=0.0,
            unallocated_kw=0.35,
            outdoor_temp_c=t_out
        )
        _, _, cop_window_50 = DhwDaytimeArbiter.evaluate_window_cost(
            slots=[dummy_slot],
            start_idx=0,
            n_req_slots=1,
            th_need_kwh=2.0,
            is_boost_60=False,
            hours_until_target=0.0,
            step_hours=0.25,
            tank_spec=spec
        )
        assert round(cop_window_50, 2) == cop_canon_50

        _, _, cop_window_60 = DhwDaytimeArbiter.evaluate_window_cost(
            slots=[dummy_slot],
            start_idx=0,
            n_req_slots=1,
            th_need_kwh=2.0,
            is_boost_60=True,
            hours_until_target=0.0,
            step_hours=0.25,
            tank_spec=spec
        )
        assert round(cop_window_60, 2) == cop_canon_60


def test_p95_stochastic_comfort_risk_trigger_and_cost_invariance():
    """
    Verifies that:
    1. A scenario with heavy/above-average tap demand (P95) triggers comfort risk
       and daytime heating when a tank at 50°C would dip to 37.4°C in the evening,
       even though P50 (41.2°C) would have falsely considered it safe.
    2. A scenario with sufficient buffer (55°C) is recognized as safe under both P50 and P95,
       remaining on Standby (Pad B1) with total_24h_cost_eur = €0.00 (not unnecessarily expensive).
    """
    now_dt = datetime(2026, 9, 15, 11, 0, tzinfo=AMS_TZ)
    model = DhwThermalModel()
    spec = DhwTankSpec()

    # Create 24h slots with cheap night and normal day
    slots = []
    for i in range(96):
        s_dt = now_dt + timedelta(minutes=15 * i)
        p = 0.22 if (s_dt.hour < 6 or s_dt.hour >= 23) else 0.28
        slots.append(
            MockSlot(
                slot_idx=i,
                dt=s_dt,
                label=s_dt.strftime("%H:%M"),
                price_all_in=p,
                solar_kw=0.0,
                unallocated_kw=0.45,
                outdoor_temp_c=12.0
            )
        )

    # 1. Tank at 50°C: P50 dip is 41.2°C (safe), but P95 dip is 37.4°C (risk!)
    arb_res_50 = DhwDaytimeArbiter.evaluate_daytime_arbitrage(
        slots=slots,
        current_dhw_temp=50.0,
        dynamic_peaks=[],
        dhw_model=model,
        now_dt=now_dt,
        tank_spec=spec
    )
    assert arb_res_50.unheated_evening_dip_c >= 40.0, "P50 was expected >= 40°C"
    assert arb_res_50.unheated_evening_dip_p95_c < 40.0, "P95 was expected < 40°C"
    # Must trigger situation 1 (risk) due to P95 safety check
    assert arb_res_50.situation == "SITUATION_1_EVENING_COMFORT_RISK"
    assert arb_res_50.planned_mode in ["forced_on", "forced_solar_boost_60"]

    # 2. Tank at 55°C: Safe under both P50 and P95
    arb_res_55 = DhwDaytimeArbiter.evaluate_daytime_arbitrage(
        slots=slots,
        current_dhw_temp=55.0,
        dynamic_peaks=[],
        dhw_model=model,
        now_dt=now_dt,
        tank_spec=spec
    )
    assert arb_res_55.unheated_evening_dip_c >= 40.0
    assert arb_res_55.unheated_evening_dip_p95_c >= 40.0
    assert arb_res_55.situation == "SITUATION_2_COMFORT_SAFE"
    assert arb_res_55.selected_path.path_id == "PAD_B1_STANDBY"
    assert arb_res_55.selected_path.day_cost_eur == 0.0
    assert arb_res_55.planned_mode == "normal"



