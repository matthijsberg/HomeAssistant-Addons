import pytest
from datetime import datetime, timezone
from models.canonical import CanonicalDispatchPlan, DispatchPlanSlot, DHWPlanSummary
from layer3_scheduling.opportunistic_merger import OpportunisticDHWMerger, OpportunisticMergeResult


def _make_dummy_plan(boost_slot_idx: int = 4, boost_price: float = 0.20) -> CanonicalDispatchPlan:
    slots = []
    for i in range(96):
        is_boost = (i == boost_slot_idx)
        slots.append(
            DispatchPlanSlot(
                slot_idx=i,
                time_label=f"{i//4:02d}:{(i%4)*15:02d}",
                dt_iso=f"2026-09-12T{i//4:02d}:{(i%4)*15:02d}:00+02:00",
                price_eur=boost_price if is_boost else 0.22,
                solar_kw=2.0 if is_boost else 0.5,
                unallocated_kw=0.4,
                heating_kw=0.0,
                dhw_kw=2.4 if is_boost else 0.0,
                net_import_kw=-1.0 if is_boost else 0.1,
                mode_code="max_on" if is_boost else "normal",
                mode_label="Maximaal aan" if is_boost else "Normaal",
                color_hex="#A855F7" if is_boost else "#1E293B",
                tailwind_class="bg-purple-900" if is_boost else "bg-slate-800",
                description="Test slot"
            )
        )
    summary = DHWPlanSummary(
        planned_mode="max_on",
        planned_mode_label="Maximaal aan",
        color_hex="#A855F7",
        tailwind_class="bg-purple-900",
        target_temp_c=60.0,
        run_start="16:00",
        run_end="17:30",
        run_duration_min=90,
        power_kw=2.4,
        total_stroom_kwh=3.6,
        arbitrage_saving_eur=0.35,
        spits_lockout_hours=0.0,
        dynamic_peaks=[],
        unheated_trajectory=[],
        counterfactual_reason="Test"
    )
    return CanonicalDispatchPlan(
        generated_at="2026-09-12T15:00:00+02:00",
        horizon_hours=24.0,
        resolution_mins=15,
        is_fresh=True,
        freshness_age_seconds=0.0,
        slots=slots,
        dhw_summary=summary,
        dynamic_peaks=[],
        validation_issues=[]
    )


def test_no_merge_when_dhw_inactive():
    plan = _make_dummy_plan(boost_slot_idx=4)
    res = OpportunisticDHWMerger.evaluate_merge(
        plan=plan,
        is_dhw_actively_heating=False,
        current_tank_temp_c=42.0,
        current_solar_kw=2.0,
        current_price_eur=0.20
    )
    assert not res.should_merge
    assert "Geen actieve" in res.reason


def test_no_merge_during_hard_lockout():
    plan = _make_dummy_plan(boost_slot_idx=4)
    res = OpportunisticDHWMerger.evaluate_merge(
        plan=plan,
        is_dhw_actively_heating=True,
        current_tank_temp_c=39.0,
        current_solar_kw=2.0,
        current_price_eur=0.20,
        is_hard_lockout_now=True
    )
    assert not res.should_merge
    assert "spitsblokkade" in res.reason


def test_no_merge_when_no_boost_in_lookahead():
    # Boost is planned at slot 15 (> 2 hours away)
    plan = _make_dummy_plan(boost_slot_idx=15)
    res = OpportunisticDHWMerger.evaluate_merge(
        plan=plan,
        is_dhw_actively_heating=True,
        current_tank_temp_c=39.0,
        current_solar_kw=1.5,
        current_price_eur=0.20
    )
    assert not res.should_merge
    assert "Geen 60°C zonnebuffer gepland" in res.reason


def test_successful_merge_with_solar_surplus():
    # Boost planned at slot 4 (1 hour away)
    plan = _make_dummy_plan(boost_slot_idx=4, boost_price=0.18)
    res = OpportunisticDHWMerger.evaluate_merge(
        plan=plan,
        is_dhw_actively_heating=True,
        current_tank_temp_c=38.5,
        current_solar_kw=2.5,
        current_price_eur=0.22
    )
    assert res.should_merge
    assert res.promoted_mode == "max_on"
    assert res.target_temp_c == 60.0
    assert res.original_slot_idx == 4
    assert 4 in res.cancelled_slots
    assert "Opportunistische Run-Fusie" in res.reason
    assert res.savings_estimate_eur > 0.0


def test_successful_merge_with_acceptable_price_difference():
    # Boost planned at slot 3 with price 0.20, current is 0.23 (diff = 0.03 <= tolerance 0.05)
    plan = _make_dummy_plan(boost_slot_idx=3, boost_price=0.20)
    res = OpportunisticDHWMerger.evaluate_merge(
        plan=plan,
        is_dhw_actively_heating=True,
        current_tank_temp_c=39.2,
        current_solar_kw=0.2,
        current_price_eur=0.23
    )
    assert res.should_merge
    assert res.promoted_mode == "max_on"
    assert res.original_slot_idx == 3


def test_reject_merge_when_current_price_excessive():
    # Boost planned at slot 3 with price 0.15, current price is expensive peak 0.35 (> tolerance) and no solar
    plan = _make_dummy_plan(boost_slot_idx=3, boost_price=0.15)
    res = OpportunisticDHWMerger.evaluate_merge(
        plan=plan,
        is_dhw_actively_heating=True,
        current_tank_temp_c=39.2,
        current_solar_kw=0.0,
        current_price_eur=0.35
    )
    assert not res.should_merge
    assert "te hoog" in res.reason
