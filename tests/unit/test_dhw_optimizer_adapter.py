"""
Unit Tests for DHW Optimizer Explanation & Plan Adapter
========================================================
Verifies layer3_scheduling/dhw_optimizer_explain.py and layer3_scheduling/dhw_plan_adapter.py:
1. Counterfactual consistency: J_cap50 >= J_optimaal and J_delay >= J_optimaal.
2. Adapter mapping to canonical DHWPlanSummary: target_temp_c, power_kw, planned_mode.
3. Sentence count invariant: len(run_explanations) == len(runs).
4. Unheated trajectory consistency via canonical dhw_step.
5. Per-slot dispatch slot overlay with StandardizedState enum.
"""

import pytest
from datetime import datetime, timezone, timedelta
from layer3_scheduling.dhw_optimizer import solve, DhwOptimizerParams
from layer3_scheduling.dhw_optimizer_explain import (
    compute_counterfactual_none,
    compute_counterfactual_cap50,
    compute_counterfactual_delay,
    explain_dhw_optimization,
)
from layer3_scheduling.dhw_plan_adapter import (
    adapt_optimizer_to_dhw_summary,
    apply_optimizer_to_dispatch_slots,
)
from layer3_scheduling.dhw_specs import DhwTankSpec
from models.canonical import (
    DHWPlanSummary,
    DispatchPlanSlot,
    StandardizedState,
    get_state_metadata,
)
from tests.unit.test_dhw_optimizer import OptimizerMockSlot, make_test_slots


def test_counterfactual_cost_consistency():
    """
    4a: Counterfactuals must be mathematically consistent with the optimal DP solution:
    J_cap50 >= J_optimaal and J_delay >= J_optimaal (constraining actions can only increase cost).
    """
    now_dt = datetime(2026, 9, 18, 12, 0, tzinfo=timezone.utc)
    slots = make_test_slots(now_dt, hours=48)
    spec = DhwTankSpec()
    params = DhwOptimizerParams()

    res_opt = solve(slots, t0_c=48.0, spec=spec, params=params)

    # 1. Cap50 counterfactual
    cf_cap50 = compute_counterfactual_cap50(
        slots=slots,
        t0_c=48.0,
        run_state0=None,
        opt_result=res_opt,
        dhw_model=None,
        tariff_provider=None,
        spec=spec,
        params=params
    )
    if cf_cap50["applies"]:
        assert cf_cap50["cost_cap50_eur"] >= res_opt.total_cost_eur - 1e-4, (
            f"Cap50 cost ({cf_cap50['cost_cap50_eur']}) must be >= optimal ({res_opt.total_cost_eur})"
        )
        assert cf_cap50["savings_eur"] >= 0.0

    # 2. Delay counterfactual
    cf_delay = compute_counterfactual_delay(
        slots=slots,
        t0_c=48.0,
        opt_result=res_opt,
        dhw_model=None,
        tariff_provider=None,
        spec=spec,
        params=params
    )
    if cf_delay["applies"]:
        assert cf_delay["cost_delay_eur"] >= res_opt.total_cost_eur - 1e-4, (
            f"Delay cost ({cf_delay['cost_delay_eur']}) must be >= optimal ({res_opt.total_cost_eur})"
        )
        assert cf_delay["savings_eur"] >= 0.0


def test_adapter_maps_to_canonical_dhw_summary():
    """
    4b: Adapter converts DhwOptimizerResult into valid DHWPlanSummary:
    - target_temp_c = T_end of first run
    - power_kw = P_el of that run
    - planned_mode = max_on if T_end >= 52°C, forced_on otherwise
    - color_hex and tailwind_class strictly consistent
    """
    now_dt = datetime(2026, 9, 18, 12, 0, tzinfo=timezone.utc)
    slots = make_test_slots(now_dt, hours=48)
    spec = DhwTankSpec()
    params = DhwOptimizerParams()

    res_opt = solve(slots, t0_c=48.0, spec=spec, params=params)
    assert len(res_opt.runs) >= 1
    first_run = res_opt.runs[0]

    summary = adapt_optimizer_to_dhw_summary(
        opt_result=res_opt,
        slots=slots,
        t0_c=48.0,
        spec=spec,
        params=params
    )

    assert isinstance(summary, DHWPlanSummary)
    assert summary.target_temp_c == round(first_run.t_end_c, 1)
    assert summary.power_kw == spec.get_electric_power_kw(first_run.t_end_c)

    expected_mode = "max_on" if first_run.t_end_c >= 52.0 else "forced_on"
    assert summary.planned_mode == expected_mode

    meta = get_state_metadata(StandardizedState(expected_mode))
    assert summary.color_hex == meta["color_hex"]
    assert summary.tailwind_class == meta["tailwind_text"]
    assert summary.decision_details is not None
    assert summary.decision_details.get("planner") == "optimizer"


