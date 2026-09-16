#!/usr/bin/env python3
"""
Open HEMS Framework & Management Console
========================================
Version: 0.94.0
Generic Energy Management Platform:
  - Solidified Data Collection Layer (Laag 1) with Full Multi-Instance CRUD:
      * InfluxDB Multi-Instance CRUD (Local HA, Remote Dedicated Servers, InfluxDB Cloud)
      * MQTT Multi-Broker CRUD (Local Mosquitto, Remote Brokers, Cloud Gateways)
      * Native Real-Time Connection Testers & Line Protocol Ingest Monitor
  - Decoupled Policy Engine with 3 Fundamental Policy Archetypes:
      1. ShiftableConsumerPolicy (Verbruik zonder opslag: vaatwasser, wasmachine)
      2. ThermalBufferPolicy (Buffer zonder teruggave: 350L SWW, CV vloer)
      3. BatteryArbitragePolicy (Accu met teruggave & economische dode zone / deadband)
  - Full CRUD for Policies, Tariffs, and Devices
  - Home Assistant Entity Dropdowns for Zero-Manual-Typing Setup
  - Interactive 24-Hour Stacked Bar Chart (Chart.js) with Recommendation Balloons
"""

import sys
import os
import re
from pathlib import Path
import argparse
import json
import urllib.parse
import urllib.request
import urllib.error
import ssl
import socket
import base64
import time
import threading
import math
from typing import Optional, Dict, Any, List, Tuple

ROOT_DIR = str(Path(__file__).resolve().parent)
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

try:
    from layer2_calibration.learned_forecaster import HybridForecastingModel
    from layer2_calibration.dhw_thermal_model import DhwThermalModel
    from layer1_data_collection.sanitizer import TelemetrySanitizer, CleanTelemetryFrame
    from layer3_scheduling.central_planner import CentralPlanner
    from layer3_scheduling.plan_store import get_plan_store, PlanStore
    from layer3_scheduling.tariff_provider import TariffProvider
    from models.mode_catalog import get_mode_meta, load_mode_catalog
    from models.canonical import StandardizedState, STATE_METADATA, CanonicalDispatchPlan
    GLOBAL_MODEL = HybridForecastingModel()
    GLOBAL_DHW_MODEL = DhwThermalModel()
except Exception as _e_model:
    print(f"[WARN] Failed to initialize Forecasting Models: {_e_model}")
    GLOBAL_MODEL = None
    GLOBAL_DHW_MODEL = None

from api import routes_analytics, routes_model, routes_schedule, routes_system

from http.server import HTTPServer, BaseHTTPRequestHandler
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
AMS_TZ = ZoneInfo('Europe/Amsterdam')

DUTCH_DAYS_SHORT = ["Ma", "Di", "Wo", "Do", "Vr", "Za", "Zo"]

def format_slot_label(dt_slot, prev_dt, is_first, is_15m):
    time_str = dt_slot.strftime("%H:%M" if is_15m else "%H:00")
    if is_first:
        return f"Nu ({time_str})"
    if prev_dt is not None and dt_slot.day != prev_dt.day:
        day_str = DUTCH_DAYS_SHORT[dt_slot.weekday()]
        return f"{day_str} {time_str}"
    return time_str
from pathlib import Path

# Site-specific adapters (decoupled from core engine)
sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, "/addons/open-hems")
sys.path.insert(0, "/opt/open-hems")
from site_adapters.daikin_p1p2 import DaikinP1P2StateClassifier, HeatPumpDisaggregation
from models.canonical import normalize_power_reading, detect_dynamic_price_peaks, calc_percentile

CONFIG_FILE = Path("/config/heatpump_config.json")
PARAMS_FILE = Path("/config/heatpump_model_parameters.json")
CACHE_FILE = Path("/config/data/energy_feed_cache.json")
HA_API_CONFIG = Path("/config/.ha_api_config.json")
SECRETS_FILE = Path("/config/open_hems_secrets.json")
WEB_DIR = Path(__file__).parent / "web"
INDEX_HTML_PATH = WEB_DIR / "index.html"


GLOBAL_CENTRAL_CACHE = {
    "timestamp": 0,
    "15m": None,
    "1h": None
}

def calculate_poa_solar_kw(
    dt_ams: datetime,
    ghi_w_m2: float,
    kwp: float = 5.76,
    tilt_deg: float = 34.0,
    azimuth_deg: float = 225.0,
    inverter_limit_kw: float = 5.5,
    eff: float = 0.88,
    lat: float = 51.9537,
    lon: float = 5.232
) -> float:
    """Calculates Plane-of-Array (POA) solar generation in AC kW based on NOAA solar geometry."""
    if ghi_w_m2 <= 1.0:
        return 0.0
    doy = dt_ams.timetuple().tm_yday
    decl = math.radians(23.45 * math.sin(math.radians(360.0 * (284.0 + doy) / 365.0)))
    b = math.radians(360.0 * (doy - 81) / 364.0)
    eot = 9.87 * math.sin(2 * b) - 7.53 * math.cos(b) - 1.5 * math.sin(b)
    tz_offset = dt_ams.utcoffset().total_seconds() / 3600.0 if dt_ams.utcoffset() else 2.0
    solar_time_h = dt_ams.hour + dt_ams.minute / 60.0 + dt_ams.second / 3600.0 + (4.0 * lon + eot) / 60.0 - tz_offset
    omega = math.radians((solar_time_h - 12.0) * 15.0)
    lat_r = math.radians(lat)
    sin_alpha = math.sin(lat_r) * math.sin(decl) + math.cos(lat_r) * math.cos(decl) * math.cos(omega)
    alpha = math.asin(max(-1.0, min(1.0, sin_alpha)))
    cos_alpha = math.cos(alpha)
    if math.degrees(alpha) <= 1.0:
        return 0.0
    cos_az = (math.sin(decl) * math.cos(lat_r) - math.cos(decl) * math.sin(lat_r) * math.cos(omega)) / max(0.001, cos_alpha)
    cos_az = max(-1.0, min(1.0, cos_az))
    az_deg = math.degrees(math.acos(cos_az))
    if omega > 0:
        az_deg = 360.0 - az_deg
    beta_r = math.radians(tilt_deg)
    gamma_diff_r = math.radians(az_deg - azimuth_deg)
    cos_aoi = math.cos(alpha) * math.sin(beta_r) * math.cos(gamma_diff_r) + math.sin(alpha) * math.cos(beta_r)
    kt = min(1.0, ghi_w_m2 / (1367.0 * max(0.05, math.sin(alpha))))
    if kt <= 0.22:
        df_frac = 1.0 - 0.09 * kt
    elif kt <= 0.80:
        df_frac = 0.9511 - 0.1604 * kt + 4.388 * (kt**2) - 16.638 * (kt**3) + 12.336 * (kt**4)
    else:
        df_frac = 0.165
    diffuse_horiz = ghi_w_m2 * max(0.15, min(1.0, df_frac))
    direct_horiz = max(0.0, ghi_w_m2 - diffuse_horiz)
    direct_normal = direct_horiz / max(0.05, math.sin(alpha))
    poa_beam = direct_normal * max(0.0, cos_aoi)
    poa_diffuse = diffuse_horiz * (1.0 + math.cos(beta_r)) / 2.0
    poa_ground = ghi_w_m2 * 0.20 * (1.0 - math.cos(beta_r)) / 2.0
    poa_total = max(0.0, poa_beam + poa_diffuse + poa_ground)
    p_dc = (poa_total / 1000.0) * kwp * eff
    return round(min(inverter_limit_kw, p_dc), 3)



def load_secrets() -> dict:
    """Loads private credentials from the 0600-permission vault."""
    if SECRETS_FILE.exists():
        try:
            with open(SECRETS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"Warning loading secrets: {e}")
    return {"influxdb": {}, "mqtt": {}}


def save_secret(domain: str, conn_id: str, secret: str):
    """Saves a credential to the isolated private vault with 0600 permissions."""
    if not secret:
        return
    sec = load_secrets()
    sec.setdefault(domain, {})[conn_id] = secret
    tmp = f"{SECRETS_FILE}.tmp.{os.getpid()}"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(sec, f, indent=2)
    os.replace(tmp, SECRETS_FILE)


_WORKING_HA_BASE_URL = None

def get_ha_client_config() -> Tuple[str, str]:
    """
    Returns (ha_url, token) for Home Assistant Core REST API.
    Auto-discovers and caches the responsive endpoint among:
      - Direct internal HA Docker bridge: https://172.30.32.1:8123
      - Direct internal Docker service name: https://homeassistant:8123
      - User-configured URL in secrets
      - http://supervisor/core
    """
    global _WORKING_HA_BASE_URL
    sec = load_secrets()
    ha_sec = sec.get("homeassistant", {})
    token = ha_sec.get("token") or os.environ.get("HASS_TOKEN", "")
    
    if not token and HA_API_CONFIG.exists():
        cfg = load_json(HA_API_CONFIG)
        token = cfg.get("HASS_TOKEN")

    if not token and os.path.exists("/data/options.json"):
        try:
            with open("/data/options.json") as f:
                opts = json.load(f)
                token = opts.get("homeassistant_token") or token
        except Exception:
            pass

    if _WORKING_HA_BASE_URL:
        return _WORKING_HA_BASE_URL, token

    configured_url = ha_sec.get("url") or os.environ.get("HASS_URL")
    candidates = [
        "https://172.30.32.1:8123",
        "https://homeassistant:8123",
        configured_url,
        "http://supervisor/core",
        "https://hass.b3rg.nl:8123"
    ]
    seen = set()
    uniq_candidates = []
    for c in candidates:
        if c and c not in seen:
            seen.add(c)
            uniq_candidates.append(c)

    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    for candidate in uniq_candidates:
        try:
            req = urllib.request.Request(f"{candidate}/api/states/zone.home", headers=headers)
            with urllib.request.urlopen(req, timeout=1.5, context=ctx) as r:
                if r.status in [200, 201]:
                    _WORKING_HA_BASE_URL = candidate
                    return _WORKING_HA_BASE_URL, token
        except Exception:
            continue

    default_url = configured_url or "https://172.30.32.1:8123"
    return default_url, token


def get_anchored_weather_forecast(base_dt: datetime) -> tuple:
    """
    Fetches Open-Meteo weather forecast for Culemborg (51.9537, 5.2320),
    reads live local Wittboy weather station from Home Assistant,
    and performs smooth observation nudging (analysis assimilation) from local measurements
    into the regional forecast over a 3-hour decay window.
    Returns (temp_map, solar_map, wind_map, rh_map) keyed by "%Y-%m-%d %H:00".
    """
    temp_map, solar_map, wind_map, rh_map = {}, {}, {}, {}

    wb_temp = None
    wb_wind = None
    wb_solar = None
    wb_rh = None
    try:
        ha_url, ha_tok = get_ha_client_config()
        if ha_tok and ha_url:
            ctx_ssl = ssl.create_default_context()
            ctx_ssl.check_hostname = False
            ctx_ssl.verify_mode = ssl.CERT_NONE

            entities_to_query = [
                ("sensor.wittboy_gw2000a_weather_station_gw2000a_outdoor_temperature", "temp"),
                ("sensor.temperatuur_buiten", "temp_fallback"),
                ("sensor.wittboy_gw2000a_weather_station_gw2000a_wind_speed", "wind"),
                ("sensor.wittboy_gw2000a_weather_station_gw2000a_solar_radiation", "solar"),
                ("sensor.wittboy_gw2000a_weather_station_gw2000a_outdoor_humidity", "rh")
            ]
            for ent, var in entities_to_query:
                try:
                    req = urllib.request.Request(f"{ha_url}/api/states/{ent}", headers={"Authorization": f"Bearer {ha_tok}", "Content-Type": "application/json"})
                    with urllib.request.urlopen(req, timeout=1.5, context=ctx_ssl) as r:
                        st = json.loads(r.read().decode())
                        val = float(st.get("state", 0.0))
                        if var == "temp" and wb_temp is None:
                            wb_temp = val
                        elif var == "temp_fallback" and wb_temp is None:
                            wb_temp = val
                        elif var == "wind" and wb_wind is None:
                            wb_wind = val / 3.6  # km/h to m/s
                        elif var == "solar" and wb_solar is None:
                            wb_solar = val  # W/m2
                        elif var == "rh" and wb_rh is None:
                            wb_rh = val
                except Exception:
                    pass
    except Exception:
        pass

    try:
        url_m = "https://api.open-meteo.com/v1/forecast?latitude=51.9537&longitude=5.2320&hourly=temperature_2m,shortwave_radiation,wind_speed_10m,relative_humidity_2m&timezone=Europe%2FAmsterdam&forecast_days=2"
        req_m = urllib.request.Request(url_m, headers={"User-Agent": "OpenHEMS/1.0"})
        with urllib.request.urlopen(req_m, timeout=5) as r_m:
            m_data = json.loads(r_m.read().decode())
            h_times = m_data["hourly"]["time"]
            h_temps = m_data["hourly"]["temperature_2m"]
            h_rads = m_data["hourly"]["shortwave_radiation"]
            h_winds = m_data["hourly"]["wind_speed_10m"]
            h_rhs = m_data["hourly"]["relative_humidity_2m"]

            start_hour_iso = base_dt.strftime("%Y-%m-%dT%H:00")
            idx_start = h_times.index(start_hour_iso) if start_hour_iso in h_times else 0

            raw_cur_temp = float(h_temps[idx_start]) if idx_start < len(h_temps) else 15.0
            delta_temp = (wb_temp - raw_cur_temp) if wb_temp is not None else 0.0

            raw_cur_wind = float(h_winds[idx_start]) if idx_start < len(h_winds) else 3.0
            delta_wind = (wb_wind - raw_cur_wind) if wb_wind is not None else 0.0

            raw_cur_solar = float(h_rads[idx_start]) if idx_start < len(h_rads) else 0.0
            delta_solar = (wb_solar - raw_cur_solar) if wb_solar is not None else 0.0

            tau_hours = 3.0  # Smooth assimilation window of 3 hours
            for offset_h in range(len(h_times) - idx_start):
                idx = idx_start + offset_h
                t_str = h_times[idx]
                k_t = t_str.replace('T', ' ')[:13] + ':00'

                weight = math.exp(-offset_h / tau_hours)

                nudged_temp = round(float(h_temps[idx]) + delta_temp * weight, 1)
                nudged_wind = round(max(0.0, float(h_winds[idx]) + delta_wind * weight), 1)
                nudged_solar = round(max(0.0, float(h_rads[idx]) + delta_solar * weight), 1)
                nudged_rh = float(h_rhs[idx])

                temp_map[k_t] = nudged_temp
                solar_map[k_t] = nudged_solar
                wind_map[k_t] = nudged_wind
                rh_map[k_t] = nudged_rh
    except Exception as e:
        print(f"Warning fetching anchored weather forecast: {e}")

    return temp_map, solar_map, wind_map, rh_map

    try:
        os.chmod(SECRETS_FILE, 0o600)
    except Exception:
        pass


