"""
Unit Tests for DHW Exact Dynamic Programming Optimizer
======================================================
Verifies layer3_scheduling/dhw_optimizer.py:
1. Single cheap quarter: heating concentrates there.
2. Comfort constraint: tank never drops below comfort minimum.
3. Start cost penalty: higher c_start consolidates fragmented runs.
4. Real-world 50.1°C solar bridging scenario: one run heats above 52°C.
5. Salvage value: terminal heat is properly credited, no wasteful late burns.
6. Receding horizon: active runs are never aborted before L_min.
7. Hard lockouts: strictly enforced (u = 0).
8. Performance: 192 slots (48h) solved well under 1 second.
"""

import math
import pytest
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from layer3_scheduling.dhw_optimizer import (
    solve,
    DhwOptimizerParams,
    DhwRun,
    DhwOptimizerResult,
)
from layer3_scheduling.dhw_specs import DhwTankSpec


@dataclass
class OptimizerMockSlot:
    slot_idx: int
    dt: datetime
    label: str
    price_all_in: float
    solar_kw: float
    unallocated_kw: float
    outdoor_temp_c: float = 12.0
    is_hard_lockout: bool = False


def make_test_slots(start_dt: datetime, hours: int = 24, base_price: float = 0.28, solar_peak: float = 2.5) -> list:
    slots = []
    total_quarters = hours * 4
    for i in range(total_quarters):
        s_dt = start_dt + timedelta(minutes=15 * i)
        lbl = s_dt.strftime("%H:%M")
        solar = 0.0
        if 11 <= s_dt.hour <= 15:
            solar = solar_peak * (1.0 - abs(s_dt.hour + s_dt.minute/60.0 - 13.0) / 3.0)
        p = base_price * 0.7 if 12 <= s_dt.hour <= 15 else (base_price * 1.3 if 17 <= s_dt.hour <= 20 else base_price)
        t_out = 15.0 + 5.0 * math.sin((s_dt.hour - 8.0) * math.pi / 12.0)
        slots.append(OptimizerMockSlot(
            slot_idx=i,
            dt=s_dt,
            label=lbl,
            price_all_in=p,
            solar_kw=max(0.0, solar),
            unallocated_kw=0.30,
            outdoor_temp_c=round(t_out, 1),
            is_hard_lockout=False
        ))
    return slots


def test_dhw_optimizer_single_cheap_quarter():
    """
    4a: Eén goedkoop kwartier in een verder dure horizon:
    Alle stookenergie landt daar (of in de aansluitende L_min slots).
    """
    now_dt = datetime(2026, 9, 18, 12, 0, tzinfo=timezone.utc)
    slots = []
    for i in range(24):
        dt_i = now_dt + timedelta(minutes=15 * i)
        # Slot 10 is very cheap (€0.02), rest is expensive (€0.40)
        p = 0.02 if i == 10 else 0.40
        slots.append(OptimizerMockSlot(
            slot_idx=i,
            dt=dt_i,
            label=dt_i.strftime("%H:%M"),
            price_all_in=p,
            solar_kw=0.0,
            unallocated_kw=0.30,
            outdoor_temp_c=10.0
        ))

    params = DhwOptimizerParams(min_run_slots=3, min_dwell_slots=4)
    res = solve(slots, t0_c=45.0, params=params)

    assert len(res.runs) == 1
    run = res.runs[0]
    # Run must cover slot 10 and respect min_run_slots (3 slots)
    assert run.start_idx <= 10 <= run.end_idx
    assert (run.end_idx - run.start_idx) >= 3
    assert 10 in res.planned_slots


def test_dhw_optimizer_comfort_guarantee():
    """
    4b: Comfort: met een zwaar P95-aftapprofiel komt T*_k nooit onder T_comf.
    """
    now_dt = datetime(2026, 9, 18, 12, 0, tzinfo=timezone.utc)
    slots = make_test_slots(now_dt, hours=48)
    spec = DhwTankSpec()

    res = solve(slots, t0_c=46.0, spec=spec)
    t_star = res.trajectory["temperatures_c"]

    # Tank must stay above comfort minimum (40.0°C) across all 192 slots
    for k, t_val in enumerate(t_star):
        assert t_val >= spec.comfort_min_temp_c, (
            f"Slot {k} dipped below comfort threshold {spec.comfort_min_temp_c}°C: got {t_val}°C"
        )


