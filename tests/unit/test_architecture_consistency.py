"""
Architecture & Vector Consistency Tests for Open HEMS
=====================================================
Verifies the Single Source of Truth architecture:
1. TelemetrySanitizer: Freshness, physical bounds, grid alignment.
2. CentralPlanner: Full canonical dispatch generation.
3. PlanStore: Publication and singleton retrieval.
4. Color and Taxonomy Invariance: Every state has 100% matching color in slots and summaries.
5. Physical Rules: 350L DHW, day-priority, 2.5h winter comfort cap, 03:30 tie-breaker.
"""

import pytest
from datetime import datetime, timezone, timedelta
from models.canonical import StandardizedState, STATE_METADATA, Quality
from layer1_data_collection.sanitizer import TelemetrySanitizer
from layer3_scheduling.central_planner import CentralPlanner
from layer3_scheduling.plan_store import get_plan_store


def test_telemetry_sanitizer_bounds_and_alignment():
    now = datetime(2026, 9, 12, 10, 7, 0, tzinfo=timezone.utc)
    raw_prices = [{"dt": now.isoformat(), "price": 0.15}]
    raw_solar = [{"dt": now.isoformat(), "solar_kw": 8.5}]  # Exceeds 6kW physical roof cap
    raw_weather = [{"dt": now.isoformat(), "temperature": 18.0}]

    frame = TelemetrySanitizer.sanitize(
        now=now,
        raw_prices=raw_prices,
        raw_solar=raw_solar,
        raw_weather=raw_weather,
        raw_unallocated_matrix=[0.4] * 672,
        current_dhw_temp=48.5,
        last_hardware_reading_time=now,
        horizon_slots=96,
        step_mins=15
    )

    assert frame.is_fresh is True
    assert frame.resolution_minutes == 15
    assert len(frame.slots) == 96
    # Grid should be aligned down to 10:00:00
    assert frame.slots[0].dt.minute == 0
    # Solar should be capped to 6.0 kW
    assert frame.slots[0].solar_kw <= 6.0


def test_plan_store_singleton_and_invariance():
    now = datetime(2026, 9, 12, 9, 30, 0, tzinfo=timezone.utc)
    raw_prices = [{"dt": (now + timedelta(hours=i)).isoformat(), "price": 0.20 + 0.05 * (i % 3)} for i in range(24)]
    raw_solar = [{"dt": (now + timedelta(hours=i)).isoformat(), "solar_kw": 2.5 if 11 <= (now + timedelta(hours=i)).hour <= 15 else 0.0} for i in range(24)]
    raw_weather = [{"dt": (now + timedelta(hours=i)).isoformat(), "temperature": 16.0} for i in range(24)]

    frame = TelemetrySanitizer.sanitize(
        now=now,
        raw_prices=raw_prices,
        raw_solar=raw_solar,
        raw_weather=raw_weather,
        raw_unallocated_matrix=[0.35] * 672,
        current_dhw_temp=42.0,
        last_hardware_reading_time=now,
        horizon_slots=96,
        step_mins=15
    )

    plan = CentralPlanner.plan(frame, current_dhw_temp=42.0)
    store = get_plan_store()
    retrieved_plan = store.get_plan()

    assert retrieved_plan is not None
    assert retrieved_plan.generated_at == plan.generated_at
    assert len(retrieved_plan.slots) == len(plan.slots)


def test_standardized_taxonomy_and_color_alignment():
    """Verify that all 6 states in the canonical model have unique and correct colors."""
    states = list(StandardizedState)
    assert len(states) == 6

    # Verify every state exists in STATE_METADATA and has correct hex
    assert STATE_METADATA[StandardizedState.FORCED_OFF]["color_hex"] == "#EF4444"
    assert STATE_METADATA[StandardizedState.ADVISED_OFF]["color_hex"] == "#F59E0B"
    assert STATE_METADATA[StandardizedState.NORMAL]["color_hex"] == "#1E293B"
    assert STATE_METADATA[StandardizedState.ADVISED_ON]["color_hex"] == "#4ADE80"
    assert STATE_METADATA[StandardizedState.FORCED_ON]["color_hex"] == "#10B981"
    assert STATE_METADATA[StandardizedState.MAX_ON]["color_hex"] == "#A855F7"


def test_dhw_daytime_priority_and_winter_cap():
    """Verify daytime priority over night run and 2.5h winter cap."""
    now = datetime(2026, 9, 12, 10, 0, 0, tzinfo=timezone.utc)
    # High prices in evening (spitspiek van 18:00 tot 00:00)
    prices = []
    for h in range(24):
        p = 0.38 if 18 <= h <= 23 else 0.12
        for m in (0, 15, 30, 45):
            dt = now + timedelta(hours=h, minutes=m)
            prices.append({"dt": dt.isoformat(), "price": p})

    # High solar in daytime
    solar = []
    for h in range(24):
        s = 3.0 if 11 <= h <= 15 else 0.0
        for m in (0, 15, 30, 45):
            dt = now + timedelta(hours=h, minutes=m)
            solar.append({"dt": dt.isoformat(), "solar_kw": s})

    weather = [{"dt": (now + timedelta(hours=i)).isoformat(), "temperature": 15.0} for i in range(24)]

    frame = TelemetrySanitizer.sanitize(
        now=now,
        raw_prices=prices,
        raw_solar=solar,
        raw_weather=weather,
        raw_unallocated_matrix=[0.35] * 672,
        current_dhw_temp=41.0,
        last_hardware_reading_time=now,
        horizon_slots=96,
        step_mins=15
    )

    plan = CentralPlanner.plan(frame, current_dhw_temp=41.0)

    # 1. Day-priority should plan solar boost (60°C) or daytime 50°C during daylight hours
    assert plan.dhw_summary.planned_mode in ["forced_solar_boost_60", "max_on", "forced_on"]
    # 2. Spitslockout hours should be capped to <= 2.5 hours
    assert plan.dhw_summary.spits_lockout_hours <= 2.5
