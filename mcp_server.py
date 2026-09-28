#!/usr/bin/env python3
"""
Open HEMS — Model Context Protocol (MCP) Server
================================================
Exposes Open HEMS functionality to AI assistants (Hermes Agent, Claude Desktop, Cursor)
via standardized Model Context Protocol tools, resources, and prompts.

Maintains strict lockstep parity with the Open HEMS OpenAPI 3.1.0 REST API.
"""

import os
import sys
import json
import urllib.request
import urllib.error
import urllib.parse
from typing import Dict, Any, List, Optional
from mcp.server.mcpserver import MCPServer

# Configuration
DEFAULT_BASE_URL = os.environ.get("OPENHEMS_BASE_URL", "http://172.30.33.10:8099")
SPEC_PATH = os.path.join(os.path.dirname(__file__), "docs", "openapi.json")

mcp = MCPServer(
    name="openhems",
    version="0.92.52",
    description="Open HEMS — Home Energy Management System with predictive heat pump, solar, and dynamic tariff dispatch."
)


def _api_request(path: str, method: str = "GET", params: Optional[Dict[str, Any]] = None, body: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Execute an HTTP request to the Open HEMS REST daemon."""
    base_url = os.environ.get("OPENHEMS_BASE_URL", DEFAULT_BASE_URL).rstrip("/")
    if params:
        query_str = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
        url = f"{base_url}{path}?{query_str}"
    else:
        url = f"{base_url}{path}"

    headers = {"Accept": "application/json", "User-Agent": "OpenHEMS-MCP/0.92.52"}
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"

    req = urllib.request.Request(url, data=data, headers=headers, method=method.upper())
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            content = resp.read().decode("utf-8")
            return json.loads(content) if content else {"status": "success", "status_code": resp.status}
    except urllib.error.HTTPError as e:
        err_msg = e.read().decode("utf-8", errors="replace")
        return {"error": f"HTTP {e.code}: {e.reason}", "details": err_msg}
    except Exception as e:
        return {"error": f"Failed to reach Open HEMS API at {url}: {str(e)}"}


# =============================================================================
# TOOLS: SCHEDULE & DISPATCH
# =============================================================================

@mcp.tool()
def openhems_get_schedule(resolution: str = "15m") -> str:
    """Get the 24-hour rolling dispatch schedule from Open HEMS.

    Args:
        resolution: Timeline resolution: '15m' (quarter-hourly) or '1h' (hourly).
    Returns:
        JSON string with quarter-hourly time slots, assigned modes (forced_off, normal, forced_on, max_on),
        dynamic electricity spot prices, solar forecasts, and predicted net power.
    """
    res = _api_request("/api/schedule/chart-data", params={"resolution": resolution})
    return json.dumps(res, indent=2)


@mcp.tool()
def openhems_recalculate_schedule() -> str:
    """Force an immediate recalculation of the 24-hour dispatch plan across all layers.

    Returns:
        Confirmation status from the CentralPlanner.
    """
    res = _api_request("/api/schedule/recalculate", method="POST")
    return json.dumps(res, indent=2)


# =============================================================================
# TOOLS: THERMODYNAMIC MODELS & FORECASTS
# =============================================================================

@mcp.tool()
def openhems_get_dhw_status(resolution: str = "15m") -> str:
    """Get domestic hot water (DHW) 350L tank thermal trajectory and 24h arbitrage status.

    Args:
        resolution: Aggregation resolution: '15m' or '1h'.
    Returns:
        JSON with live tank temperature (°C), predicted unheated vs heated trajectories,
        thermal heat demand in kWh_th, consumer equivalent V40 liters, and daytime arbitrage decisions.
    """
    res = _api_request("/api/model/dhw-status", params={"resolution": resolution})
    return json.dumps(res, indent=2)


@mcp.tool()
def openhems_get_battery_status(resolution: str = "15m", horizon: str = "24h") -> str:
    """Get 15 kWh electrochemical battery storage 24h/48h dispatch plan and state of charge trajectory.

    Args:
        resolution: Aggregation resolution: '15m' or '1h'.
        horizon: Planning horizon: '24h' or '48h'.
    Returns:
        JSON with state of charge trajectory (SoC % and kWh), charging/discharging power (kW),
        overlay operation windows, and financial savings calculated using multi-pass dynamic shadow pricing.
    """
    res = _api_request("/api/model/battery-status", params={"resolution": resolution, "horizon": horizon})
    return json.dumps(res, indent=2)


@mcp.tool()
def openhems_get_heating_forecast(resolution: str = "15m") -> str:
    """Get space heating (CV) 2R1C building thermal forecast and pre-heat recommendations.

    Args:
        resolution: Aggregation resolution: '15m' or '1h'.
    Returns:
        Indoor temperature forecast, thermal loss curve, heating power demand, and night-valley pre-heat windows.
    """
    res = _api_request("/api/model/heating-forecast", params={"resolution": resolution})
    return json.dumps(res, indent=2)


@mcp.tool()
def openhems_get_parameter_history(parameter_id: str = "building_ua", timeframe: str = "quarter") -> str:
    """Get calibration and adjustment history for a learned model parameter.

    Args:
        parameter_id: Identifier of parameter ('building_ua', 'c_wind', 'c_solar', 'night_baseload', 'floor_capacity', 'pv_yield_ratio').
        timeframe: History timeframe: 'quarter' (default, 90d), '30d', '1y', or 'all'.
    Returns:
        JSON with parameter drift, chronological adjustment events, and timeline trend data.
    """
    res = _api_request("/api/model/parameter-history", params={"parameter_id": parameter_id, "timeframe": timeframe})
    return json.dumps(res, indent=2)


# =============================================================================
# TOOLS: ANALYTICS, AUDIT & TELEMETRY HISTORY
# =============================================================================

@mcp.tool()
def openhems_get_analytics_power(range: str = "24h", resolution: str = "1h") -> str:
    """Get historical power producer and grid telemetry (solar, heat pump, P1 import/export).

    Args:
        range: Historical timeframe: '1h', '6h', '24h', '48h', or '7d'.
        resolution: Bucket size: '15m' or '1h'.
    Returns:
        Time series of power (kW) and energy (kWh) per producer and grid flow.
    """
    res = _api_request("/api/analytics/power_producers", params={"range": range, "resolution": resolution})
    return json.dumps(res, indent=2)


@mcp.tool()
def openhems_get_analytics_dhw(range: str = "24h", resolution: str = "15m") -> str:
    """Get historical DHW tank temperature (°C) and thermal heat delivered (kWh_th).

    Args:
        range: Historical timeframe: '24h', '48h', or '7d'.
        resolution: Bucket size: '15m' or '1h'.
    Returns:
        Actual sensor temperatures and energy delivered over the selected historical period.
    """
    res = _api_request("/api/analytics/dhw_history", params={"range": range, "resolution": resolution})
    return json.dumps(res, indent=2)


@mcp.tool()
def openhems_get_analytics_battery(range: str = "24h", resolution: str = "1h") -> str:
    """Get historical battery State of Charge (%), charging/discharging powers (kW), and dynamic tariffs.

    Args:
        range: Historical timeframe: '24h', '48h', or '7d'.
        resolution: Bucket size: '15m' or '1h'.
    Returns:
        Historical battery trajectory, power flows, and financial impacts.
    """
    res = _api_request("/api/analytics/battery_history", params={"range": range, "resolution": resolution})
    return json.dumps(res, indent=2)


@mcp.tool()
def openhems_get_plan_vs_actual(range: str = "24h", resolution: str = "15m") -> str:
    """Get aligned plan vs actual time-series comparing realized telemetry to planned dispatch schedules and forecasts.

    Args:
        range: Historical timeframe: '24h', '48h', or '7d'.
        resolution: Bucket size: '15m' or '1h'.
    Returns:
        JSON string with aligned actual vs planned solar, heat pump power, and boiler trajectories.
    """
    res = _api_request("/api/analytics/plan_vs_actual", params={"range": range, "resolution": resolution})
    return json.dumps(res, indent=2)


@mcp.tool()
def openhems_get_decisions(limit: int = 20, offset: int = 0) -> str:
    """Get chronological audit trail of all dispatch decisions with physical and financial rationale.

    Args:
        limit: Number of decision records to return (default: 20).
        offset: Record offset for pagination.
    Returns:
        Array of decision logs including inputs (tank temp, prices, weather), physical constraints, and reasoning.
    """
    res = _api_request("/api/analytics/decisions", params={"limit": limit, "offset": offset})
    return json.dumps(res, indent=2)


# =============================================================================
# TOOLS: HARDWARE, TOPOLOGY & SYSTEM HEALTH
# =============================================================================

@mcp.tool()
def openhems_get_devices() -> str:
    """List all registered energy devices, physical capabilities, and Home Assistant / MQTT bindings.

    Returns:
        List of devices with capabilities (can_delay, can_modulate, can_store, is_thermal).
    """
    res = _api_request("/api/devices")
    return json.dumps(res, indent=2)


@mcp.tool()
def openhems_get_system_health() -> str:
    """Verify Single Source of Truth consistency, pipeline accumulator health, and runtime status.

    Returns:
        Overall system status, single_source_of_truth_verified boolean, and pipeline flush metrics.
    """
    status = _api_request("/api/status")
    consistency = _api_request("/api/health/consistency")
    pipeline = _api_request("/api/pipeline/status")
    return json.dumps({
        "status": status,
        "consistency": consistency,
        "pipeline": pipeline
    }, indent=2)


@mcp.tool()
def openhems_call_api_endpoint(path: str, method: str = "GET", params_json: str = "{}", body_json: str = "{}") -> str:
    """Generic OpenAPI lockstep invocation tool: Call ANY endpoint declared in the Open HEMS OpenAPI specification.

    Args:
        path: API path (e.g. '/api/tariffs', '/api/policies', '/api/config/solar').
        method: HTTP method ('GET' or 'POST').
        params_json: JSON string of query parameters (e.g. '{"range": "24h"}').
        body_json: JSON string of request body for POST requests.
    Returns:
        JSON response directly from the API.
    """
    try:
        params = json.loads(params_json) if params_json else None
    except Exception:
        params = None
    try:
        body = json.loads(body_json) if body_json and body_json != "{}" else None
    except Exception:
        body = None

    res = _api_request(path, method=method, params=params, body=body)
    return json.dumps(res, indent=2)


# =============================================================================
# RESOURCES: DYNAMIC CONTEXT
# =============================================================================

@mcp.resource("hems://schedule/current")
def get_current_schedule_resource() -> str:
    """Current active quarter-hour operating mode and power targets."""
    res = _api_request("/api/schedule/chart-data", params={"resolution": "15m"})
    slots = res.get("slots", [])
    current_slot = slots[0] if slots else {}
    return json.dumps({
        "active_slot": current_slot,
        "current_time": res.get("current_time"),
        "active_profile": res.get("active_profile")
    }, indent=2)


@mcp.resource("hems://models/dhw")
def get_dhw_resource() -> str:
    """Live 350L DHW boiler status, comfort margin, and daytime arbitrage recommendation."""
    res = _api_request("/api/model/dhw-status")
    return json.dumps(res, indent=2)


@mcp.resource("hems://openapi/spec")
def get_openapi_spec_resource() -> str:
    """The complete, machine-readable OpenAPI 3.1.0 specification for Open HEMS."""
    if os.path.exists(SPEC_PATH):
        with open(SPEC_PATH, "r", encoding="utf-8") as f:
            return f.read()
    res = _api_request("/api/openapi.json")
    return json.dumps(res, indent=2)


# =============================================================================
# PROMPTS: PRE-CONFIGURED REASONING WORKFLOWS
# =============================================================================

@mcp.prompt()
def explain_dispatch_decision(time_slot: str) -> str:
    """Analyze and explain why a specific Smart Grid mode was chosen for a given time window."""
    return f"""Please query the Open HEMS MCP server for the latest dispatch schedule using `openhems_get_schedule` and the decision audit log with `openhems_get_decisions`.
Focus on the time slot around '{time_slot}'.
Provide a structured explanation:
1. What was the assigned Smart Grid mode (forced_off, advised_off, normal, advised_on, forced_on, or max_on)?
2. What were the market conditions (EPEX spot price, threshold delta)?
3. What were the physical constraints (comfort limit, tank temperature, floor thermal lag)?
4. What was the economic / cost comparison that drove this decision?
"""


@mcp.prompt()
def evaluate_dhw_comfort() -> str:
    """Evaluate domestic hot water comfort risk and overnight heating decisions."""
    return """Please query the Open HEMS MCP server using `openhems_get_dhw_status`.
Evaluate the following:
1. Actuele tanksensortemperatuur en afstand tot de 40°C comfortgrens.
2. Voorspelde ochtenddip vóór de ochtendspits (06:00–10:00).
3. Is er een nachtverwarming (forced_on) gepland, en wat zijn de verwachte kosten en COP?
4. Is er een zonnebuffer (max_on naar 60°C) overdag voordeliger bevonden door de DhwDaytimeArbiter?
"""


# =============================================================================
# ENTRY POINT
# =============================================================================

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Open HEMS MCP Server")
    parser.add_argument("--transport", choices=["stdio", "sse", "streamable-http"], default="stdio",
                        help="Transport protocol (default: stdio)")
    parser.add_argument("--port", type=int, default=8101, help="Port for SSE/HTTP transport")
    parser.add_argument("--host", default="0.0.0.0", help="Host for SSE/HTTP transport")
    args = parser.parse_args()

    if args.transport == "stdio":
        mcp.run(transport="stdio")
    else:
        mcp.run(transport=args.transport, host=args.host, port=args.port)
