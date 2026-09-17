"""
Unit Tests for LearnedForecaster Model Governance & Calibration Pipeline
========================================================================
Verifies:
1. Invariant #3 compliance: Recommendations must be 100% derived from empirical input samples,
   never from hardcoded synthetic constants. Changing input telemetry directly alters proposed values.
2. Absolute absence of hardcoded mock recommendations ('heating_modulation', 'dhw_standby').
3. Dynamic auto_applied logic based on drift_pct vs auto_accept_threshold (never hardcoded True).
4. Sample size guards (<14 stookdagen suppresses premature building_ua recommendation).
"""

import json
import unittest.mock as mock
import pytest
from layer2_calibration.learned_forecaster import HybridForecastingModel


from models.physics import calculate_carnot_cop


def _make_influx_response(unalloc_val: float, ua_slope_kwh_per_k: float, num_heating_days: int = 20) -> dict:
    """Helper to generate clean InfluxDB series with controlled ground truth parameters."""
    base_ts = 1758000000
    unalloc_values = []
    for q in range(96 * 7):
        ts = base_ts + q * 900
        unalloc_values.append([ts, unalloc_val])

    cv_values = []
    tout_values = []
    for d in range(num_heating_days):
        ts_day = base_ts + d * 86400
        tout = 5.0 + (d % 8) * 1.0  # tout varies 5.0C .. 12.0C -> dt_k varies 14.5 .. 7.5
        dt_k = 19.5 - tout
        cop = calculate_carnot_cop(tout)
        th_kwh = ua_slope_kwh_per_k * dt_k
        el_kwh = th_kwh / cop
        cv_values.append([ts_day, el_kwh])
        tout_values.append([ts_day, tout])

    return {
        "results": [
            {"series": [{"name": "energy_telemetry", "values": unalloc_values}]},
            {"series": [{"name": "energy_telemetry", "values": cv_values}]},
            {"series": [{"name": "energy_telemetry", "values": tout_values}]}
        ]
    }


def test_recommendations_strictly_derived_from_input_samples():
    """Verify that recommendations are purely functional transformations of InfluxDB inputs."""
    forecaster = HybridForecastingModel()
    forecaster.secrets = {"influxdb": {"local_ha_influxdb": "dummy_pw"}}
    forecaster.params = {
        "building": {"ua_base_w_per_k": 300.0},
        "unallocated": {"night_baseload_floor_w": 250.0},
        "auto_accept_max_drift_pct": 5.0,
        "rolling_window_days": 90,
        "learning_rate_ewma": 1.0
    }
    forecaster.profile = {"profile_96_quarters": [[250.0] * 96 for _ in range(7)]}

    # --- Scenario A: UA target ~350 W/K (slope = 350 * 24 / 1000 = 8.4 kWh/K), Baseload = 220W ---
    target_ua_a = 350.0
    slope_a = (target_ua_a * 24.0) / 1000.0
    baseload_a = 220.0
    mock_payload_a = json.dumps(_make_influx_response(baseload_a, slope_a, 20)).encode("utf-8")

    with mock.patch("urllib.request.urlopen") as mock_url, \
         mock.patch("layer2_calibration.learned_forecaster.save_json"):
        mock_resp = mock.MagicMock()
        mock_resp.read.return_value = mock_payload_a
        mock_url.return_value.__enter__.return_value = mock_resp

        res_a = forecaster.retrain_from_openhems()
        assert res_a["status"] == "success"

        recs_list_a = res_a["recommendations"]["recommendations"]
        recs_a = {r["id"]: r for r in recs_list_a}

        # Guaranteed absence of mock items
        assert "heating_modulation" not in recs_a
        assert "dhw_standby" not in recs_a

        # Output derived directly from input A
        assert recs_a["building_ua"]["proposed_value"] == pytest.approx(target_ua_a, abs=5.0)
        assert recs_a["night_baseload"]["proposed_value"] == pytest.approx(baseload_a, abs=1.0)

    # --- Scenario B: Change input telemetry (UA target ~270 W/K, Baseload = 310W) ---
    target_ua_b = 270.0
    slope_b = (target_ua_b * 24.0) / 1000.0
    baseload_b = 310.0
    mock_payload_b = json.dumps(_make_influx_response(baseload_b, slope_b, 20)).encode("utf-8")

    # Reset base params
    forecaster.params["building"]["ua_base_w_per_k"] = 300.0
    forecaster.params["unallocated"]["night_baseload_floor_w"] = 250.0

    with mock.patch("urllib.request.urlopen") as mock_url, \
         mock.patch("layer2_calibration.learned_forecaster.save_json"):
        mock_resp = mock.MagicMock()
        mock_resp.read.return_value = mock_payload_b
        mock_url.return_value.__enter__.return_value = mock_resp

        res_b = forecaster.retrain_from_openhems()
        assert res_b["status"] == "success"

        recs_list_b = res_b["recommendations"]["recommendations"]
        recs_b = {r["id"]: r for r in recs_list_b}

        # Output changes dynamically with inputs
        assert recs_b["building_ua"]["proposed_value"] == pytest.approx(target_ua_b, abs=5.0)
        assert recs_b["night_baseload"]["proposed_value"] == pytest.approx(baseload_b, abs=1.0)
        assert recs_b["building_ua"]["proposed_value"] != recs_a["building_ua"]["proposed_value"]
        assert recs_b["night_baseload"]["proposed_value"] != recs_a["night_baseload"]["proposed_value"]