def test_sentence_count_strictly_equals_run_count():
    """
    4c: Het aantal zinnen in de uitleg moet gelijk zijn aan het aantal runs,
    zodat elk 'Verwarmt'-blok in de grafiek exact één zin heeft.
    """
    now_dt = datetime(2026, 9, 18, 12, 0, tzinfo=timezone.utc)
    spec = DhwTankSpec()
    params = DhwOptimizerParams()

    # Scenario with 2 runs
    slots_2runs = make_test_slots(now_dt, hours=48)
    res_2runs = solve(slots_2runs, t0_c=48.0, spec=spec, params=params)
    assert len(res_2runs.runs) == 2

    summary_2runs = adapt_optimizer_to_dhw_summary(
        opt_result=res_2runs,
        slots=slots_2runs,
        t0_c=48.0,
        spec=spec,
        params=params
    )
    assert summary_2runs.decision_details is not None
    run_sentences_2 = summary_2runs.decision_details["run_explanations"]
    assert len(run_sentences_2) == 2, f"Expected 2 sentences for 2 runs, got {len(run_sentences_2)}"
    assert run_sentences_2[0].startswith("Run 1:")
    assert run_sentences_2[1].startswith("Run 2:")

    # Scenario with 1 run
    slots_1run = []
    for i in range(48):
        dt_i = now_dt + timedelta(minutes=15 * i)
        solar = 3.5 if 4 <= i <= 8 else 0.0
        slots_1run.append(OptimizerMockSlot(
            slot_idx=i,
            dt=dt_i,
            label=dt_i.strftime("%H:%M"),
            price_all_in=0.25,
            solar_kw=solar,
            unallocated_kw=0.30,
            outdoor_temp_c=12.0
        ))
    res_1run = solve(slots_1run, t0_c=48.0, spec=spec, params=params)
    assert len(res_1run.runs) == 1

    summary_1run = adapt_optimizer_to_dhw_summary(
        opt_result=res_1run,
        slots=slots_1run,
        t0_c=48.0,
        spec=spec,
        params=params
    )
    assert summary_1run.decision_details is not None
    run_sentences_1 = summary_1run.decision_details["run_explanations"]
    assert len(run_sentences_1) == 1, f"Expected 1 sentence for 1 run, got {len(run_sentences_1)}"
    assert run_sentences_1[0].startswith("Run 1:")


def test_unheated_trajectory_from_canonical_balance():
    """
    4d: unheated_trajectory en trajectory komen uit dezelfde dhw_step als de solver
    (geen aparte simulate_trajectory aanroep meer met afwijkende aannames).
    """
    now_dt = datetime(2026, 9, 18, 12, 0, tzinfo=timezone.utc)
    slots = make_test_slots(now_dt, hours=24)
    spec = DhwTankSpec()
    params = DhwOptimizerParams()

    res = solve(slots, t0_c=50.0, spec=spec, params=params)
    summary = adapt_optimizer_to_dhw_summary(res, slots, 50.0, spec=spec, params=params)

    unh_list = summary.unheated_trajectory
    assert len(unh_list) == len(slots)
    # Slot 0 unheated must match start temp exactly
    assert abs(unh_list[0]["temperature_c"] - 50.0) < 1e-4

    # Trajectory must be monotonically non-increasing (since u == 0)
    for i in range(len(unh_list) - 1):
        assert unh_list[i]["temperature_c"] >= unh_list[i + 1]["temperature_c"], (
            f"Unheated trajectory must cool: {unh_list[i]['temperature_c']} < {unh_list[i+1]['temperature_c']}"
        )


def test_apply_optimizer_to_dispatch_slots():
    """
    4e: apply_optimizer_to_dispatch_slots overlays dhw_kw and mode_code with StandardizedState enum.
    """
    now_dt = datetime(2026, 9, 18, 12, 0, tzinfo=timezone.utc)
    slots = make_test_slots(now_dt, hours=24)
    spec = DhwTankSpec()
    params = DhwOptimizerParams()

    res = solve(slots, t0_c=48.0, spec=spec, params=params)

    # Create dummy canonical dispatch plan slots
    dispatch_slots = []
    for i, s in enumerate(slots):
        dispatch_slots.append(DispatchPlanSlot(
            slot_idx=i,
            time_label=s.label,
            dt_iso=s.dt.isoformat(),
            price_eur=s.price_all_in,
            solar_kw=s.solar_kw,
            unallocated_kw=s.unallocated_kw,
            heating_kw=0.0,
            dhw_kw=0.0,
            net_import_kw=0.0,
            mode_code=StandardizedState.NORMAL,
            mode_label="Normaal",
            color_hex="#94A3B8",
            tailwind_class="text-slate-400",
            description=""
        ))

    updated = apply_optimizer_to_dispatch_slots(dispatch_slots, res, spec=spec)
    assert len(updated) == len(dispatch_slots)

    for k in res.planned_slots:
        assert updated[k].dhw_kw > 0.0
        assert updated[k].mode_code in (StandardizedState.MAX_ON, StandardizedState.FORCED_ON)
        meta = get_state_metadata(updated[k].mode_code)
        assert updated[k].color_hex == meta["color_hex"]
        assert updated[k].tailwind_class == meta["tailwind_text"]