def get_secret(domain: str, conn_id: str, default: str = "") -> str:
    """Retrieves a credential from the private vault."""
    sec = load_secrets()
    return sec.get(domain, {}).get(conn_id, default)


# Ensure module imports prioritize local add-on packages
sys.path.insert(0, "/config/lib")


def load_json(p: Path, default=None):
    if default is None:
        default = {}
    try:
        if p.exists():
            with open(p, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception as e:
        print(f"Warning loading {p}: {e}")
    return default


def save_json(p: Path, data: dict):
    tmp = f"{p}.tmp.{os.getpid()}"
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, p)
    os.chmod(p, 0o644)


def fetch_ha_entities():
    """Queries Home Assistant Core REST API for available entities for dropdown selection."""
    ha_url, token = get_ha_client_config()
    if not token:
        return []

    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    try:
        req = urllib.request.Request(f"{ha_url}/api/states", headers=headers)
        with urllib.request.urlopen(req, timeout=5, context=ctx) as r:
            states = json.loads(r.read().decode("utf-8"))
            filtered = []
            for s in states:
                eid = s.get("entity_id", "")
                domain = eid.split(".")[0]
                if domain in ["sensor", "switch", "climate", "binary_sensor", "input_boolean", "weather"]:
                    fname = s.get("attributes", {}).get("friendly_name") or eid
                    filtered.append({
                        "entity_id": eid,
                        "friendly_name": fname,
                        "domain": domain,
                        "unit": s.get("attributes", {}).get("unit_of_measurement"),
                        "state": s.get("state")
                    })
            return sorted(filtered, key=lambda x: x["friendly_name"].lower())
    except Exception as e:
        print(f"Warning fetching HA entities: {e}")
        return []


def get_ha_states_map():
    """Returns a dict mapping entity_id -> state dict from HA Core."""
    return {e["entity_id"]: e for e in fetch_ha_entities()}


def write_hems_annotation(event_type: str, title: str, description: str, state_code: str, power_kw: float = 0.0, target_temp_c: float = 0.0, savings_eur: float = 0.0, severity: str = "info"):
    """Writes a native semantic event annotation to openhems InfluxDB for Grafana dashboards."""
    try:
        cfg = load_json(CONFIG_FILE)
        sec = load_secrets()
        active_conn = cfg.get("influxdb_connections", [{}])[0]
        db_name = active_conn.get("database", "openhems")
        db_user = active_conn.get("username", "openhems")
        db_url = active_conn.get("url", "http://a0d7b954-influxdb:8086")
        db_pwd = sec.get("influxdb", {}).get(active_conn.get("id"), "") or sec.get("influxdb", {}).get("local_ha_influxdb", "")

        now_ns = int(time.time() * 1e9)
        safe_title = title.replace('"', '\\"').replace('\n', ' ')
        safe_desc = description.replace('"', '\\"').replace('\n', ' ')

        line = (
            f'hems_annotations,event_type={event_type},severity={severity},state_code={state_code} '
            f'title="{safe_title}",description="{safe_desc}",power_kw={power_kw:.2f},'
            f'target_temp_c={target_temp_c:.1f},savings_eur={savings_eur:.2f} {now_ns}'
        )

        write_url = f"{db_url}/write?" + urllib.parse.urlencode({"u": db_user, "p": db_pwd, "db": db_name})
        req = urllib.request.Request(write_url, data=line.encode("utf-8"), method="POST")
        with urllib.request.urlopen(req, timeout=3) as resp:
            pass
    except Exception as e:
        print(f"Warning writing Grafana annotation: {e}")


def log_technical_error(domain: str, event_type: str, reason: str, explanation: str, inputs: Dict[str, Any], category: str = "ERROR"):
    """Logs technical errors, failed actuations, and API issues to InfluxDB and the Audit Logger."""
    try:
        sev = "error" if category == "ERROR" else "warning"
        write_hems_annotation(
            event_type=event_type,
            title=reason,
            description=explanation,
            state_code=sev,
            power_kw=0.0,
            target_temp_c=0.0,
            savings_eur=0.0,
            severity=sev
        )
        DecisionAuditLogger.log_decision(
            domain=domain,
            decision_type=event_type,
            chosen_mode=sev,
            target_temp_c=None,
            inputs=inputs,
            reason=reason,
            explanation=explanation,
            savings_estimate_eur=0.0,
            category=category
        )
    except Exception as e_log:
        print(f"Warning logging technical error: {e_log}")


def call_ha_service_detailed(domain: str, service: str, service_data: dict) -> Tuple[bool, Optional[str]]:
    """Calls a Home Assistant Core REST API service and returns success status plus error message."""
    global _WORKING_HA_BASE_URL
    ha_url, token = get_ha_client_config()
    if not token:
        return False, "Geen Home Assistant token geconfigureerd in options.json of environment"

    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    url = f"{ha_url}/api/services/{domain}/{service}"
    try:
        req = urllib.request.Request(url, data=json.dumps(service_data).encode("utf-8"), headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=5, context=ctx) as r:
            if r.status in [200, 201]:
                return True, None
            return False, f"HTTP status {r.status}"
    except urllib.error.HTTPError as he:
        return False, f"HTTP Fout {he.code}: {he.reason}"
    except urllib.error.URLError as ue:
        _WORKING_HA_BASE_URL = None
        return False, f"Verbindingsfout naar HA ({ha_url}): {ue.reason}"
    except Exception as e:
        _WORKING_HA_BASE_URL = None
        return False, str(e)


def call_ha_service(domain: str, service: str, service_data: dict) -> bool:
    """Calls a Home Assistant Core REST API service and logs a technical error if the call fails."""
    success, err_msg = call_ha_service_detailed(domain, service, service_data)
    if not success:
        print(f"Error calling HA service {domain}.{service}: {err_msg}")
        try:
            log_technical_error(
                domain="hardware",
                event_type="ha_service_error",
                reason=f"❌ HA Schakelfout: {domain}.{service} Mislukt",
                explanation=f"Aanroep naar Home Assistant service '{domain}.{service}' met data {json.dumps(service_data)} mislukt: {err_msg}",
                inputs={"domain": domain, "service": service, "data": service_data, "error": err_msg},
                category="ERROR"
            )
        except Exception:
            pass
    return success


from layer3_scheduling.opportunistic_merger import OpportunisticDHWMerger, OpportunisticMergeResult
from layer3_scheduling.plan_store import PlanStore
from layer3_scheduling.decision_audit import DecisionAuditLogger

GLOBAL_OPPORTUNISTIC_MERGE: Optional[OpportunisticMergeResult] = None
_LAST_LOGGED_DECISION: Dict[str, Any] = {"state": None, "ts": 0.0}


def evaluate_and_apply_dhw_run_merger(plan: Any, t_live: float) -> Any:
    """
    Evaluates whether the heat pump has autonomously started heating DHW (e.g. after a shower)
    and merges an upcoming 60C solar boost run into the active run if cost-effective.
    """
    global GLOBAL_OPPORTUNISTIC_MERGE
    states_map = get_ha_states_map()

    wp_power_sensor = states_map.get("sensor.warmtepomp_power", {})
    try:
        wp_power = float(wp_power_sensor.get("state", 0.0))
    except (ValueError, TypeError):
        wp_power = 0.0

    dhw_demand_sensor = states_map.get("binary_sensor.hc_dhw_dhw_demand", {})
    dhw_valve_sensor = states_map.get("binary_sensor.hc_dhw_valve_dhw_tank", {})

    is_actively_heating = (
        (wp_power > 600.0 and (dhw_demand_sensor.get("state") == "on" or dhw_valve_sensor.get("state") == "on"))
        or (wp_power > 1200.0 and t_live < 58.0)
    )

    solar_kw_now = GLOBAL_CENTRAL_CACHE.get("current_solar_kw", 0.0)
    price_now = GLOBAL_CENTRAL_CACHE.get("current_price_eur", 0.24)

    is_hard_lockout = False
    if plan and getattr(plan, "dynamic_peaks", None):
        for p_peak in plan.dynamic_peaks:
            if p_peak.get("is_hard_lockout") and p_peak.get("start_idx", 99) <= 0 <= p_peak.get("end_idx", -1):
                is_hard_lockout = True
                break

    merge_res = OpportunisticDHWMerger.evaluate_merge(
        plan=plan,
        is_dhw_actively_heating=is_actively_heating,
        current_tank_temp_c=t_live,
        current_solar_kw=solar_kw_now,
        current_price_eur=price_now,
        is_hard_lockout_now=is_hard_lockout,
        current_power_kw=round(wp_power / 1000.0, 2)
    )

    GLOBAL_OPPORTUNISTIC_MERGE = merge_res

    if merge_res.should_merge:
        # Actuate HA to promote setpoint to 60°C and set hardware interlocks
        call_ha_service("climate", "set_temperature", {"entity_id": "climate.hc_dhw_dhw_setpoint", "temperature": 60.0})
        call_ha_service("switch", "turn_on", {"entity_id": "switch.warmtepomp_smart_grid_1_s10s"})
        call_ha_service("switch", "turn_on", {"entity_id": "switch.warmtepomp_smart_grid_2_s11s"})
        call_ha_service("switch", "turn_off", {"entity_id": "switch.hc_mode_altherma_on"})

        # Cancel the upcoming planned slots in the plan!
        for slot_idx in merge_res.cancelled_slots:
            if slot_idx < len(plan.slots):
                plan.slots[slot_idx].mode_code = "normal"
                plan.slots[slot_idx].mode_label = "Normaal (50°C)"
                plan.slots[slot_idx].dhw_kw = 0.0
                plan.slots[slot_idx].color_hex = "#1E293B"
                plan.slots[slot_idx].tailwind_class = "bg-slate-800"

        run_pwr = max(1.8, round(wp_power / 1000.0, 2))
        # Reflect active run on current slot (slot 0) and next slots
        if plan and plan.slots:
            for run_i in range(min(4, len(plan.slots))):
                plan.slots[run_i].mode_code = "max_on"
                plan.slots[run_i].mode_label = "Zonnebuffer (Fusie tot 60°C)"
                plan.slots[run_i].dhw_kw = run_pwr
                plan.slots[run_i].heating_kw = 0.0
                plan.slots[run_i].color_hex = "#A855F7"
                plan.slots[run_i].tailwind_class = "bg-purple-900"

        store = PlanStore.get_instance()
        store.publish_plan(plan)
        
        now_ts = time.time()
        if _LAST_LOGGED_DECISION.get("state") != "max_on" or (now_ts - _LAST_LOGGED_DECISION.get("ts", 0)) >= 900.0:
            _LAST_LOGGED_DECISION["state"] = "max_on"
            _LAST_LOGGED_DECISION["ts"] = now_ts
            write_hems_annotation(
                event_type="run_merger",
                title="⚡ DHW Zonnebuffer Fusie (60°C)",
                description=merge_res.decision_explanation,
                state_code="max_on",
                power_kw=run_pwr,
                target_temp_c=60.0,
                savings_eur=merge_res.savings_estimate_eur
            )
            DecisionAuditLogger.log_decision(
                domain="dhw",
                decision_type="opportunistic_merge",
                chosen_mode="max_on",
                target_temp_c=60.0,
                inputs={
                    "tank_temp_c": t_live,
                    "wp_power_w": wp_power,
                    "solar_kw": solar_kw_now,
                    "current_price_eur": price_now,
                    "price_tolerance_eur": 0.05
                },
                reason=merge_res.reason,
                explanation=merge_res.decision_explanation,
                savings_estimate_eur=merge_res.savings_estimate_eur
            )
    elif is_actively_heating and t_live >= 49.8:
        # 50°C target already reached! Stop forced mode and return relays to SG2 (Automatisch)
        call_ha_service("switch", "turn_off", {"entity_id": "switch.warmtepomp_smart_grid_1_s10s"})
        call_ha_service("switch", "turn_off", {"entity_id": "switch.warmtepomp_smart_grid_2_s11s"})
        call_ha_service("switch", "turn_on", {"entity_id": "switch.hc_mode_altherma_on"})
        call_ha_service("climate", "set_temperature", {"entity_id": "climate.hc_dhw_dhw_setpoint", "temperature": 50.0})

        if plan and plan.slots:
            for slot_idx in merge_res.cancelled_slots:
                if slot_idx < len(plan.slots):
                    plan.slots[slot_idx].mode_code = "normal"
                    plan.slots[slot_idx].mode_label = "Normaal"
                    plan.slots[slot_idx].dhw_kw = 0.0
                    plan.slots[slot_idx].color_hex = "#1E293B"
                    plan.slots[slot_idx].tailwind_class = "bg-slate-800"

            plan.slots[0].mode_code = "normal"
            plan.slots[0].mode_label = "Normaal (Standby)"
            plan.slots[0].dhw_kw = 0.0
            plan.slots[0].color_hex = "#1E293B"
            plan.slots[0].tailwind_class = "bg-slate-800"

            store = PlanStore.get_instance()
            store.publish_plan(plan)

        now_ts = time.time()
        if _LAST_LOGGED_DECISION.get("state") != "released_50" or (now_ts - _LAST_LOGGED_DECISION.get("ts", 0)) >= 900.0:
            _LAST_LOGGED_DECISION["state"] = "released_50"
            _LAST_LOGGED_DECISION["ts"] = now_ts
            write_hems_annotation(
                event_type="system_release",
                title="✅ DHW Doel 50°C Bereikt — Automatisch (SG2)",
                description="Boilervat op doeltemperatuur. Warmtepomp vrijgegeven naar ruststand.",
                state_code="normal",
                power_kw=0.0,
                target_temp_c=50.0
            )
            DecisionAuditLogger.log_decision(
                domain="dhw",
                decision_type="system_release",
                chosen_mode="normal",
                target_temp_c=50.0,
                inputs={
                    "tank_temp_c": t_live,
                    "wp_power_w": wp_power,
                    "target_temp_c": 50.0
                },
                reason="✅ DHW Doel 50°C Bereikt — Automatisch (SG2)",
                explanation="Boilervat is op doeltemperatuur (>= 50°C). Smart Grid relais zijn vrijgegeven naar Automatisch (SG2 ruststand).",
                savings_estimate_eur=0.00
            )
    elif is_actively_heating and plan and plan.slots:
        # Boiler is actively heating to 50°C standard comfort (still below 49.8°C)!
        call_ha_service("switch", "turn_on", {"entity_id": "switch.warmtepomp_smart_grid_1_s10s"})
        call_ha_service("switch", "turn_on", {"entity_id": "switch.warmtepomp_smart_grid_2_s11s"})
        call_ha_service("switch", "turn_off", {"entity_id": "switch.hc_mode_altherma_on"})

        run_pwr = max(1.8, round(wp_power / 1000.0, 2))
        for run_i in range(min(3, len(plan.slots))):
            plan.slots[run_i].mode_code = "forced_on"
            plan.slots[run_i].mode_label = "Geforceerd aan (50°C)"
            plan.slots[run_i].dhw_kw = run_pwr
            plan.slots[run_i].heating_kw = 0.0
            plan.slots[run_i].color_hex = "#10B981"
            plan.slots[run_i].tailwind_class = "bg-emerald-900"

        for slot_idx in merge_res.cancelled_slots:
            if slot_idx < len(plan.slots):
                plan.slots[slot_idx].mode_code = "normal"
                plan.slots[slot_idx].mode_label = "Normaal"
                plan.slots[slot_idx].dhw_kw = 0.0
                plan.slots[slot_idx].color_hex = "#1E293B"
                plan.slots[slot_idx].tailwind_class = "bg-slate-800"

        store = PlanStore.get_instance()
        store.publish_plan(plan)

        now_ts = time.time()
        if _LAST_LOGGED_DECISION.get("state") != "forced_50" or (now_ts - _LAST_LOGGED_DECISION.get("ts", 0)) >= 900.0:
            _LAST_LOGGED_DECISION["state"] = "forced_50"
            _LAST_LOGGED_DECISION["ts"] = now_ts
            write_hems_annotation(
                event_type="dhw_run",
                title="🚿 DHW Basislading (50°C) Gestart",
                description=merge_res.decision_explanation,
                state_code="forced_on",
                power_kw=run_pwr,
                target_temp_c=50.0
            )
            DecisionAuditLogger.log_decision(
                domain="dhw",
                decision_type="standard_charge",
                chosen_mode="forced_on",
                target_temp_c=50.0,
                inputs={
                    "tank_temp_c": t_live,
                    "wp_power_w": wp_power,
                    "target_temp_c": 50.0
                },
                reason="DHW Basislading (50°C) Gestart",
                explanation=merge_res.decision_explanation,
                savings_estimate_eur=0.10
            )
    else:
        if _LAST_LOGGED_DECISION.get("state") in ["max_on", "released_50", "forced_50"]:
            _LAST_LOGGED_DECISION["state"] = "idle"

    return merge_res


