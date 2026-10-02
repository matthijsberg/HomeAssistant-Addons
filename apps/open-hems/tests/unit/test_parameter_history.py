"""
Unit tests for Parameter Adjustment & Calibration History Tracking.
"""

import pytest
from layer2_calibration.parameter_history import ParameterHistoryManager, PARAMETER_METADATA


def test_parameter_history_defaults():
    history = ParameterHistoryManager.get_parameter_history("building_ua", timeframe="quarter")
    assert history["parameter_id"] == "building_ua"
    assert history["unit"] == "W/K"
    assert history["timeframe"] == "quarter"
    assert history["current_value"] > 0
    assert len(history["timeline"]) > 0
    assert len(history["records"]) > 0


def test_parameter_history_timeframes():
    for tf in ["30d", "quarter", "1y", "all"]:
        history = ParameterHistoryManager.get_parameter_history("c_wind", timeframe=tf)
        assert history["timeframe"] == tf
        assert history["parameter_name"] == PARAMETER_METADATA["c_wind"]["name"]


def test_record_adjustment():
    entry = ParameterHistoryManager.record_adjustment(
        parameter_id="building_ua",
        old_value=300.0,
        new_value=292.6,
        drift_pct=-2.5,
        change_type="test_run",
        evidence="Unit test calibration record"
    )
    assert entry["parameter_id"] == "building_ua"
    assert entry["old_value"] == 300.0
    assert entry["new_value"] == 292.6
    assert entry["drift_pct"] == -2.5

    updated = ParameterHistoryManager.get_parameter_history("building_ua", timeframe="30d")
    assert any(r["change_type"] == "test_run" for r in updated["records"])