def test_auto_applied_is_dynamically_evaluated():
    """Verify that auto_applied is never hardcoded and honors auto_accept_threshold."""
    forecaster = HybridForecastingModel()
    forecaster.secrets = {"influxdb": {"local_ha_influxdb": "dummy_pw"}}
    forecaster.params = {
        "building": {"ua_base_w_per_k": 300.0},
        "unallocated": {"night_baseload_floor_w": 250.0},
        "auto_accept_max_drift_pct": 3.0,  # 3% tolerance
        "rolling_window_days": 90
    }
    forecaster.profile = {"profile_96_quarters": [[250.0] * 96 for _ in range(7)]}

    # 1. Large drift (UA 340 vs 300 = +13.3% > 3% threshold) -> auto_applied MUST be False
    mock_payload_large = json.dumps(_make_influx_response(250.0, 340.0 * 24.0 / 1000.0, 20)).encode("utf-8")
    with mock.patch("urllib.request.urlopen") as mock_url, \
         mock.patch("layer2_calibration.learned_forecaster.save_json"):
        mock_resp = mock.MagicMock()
        mock_resp.read.return_value = mock_payload_large
        mock_url.return_value.__enter__.return_value = mock_resp

        res = forecaster.retrain_from_openhems()
        recs = {r["id"]: r for r in res["recommendations"]["recommendations"]}
        assert recs["building_ua"]["auto_applied"] is False
        assert recs["building_ua"]["drift_pct"] > 3.0

    # 2. Small drift (UA 303 vs 300 = +1.0% <= 3% threshold) -> auto_applied MUST be True
    mock_payload_small = json.dumps(_make_influx_response(250.0, 303.0 * 24.0 / 1000.0, 20)).encode("utf-8")
    forecaster.params["building"]["ua_base_w_per_k"] = 300.0
    with mock.patch("urllib.request.urlopen") as mock_url, \
         mock.patch("layer2_calibration.learned_forecaster.save_json"):
        mock_resp = mock.MagicMock()
        mock_resp.read.return_value = mock_payload_small
        mock_url.return_value.__enter__.return_value = mock_resp

        res = forecaster.retrain_from_openhems()
        recs = {r["id"]: r for r in res["recommendations"]["recommendations"]}
        assert recs["building_ua"]["auto_applied"] is True
        assert recs["building_ua"]["drift_pct"] <= 3.0


def test_sample_size_guard_suppresses_premature_ua_recommendation():
    """Verify that insufficient stookdagen (<14) suppresses building_ua recommendation."""
    forecaster = HybridForecastingModel()
    forecaster.secrets = {"influxdb": {"local_ha_influxdb": "dummy_pw"}}
    forecaster.params = {
        "building": {"ua_base_w_per_k": 300.0},
        "unallocated": {"night_baseload_floor_w": 250.0},
        "auto_accept_max_drift_pct": 5.0,
        "rolling_window_days": 90
    }
    forecaster.profile = {"profile_96_quarters": [[250.0] * 96 for _ in range(7)]}

    # Only 5 heating days (insufficient sample size)
    mock_payload_insufficient = json.dumps(_make_influx_response(250.0, 8.0, num_heating_days=5)).encode("utf-8")
    with mock.patch("urllib.request.urlopen") as mock_url, \
         mock.patch("layer2_calibration.learned_forecaster.save_json"):
        mock_resp = mock.MagicMock()
        mock_resp.read.return_value = mock_payload_insufficient
        mock_url.return_value.__enter__.return_value = mock_resp

        res = forecaster.retrain_from_openhems()
        rec_ids = [r["id"] for r in res["recommendations"]["recommendations"]]
        assert "building_ua" not in rec_ids
        assert "night_baseload" in rec_ids