_LAST_NIGHT_AUDIT_LOG: Dict[str, Any] = {"state": None, "ts": 0.0}
_LAST_LOGGED_ACTUATION: Dict[str, Any] = {"state": None, "ts": 0.0}

def evaluate_and_log_night_boiler_decision(plan: Any, t_live: float):
    global _LAST_NIGHT_AUDIT_LOG
    now_ams = datetime.now(ZoneInfo("Europe/Amsterdam"))
    # Active during evening and night (19:00 - 06:00)
    is_evening_or_night = (now_ams.hour >= 19 or now_ams.hour < 6)
    if not is_evening_or_night:
        return

    try:
        from layer2_calibration.dhw_thermal_model import DhwThermalModel
        dhw_model = DhwThermalModel()
        decision_data = dhw_model.evaluate_night_heating_decision(
            t_current_c=t_live,
            now_dt=now_ams,
            tomorrow_solar_peak_kw=2.5,
            planned_heat_hour=12.5
        )

        comfort_safe = decision_data.get("morning_is_safe", True)
        m_dip = decision_data.get("morning_dip_temp_c", 40.0)
        m_time = decision_data.get("morning_dip_time", "08:30")
        p95_dip = decision_data.get("morning_dip_p95_c", 38.0)
        savings = float(decision_data.get("savings_by_waiting", 0.23))
        chosen_mode = "normal" if comfort_safe else "forced_on"
        decision_state_key = f"{chosen_mode}_{round(m_dip, 0)}"

        now_ts = time.time()
        # Log if state changed or if at least 2 hours have passed since last night decision log
        if _LAST_NIGHT_AUDIT_LOG.get("state") != decision_state_key or (now_ts - _LAST_NIGHT_AUDIT_LOG.get("ts", 0)) >= 7200.0:
            _LAST_NIGHT_AUDIT_LOG["state"] = decision_state_key
            _LAST_NIGHT_AUDIT_LOG["ts"] = now_ts

            reason = "🌙 DHW Nachtbesluit: Wachten op Middagzon (Geen nachtlading nodig)" if comfort_safe else "🌙 DHW Nachtbesluit: Nachtlading Gepland (Comfortzekerheid)"
            explanation = decision_data.get("decision_explanation", "")
            if not explanation:
                if comfort_safe:
                    explanation = f"Het vat daalt vannacht zonder verwarming naar prognose {m_dip}°C om {m_time}u (P95 zware douche: {p95_dip}°C). Ochtendcomfort blijft ruim boven 40°C gewaarborgd. Nachtlading overbodig; wachten op middagzon bespaart ~€{savings:.2f}."
                else:
                    explanation = f"Comfortrisico dreigt: vat daalt naar prognose {m_dip}°C om {m_time}u (<40°C). Een nachtlading naar 50°C is ingepland in het goedkoopste dalkwartier."

            write_hems_annotation(
                event_type="night_decision",
                title=reason,
                description=explanation,
                state_code=chosen_mode,
                power_kw=0.0 if comfort_safe else 1.8,
                target_temp_c=50.0 if not comfort_safe else 0.0,
                savings_eur=savings if comfort_safe else 0.0
            )

            DecisionAuditLogger.log_decision(
                domain="dhw",
                decision_type="night_decision",
                chosen_mode=chosen_mode,
                target_temp_c=50.0 if not comfort_safe else None,
                inputs={
                    "tank_nu_c": round(t_live, 1),
                    "ochtend_dip_c": m_dip,
                    "ochtend_dip_tijd": m_time,
                    "ochtend_dip_p95_c": p95_dip,
                    "comfort_gewaarborgd": comfort_safe,
                    "besparing_wachten_eur": savings
                },
                reason=reason,
                explanation=explanation,
                savings_estimate_eur=savings if comfort_safe else 0.0
            )
    except Exception as e:
        print(f"Warning in evaluate_and_log_night_boiler_decision: {e}")


_LAST_PLAN_LOGS: Dict[str, Any] = {
    "peaks_key": None,
    "cv_key": None,
    "dhw_strategy_key": None,
    "last_ts": 0.0
}

def evaluate_and_log_planner_decisions(plan: Any, frame: Any):
    global _LAST_PLAN_LOGS
    now_ts = time.time()
    now_ams = datetime.now(ZoneInfo("Europe/Amsterdam"))
    today_date = now_ams.strftime("%Y-%m-%d")

    # 1. Dynamic Spitsblokkades (Peak Lockouts) - Evaluated 1x per calendar day when day-ahead prices arrive
    if hasattr(plan, "dynamic_peaks") and plan.dynamic_peaks:
        if _LAST_PLAN_LOGS.get("peaks_date") != today_date:
            _LAST_PLAN_LOGS["peaks_date"] = today_date

            p_names = [f"{p.get('name', 'Spits')} ({p.get('start_time')}–{p.get('end_time')}, max €{p.get('max_price', 0):.2f}/kWh)" for p in plan.dynamic_peaks]
            peaks_desc = ", ".join(p_names)
            title = f"📅 EPEX Spitsblokkades Vastgesteld voor Vandaag ({len(plan.dynamic_peaks)} pieken)"
            desc = f"Beursnoteringen voor {now_ams.strftime('%d-%m-%Y')} verwerkt: {peaks_desc}. Warmtepomp zal tijdens deze uren automatisch worden vergrendeld (SG1) om dure piekafname te vermijden."

            write_hems_annotation(
                event_type="peak_schedule",
                title=title,
                description=desc,
                state_code="planned",
                power_kw=0.0,
                target_temp_c=0.0,
                savings_eur=0.45
            )
            DecisionAuditLogger.log_decision(
                domain="grid_tariff",
                decision_type="peak_detection",
                chosen_mode="planned",
                target_temp_c=None,
                inputs={"datum": today_date, "pieken": peaks_desc, "aantal": len(plan.dynamic_peaks)},
                reason=title,
                explanation=desc,
                savings_estimate_eur=0.45,
                category="DECISION"
            )

    # 2. CV Ruimteverwarming Policy (Space Heating)
    if hasattr(plan, "space_heating_summary") and plan.space_heating_summary:
        sh = plan.space_heating_summary
        cv_state = "summer_lockout" if sh.is_summer_lockout else ("preheat" if sh.preheat_hours > 0 else "modulating")
        if _LAST_PLAN_LOGS.get("cv_key") != cv_state or (now_ts - _LAST_PLAN_LOGS.get("last_ts", 0)) >= 14400.0:
            _LAST_PLAN_LOGS["cv_key"] = cv_state

            if sh.is_summer_lockout:
                title = f"☀️ CV Vloerverwarming: Zomerstop Actief ({sh.mean_outdoor_temp_c:.1f}°C)"
                desc = f"Gemiddelde buitentemperatuur is {sh.mean_outdoor_temp_c:.1f}°C (>= 16,0°C drempel). Ruimteverwarming is uitgeschakeld; warmtepomp blijft 100% beschikbaar voor tapwater."
                mode = "normal"
            elif sh.preheat_hours > 0:
                title = f"♨️ CV Vloerverwarming: Nachtdal Pre-Heat Gepland ({sh.preheat_hours:.1f}u)"
                desc = f"Verwarming laadt {sh.preheat_kwh_th:.1f} kWh thermische buffer in de dekvloer tijdens goedkope nachturen (02:00–06:00). Voorkomt piekafname overdag."
                mode = "advised_on"
            else:
                title = f"♨️ CV Vloerverwarming: Stooklijn Modulatie"
                desc = f"Verwarming volgt reguliere stooklijn (gemiddeld {sh.mean_outdoor_temp_c:.1f}°C buiten)."
                mode = "normal"

            write_hems_annotation(
                event_type="space_heating_policy",
                title=title,
                description=desc,
                state_code=mode,
                power_kw=round(sh.total_electric_kwh / 24.0, 2),
                target_temp_c=20.0,
                savings_eur=0.35 if sh.preheat_hours > 0 else 0.0
            )
            DecisionAuditLogger.log_decision(
                domain="space_heating",
                decision_type="heating_policy",
                chosen_mode=mode,
                target_temp_c=20.0,
                inputs={
                    "buitentemp_gem_c": round(sh.mean_outdoor_temp_c, 1),
                    "zomerstop_actief": sh.is_summer_lockout,
                    "cop_gemiddeld": round(sh.average_cop, 2),
                    "warmtevraag_24u_kwh": round(sh.total_heat_demand_kwh, 1)
                },
                reason=title,
                explanation=desc,
                savings_estimate_eur=0.35 if sh.preheat_hours > 0 else 0.0
            )

    # 3. DHW Daytime Arbitration Logging (Full 24h Traceability)
    if hasattr(plan, "metadata") and isinstance(plan.metadata, dict) and "daytime_arbitrage_audit" in plan.metadata:
        audit = plan.metadata["daytime_arbitrage_audit"]
        if audit:
            last_mode = _LAST_PLAN_LOGS.get("dhw_day_mode")
            cur_mode = audit.get("planned_mode")
            if last_mode != cur_mode or (now_ts - _LAST_PLAN_LOGS.get("last_dhw_day_ts", 0)) >= 3600.0:
                _LAST_PLAN_LOGS["dhw_day_mode"] = cur_mode
                _LAST_PLAN_LOGS["last_dhw_day_ts"] = now_ts

                title = f"♨️ DHW Dagplanning Arbitrage: {audit.get('planned_mode_label')}"
                explanation = audit.get("explanation", "")
                DecisionAuditLogger.log_decision(
                    domain="dhw_boiler",
                    decision_type="daytime_arbitrage",
                    chosen_mode=cur_mode,
                    target_temp_c=audit.get("target_temp_c"),
                    inputs={
                        "situation": audit.get("situation"),
                        "evening_dip_c": audit.get("unheated_evening_dip_c"),
                        "evening_dip_time": audit.get("evening_dip_time"),
                        "selected_path": audit.get("selected_path_id"),
                        "evaluated_paths": audit.get("evaluated_paths")
                    },
                    reason=title,
                    explanation=explanation,
                    savings_estimate_eur=audit.get("savings_eur", 0.0),
                    category="DECISION"
                )




