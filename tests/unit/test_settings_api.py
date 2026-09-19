"""
Unit Tests for Settings API & Comfort Margin Roundtrip (WP4)
============================================================
Verifies:
1. GET /api/settings returns effective settings with dhw_optimizer.comfort_margin.
2. POST /api/settings updates comfort_margin mode, factor, min_margin, fixed_margin.
3. Plan includes comfort_margin_mode, comfort_boundary_c in decision_details.
4. Input validation rejects invalid modes or out-of-range bounds.
"""

import pytest
from unittest.mock import MagicMock
from api import routes_system
from api.secrets_store import CONFIG_FILE, load_json, save_json
from layer3_scheduling.central_planner import CentralPlanner
from layer3_scheduling.dhw_optimizer import DhwOptimizerParams
from tests.unit.test_dhw_optimizer import make_test_slots
from layer1_data_collection.sanitizer import CleanTelemetryFrame
from datetime import datetime, timezone


class MockHandler:
    def __init__(self, body=None, path="/api/settings"):
        self.body = body or {}
        self.path = path
        self.sent_json = None
        self.sent_status = 200

    def _read_json_body(self):
        return self.body

    def _send_json(self, data, status=200):
        self.sent_json = data
        self.sent_status = status


def test_get_settings_contains_comfort_margin():
    handler = MockHandler(path="/api/settings")
    res = routes_system.handle_get(handler, "/api/settings", {})
    assert res is True
    assert handler.sent_json is not None
    assert "dhw_optimizer" in handler.sent_json
    cm = handler.sent_json["dhw_optimizer"]["comfort_margin"]
    assert "mode" in cm
    assert cm["mode"] in ("p95", "p50", "fixed")


def test_post_settings_updates_and_validates():
    # 1. Valid update to p50
    body = {
        "dhw_optimizer": {
            "comfort_margin": {
                "mode": "p50",
                "min_margin_c": 0.5
            }
        }
    }
    handler = MockHandler(body=body, path="/api/settings")
    res = routes_system.handle_post(handler, "/api/settings", body)
    assert res is True
    assert handler.sent_status == 200
    assert handler.sent_json is not None
    assert handler.sent_json["dhw_optimizer"]["comfort_margin"]["mode"] == "p50"
    assert handler.sent_json["dhw_optimizer"]["comfort_margin"]["min_margin_c"] == 0.5

    # 2. Invalid mode rejected
    bad_body = {
        "dhw_optimizer": {
            "comfort_margin": {
                "mode": "invalid_mode"
            }
        }
    }
    bad_handler = MockHandler(body=bad_body, path="/api/settings")
    routes_system.handle_post(bad_handler, "/api/settings", bad_body)
    assert bad_handler.sent_status == 400

    # 3. Out-of-bounds factor rejected
    bad_factor_body = {
        "dhw_optimizer": {
            "comfort_margin": {
                "tap_stress_factor": 5.0
            }
        }
    }
    bad_f_handler = MockHandler(body=bad_factor_body, path="/api/settings")
    routes_system.handle_post(bad_f_handler, "/api/settings", bad_factor_body)
    assert bad_f_handler.sent_status == 400


from tests.unit.test_central_planner_explanations import create_sample_telemetry_frame


def test_planner_reflects_comfort_margin_in_decision_details():
    now_dt = datetime(2026, 9, 19, 0, 0, tzinfo=timezone.utc)
    frame = create_sample_telemetry_frame(now_dt, current_dhw_temp=45.0)

    params_p50 = DhwOptimizerParams(comfort_margin_mode="p50", min_comfort_margin_c=0.5)
    plan = CentralPlanner.plan(frame, current_dhw_temp=45.0, dhw_optimizer_params=params_p50)

    assert plan.dhw_summary is not None
    details = plan.dhw_summary.decision_details
    assert details is not None
    assert details.get("comfort_margin_mode") == "p50"
    assert details.get("comfort_boundary_c") == 40.5