def test_dhw_optimizer_start_cost_consolidation():
    """
    4c: Startkosten: met c_start = 0 mogen meerdere runs ontstaan;
    met hogere c_start wordt hetzelfde scenario in minder runs opgelost.
    """
    now_dt = datetime(2026, 9, 18, 12, 0, tzinfo=timezone.utc)
    slots = []
    # Two separate cheap dips at slots 2..4 and slots 12..14
    for i in range(40):
        dt_i = now_dt + timedelta(minutes=15 * i)
        if i in (2, 3, 4):
            p = 0.08
        elif i in (12, 13, 14):
            p = 0.085
        else:
            p = 0.40
        slots.append(OptimizerMockSlot(
            slot_idx=i,
            dt=dt_i,
            label=dt_i.strftime("%H:%M"),
            price_all_in=p,
            solar_kw=0.0,
            unallocated_kw=0.30,
            outdoor_temp_c=10.0
        ))

    # With zero ignition cost, multiple distinct runs are optimal
    params_zero = DhwOptimizerParams(c_start=0.0, min_run_slots=3, min_dwell_slots=4)
    res_zero = solve(slots, t0_c=43.0, params=params_zero)

    # With high ignition cost (€0.35 per start), runs consolidate
    params_high = DhwOptimizerParams(c_start=0.35, min_run_slots=3, min_dwell_slots=4)
    res_high = solve(slots, t0_c=43.0, params=params_high)

    assert len(res_zero.runs) >= 2, f"Expected >= 2 runs with c_start=0, got {len(res_zero.runs)}"
    assert len(res_high.runs) < len(res_zero.runs), (
        f"High c_start should have fewer runs: {len(res_high.runs)} vs {len(res_zero.runs)}"
    )


def test_dhw_optimizer_solar_window_bridging_above_52c():
    """
    4d: Het scenario uit de praktijk: tank 50,1°C, één goedkoop zonvenster nu,
    volgend goedkoop venster pas 20 uur later:
    De solver kiest één run tot boven 52°C nu in plaats van 'net genoeg' plus een tweede run later.
    Assert het aantal runs en dat T_end van de eerste run boven 52°C ligt.
    """
    now_dt = datetime(2026, 9, 18, 12, 0, tzinfo=timezone.utc)
    slots = []
    for i in range(96):  # 24 hours
        dt_i = now_dt + timedelta(minutes=15 * i)
        # Cheap solar surplus now (slots 0..5)
        if i < 6:
            solar = 3.2
            p = 0.22
        elif i >= 80:  # Next cheap window 20 hours later
            solar = 3.0
            p = 0.22
        else:
            solar = 0.0
            p = 0.38
        slots.append(OptimizerMockSlot(
            slot_idx=i,
            dt=dt_i,
            label=dt_i.strftime("%H:%M"),
            price_all_in=p,
            solar_kw=solar,
            unallocated_kw=0.30,
            outdoor_temp_c=12.0
        ))

    res = solve(slots, t0_c=50.1)

    # Must choose exactly 1 daytime run
    assert len(res.runs) == 1, f"Expected exactly 1 run bridging the horizon, got {len(res.runs)}"
    first_run = res.runs[0]
    # End temperature must exceed 52.0°C (thermal buffering)
    assert first_run.t_end_c > 52.0, f"Expected T_end > 52.0°C, got {first_run.t_end_c}°C"


def test_dhw_optimizer_terminal_salvage_value():
    """
    4e: Restwaarde: aan het einde van de horizon wordt de tank niet leeggelaten
    en ook niet zinloos volgestookt in het laatste dure kwartier.
    """
    now_dt = datetime(2026, 9, 18, 12, 0, tzinfo=timezone.utc)
    slots = []
    for i in range(32):
        dt_i = now_dt + timedelta(minutes=15 * i)
        # Very expensive electricity at horizon end (slots 30, 31 at €0.65/kWh)
        p = 0.65 if i >= 30 else 0.20
        slots.append(OptimizerMockSlot(
            slot_idx=i,
            dt=dt_i,
            label=dt_i.strftime("%H:%M"),
            price_all_in=p,
            solar_kw=0.0,
            unallocated_kw=0.30,
            outdoor_temp_c=12.0
        ))

    res = solve(slots, t0_c=48.0)

    # Assert that the last expensive slots are NEVER heated
    assert 30 not in res.planned_slots
    assert 31 not in res.planned_slots