def fetch_recent_telemetry_history(is_15m: bool, base_dt: datetime) -> list:
    """Fetches real historical telemetry for the 1 hour immediately preceding the forecast horizon."""
    num_slots = 4 if is_15m else 1
    step_mins = 15 if is_15m else 60
    start_dt = base_dt - timedelta(minutes=num_slots * step_mins)

    start_utc = start_dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:00Z")
    end_utc = base_dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:00Z")
    bucket = "15m" if is_15m else "1h"

    en_map, tank_map, out_map = {}, {}, {}
    try:
        sec = load_secrets()
        pwd = sec.get("influxdb", {}).get("openhems_db", "") or sec.get("influxdb", {}).get("local_ha_influxdb", "")

        q = f"""
        SELECT mean("solar_w")/1000.0 as solar, mean("total_house_w")/1000.0 as house, mean("unallocated_w")/1000.0 as unalloc, mean("heatpump_w")/1000.0 as hp
        FROM "energy_telemetry" 
        WHERE time >= '{start_utc}' AND time < '{end_utc}'
        GROUP BY time({bucket}) fill(linear);
        SELECT mean("temperature_c") as tank_temp
        FROM "energy_telemetry" 
        WHERE "device_id" = 'dhw_tank' AND time >= '{start_utc}' AND time < '{end_utc}'
        GROUP BY time({bucket}) fill(linear);
        SELECT mean("temperature_c") as outdoor_temp
        FROM "energy_telemetry" 
        WHERE "device_id" = 'outdoor_weather' AND time >= '{start_utc}' AND time < '{end_utc}'
        GROUP BY time({bucket}) fill(linear);
        """
        url = f"http://a0d7b954-influxdb:8086/query?u=openhems&p={pwd}&db=openhems&q={urllib.parse.quote(q)}"
        with urllib.request.urlopen(url, timeout=4) as r:
            res = json.loads(r.read().decode())

        res_list = res.get("results", [])
        if len(res_list) > 0 and "series" in res_list[0]:
            en_map = {row[0]: row[1:] for row in res_list[0]["series"][0].get("values", [])}
        if len(res_list) > 1 and "series" in res_list[1]:
            tank_map = {row[0]: row[1] for row in res_list[1]["series"][0].get("values", [])}
        if len(res_list) > 2 and "series" in res_list[2]:
            out_map = {row[0]: row[1] for row in res_list[2]["series"][0].get("values", [])}
    except Exception as e:
        print(f"Warning fetching history telemetry: {e}")

    # Read live tank temperature from HA to avoid artificial 50°C cliff
    cur_tank_default = 42.8
    cur_room_default = 24.5
    try:
        sm = get_ha_states_map()
        v = float(sm.get("sensor.hc_dhw_temperature_r5t_dhw_tank", {}).get("state", 42.8))
        if 20.0 <= v <= 75.0:
            cur_tank_default = v
        cl_t = sm.get("climate.woonkamer_climate_daikin", {}).get("attributes", {}).get("current_temperature")
        if cl_t is not None and 15.0 <= float(cl_t) <= 35.0:
            cur_room_default = float(cl_t)
        else:
            r_t = float(sm.get("sensor.hc_sensors_temperature_room", {}).get("state", 24.5))
            if 15.0 <= r_t <= 35.0:
                cur_room_default = r_t
    except Exception:
        pass

    history_pts = []
    last_tank = cur_tank_default
    for i in range(num_slots):
        slot_dt = start_dt + timedelta(minutes=step_mins * i)
        slot_utc_str = slot_dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:00Z")

        en_row = en_map.get(slot_utc_str, [0.0, 0.45, 0.45, 0.033])
        solar_kw = round(max(0.0, float(en_row[0] or 0.0)), 3)
        house_kw = round(max(0.0, float(en_row[1] or 0.45)), 3)
        unalloc_kw = round(max(0.0, float(en_row[2] or 0.35)), 3)
        hp_kw = round(max(0.0, float(en_row[3] or 0.033)), 3)

        tank_t = tank_map.get(slot_utc_str)
        if tank_t is not None:
            last_tank = round(float(tank_t), 1)
        tank_t = last_tank

        out_t = out_map.get(slot_utc_str)
        out_t = round(float(out_t), 1) if out_t is not None else 18.0

        dhw_kw = hp_kw if hp_kw > 0.5 else 0.0
        cv_kw = hp_kw if hp_kw > 0.5 and dhw_kw == 0.0 else 0.0

        lbl = slot_dt.strftime("%H:%M" if is_15m else "%H:00")
        k_full = slot_dt.strftime("%Y-%m-%d %H:%M" if is_15m else "%Y-%m-%d %H:00")

        history_pts.append({
            "idx": -num_slots + i,
            "dt": slot_dt,
            "key": k_full,
            "label": lbl,
            "is_history": True,
            "solar_kw": solar_kw,
            "total_house_kw": house_kw,
            "unallocated_kw": unalloc_kw,
            "heatpump_kw": hp_kw,
            "dhw_kw": dhw_kw,
            "heating_kw": cv_kw,
            "tank_temp_c": tank_t,
            "outdoor_temp_c": out_t,
            "indoor_temp_c": cur_room_default
        })

    return history_pts


from integrations.daikin_altherma.actuator import DaikinActuator


def make_daikin_ha_actuator() -> DaikinActuator:
    def switch_caller(switch_name: str, state: bool):
        entity_map = {
            "s10s": "switch.warmtepomp_smart_grid_1_s10s",
            "s11s": "switch.warmtepomp_smart_grid_2_s11s",
            "cv_master": "switch.hc_mode_altherma_on"
        }
        eid = entity_map.get(switch_name)
        if eid:
            service = "turn_on" if state else "turn_off"
            ok = call_ha_service("switch", service, {"entity_id": eid})
            if not ok:
                raise RuntimeError(f"Home Assistant service call mislukt voor {eid} -> {service}")
        return True

    def climate_caller(climate_name: str, temp: float):
        ok = call_ha_service("climate", "set_temperature", {"entity_id": "climate.hc_dhw_dhw_setpoint", "temperature": temp})
        if not ok:
            raise RuntimeError(f"Home Assistant service call mislukt voor climate.hc_dhw_dhw_setpoint -> {temp}°C")
        return True

    return DaikinActuator(switch_caller=switch_caller, climate_caller=climate_caller)


def test_influxdb_connection(url, database, username="", password="", retention="autogen"):
    """Tests connection, authentication, and database availability against InfluxDB."""
    t0 = time.time()
    try:
        clean_url = url.rstrip("/")
        # 1. Ping
        ping_req = urllib.request.Request(f"{clean_url}/ping")
        with urllib.request.urlopen(ping_req, timeout=3) as r:
            if r.status not in (200, 204):
                return {"status": "error", "message": f"Ping mislukt met HTTP code {r.status}"}

        # 2. Query Databases
        q_url = f"{clean_url}/query?" + urllib.parse.urlencode({"q": "SHOW DATABASES"})
        req_q = urllib.request.Request(q_url)
        auth = base64.b64encode(f"{username}:{password}".encode()).decode() if (username and password) else None
        if auth:
            req_q.add_header("Authorization", f"Basic {auth}")

        with urllib.request.urlopen(req_q, timeout=4) as r:
            res = json.loads(r.read().decode("utf-8"))
            dbs = [v[0] for v in res.get("results", [{}])[0].get("series", [{}])[0].get("values", [])]

        # 3. Check specific database series count
        series_count = 0
        target_db = database or "hermes"
        if target_db in dbs:
            q_meas = f"{clean_url}/query?" + urllib.parse.urlencode({"q": f"SHOW MEASUREMENTS ON {target_db}"})
            req_m = urllib.request.Request(q_meas)
            if auth:
                req_m.add_header("Authorization", f"Basic {auth}")
            with urllib.request.urlopen(req_m, timeout=4) as r:
                res_m = json.loads(r.read().decode("utf-8"))
                series_vals = res_m.get("results", [{}])[0].get("series", [{}])[0].get("values", [])
                series_count = len(series_vals)

        latency = round((time.time() - t0) * 1000, 1)
        return {
            "status": "success",
            "message": f"Verbinding geslaagd! Database '{target_db}' bereikbaar ({series_count} meetreeksen).",
            "databases": dbs,
            "series_count": series_count,
            "latency_ms": latency
        }
    except urllib.error.HTTPError as e:
        latency = round((time.time() - t0) * 1000, 1)
        if e.code == 401:
            return {"status": "error", "message": "HTTP 401: Niet geautoriseerd. Controleer gebruikersnaam en wachtwoord.", "latency_ms": latency}
        if e.code == 403:
            return {"status": "error", "message": "HTTP 403: Toegang geweigerd tot deze database voor deze gebruiker.", "latency_ms": latency}
        return {"status": "error", "message": f"HTTP {e.code}: {e.reason}", "latency_ms": latency}
    except Exception as e:
        latency = round((time.time() - t0) * 1000, 1)
        return {"status": "error", "message": f"Fout bij verbinden: {str(e)}", "latency_ms": latency}


def test_mqtt_connection(host, port, username="", password="", client_id=""):
    """Tests TCP connectivity and performs an MQTT 3.1.1 CONNECT handshake."""
    t0 = time.time()
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(3.0)
    try:
        s.connect((host, int(port)))
        cid = (client_id or "open-hems-test").encode("utf-8")
        clean_session = 0x02
        flags = clean_session
        if username:
            flags |= 0x80
        if password:
            flags |= 0x40

        payload = bytes([0, len(cid)]) + cid
        if username:
            ub = username.encode("utf-8")
            payload += bytes([0, len(ub)]) + ub
        if password:
            pb = password.encode("utf-8")
            payload += bytes([0, len(pb)]) + pb

        var_header = b"\x00\x04MQTT\x04" + bytes([flags, 0, 60])
        packet = bytes([0x10, len(var_header) + len(payload)]) + var_header + payload
        s.send(packet)
        resp = s.recv(10)
        s.close()
        latency = round((time.time() - t0) * 1000, 1)

        if len(resp) >= 4 and resp[0] == 0x20:
            rc = resp[3]
            rc_map = {
                0: ("success", f"Verbinding geslaagd met broker ({host}:{port})! (RC 0: OK)"),
                1: ("error", "Protocolversie niet geaccepteerd door broker (RC 1)"),
                2: ("error", "Client ID geweigerd door broker (RC 2)"),
                4: ("error", "Gebruikersnaam of wachtwoord onjuist (RC 4)"),
                5: ("error", "Niet geautoriseerd: broker vereist geldige login (RC 5)")
            }
            st, msg = rc_map.get(rc, ("error", f"Broker return code: {rc}"))
            return {"status": st, "message": msg, "latency_ms": latency}
        return {"status": "error", "message": "Geen geldig MQTT CONNACK pakket ontvangen.", "latency_ms": latency}
    except socket.timeout:
        return {"status": "error", "message": f"Timeout bij verbinden met {host}:{port}.", "latency_ms": round((time.time() - t0) * 1000, 1)}
    except ConnectionRefusedError:
        return {"status": "error", "message": f"Verbinding geweigerd op {host}:{port}. Is de broker actief?", "latency_ms": round((time.time() - t0) * 1000, 1)}
    except Exception as e:
        return {"status": "error", "message": f"Fout: {str(e)}", "latency_ms": round((time.time() - t0) * 1000, 1)}


