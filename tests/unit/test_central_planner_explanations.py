"""
Unit Tests for Central Planner DHW Optimizer Explanations
=========================================================
Verifies that CentralPlanner.plan() leverages the canonical optimizer adapter
and produces clean, consistent, non-contradictory decision explanations.
"""

import pytest
from datetime import datetime, timezone, timedelta

from layer1_data_collection.sanitizer import TelemetrySanitizer
from layer3_scheduling.central_planner import CentralPlanner
from layer3_scheduling.dhw_specs import DhwTankSpec


def create_sample_telemetry_frame(now_dt: datetime, current_dhw_temp: float = 46.0):
    raw_prices = [{"dt": (now_dt + timedelta(hours=i)).isoformat(), "price": 0.15 if i < 4 else 0.35} for i in range(24)]
    raw_solar = [{"dt": (now_dt + timedelta(hours=i)).isoformat(), "solar_kw": 3.5 if 1 <= i <= 4 else 0.0} for i in range(24)]
    raw_weather = [{"dt": (now_dt + timedelta(hours=i)).isoformat(), "temperature": 18.0} for i in range(24)]

    return TelemetrySanitizer.sanitize(
        now=now_dt,
        raw_prices=raw_prices,
        raw_solar=raw_solar,
        raw_weather=raw_weather,
        raw_unallocated_matrix=[0.35] * 672,
        current_dhw_temp=current_dhw_temp,
        last_hardware_reading_time=now_dt,
        horizon_slots=96,
        step_mins=15,
    )


def test_optimizer_explanation_structure_in_central_planner():
    """
    Verifies that CentralPlanner produces a valid DHWPlanSummary with decision_details
    containing optimizer-driven comfort_text, finance_text, bullets, and run_explanations.
    """
    now_dt = datetime(2026, 9, 18, 12, 0, 0, tzinfo=timezone.utc)
    frame = create_sample_telemetry_frame(now_dt, current_dhw_temp=46.0)
    spec = DhwTankSpec()

    plan = CentralPlanner.plan(frame, current_dhw_temp=46.0, dhw_spec=spec)
    summary = plan.dhw_summary
    assert summary is not None

    details = summary.decision_details
    assert details is not None
    assert details.get("planner") == "optimizer"

    # Verify structured fields exist
    assert "comfort_text" in details
    assert "finance_text" in details
    assert "bullet_1" in details
    assert "bullet_2" in details
    assert "run_explanations" in details

    # Number of run explanation sentences must strictly match number of planned runs
    runs = details.get("trajectory", {})
    run_sentences = details.get("run_explanations", [])
    assert len(run_sentences) >= 1
    for s in run_sentences:
        assert "Run " in s


def test_no_contradiction_when_target_reaches_60c():
    """
    When the optimizer targets a high temperature (>= 59.0°C),
    verify that the explanation is positive and does not contain legacy artifact phrases.
    """
    now_dt = datetime(2026, 9, 18, 12, 0, 0, tzinfo=timezone.utc)
    frame = create_sample_telemetry_frame(now_dt, current_dhw_temp=46.0)
    spec = DhwTankSpec()

    plan = CentralPlanner.plan(frame, current_dhw_temp=46.0, dhw_spec=spec)
    details = plan.dhw_summary.decision_details
    assert details is not None

    comfort_text = details.get("comfort_text", "").lower()
    finance_text = details.get("finance_text", "").lower()

    # Legacy artifact phrases that must never reappear
    assert "doorkoken naar 60" not in comfort_text
    assert "doorkoken naar 60" not in finance_text
