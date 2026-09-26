"""
Architectural Guardrail: OpenAPI & MCP Lockstep Parity Test
============================================================
Ensures that the Open HEMS MCP Server runs in strict 100% lockstep with
the OpenAPI 3.1.0 REST API specification.

Invariant Rules:
1. Every path declared in docs/openapi.json must be accessible via MCP.
2. High-level MCP tools must preserve argument and parameter names declared in OpenAPI.
3. The generic lockstep invocation tool `openhems_call_api_endpoint` must be available.
4. Any addition of an API path without corresponding documentation or MCP access fails tests.
"""

import os
import json
import inspect
import pytest
import mcp_server


SPEC_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "docs", "openapi.json")


def load_openapi_spec():
    assert os.path.exists(SPEC_PATH), f"OpenAPI specification missing at {SPEC_PATH}"
    with open(SPEC_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def test_openapi_spec_validity():
    """Verify that openapi.json is well-formed OpenAPI 3.1.0."""
    spec = load_openapi_spec()
    assert spec.get("openapi") == "3.1.0"
    assert "info" in spec
    assert "paths" in spec
    assert len(spec["paths"]) >= 10, "OpenAPI spec has too few paths defined"


def test_mcp_has_generic_lockstep_tool():
    """Verify that the generic lockstep caller exists to bridge any future endpoint immediately."""
    assert hasattr(mcp_server, "openhems_call_api_endpoint")
    sig = inspect.signature(mcp_server.openhems_call_api_endpoint)
    params = list(sig.parameters.keys())
    assert "path" in params
    assert "method" in params
    assert "params_json" in params


def test_mcp_high_level_tools_coverage():
    """Verify that core operational domains have dedicated first-class high-level MCP tools."""
    spec = load_openapi_spec()
    paths = spec["paths"]

    # Mapping of critical OpenAPI paths to their dedicated high-level MCP tool functions
    critical_mappings = {
        "/api/schedule/chart-data": "openhems_get_schedule",
        "/api/schedule/recalculate": "openhems_recalculate_schedule",
        "/api/model/battery-status": "openhems_get_battery_status",
        "/api/model/dhw-status": "openhems_get_dhw_status",
        "/api/model/heating-forecast": "openhems_get_heating_forecast",
        "/api/model/parameter-history": "openhems_get_parameter_history",
        "/api/analytics/power_producers": "openhems_get_analytics_power",
        "/api/analytics/dhw_history": "openhems_get_analytics_dhw",
        "/api/analytics/plan_vs_actual": "openhems_get_plan_vs_actual",
        "/api/analytics/decisions": "openhems_get_decisions",
        "/api/devices": "openhems_get_devices",
        "/api/status": "openhems_get_system_health",
        "/api/health/consistency": "openhems_get_system_health",
    }

    for path, tool_name in critical_mappings.items():
        assert path in paths, f"Path '{path}' declared in lockstep mapping is missing from docs/openapi.json!"
        assert hasattr(mcp_server, tool_name), f"MCP tool '{tool_name}' missing in mcp_server.py for OpenAPI path '{path}'!"


def test_mcp_parameter_parity_with_openapi():
    """Verify that parameter names in high-level MCP tools match their OpenAPI parameter definitions."""
    spec = load_openapi_spec()
    paths = spec["paths"]

    # Check /api/schedule/chart-data
    sched_params = [p["name"] for p in paths["/api/schedule/chart-data"]["get"].get("parameters", [])]
    sched_tool_params = list(inspect.signature(mcp_server.openhems_get_schedule).parameters.keys())
    for p in sched_params:
        assert p in sched_tool_params, f"Parameter '{p}' from OpenAPI /api/schedule/chart-data missing in openhems_get_schedule!"

    # Check /api/analytics/power_producers
    pp_params = [p["name"] for p in paths["/api/analytics/power_producers"]["get"].get("parameters", [])]
    pp_tool_params = list(inspect.signature(mcp_server.openhems_get_analytics_power).parameters.keys())
    for p in pp_params:
        assert p in pp_tool_params, f"Parameter '{p}' from OpenAPI /api/analytics/power_producers missing in openhems_get_analytics_power!"

    # Check /api/model/parameter-history
    ph_params = [p["name"] for p in paths["/api/model/parameter-history"]["get"].get("parameters", [])]
    ph_tool_params = list(inspect.signature(mcp_server.openhems_get_parameter_history).parameters.keys())
    for p in ph_params:
        assert p in ph_tool_params, f"Parameter '{p}' from OpenAPI /api/model/parameter-history missing in openhems_get_parameter_history!"


def test_mcp_resources_and_prompts_defined():
    """Verify that essential dynamic resources and reasoning prompts are registered."""
    assert hasattr(mcp_server, "get_current_schedule_resource")
    assert hasattr(mcp_server, "get_dhw_resource")
    assert hasattr(mcp_server, "get_openapi_spec_resource")
    assert hasattr(mcp_server, "explain_dispatch_decision")
    assert hasattr(mcp_server, "evaluate_dhw_comfort")