def ensure_framework_defaults(cfg: dict):
    """Initializes the generic framework defaults if config is fresh."""
    dirty = False

    # Multi-instance InfluxDB connections
    if "influxdb_connections" not in cfg or not cfg["influxdb_connections"]:
        cfg["influxdb_connections"] = [
            {
                "id": "local_ha_influxdb",
                "name": "Lokale Open HEMS InfluxDB (1.8)",
                "type": "influx_v1",
                "url": cfg.get("influxdb", {}).get("url", "http://a0d7b954-influxdb:8086"),
                "database": "openhems",
                "read_database": "openhems",
                "username": "openhems",
                "password": "",
                "retention_policy": "autogen",
                "enabled": True,
                "is_default": True
            }
        ]
        dirty = True
    else:
        for c in cfg["influxdb_connections"]:
            if c.get("id") == "local_ha_influxdb":
                if c.get("database") in ["hermes", "hassio"]:
                    c["database"] = "openhems"
                    dirty = True
                if c.get("username") == "hermes":
                    c["username"] = "openhems"
                    dirty = True

    # Multi-instance MQTT connections
    if "mqtt_connections" not in cfg or not cfg["mqtt_connections"]:
        cfg["mqtt_connections"] = [
            {
                "id": "local_mosquitto",
                "name": "Lokale Mosquitto Broker",
                "type": "standard",
                "host": cfg.get("mqtt", {}).get("host", "core-mosquitto"),
                "port": cfg.get("mqtt", {}).get("port", 1883),
                "base_topic": cfg.get("mqtt", {}).get("base_topic", "openhems"),
                "client_id": cfg.get("mqtt", {}).get("client_id", "open-hems-collector"),
                "username": cfg.get("mqtt", {}).get("username", ""),
                "password": cfg.get("mqtt", {}).get("password", ""),
                "tls": cfg.get("mqtt", {}).get("tls", False),
                "enabled": True,
                "is_default": True
            }
        ]
        dirty = True

    # Keep top-level influxdb and mqtt pointers synchronized
    if "influxdb" not in cfg:
        cfg["influxdb"] = cfg["influxdb_connections"][0]
        dirty = True
    if "mqtt" not in cfg:
        cfg["mqtt"] = cfg["mqtt_connections"][0]
        dirty = True

    if "providers" not in cfg:
        cfg["providers"] = {
            "epex_spot": {
                "id": "epex_spot",
                "name": "EPEX Spot Day-Ahead & Quarter-Hourly Prices",
                "type": "market_prices",
                "url": "https://api.energyzero.net/v1/energyprices",
                "enabled": True
            },
            "open_meteo": {
                "id": "open_meteo",
                "name": "Open-Meteo Solar & Weather Forecast",
                "type": "weather_solar",
                "url": "https://api.open-meteo.com/v1/forecast",
                "enabled": True
            }
        }
        dirty = True

    if "tariffs_list" not in cfg:
        cfg["tariffs_list"] = [
            {
                "id": "powerpeers_dynamic",
                "name": "Powerpeers Dynamisch",
                "provider": "epex_spot",
                "import_markup_eur_kwh": 0.01210,
                "export_markup_eur_kwh": 0.01210,
                "electricity_tax_eur_kwh": 0.11085,
                "fixed_monthly_fee_eur": 6.25,
                "contract_start_date": "2026-09-25",
                "interval": "15m",
                "active": True
            }
        ]
        dirty = True

    # Decoupled Policies
    if "policies" not in cfg or not cfg["policies"]:
        cfg["policies"] = [
            {
                "id": "dhw_thermal_buffer_policy",
                "name": "350L SWW Boiler Buffer Beleid",
                "type": "thermal_buffer",
                "target_devices": ["daikin_heat_pump", "dhw_tank"],
                "parameters": {
                    "storage_volume_liters": 350,
                    "emergency_threshold_c": 38.0,
                    "deadband_reheat_c": 46.0,
                    "target_temperature_c": 50.0,
                    "solar_boost_temperature_c": 60.0,
                    "morning_peak_lockout": True,
                    "evening_peak_lockout": True,
                    "isolate_space_heating_during_dhw": True,
                    "min_run_time_minutes": 20
                }
            },
            {
                "id": "deye_battery_arbitrage_policy",
                "name": "Deye Accu Arbitrage & Zelfconsumptie",
                "type": "battery_arbitrage",
                "target_devices": ["deye_home_battery"],
                "parameters": {
                    "capacity_kwh": 10.0,
                    "roundtrip_efficiency": 0.87,
                    "lcos_depreciation_eur_kwh": 0.0741,
                    "min_price_spread_eur_kwh": 0.115,
                    "solar_surplus_priority": True,
                    "min_soc_pct": 10.0,
                    "max_soc_pct": 95.0,
                    "peak_shaving_threshold_amps": 20.0
                }
            },
            {
                "id": "dishwasher_shiftable_policy",
                "name": "Vaatwasser Dal- & Zonnestart",
                "type": "shiftable_consumer",
                "target_devices": [],
                "parameters": {
                    "duration_minutes": 90,
                    "power_watts": 1200,
                    "can_interrupt": False,
                    "window_start_hour": 8,
                    "window_end_hour": 20,
                    "prefer_solar_surplus": True,
                    "min_solar_surplus_watts": 1500
                }
            }
        ]
        dirty = True

    # Devices (pure hardware)
    if "devices" not in cfg or not cfg["devices"]:
        cfg["devices"] = [
            {
                "id": "main_grid_meter",
                "name": "Hoofdmeter (P1 DSMR)",
                "type": "grid_meter",
                "adapter": "p1_dsmr",
                "capabilities": ["read_power", "read_energy"],
                "ha_power_entity": "sensor.power_production_in_watt_avg",
                "ha_energy_entity": "sensor.energy_consumed_tariff_1",
                "parameters": {"phases": 3, "max_amps": 25.0}
            },
            {
                "id": "rooftop_solar",
                "name": "Zonnepanelen (SolarEdge)",
                "type": "solar_inverter",
                "adapter": "sunspec_modbus",
                "capabilities": ["read_power", "read_energy", "curtail_production"],
                "ha_power_entity": "sensor.zonnepanelen_power_avg_5_minutes",
                "ha_energy_entity": "sensor.daily_energy_production_solar2",
                "parameters": {"peak_power_kw": 5.5, "tilt_deg": 40.0, "azimuth_deg": 225.0}
            },
            {
                "id": "daikin_heat_pump",
                "name": "Daikin Altherma 3 H HT (18 kW)",
                "type": "heat_pump",
                "adapter": "smart_grid_relay",
                "capabilities": ["set_mode", "read_power"],
                "ha_power_entity": "sensor.warmtepomp_power",
                "ha_control_entity": "switch.warmtepomp_smart_grid_1_s10s",
                "parameters": {"compressor_power_kw": 3.0, "min_run_time_minutes": 20}
            },
            {
                "id": "dhw_tank",
                "name": "Warm Tapwatervat (OEG 350L SWW)",
                "type": "thermal_storage",
                "adapter": "temperature_sensor",
                "capabilities": ["read_temperature", "read_energy"],
                "ha_temp_entity": "sensor.hc_dhw_temperature_r5t_dhw_tank",
                "parameters": {"volume_liters": 350}
            },
            {
                "id": "deye_home_battery",
                "name": "Deye Hybride Thuisaccu (10 kW / 10 kWh)",
                "type": "home_battery",
                "adapter": "deye_modbus_tcp",
                "capabilities": ["read_power", "read_soc", "set_power_limit", "set_mode"],
                "ha_power_entity": "sensor.battery_power",
                "parameters": {"capacity_kwh": 10.0, "max_charge_power_w": 5000, "max_discharge_power_w": 5000}
            }
        ]
        dirty = True

    if dirty:
        save_json(CONFIG_FILE, cfg)


EPEX_CACHE_FILE = Path("/config/open_hems_epex_cache.json")
_EPEX_CACHE_DATA: Dict[str, Any] = {}
_LAST_EPEX_POLL_TS = 0.0

def _load_epex_cache():
    global _EPEX_CACHE_DATA
    if not _EPEX_CACHE_DATA and EPEX_CACHE_FILE.exists():
        try:
            with open(EPEX_CACHE_FILE, "r", encoding="utf-8") as f:
                _EPEX_CACHE_DATA = json.load(f)
        except Exception:
            _EPEX_CACHE_DATA = {}

def _save_epex_cache():
    try:
        tmp = f"{EPEX_CACHE_FILE}.tmp.{os.getpid()}"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(_EPEX_CACHE_DATA, f, indent=2)
        os.replace(tmp, EPEX_CACHE_FILE)
    except Exception as e:
        print(f"Warning saving EPEX cache: {e}")

def get_epex_tariffs_cached(is_15m: bool = True) -> Tuple[List[Dict[str, Any]], Dict[str, float], Dict[str, float]]:
    """
    Authoritative EPEX Day-Ahead price caching & polling manager:
    - Today's prices are served instantly from disk/memory cache.
    - Tomorrow's prices are polled strictly between 13:00 and 15:00 every 15 minutes.
    - Once tomorrow's prices are retrieved, polling stops completely until tomorrow 13:00.
    - Returns (raw_price_list, map_all_in, map_base).
    """
    global _LAST_EPEX_POLL_TS, _EPEX_CACHE_DATA
    _load_epex_cache()
    now_ams = datetime.now(ZoneInfo("Europe/Amsterdam"))
    today_key = now_ams.strftime("%Y-%m-%d")
    tomorrow_key = (now_ams + timedelta(days=1)).strftime("%Y-%m-%d")
    cache_type = "15m" if is_15m else "1h"
    interval_str = "INTERVAL_QUARTER" if is_15m else "INTERVAL_HOUR"

    # Purge keys older than yesterday to keep cache lean
    yesterday_key = (now_ams - timedelta(days=1)).strftime("%Y-%m-%d")
    _EPEX_CACHE_DATA = {k: v for k, v in _EPEX_CACHE_DATA.items() if k >= yesterday_key}

    now_ts = time.time()
    hour = now_ams.hour

    need_today = (today_key not in _EPEX_CACHE_DATA or cache_type not in _EPEX_CACHE_DATA[today_key])
    has_tomorrow = (tomorrow_key in _EPEX_CACHE_DATA and cache_type in _EPEX_CACHE_DATA[tomorrow_key] and len(_EPEX_CACHE_DATA[tomorrow_key][cache_type].get("all_in", [])) >= (96 if is_15m else 24))
    need_tomorrow = False
    if not has_tomorrow:
        if 13 <= hour < 15:
            if (now_ts - _LAST_EPEX_POLL_TS) >= 900.0:  # Every 15 min between 13:00 and 15:00
                need_tomorrow = True
        elif hour >= 15:
            if (now_ts - _LAST_EPEX_POLL_TS) >= 3600.0: # Every 60 min after 15:00 until published
                need_tomorrow = True

    dates_to_fetch = []
    if need_today:
        dates_to_fetch.append((today_key, now_ams.strftime("%d-%m-%Y")))
    if need_tomorrow:
        dates_to_fetch.append((tomorrow_key, (now_ams + timedelta(days=1)).strftime("%d-%m-%Y")))

    if dates_to_fetch:
        _LAST_EPEX_POLL_TS = now_ts
        dirty = False
        for date_key, d_str in dates_to_fetch:
            try:
                url_p = f"https://public.api.energyzero.nl/public/v1/prices?energyType=ENERGY_TYPE_ELECTRICITY&date={d_str}&interval={interval_str}"
                req_p = urllib.request.Request(url_p, headers={"User-Agent": "OpenHEMS/1.0"})
                with urllib.request.urlopen(req_p, timeout=6) as r_p:
                    res_p = json.loads(r_p.read().decode())
                    all_in_items = res_p.get("all_in_with_vat", [])
                    base_items = res_p.get("base", [])
                    if len(all_in_items) >= (96 if is_15m else 24):
                        _EPEX_CACHE_DATA.setdefault(date_key, {})[cache_type] = {
                            "all_in": [{"start": it["start"], "val": float(it.get("price", {}).get("value", 0.25))} for it in all_in_items],
                            "base": [{"start": it["start"], "val": float(it.get("price", {}).get("value", 0.12))} for it in base_items]
                        }
                        dirty = True
                        if date_key == tomorrow_key:
                            print(f"[Open HEMS] EPEX Day-Ahead prijzen voor morgen ({tomorrow_key}) binnengehaald ({len(all_in_items)} slots). Polling stopt tot morgen 13:00.")
            except Exception as e:
                print(f"[Open HEMS] Polling EPEX tarieven voor {d_str} gaf nog geen data: {e}")
        if dirty:
            _save_epex_cache()

    raw_prices = []
    map_all_in = {}
    map_base = {}

    for d_k in [today_key, tomorrow_key]:
        if d_k in _EPEX_CACHE_DATA and cache_type in _EPEX_CACHE_DATA[d_k]:
            blob = _EPEX_CACHE_DATA[d_k][cache_type]
            for it in blob.get("all_in", []):
                dt = datetime.fromisoformat(it["start"].replace("Z", "+00:00")).astimezone(ZoneInfo("Europe/Amsterdam"))
                k_dt = dt.strftime("%Y-%m-%d %H:%M" if is_15m else "%Y-%m-%d %H:00")
                p_val = round(it["val"], 4)
                map_all_in[k_dt] = p_val
                raw_prices.append({"dt": dt, "price": p_val})
            for it in blob.get("base", []):
                dt = datetime.fromisoformat(it["start"].replace("Z", "+00:00")).astimezone(ZoneInfo("Europe/Amsterdam"))
                k_dt = dt.strftime("%Y-%m-%d %H:%M" if is_15m else "%Y-%m-%d %H:00")
                map_base[k_dt] = round(it["val"], 4)

    return raw_prices, map_all_in, map_base