def test_dhw_optimizer_receding_horizon_commitment():
    """
    4f: Receding horizon: los op, verschuif één slot, los opnieuw op met T_0 = T*_1
    en de bijbehorende runtoestand: het nieuwe plan mag een lopende run niet afbreken vóór L_min.
    """
    now_dt = datetime(2026, 9, 18, 12, 0, tzinfo=timezone.utc)
    slots = make_test_slots(now_dt, hours=24)
    # Make beginning cheap so a run initiates at slot 0
    slots[0].price_all_in = -0.05
    slots[1].price_all_in = -0.05
    slots[2].price_all_in = -0.05

    params = DhwOptimizerParams(min_run_slots=3, min_dwell_slots=4)

    # Initial solution from cold start
    res1 = solve(slots, t0_c=45.0, run_state0=("OFF_FREE", 0), params=params)
    assert 0 in res1.planned_slots, "Run should start at slot 0"

    # Step forward 1 slot into the future:
    t0_shifted = res1.trajectory["temperatures_c"][1]
    slots_shifted = slots[1:]
    # Compressor has now been running for 1 slot
    run_state_shifted = ("ON_MANDATORY", 1)

    res2 = solve(slots_shifted, t0_c=t0_shifted, run_state0=run_state_shifted, params=params)

    # In shifted coordinates, slot 0 (old slot 1) must continue running
    assert 0 in res2.planned_slots, "Active run was aborted prematurely!"
    # Must complete the remaining 2 mandatory slots of L_min
    assert res2.planned_slots[:2] == [0, 1]


def test_dhw_optimizer_hard_lockouts_strictly_enforced():
    """
    4g: Harde vergrendelingen (dynamic_peaks met is_hard_lockout): u_k = 0 in die slots.
    """
    now_dt = datetime(2026, 9, 18, 12, 0, tzinfo=timezone.utc)
    slots = make_test_slots(now_dt, hours=24)

    # Lock slots 2 and 3
    slots[2].is_hard_lockout = True
    slots[3].is_hard_lockout = True

    res = solve(slots, t0_c=45.0)

    # Locked slots must NEVER be planned
    assert 2 not in res.planned_slots
    assert 3 not in res.planned_slots
    assert res.slot_modes[2] == "forced_off"
    assert res.slot_modes[3] == "forced_off"


def test_dhw_optimizer_graceful_degradation_below_comfort():
    """
    Degradatie: als tank al onder comfortbuffer start, degradeer netjes naar
    noodopwarming buiten harde lockouts zonder exceptions te gooien.
    """
    now_dt = datetime(2026, 9, 18, 12, 0, tzinfo=timezone.utc)
    slots = make_test_slots(now_dt, hours=24)
    # Slot 0 is hard lockout, slot 1 is available
    slots[0].is_hard_lockout = True

    res = solve(slots, t0_c=36.0)

    assert res.validation_issue is not None
    assert "below comfort boundary" in res.validation_issue.lower()
    # Respects hard lockout even during emergency recovery
    assert 0 not in res.planned_slots
    # Activates emergency heating as soon as lockout lifts
    assert 1 in res.planned_slots
    assert res.slot_modes[0] == "forced_off"
    assert res.slot_modes[1] in ("forced_on", "max_on")


def test_dhw_optimizer_performance_48h_under_1_second():
    """
    2. Zuiver Python; ruim onder 1s voor 192 slots op de host.
    """
    now_dt = datetime(2026, 9, 18, 12, 0, tzinfo=timezone.utc)
    slots = make_test_slots(now_dt, hours=48)  # Exactly 192 quarter-hour slots

    res = solve(slots, t0_c=48.0)

    assert res.solve_duration_ms < 1000.0, f"Solver took too long: {res.solve_duration_ms:.2f} ms"
    print(f"\n[BENCHMARK] 192-slot DP solve completed in {res.solve_duration_ms:.2f} ms")
