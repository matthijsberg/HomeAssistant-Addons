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
    # Warm tank at 50°C -> evening dip >= 40°C -> Situation 2
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
        current_dhw_temp=50.0,
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