_LAST_CANONICAL_PLAN_TIME = None

def ensure_active_canonical_plan(force_refresh=False):
    """
    Ensures an authoritative, synchronized CanonicalDispatchPlan is cached in PlanStore.
    Re-plans every 60 seconds or when explicitly forced.
    """
    global _LAST_CANONICAL_PLAN_TIME
    now_ams = datetime.now(ZoneInfo("Europe/Amsterdam"))
    store = get_plan_store()
    current_plan = store.get_plan()

    if not force_refresh and current_plan is not None and _LAST_CANONICAL_PLAN_TIME is not None:
        if (now_ams - _LAST_CANONICAL_PLAN_TIME).total_seconds() < 60:
            return current_plan

    # 1. Fetch EPEX prices from dedicated 24h Day-Ahead cache
    raw_prices, _, _ = get_epex_tariffs_cached(is_15m=True)

    # 2. Fetch Solar & Weather Forecast for Culemborg
    cfg = load_json(CONFIG_FILE)
    s_cfg = cfg.get("solar", {})
    s_kwp = float(s_cfg.get("kwp", 5.76))
    s_inv = float(s_cfg.get("inverter_max_w", 5500)) / 1000.0
    s_tilt = float(s_cfg.get("tilt_degrees", 34))
    s_az = float(s_cfg.get("azimuth_degrees", 225))
    s_cal = float(s_cfg.get("calibration_factor", 1.18))
    use_fs = s_cfg.get("forecast_provider", "forecast_solar") == "forecast_solar"

    raw_solar = []
    if use_fs:
        try:
            from layer1_data_collection.forecast_solar import ForecastSolarProvider
            fs_prov = ForecastSolarProvider(
                lat=51.9537, lon=5.2320, tilt=s_tilt,
                azimuth_deg_south=45.0,
                kwp=s_kwp, inverter_max_kw=s_inv,
                calibration_factor=s_cal
            )
            raw_solar = fs_prov.get_calibrated_quarter_slots(now_ams, horizon_slots=96, step_mins=15)
        except Exception as e_fs:
            print(f"Warning fetching Forecast.Solar: {e_fs}")

    has_solar_plan = any(s.get("solar_kw", 0.0) > 0.05 for s in raw_solar)
    if not has_solar_plan:
        raw_solar = []

    raw_weather = []
    try:
        url_m = "https://api.open-meteo.com/v1/forecast?latitude=51.9537&longitude=5.2320&hourly=temperature_2m,shortwave_radiation,wind_speed_10m&timezone=Europe%2FAmsterdam&forecast_days=2"
        req_m = urllib.request.Request(url_m, headers={"User-Agent": "OpenHEMS/1.0"})
        with urllib.request.urlopen(req_m, timeout=5) as r_m:
            m_data = json.loads(r_m.read().decode())
            m_times = m_data.get("hourly", {}).get("time", [])
            m_rads = m_data.get("hourly", {}).get("shortwave_radiation", [])
            m_temps = m_data.get("hourly", {}).get("temperature_2m", [])
            s_eff = float(s_cfg.get("efficiency_factor", 0.88))

            for t, rad, tmp in zip(m_times, m_rads, m_temps):
                k_t = t.replace('T', ' ')[:13] + ':00'
                dt_h = datetime.strptime(k_t, "%Y-%m-%d %H:%M").replace(tzinfo=ZoneInfo("Europe/Amsterdam"))
                if not has_solar_plan:
                    poa_kw = calculate_poa_solar_kw(dt_h, float(rad), kwp=s_kwp, tilt_deg=s_tilt, azimuth_deg=s_az, inverter_limit_kw=s_inv, eff=s_eff)
                    raw_solar.append({"dt": dt_h, "solar_kw": poa_kw})
                raw_weather.append({"dt": dt_h, "temperature": float(tmp)})
    except Exception as e_w:
        print(f"Warning fetching Open-Meteo in ensure_active_canonical_plan: {e_w}")

    # 3. Read current tank and room temperature
    cur_dhw = 48.0
    cur_room = 20.0
    cur_target_room = 20.0
    last_hw_time = None
    try:
        states_map = get_ha_states_map()
        t_tank = float(states_map.get("sensor.hc_dhw_temperature_r5t_dhw_tank", {}).get("state", 0.0))
        if 20.0 <= t_tank <= 75.0:
            cur_dhw = t_tank
            last_hw_time = datetime.now(ZoneInfo("Europe/Amsterdam"))
        
        # Read live room temperature and target setpoint from Daikin climate entity
        daikin_cl = states_map.get("climate.woonkamer_climate_daikin", {})
        if daikin_cl:
            c_temp = daikin_cl.get("attributes", {}).get("current_temperature")
            if c_temp is not None:
                try:
                    v_t = float(c_temp)
                    if 15.0 <= v_t <= 35.0:
                        cur_room = v_t
                except (ValueError, TypeError):
                    pass
            sp_temp = daikin_cl.get("attributes", {}).get("temperature") or daikin_cl.get("attributes", {}).get("target_temp_low")
            if sp_temp is not None:
                try:
                    v_sp = float(sp_temp)
                    if 15.0 <= v_sp <= 25.0:
                        cur_target_room = v_sp
                except (ValueError, TypeError):
                    pass
        if cur_room == 20.0:
            for s_id in ["sensor.hc_sensors_temperature_room", "sensor.sco2_staging_01_woonkamer_co2_temperature", "sensor.woonkamer_temperatuur"]:
                s_val = states_map.get(s_id, {}).get("state")
                if s_val is not None:
                    try:
                        v_s = float(s_val)
                        if 15.0 <= v_s <= 35.0:
                            cur_room = v_s
                            break
                    except (ValueError, TypeError):
                        pass
    except Exception as e_st:
        print(f"Warning reading HA states in ensure_active_canonical_plan: {e_st}")

    if last_hw_time is None:
        try:
            sec = load_secrets()
            pw = sec.get("influx_password", "")
            if pw:
                query = 'SELECT last("temperature") FROM "daikin_heat_pump" WHERE "mode" = \'dhw\' AND time > now() - 2h'
                q_url = f"http://a0d7b954-influxdb:8086/query?u=openhems&p={pw}&db=openhems&q={urllib.parse.quote(query)}"
                with urllib.request.urlopen(q_url, timeout=3) as r:
                    res = json.loads(r.read().decode())
                    series = res.get("results", [{}])[0].get("series", [])
                    if series:
                        cur_dhw = float(series[0]["values"][0][1])
                        last_hw_time = datetime.now(ZoneInfo("Europe/Amsterdam"))
        except Exception:
            pass

    # 4. Extract unallocated profile
    grid_96 = []
    if GLOBAL_MODEL and GLOBAL_MODEL.profile:
        grid_96 = GLOBAL_MODEL.profile.get("profile_96_quarters", [])

    # 5. Sanitize telemetry
    frame = TelemetrySanitizer.sanitize(
        now=now_ams,
        raw_prices=raw_prices,
        raw_solar=raw_solar,
        raw_weather=raw_weather,
        raw_unallocated_matrix=grid_96,
        current_dhw_temp=cur_dhw,
        current_room_temp=cur_room,
        target_room_temp=cur_target_room,
        last_hardware_reading_time=last_hw_time,
        horizon_slots=96,
        step_mins=15
    )

    # 5.5. Determine past lockout duration and dwell time from live HA states
    past_lockout_mins = 0
    mins_since_last_lockout = 999
    try:
        states_map = get_ha_states_map()
        s10_st = states_map.get("switch.warmtepomp_smart_grid_1_s10s", {})
        s11_st = states_map.get("switch.warmtepomp_smart_grid_2_s11s", {})
        s10_on = (s10_st.get("state") == "on")
        s11_on = (s11_st.get("state") == "on")
        lc_str = s11_st.get("last_changed")
        if lc_str:
            lc_dt = datetime.fromisoformat(lc_str.replace("Z", "+00:00"))
            now_utc = datetime.now(timezone.utc)
            diff_mins = max(0.0, (now_utc - lc_dt).total_seconds() / 60.0)
            if not s10_on and s11_on:
                past_lockout_mins = int(diff_mins)
                mins_since_last_lockout = 0
            else:
                past_lockout_mins = 0
                mins_since_last_lockout = int(diff_mins)
    except Exception as e_lk:
        print(f"Warning determining lockout history: {e_lk}")

    # 6. Plan & Publish
    plan = CentralPlanner.plan(
        frame,
        current_dhw_temp=cur_dhw,
        past_continuous_lockout_mins=past_lockout_mins,
        mins_since_last_lockout=mins_since_last_lockout
    )
    _LAST_CANONICAL_PLAN_TIME = now_ams
    try:
        evaluate_and_log_planner_decisions(plan, frame)
    except Exception as e_pld:
        print(f"Warning in evaluate_and_log_planner_decisions: {e_pld}")
    return plan


class HemsApiHandler(BaseHTTPRequestHandler):

    def _send_json(self, data, status=200):
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
        self.wfile.write(json.dumps(data, indent=2).encode("utf-8"))

    def _read_json_body(self):
        try:
            length = int(self.headers.get("Content-Length", 0))
            if length > 0:
                body = self.rfile.read(length).decode("utf-8")
                return json.loads(body)
        except Exception as e:
            print(f"Error parsing JSON body: {e}")
        return {}

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    # =========================================================================
    # MODULAR HTTP ROUTERS DELEGATION (Fase 2)
    # =========================================================================
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path.rstrip("/")
        if not path:
            path = "/"
        query_params = urllib.parse.parse_qs(parsed.query)

        # 1. Dispatch to modular domain routers
        if routes_analytics.handle_get(self, path, query_params):
            return
        if routes_model.handle_get(self, path, query_params):
            return
        if routes_schedule.handle_get(self, path, query_params):
            return
        if routes_system.handle_get(self, path, query_params):
            return

        # 2. Static file serving from web/
        if path.startswith("/static/") or path.startswith("/web/"):
            rel_path = path.replace("/static/", "").replace("/web/", "")
            file_path = (WEB_DIR / rel_path).resolve()
            if str(file_path).startswith(str(WEB_DIR.resolve())) and file_path.is_file():
                self._serve_static_file(file_path)
                return

        # 3. HTML Single Page Application fallback
        self._serve_spa()

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path.rstrip("/")
        body = self._read_json_body()

        if routes_analytics.handle_post(self, path, body):
            return
        if routes_model.handle_post(self, path, body):
            return
        if routes_schedule.handle_post(self, path, body):
            return
        if routes_system.handle_post(self, path, body):
            return

        self._send_json({"error": "Endpoint not found"}, 404)

    def do_PUT(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path.rstrip("/")
        body = self._read_json_body()

        if routes_schedule.handle_put(self, path, body):
            return
        if routes_system.handle_put(self, path, body):
            return

        self._send_json({"error": "Endpoint not found"}, 404)

    def do_DELETE(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path.rstrip("/")

        if routes_system.handle_delete(self, path):
            return

        self._send_json({"error": "Endpoint not found"}, 404)

    # =========================================================================
    # HTML SINGLE PAGE APPLICATION & STATIC ASSETS (Served from web/)
    # =========================================================================
    def _serve_spa(self):
        try:
            if INDEX_HTML_PATH.exists():
                with open(INDEX_HTML_PATH, "rb") as f:
                    content = f.read()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(content)))
                self.send_header("Cache-Control", "no-cache")
                self.end_headers()
                self.wfile.write(content)
            else:
                self._send_json({"error": f"web/index.html not found at {INDEX_HTML_PATH}"}, 404)
        except Exception as e:
            self._send_json({"error": str(e)}, 500)

    def _serve_static_file(self, file_path: Path):
        try:
            import mimetypes
            ctype, _ = mimetypes.guess_type(str(file_path))
            ctype = ctype or "application/octet-stream"
            with open(file_path, "rb") as f:
                content = f.read()
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Cache-Control", "public, max-age=3600")
            self.end_headers()
            self.wfile.write(content)
        except Exception as e:
            self._send_json({"error": str(e)}, 500)



class HemsMqttSubscriberThread(threading.Thread):
    """
    Background subscriber thread connecting directly to MQTT broker (Mosquitto)
    using configured credentials. Ingests high-frequency Modbus (MBMD) and device streams
    into an in-memory cache for zero-latency Layer 1 sampling.
    """
    def __init__(self, host="core-mosquitto", port=1883, user="openhems", pwd=""):
        super().__init__(daemon=True, name="HemsMqttSubscriber")
        self.host = host
        self.port = int(port)
        self.user = user
        self.pwd = pwd
        self.cache = {}
        self.running = True
        self.connected = False
        self.last_msg_time = 0

    def run(self):
        while self.running:
            try:
                # Reload secrets if password was empty
                if not self.pwd:
                    sec = load_secrets()
                    self.pwd = sec.get("mqtt", {}).get("local_mosquitto") or sec.get("mqtt", {}).get("openhems_mqtt") or ""

                s = socket.socket()
                s.settimeout(10)
                s.connect((self.host, self.port))

                proto = b"MQTT"
                flags = 0x02
                if self.user: flags |= 0x80
                if self.pwd: flags |= 0x40
                var_h = bytearray([0, 4]) + proto + bytearray([4, flags, 0, 60])
                payload = bytearray([0, 15]) + b"openhems-stream"
                if self.user:
                    u_b = self.user.encode()
                    payload += bytearray([0, len(u_b)]) + u_b
                if self.pwd:
                    p_b = self.pwd.encode()
                    payload += bytearray([0, len(p_b)]) + p_b

                pkt = bytearray([0x10, len(var_h) + len(payload)]) + var_h + payload
                s.sendall(pkt)
                connack = s.recv(4)
                if not (len(connack) >= 4 and connack[3] == 0):
                    self.connected = False
                    time.sleep(5)
                    continue

                self.connected = True
                # Subscribe to mbmd/# and openhems/#
                for sub_t in [b"mbmd/#", b"openhems/#", b"P1P2/#"]:
                    sub_pkt = bytearray([0x82, 5 + len(sub_t), 0, 1, 0, len(sub_t)]) + sub_t + bytearray([0])
                    s.sendall(sub_pkt)
                    s.recv(5)

                buf = bytearray()
                s.settimeout(2.0)
                while self.running:
                    try:
                        chunk = s.recv(2048)
                        if not chunk: break
                        buf.extend(chunk)
                        while len(buf) > 2:
                            pkt_type = buf[0] >> 4
                            if pkt_type == 3: # PUBLISH
                                rem = buf[1]
                                if len(buf) >= 2 + rem:
                                    data = buf[2:2+rem]
                                    buf = buf[2+rem:]
                                    t_len = (data[0] << 8) | data[1]
                                    top = data[2:2+t_len].decode("utf-8", errors="ignore")
                                    val_str = data[2+t_len:].decode("utf-8", errors="ignore")
                                    try:
                                        self.cache[top] = float(val_str)
                                    except ValueError:
                                        self.cache[top] = val_str
                                    self.last_msg_time = time.time()
                                else: break
                            else:
                                buf = buf[1:]
                    except socket.timeout:
                        continue
            except Exception as e:
                self.connected = False
                time.sleep(5)


class HemsBackgroundCollector(threading.Thread):
    """
    Continuous background collector for Layer 1.
    Samples all configured devices every 10s into an in-memory window accumulator.
    Averages values over a 60-second tumble window to eliminate spikes and noise.
    Flushes clean, noise-filtered 1-minute averages to the dedicated openhems InfluxDB.
    """
    def __init__(self, sample_interval_seconds=10, flush_window_seconds=60):
        super().__init__(daemon=True, name="HemsBackgroundCollector")
        self.sample_interval = sample_interval_seconds
        self.flush_window = flush_window_seconds
        self.running = True
        self.last_write_status = "idle"
        self.total_points_written = 0
        self._lock = threading.Lock()
        self._accumulator = {}
        self._last_flush_time = time.time()
        self.sample_count_in_window = 0
        self.last_flush_iso = "Zojuist gestart"
        self.live_hp_disagg = None
        self.mqtt_sub = HemsMqttSubscriberThread()
        self.mqtt_sub.start()
        self.last_actuation = {}
        self.live_balance = {
            "p1_import_w": 0.0,
            "p1_export_w": 0.0,
            "net_grid_w": 0.0,
            "solar_w": 0.0,
            "heatpump_w": 0.0,
            "battery_w": 0.0,
            "direct_solar_w": 0.0,
            "total_house_w": 0.0,
            "unallocated_w": 0.0
        }

    def run(self):
        print(f"[Open HEMS Collector & Dispatcher] Started 60s Loop (Sample: {self.sample_interval}s, Flush & Dispatch: {self.flush_window}s)")
        time.sleep(3)
        while self.running:
            try:
                self.sample_devices()
                now = time.time()
                if now - self._last_flush_time >= self.flush_window:
                    self.flush_window_to_influx()
                    self._last_flush_time = now
                    # Layer 4 Active Live Dispatch Execution
                    self.execute_live_dispatch()
            except Exception as e:
                print(f"[Open HEMS Collector] Error in loop: {e}")
            time.sleep(self.sample_interval)

    def execute_live_dispatch(self):
        """
        Active Layer 4 Dispatch Execution:
        Takes the active CanonicalDispatchPlan, evaluates opportunistic mergers,
        and enforces physical relay & setpoint actuation via DaikinActuator.
        """
        global _LAST_LOGGED_ACTUATION
        try:
            plan = ensure_active_canonical_plan()
            if not plan or not plan.slots:
                return

            states_map = get_ha_states_map()
            dhw_st = states_map.get("sensor.hc_dhw_temperature_r5t_dhw_tank", {})
            try:
                t_live = float(dhw_st.get("state", 50.0))
            except (ValueError, TypeError):
                t_live = 50.0

            wp_st = states_map.get("sensor.warmtepomp_power", {})
            try:
                wp_power_val = float(wp_st.get("state", 0.0))
            except (ValueError, TypeError):
                wp_power_val = 0.0

            cv_st = states_map.get("switch.hc_mode_altherma_on", {})
            cv_active = (cv_st.get("state") == "on")

            # 1. Run opportunistic run merger (e.g. if showering occurred)
            merge_res = evaluate_and_apply_dhw_run_merger(plan, t_live)
            evaluate_and_log_night_boiler_decision(plan, t_live)

            # 2. Get current slot mode
            cur_slot = plan.slots[0]
            mode_to_execute = cur_slot.mode_code

            # Determine continuous lockout duration from HA state
            cur_lockout_mins = 0.0
            s10_st = states_map.get("switch.warmtepomp_smart_grid_1_s10s", {})
            s11_st = states_map.get("switch.warmtepomp_smart_grid_2_s11s", {})
            s10_on = (s10_st.get("state") == "on")
            s11_on = (s11_st.get("state") == "on")
            if not s10_on and s11_on:
                lc_str = s11_st.get("last_changed")
                if lc_str:
                    try:
                        lc_dt = datetime.fromisoformat(lc_str.replace("Z", "+00:00"))
                        cur_lockout_mins = max(0.0, (datetime.now(timezone.utc) - lc_dt).total_seconds() / 60.0)
                    except Exception:
                        pass

            # 3. Instantiate DaikinActuator and execute mode
            actuator = make_daikin_ha_actuator()
            target_t = 60.0 if mode_to_execute in ["max_on", "forced_solar_boost_60"] else (50.0 if mode_to_execute in ["forced_on", "forced_night_50"] else None)
            res = actuator.execute_mode(
                requested_mode=mode_to_execute,
                current_cv_switch_state=cv_active,
                target_temp=target_t,
                current_continuous_lockout_mins=cur_lockout_mins
            )

            # 4. Check if actuation succeeded or failed
            if not res.success:
                log_technical_error(
                    domain="hardware",
                    event_type="actuation_failed",
                    reason=f"❌ Schakelfout: Actuatie {mode_to_execute} Mislukt",
                    explanation=f"DaikinActuator kon de gewenste stand '{mode_to_execute}' niet doorvoeren naar Home Assistant: {res.error_message}",
                    inputs={
                        "mode_gevraagd": mode_to_execute,
                        "foutmelding": res.error_message,
                        "s10s_doel": res.command.s10s_relay_on,
                        "s11s_doel": res.command.s11s_relay_on,
                        "cv_doel": res.command.cv_master_switch_on
                    },
                    category="ERROR"
                )
            else:
                # 5. Audit Log Hardware Actuation & verify HA state confirmation
                curr_s10s = (states_map.get("switch.warmtepomp_smart_grid_1_s10s", {}).get("state") == "on")
                curr_s11s = (states_map.get("switch.warmtepomp_smart_grid_2_s11s", {}).get("state") == "on")
                curr_cv = (states_map.get("switch.hc_mode_altherma_on", {}).get("state") == "on")

                has_switched = (
                    res.command.s10s_relay_on != curr_s10s or
                    res.command.s11s_relay_on != curr_s11s or
                    res.command.cv_master_switch_on != curr_cv
                )

                if has_switched:
                    time.sleep(0.5)
                    fresh_states = get_ha_states_map()
                    act_s10s = (fresh_states.get("switch.warmtepomp_smart_grid_1_s10s", {}).get("state") == "on")
                    act_s11s = (fresh_states.get("switch.warmtepomp_smart_grid_2_s11s", {}).get("state") == "on")
                    act_cv = (fresh_states.get("switch.hc_mode_altherma_on", {}).get("state") == "on")

                    unconfirmed = []
                    if res.command.s10s_relay_on != act_s10s:
                        unconfirmed.append(f"S10S (doel {'AAN' if res.command.s10s_relay_on else 'UIT'}, is {'AAN' if act_s10s else 'UIT'})")
                    if res.command.s11s_relay_on != act_s11s:
                        unconfirmed.append(f"S11S (doel {'AAN' if res.command.s11s_relay_on else 'UIT'}, is {'AAN' if act_s11s else 'UIT'})")
                    if res.command.cv_master_switch_on != act_cv:
                        unconfirmed.append(f"CV Master (doel {'AAN' if res.command.cv_master_switch_on else 'UIT'}, is {'AAN' if act_cv else 'UIT'})")

                    if unconfirmed:
                        log_technical_error(
                            domain="hardware",
                            event_type="actuation_unconfirmed",
                            reason="❌ Schakeling Niet Bevestigd door Home Assistant",
                            explanation=f"Open HEMS heeft de relais omgezet voor {mode_to_execute}, maar Home Assistant bevestigt de toestand niet: {', '.join(unconfirmed)}.",
                            inputs={
                                "mode_gevraagd": mode_to_execute,
                                "onbevestigd": unconfirmed,
                                "s10s_werkelijk": act_s10s,
                                "s11s_werkelijk": act_s11s,
                                "cv_werkelijk": act_cv
                            },
                            category="ERROR"
                        )
                    else:
                        mode_titles = {
                            "normal": "⚙️ Smart Grid Relais: Terug naar Ruststand (SG2)",
                            "max_on": "⚡ Smart Grid Relais: DHW Zonnebuffer 60°C (SG4)",
                            "forced_on": "🚿 Smart Grid Relais: DHW Basislading 50°C (SG4)",
                            "advised_on": "♨️ Smart Grid Relais: Pre-Heat Vloerbuffer (SG3)",
                            "forced_off": "🚫 Smart Grid Relais: Spitsblokkade (SG1)",
                            "advised_off": "⏸️ Smart Grid Relais: Gereduceerde Modulatie (SG1)"
                        }
                        act_title = mode_titles.get(res.effective_mode, f"⚙️ Smart Grid Relais: {res.effective_mode}")
                        act_desc = f"Smart Grid relais omgezet: S10S={'AAN' if res.command.s10s_relay_on else 'UIT'}, S11S={'AAN' if res.command.s11s_relay_on else 'UIT'}, CV Master={'AAN' if res.command.cv_master_switch_on else 'UIT'}."
                        if res.downgrade_reason:
                            act_desc += f" (Veiligheidsinterlock: {res.downgrade_reason})"
                        elif target_t:
                            act_desc += f" Tapwater setpoint: {target_t}°C."

                        write_hems_annotation(
                            event_type="hardware_actuation",
                            title=act_title,
                            description=act_desc,
                            state_code=res.effective_mode,
                            power_kw=round(wp_power_val / 1000.0, 2),
                            target_temp_c=target_t or 0.0,
                            savings_eur=0.0
                        )
                        DecisionAuditLogger.log_decision(
                            domain="hardware",
                            decision_type="live_actuation",
                            chosen_mode=res.effective_mode,
                            target_temp_c=target_t,
                            inputs={
                                "s10s": res.command.s10s_relay_on,
                                "s11s": res.command.s11s_relay_on,
                                "cv_master": res.command.cv_master_switch_on,
                                "effective_mode": res.effective_mode,
                                "tank_temp_c": round(t_live, 1),
                                "downgrade_reason": res.downgrade_reason
                            },
                            reason=act_title,
                            explanation=act_desc,
                            savings_estimate_eur=0.0,
                            category="ACTION"
                        )

            self.last_actuation = {
                "timestamp": datetime.now(AMS_TZ).isoformat(),
                "slot_time": cur_slot.time_label,
                "requested_mode": mode_to_execute,
                "effective_mode": res.effective_mode,
                "downgrade_reason": res.downgrade_reason,
                "s10s": res.command.s10s_relay_on,
                "s11s": res.command.s11s_relay_on,
                "cv_master": res.command.cv_master_switch_on,
                "target_dhw_c": res.command.target_dhw_temp_c,
                "success": res.success
            }
            print(f"[Open HEMS Dispatcher] Live actuation: {mode_to_execute} -> {res.effective_mode} (target {target_t}°C, S10S={res.command.s10s_relay_on}, S11S={res.command.s11s_relay_on}, CV={res.command.cv_master_switch_on})", flush=True)
        except Exception as e:
            print(f"[Open HEMS Dispatcher] Error executing live dispatch: {e}", flush=True)
            time.sleep(self.sample_interval)

    def sample_devices(self):
        cfg = load_json(CONFIG_FILE)
        ha_states = get_ha_states_map()
        if not ha_states:
            return

        def get_val_w(eid, dev=None):
            """Returns value converted to Watt deterministically via canonical normalization."""
            st_obj = ha_states.get(eid, {})
            val_raw = st_obj.get("state")
            unit = st_obj.get("unit") or st_obj.get("attributes", {}).get("unit_of_measurement", "")
            return normalize_power_reading(val_raw, unit=unit, device_cfg=dev or {})

        def get_val_raw(eid):
            st_obj = ha_states.get(eid, {})
            val_raw = st_obj.get("state")
            try:
                return float(val_raw)
            except (ValueError, TypeError):
                return None

        with self._lock:
            for dev in cfg.get("devices", []):
                dev_id = dev.get("id")
                dev_type = dev.get("type", "generic")
                src_type = dev.get("source_type", "homeassistant")

                # P1 Grid Meter (converts kW to W)
                if dev_type in ["grid_meter", "p1_meter"]:
                    p_imp = get_val_w(dev.get("ha_power_entity", "sensor.power_consumption"))
                    p_exp = get_val_w(dev.get("parameters", {}).get("production_entity", "sensor.power_production"))
                    if p_imp is not None:
                        k = f"energy_telemetry|{dev_id}|grid_meter|IMPORT|{src_type}|ELECTRICITY"
                        entry = self._accumulator.setdefault(k, {"sum": 0.0, "count": 0, "type": "power_w"})
                        entry["sum"] += p_imp
                        entry["count"] += 1
                    if p_exp is not None:
                        k = f"energy_telemetry|{dev_id}|grid_meter|EXPORT|{src_type}|ELECTRICITY"
                        entry = self._accumulator.setdefault(k, {"sum": 0.0, "count": 0, "type": "power_w"})
                        entry["sum"] += p_exp
                        entry["count"] += 1

                # Solar PV / Inverter (Direct MQTT MBMD / HA Fallback)
                elif dev_type in ["solar_pv", "solar_inverter", "solar"]:
                    p_sol = None
                    if src_type == "mqtt":
                        t_pow = dev.get("mqtt_power_topic", "mbmd/inepro1-103/Power")
                        if t_pow in self.mqtt_sub.cache:
                            p_sol = abs(float(self.mqtt_sub.cache[t_pow]))
                    if p_sol is None:
                        sol_eid = dev.get("ha_power_entity") or "sensor.zonnepanelen_power"
                        p_sol = get_val_w(sol_eid)
                        if p_sol is None:
                            p_sol = get_val_w("sensor.zonnepanelen_power_avg_5_minutes")
                        if p_sol is not None:
                            p_sol = abs(p_sol)

                    if p_sol is not None:
                        k = f"energy_telemetry|{dev_id}|solar_pv|GENERATION|{src_type}|ELECTRICITY"
                        entry = self._accumulator.setdefault(k, {"sum": 0.0, "count": 0, "type": "power_w"})
                        entry["sum"] += p_sol
                        entry["count"] += 1

                # Heat Pump (Direct MQTT MBMD + Site Adapter P1P2 Disaggregation)
                elif dev_type == "heat_pump":
                    p_hp = None
                    if src_type == "mqtt":
                        t_pow = dev.get("mqtt_power_topic", "mbmd/inepro1-102/Power")
                        if t_pow in self.mqtt_sub.cache:
                            p_hp = float(self.mqtt_sub.cache[t_pow])
                    if p_hp is None:
                        p_hp = get_val_w(dev.get("ha_power_entity", "sensor.warmtepomp_power"))

                    if p_hp is not None:
                        # Apply Site-Specific Daikin P1P2 State Classifier
                        disagg = DaikinP1P2StateClassifier.classify(
                            total_power_w=p_hp,
                            mqtt_cache=self.mqtt_sub.cache,
                            ha_states=ha_states
                        )
                        mode_tag = disagg.mode.lower()

                        # Store total power with mode tag
                        k = f"energy_telemetry|{dev_id}|heat_pump|CONSUMPTION|{src_type}|HEAT|{mode_tag}"
                        entry = self._accumulator.setdefault(k, {"sum": 0.0, "count": 0, "type": "power_w", "mode": mode_tag})
                        entry["sum"] += p_hp
                        entry["count"] += 1

                        # Track disaggregated live states
                        self.live_hp_disagg = disagg

                # Thermal Buffer (DHW Tank / Boiler)
                elif dev_type in ["thermal_buffer", "dhw_tank", "dhw_boiler"]:
                    t_dhw = get_val_raw(dev.get("ha_temp_entity", "sensor.hc_dhw_temperature_r5t_dhw_tank"))
                    if t_dhw is not None:
                        k = f"energy_telemetry|{dev_id}|thermal_buffer|STORAGE|{src_type}|HEAT"
                        entry = self._accumulator.setdefault(k, {"sum": 0.0, "count": 0, "type": "temperature_c"})
                        entry["sum"] += t_dhw
                        entry["count"] += 1

                # Battery
                elif dev_type in ["battery", "home_battery", "battery_storage"]:
                    p_bat = get_val_w(dev.get("ha_power_entity", "sensor.battery_power"))
                    if p_bat is not None:
                        flow = "STORAGE_CHARGE" if p_bat >= 0 else "STORAGE_DISCHARGE"
                        k = f"energy_telemetry|{dev_id}|battery|{flow}|{src_type}|ELECTRICITY"
                        entry = self._accumulator.setdefault(k, {"sum": 0.0, "count": 0, "type": "power_w"})
                        entry["sum"] += abs(p_bat)
                        entry["count"] += 1

            # Market & Weather Feeds
            epex_val = get_val_raw(cfg.get("providers", {}).get("epex_spot", {}).get("ha_sensor_entity", "sensor.energyzero_today_energy_current_hour_price"))
            if epex_val is not None:
                k = "market_tariffs|epex_spot|1h|spot_electricity"
                entry = self._accumulator.setdefault(k, {"sum": 0.0, "count": 0, "type": "price_eur"})
                entry["sum"] += epex_val
                entry["count"] += 1

            # Update Live Pipeline Power Balance (10s snapshot) with direct MQTT priority
            p1_imp = get_val_w("sensor.power_consumption") or 0.0
            p1_exp = get_val_w("sensor.power_production") or 0.0
            
            # Read Solar (Direct MQTT Inepro 103 -> HA Fallback)
            if "mbmd/inepro1-103/Power" in self.mqtt_sub.cache:
                sol = abs(float(self.mqtt_sub.cache["mbmd/inepro1-103/Power"]))
            else:
                sol_raw = get_val_w("sensor.zonnepanelen_power") or get_val_w("sensor.zonnepanelen_power_avg_5_minutes") or 0.0
                sol = abs(sol_raw)

            # Read Heat Pump (Direct MQTT Inepro 102 -> HA Fallback)
            if "mbmd/inepro1-102/Power" in self.mqtt_sub.cache:
                wp = float(self.mqtt_sub.cache["mbmd/inepro1-102/Power"])
            else:
                wp = get_val_w("sensor.warmtepomp_power") or 0.0

            bat = get_val_w("sensor.battery_power") or 0.0

            # Mathematical Triple Check:
            # 1. Net Grid = Import - Export
            net_grid = p1_imp - p1_exp
            # 2. Direct Consumed Solar = Solar produced minus what was pushed to the grid
            dir_sol = max(0.0, sol - p1_exp)
            # 3. Total Real Household Load = Net Grid Import + Solar
            tot_house = max(0.0, net_grid + sol)
            # 4. Unallocated Load = Total House Load - Heatpump - Battery charging
            unalloc = max(50.0, tot_house - wp)

            hp_mode = self.live_hp_disagg.mode if self.live_hp_disagg else "STANDBY"
            hp_dhw = self.live_hp_disagg.dhw_w if self.live_hp_disagg else 0.0
            hp_heat = self.live_hp_disagg.heating_w if self.live_hp_disagg else 0.0
            hp_cool = self.live_hp_disagg.cooling_w if self.live_hp_disagg else 0.0
            hp_standby = self.live_hp_disagg.standby_w if self.live_hp_disagg else wp

            self.live_balance = {
                "p1_import_w": round(p1_imp, 1),
                "p1_export_w": round(p1_exp, 1),
                "net_grid_w": round(net_grid, 1),
                "solar_w": round(sol, 1),
                "heatpump_w": round(wp, 1),
                "heatpump_mode": hp_mode,
                "heatpump_dhw_w": round(hp_dhw, 1),
                "heatpump_heating_w": round(hp_heat, 1),
                "heatpump_cooling_w": round(hp_cool, 1),
                "heatpump_standby_w": round(hp_standby, 1),
                "battery_w": round(bat, 1),
                "direct_solar_w": round(dir_sol, 1),
                "total_house_w": round(tot_house, 1),
                "unallocated_w": round(unalloc, 1)
            }
            self.sample_count_in_window += 1

            temp_val = get_val_raw(cfg.get("providers", {}).get("open_meteo", {}).get("ha_temp_sensor", "sensor.wittboy_gw2000a_weather_station_gw2000a_outdoor_temperature"))
            if temp_val is not None:
                k = "weather_forecast|wittboy"
                entry = self._accumulator.setdefault(k, {"sum": 0.0, "count": 0, "type": "outdoor_temp_c"})
                entry["sum"] += temp_val
                entry["count"] += 1

    def flush_window_to_influx(self):
        with self._lock:
            if not self._accumulator:
                return
            snapshot = self._accumulator
            self._accumulator = {}
            self.sample_count_in_window = 0
            self.last_flush_iso = datetime.now(AMS_TZ).strftime("%H:%M:%S")

        cfg = load_json(CONFIG_FILE)
        sec = load_secrets()
        active_conn = cfg.get("influxdb_connections", [{}])[0]
        db_name = active_conn.get("database", "openhems")
        db_user = active_conn.get("username", "openhems")
        db_url = active_conn.get("url", "http://a0d7b954-influxdb:8086")
        db_pwd = sec.get("influxdb", {}).get(active_conn.get("id"), "") or sec.get("influxdb", {}).get("local_ha_influxdb", "")

        now_ns = int(time.time() * 1e9)
        lines = []

        for k, v in snapshot.items():
            if v["count"] == 0:
                continue
            mean_val = round(v["sum"] / v["count"], 2)
            parts = k.split("|")
            m_name = parts[0]

            if m_name == "energy_telemetry":
                dev_id, dev_type, flow, src_type, vector = parts[1], parts[2], parts[3], parts[4], parts[5]
                mode_tag = f",mode={parts[6]}" if len(parts) > 6 else ""
                field_name = v["type"]
                lines.append(f"energy_telemetry,device_id={dev_id},device_type={dev_type},flow={flow},source_type={src_type},vector={vector}{mode_tag} {field_name}={mean_val} {now_ns}")
            elif m_name == "market_tariffs":
                provider, res, t_type = parts[1], parts[2], parts[3]
                lines.append(f"market_tariffs,provider={provider},resolution={res},tariff_type={t_type} price_eur={mean_val} {now_ns}")
            elif m_name == "weather_forecast":
                provider = parts[1]
                lines.append(f"weather_forecast,provider={provider} outdoor_temp_c={mean_val} {now_ns}")

        # Add canonical synchronized power balance record to openhems
        b = self.live_balance
        lines.append(
            f"energy_telemetry,source=canonical_accumulator,device_type=balance "
            f"p1_import_w={b['p1_import_w']:.1f},p1_export_w={b['p1_export_w']:.1f},solar_w={b['solar_w']:.1f},"
            f"heatpump_w={b['heatpump_w']:.1f},direct_solar_w={b['direct_solar_w']:.1f},"
            f"total_house_w={b['total_house_w']:.1f},unallocated_w={b['unallocated_w']:.1f} {now_ns}"
        )

        if not lines:
            return

        write_url = f"{db_url}/write?" + urllib.parse.urlencode({"u": db_user, "p": db_pwd, "db": db_name})
        payload = "\n".join(lines).encode("utf-8")
        req = urllib.request.Request(write_url, data=payload, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                if resp.status in [200, 204]:
                    self.last_write_status = "success"
                    self.total_points_written += len(lines)
                    print(f"[Open HEMS Collector] Flushed {len(lines)} points to {db_name} (Total: {self.total_points_written})", flush=True)
                else:
                    self.last_write_status = f"status_{resp.status}"
                    print(f"[Open HEMS Collector] Write status: {resp.status}", flush=True)
        except Exception as e:
            print(f"[Open HEMS Collector] Write error: {e}", flush=True)
            self.last_write_status = f"err_{str(e)[:30]}"
            try:
                log_technical_error(
                    domain="database",
                    event_type="influx_write_error",
                    reason="❌ Database Fout: InfluxDB Write Mislukt",
                    explanation=f"Wegschrijven van {len(lines)} telemetrie-punten naar InfluxDB '{db_name}' ({db_url}) mislukt: {e}",
                    inputs={"database": db_name, "points_count": len(lines), "error": str(e)},
                    category="ERROR"
                )
            except Exception:
                pass

GLOBAL_COLLECTOR = None

def run_server(port=8099):
    global GLOBAL_COLLECTOR
    collector = HemsBackgroundCollector(sample_interval_seconds=10, flush_window_seconds=60)
    collector.start()
    GLOBAL_COLLECTOR = collector
    import api.context
    api.context.GLOBAL_COLLECTOR = collector
    server = HTTPServer(("0.0.0.0", port), HemsApiHandler)
    print(f"Open HEMS Framework Console running on port {port}...")
    server.serve_forever()


def main():
    global CONFIG_FILE
    parser = argparse.ArgumentParser(description="Open HEMS Daemon")
    parser.add_argument("--config", default=str(CONFIG_FILE))
    parser.add_argument("--interval", type=int, default=15)
    parser.add_argument("--port", type=int, default=8099)
    args = parser.parse_args()

    if args.config:
        CONFIG_FILE = Path(args.config)

    cfg = load_json(CONFIG_FILE)
    ensure_framework_defaults(cfg)
    run_server(args.port)


if __name__ == "__main__":
    main()
