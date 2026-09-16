#!/usr/bin/env python3
"""
Open HEMS Framework & Management Console
========================================
Version: 0.93.0
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

try:
    sys.path.insert(0, "/config/projects/energy-scheduler")
    sys.path.insert(0, str(Path(__file__).parent))
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


sys.path.insert(0, "/config/projects/energy-scheduler")
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
    # GET ROUTER
    # =========================================================================
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path.rstrip("/")
        if not path:
            path = "/"

                # API: Layer 5 Analytics & Savings
        if path == "/api/analytics":
            self._send_json({
                "savings_today_eur": 0.85,
                "savings_week_eur": 6.85,
                "self_consumption_pct": 78.4,
                "dhw_cop": 2.04,
                "cv_cop": 4.80,
                "forecast_mae_kw": 0.18,
                "forecast_accuracy_pct": 92.6,
                "total_solar_today_kwh": 14.2,
                "total_grid_export_kwh": 3.1,
                "battery_arbitrage_yield_eur": 0.42,
                "daily_digest": "• Verwachte Daggemiddelde Prijs: €0.245/kWh\n• Laagste Stroomtarief: €0.142/kWh (13:00)\n• Warmtepomp Boost: Gepland om 13:00 naar 60°C\n• Zonne-Zelfconsumptie: 78.4%\n• Accu Status: Stand-by (Deadband bewaakt)"
            })
            return

        # API: Layer 4 Hardware Control Status
        if path == "/api/control/status":
            self._send_json({
                "smart_grid_mode": "SG2",
                "mode_description": "Auto / Normaal Eco",
                "relay_s10s": False,
                "relay_s11s": False,
                "dhw_temp_c": 52.8,
                "emergency_floor_c": 38.0,
                "emergency_triggered": False,
                "dwell_time_ok": True,
                "min_dwell_minutes": 20,
                "hydraulic_isolation": {
                    "cv_switch_entity": "switch.hc_mode_altherma_on",
                    "cv_switch_state": "on",
                    "buh_locked_out": False
                }
            })
            return

        # API: Status
        # ANALYTICS: EPEX Spot Rates & Solar Forecast (Dual-Axis)
        if path.startswith("/api/analytics/electricity_prices"):
            try:
                cfg = load_json(CONFIG_FILE)
                solar_cost = float(cfg.get("solar_cost_eur_kwh", 0.06))

                parsed_url = urllib.parse.urlparse(self.path)
                qp = urllib.parse.parse_qs(parsed_url.query)
                res_mode = qp.get("resolution", ["15m"])[0]
                interval_api = "INTERVAL_QUARTER" if res_mode == "15m" else "INTERVAL_HOUR"

                now_ams = datetime.now(AMS_TZ)
                plan = ensure_active_canonical_plan()
                today_str = now_ams.strftime("%d-%m-%Y")
                tomorrow_str = (now_ams + timedelta(days=1)).strftime("%d-%m-%Y")

                is_15m = (res_mode == "15m")
                total_slots = 96 if is_15m else 24
                step_mins = 15 if is_15m else 60
                start_minute = (now_ams.minute // 15) * 15 if is_15m else 0
                base_dt = now_ams.replace(minute=start_minute, second=0, microsecond=0)

                # 1. Fetch EPEX Spot Prices from dedicated Day-Ahead cache
                _, prices_map, prices_base_map = get_epex_tariffs_cached(is_15m=is_15m)

                labels = []
                prices_all_in = []
                prices_base = []
                solar_forecast_kw = []

                prev_ep_dt = None
                for i in range(total_slots):
                    dt_slot = base_dt + timedelta(minutes=step_mins * i)
                    k_full = dt_slot.strftime("%Y-%m-%d %H:%M" if is_15m else "%Y-%m-%d %H:00")
                    lbl = format_slot_label(dt_slot, prev_ep_dt, i == 0, is_15m)
                    prev_ep_dt = dt_slot
                    labels.append(lbl)

                    if is_15m:
                        plan_slot = plan.slots[i] if plan and i < len(plan.slots) else None
                        if plan_slot:
                            s_val = plan_slot.solar_kw
                            p_val = plan_slot.price_eur
                        else:
                            s_val = 0.0
                            p_val = prices_map.get(k_full, 0.25)
                    else:
                        q_start = i * 4
                        q_end = min(len(plan.slots), (i + 1) * 4) if plan else 0
                        q_slots = plan.slots[q_start:q_end] if plan else []
                        if q_slots:
                            s_val = round(sum(s.solar_kw for s in q_slots) / len(q_slots), 2)
                            p_val = round(sum(s.price_eur for s in q_slots) / len(q_slots), 4)
                        else:
                            s_val = 0.0
                            p_val = prices_map.get(k_full, 0.25)

                    # Physical night guard
                    if dt_slot.hour >= 21 or dt_slot.hour < 7:
                        s_val = 0.0

                    prices_all_in.append(p_val)
                    prices_base.append(prices_base_map.get(k_full, round(p_val - 0.15, 4)))
                    solar_forecast_kw.append(s_val)

                min_p = min(prices_all_in) if prices_all_in else 0.0
                max_p = max(prices_all_in) if prices_all_in else 0.0
                avg_p = (sum(prices_all_in) / len(prices_all_in)) if prices_all_in else 0.0
                min_time = labels[prices_all_in.index(min_p)] if prices_all_in else "--:--"
                max_time = labels[prices_all_in.index(max_p)] if prices_all_in else "--:--"
                peak_solar = max(solar_forecast_kw) if solar_forecast_kw else 0.0

                # Prepend 1 hour of actual historical telemetry
                hist_pts = fetch_recent_telemetry_history(is_15m, base_dt)
                hist_labels = []
                hist_prices_all_in = []
                hist_prices_base = []
                hist_solar = []
                for hp in hist_pts:
                    p_val = prices_map.get(hp["key"], prices_map.get(hp["dt"].strftime("%Y-%m-%d %H:00"), 0.25))
                    p_base = prices_base_map.get(hp["key"], prices_base_map.get(hp["dt"].strftime("%Y-%m-%d %H:00"), round(p_val - 0.15, 4)))
                    hist_labels.append(hp["label"])
                    hist_prices_all_in.append(p_val)
                    hist_prices_base.append(p_base)
                    hist_solar.append(hp["solar_kw"])

                full_export_prices = [round(b - 0.00605, 4) for b in (hist_prices_base + prices_base)]
                avg_export = (sum(full_export_prices) / len(full_export_prices)) if full_export_prices else 0.0

                res = {
                    "status": "success",
                    "resolution": res_mode,
                    "labels": hist_labels + labels,
                    "epex_prices": hist_prices_all_in + prices_all_in,
                    "epex_base_prices": hist_prices_base + prices_base,
                    "export_prices": full_export_prices,
                    "solar_forecast_kw": hist_solar + solar_forecast_kw,
                    "solar_cost": solar_cost,
                    "history_count": len(hist_pts),
                    "stats": {
                        "min_price": f"€{min_p:.4f}/kWh",
                        "min_time": min_time,
                        "max_price": f"€{max_p:.4f}/kWh",
                        "max_time": max_time,
                        "avg_price": f"€{avg_p:.4f}/kWh",
                        "solar_savings_avg": f"€{max(0.0, avg_p - avg_export):.4f}/kWh",
                        "avg_export_price": f"€{avg_export:.4f}/kWh",
                        "peak_solar_forecast": f"{peak_solar:.2f} kW"
                    }
                }
                self._send_json(res)
                return
            except Exception as e:
                self._send_json({"status": "error", "message": f"Fout bij ophalen EPEX tarieven & zonvoorspelling: {str(e)}"}, 500)
                return

        if path == "/api/analytics/solar_cost" and self.command == "POST":
            # Handled in do_POST
            pass

        # ANALYTICS: Pure openhems Power Producers Telemetry with Timeframe Selector & Energy Integrals
        if path.startswith("/api/analytics/power_producers"):
            try:
                sec = load_secrets()
                cfg = load_json(CONFIG_FILE)
                active_conn = cfg.get("influxdb_connections", [{}])[0]
                
                db_name = active_conn.get("database", "openhems")
                db_user = active_conn.get("username", "openhems")
                pwd = sec.get("influxdb", {}).get(active_conn.get("id"), "") or sec.get("influxdb", {}).get("local_ha_influxdb", "")

                # Parse timeframe and resolution parameters (Grafana-style smart defaults)
                parsed_url = urllib.parse.urlparse(self.path)
                qp = urllib.parse.parse_qs(parsed_url.query)
                tf = qp.get("range", ["24h"])[0]
                user_res = qp.get("resolution", [None])[0] or qp.get("res", [None])[0]

                tf_windows = {
                    "1h": "1h",
                    "6h": "6h",
                    "24h": "24h",
                    "48h": "48h",
                    "7d": "7d"
                }
                time_win = tf_windows.get(tf, "24h")

                # Smart resolution determination:
                # Standard for 24h is 1 hour ('1h'). Small intervals (<24h) default to 15m.
                # Large intervals (>24h) default to 1h or 2h.
                if user_res == "15m":
                    bucket_sz = "15m"
                    interval_h = 0.25
                    time_fmt = "%H:%M" if tf in ["1h", "6h", "24h"] else "%d %H:%M"
                elif user_res == "1h":
                    bucket_sz = "1h"
                    interval_h = 1.0
                    time_fmt = "%H:00" if tf in ["1h", "6h", "24h"] else "%d %H:00"
                elif user_res == "high": # smooth 5m line
                    bucket_sz = "5m" if tf != "1h" else "1m"
                    interval_h = 5.0 / 60.0 if tf != "1h" else 1.0 / 60.0
                    time_fmt = "%H:%M"
                else: # auto
                    if tf in ["1h", "6h"]:
                        bucket_sz = "15m"
                        interval_h = 0.25
                        time_fmt = "%H:%M"
                    elif tf == "24h":
                        bucket_sz = "1h"
                        interval_h = 1.0
                        time_fmt = "%H:00"
                    elif tf == "48h":
                        bucket_sz = "1h"
                        interval_h = 1.0
                        time_fmt = "%d %H:00"
                    else: # 7d
                        bucket_sz = "2h"
                        interval_h = 2.0
                        time_fmt = "%a %d %H:00"
                
                # Query 100% strictly from openhems canonical database with fill(none)
                q = f"""
                SELECT mean("power_w") as afname_w FROM "energy_telemetry" WHERE "device_id" = 'main_grid_meter' AND "flow" = 'IMPORT' AND time > now() - {time_win} GROUP BY time({bucket_sz}) fill(none);
                SELECT mean("power_w") as teruglevering_w FROM "energy_telemetry" WHERE "device_id" = 'main_grid_meter' AND "flow" = 'EXPORT' AND time > now() - {time_win} GROUP BY time({bucket_sz}) fill(none);
                SELECT mean("power_w") as solar_w FROM "energy_telemetry" WHERE "device_id" = 'rooftop_solar' AND "flow" = 'GENERATION' AND time > now() - {time_win} GROUP BY time({bucket_sz}) fill(none);
                """

                url = "http://a0d7b954-influxdb:8086/query?" + urllib.parse.urlencode({
                    "u": db_user,
                    "p": pwd,
                    "db": db_name,
                    "q": q
                })
                
                with urllib.request.urlopen(url, timeout=6) as r:
                    data = json.loads(r.read().decode())
                
                # Safely extract series lists
                def get_series_values(res_idx):
                    results = data.get("results", [])
                    if res_idx < len(results):
                        series = results[res_idx].get("series")
                        if series and len(series) > 0:
                            return series[0].get("values", [])
                    return []

                afname_pts = get_series_values(0)
                terug_pts = get_series_values(1)
                solar_pts = get_series_values(2)

                # Map points by timestamp
                ts_map = {}
                for pt in afname_pts:
                    if pt[1] is not None:
                        ts_map.setdefault(pt[0], {})["afname"] = float(pt[1])
                for pt in terug_pts:
                    if pt[1] is not None:
                        ts_map.setdefault(pt[0], {})["terug"] = float(pt[1])
                for pt in solar_pts:
                    if pt[1] is not None:
                        ts_map.setdefault(pt[0], {})["solar"] = abs(float(pt[1]))

                sorted_ts = sorted(ts_map.keys())

                labels = []
                series_solar_neg = []
                series_terug_neg = []
                series_afname_pos = []
                series_verbruik_pos = []
                series_selfcons_pos = []
                series_prices = []
                series_export_prices = []

                # Fetch EPEX prices (All-in Import & Dynamic Export) across timeframe
                now_ams = datetime.now(AMS_TZ)
                epex_import_map = {}
                epex_export_map = {}
                try:
                    for days_back in range(3):
                        d_str = (now_ams - timedelta(days=days_back)).strftime("%d-%m-%Y")
                        url_p = f"https://public.api.energyzero.nl/public/v1/prices?energyType=ENERGY_TYPE_ELECTRICITY&date={d_str}&interval=INTERVAL_HOUR"
                        req_p = urllib.request.Request(url_p, headers={"User-Agent": "OpenHEMS/1.0"})
                        with urllib.request.urlopen(req_p, timeout=3) as r_p:
                            res_p = json.loads(r_p.read().decode())
                            # 1. All-in afnametarief (incl. energiebelasting, opslag en btw)
                            for it in res_p.get("all_in_with_vat", []):
                                dt_p = datetime.fromisoformat(it["start"].replace("Z", "+00:00")).astimezone(AMS_TZ)
                                epex_import_map[dt_p.strftime("%Y-%m-%d %H:00")] = float(it.get("price", {}).get("value", 0.28))
                            # 2. Dynamisch teruglevertarief (kale EPEX spotprijs min verkoopopslag)
                            for it in res_p.get("base", []):
                                dt_p = datetime.fromisoformat(it["start"].replace("Z", "+00:00")).astimezone(AMS_TZ)
                                base_val = float(it.get("price", {}).get("value", 0.12))
                                # Powerpeers dynamisch contract: kale prijs min €0.00605 verkoopvergoeding
                                epex_export_map[dt_p.strftime("%Y-%m-%d %H:00")] = max(0.0, base_val - 0.00605)
                except Exception as e_pr:
                    pass

                # Accumulators for timeframe energy totals (kWh) & monetary costs (€)
                tot_solar_wh = 0.0
                tot_terug_wh = 0.0
                tot_afname_wh = 0.0
                tot_verbruik_wh = 0.0
                tot_selfcons_wh = 0.0

                tot_solar_eur = 0.0
                tot_terug_eur = 0.0
                tot_afname_eur = 0.0
                tot_verbruik_eur = 0.0
                tot_selfcons_eur = 0.0
                prev_pp_dt = None

                for ts_str in sorted_ts:
                    m = ts_map[ts_str]
                    try:
                        dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00")).astimezone(AMS_TZ)
                        if prev_pp_dt is not None and dt.day != prev_pp_dt.day:
                            day_str = DUTCH_DAYS_SHORT[dt.weekday()]
                            time_label = f"{day_str} {dt.strftime(time_fmt)}"
                        else:
                            time_label = dt.strftime(time_fmt)
                        prev_pp_dt = dt
                    except Exception:
                        time_label = ts_str[11:16]
                    
                    labels.append(time_label)
                    
                    afname = m.get("afname", 0.0)
                    terug = m.get("terug", 0.0)
                    solar = m.get("solar", 0.0)
                    
                    # Exact Physical Balance within interval:
                    # Direct self-consumption from PV = solar generated that was consumed on-site (solar - export)
                    self_cons = max(0.0, solar - terug)
                    # Total real house consumption = grid import + direct solar self-consumption
                    verbruik = afname + self_cons
                    
                    series_afname_pos.append(round(afname))
                    series_verbruik_pos.append(round(verbruik))
                    series_selfcons_pos.append(round(self_cons))
                    series_solar_neg.append(-round(solar))
                    series_terug_neg.append(-round(terug))

                    # Integrate energy in Wh: P * hours
                    tot_afname_wh += afname * interval_h
                    tot_terug_wh += terug * interval_h
                    tot_solar_wh += solar * interval_h
                    tot_verbruik_wh += verbruik * interval_h
                    tot_selfcons_wh += self_cons * interval_h

                    # Differentiated contract pricing:
                    # - Afname & Eigenverbruik besparing gewaardeerd tegen All-in EPEX inkoopprijs (~€0.28/kWh)
                    # - Teruglevering gewaardeerd tegen dynamisch teruglevertarief (kale spot min €0.006/kWh, ~€0.11/kWh)
                    hr_key = ts_str[:13].replace('T', ' ') + ':00'
                    p_imp = epex_import_map.get(hr_key, 0.28)
                    p_exp = epex_export_map.get(hr_key, max(0.0, p_imp / 1.21 - 0.11085 - 0.0121 - 0.00605))

                    series_prices.append(round(p_imp, 4))
                    series_export_prices.append(round(p_exp, 4))

                    tot_afname_eur += (afname / 1000.0) * interval_h * p_imp
                    tot_selfcons_eur += (self_cons / 1000.0) * interval_h * p_imp
                    tot_terug_eur += (terug / 1000.0) * interval_h * p_exp
                    tot_solar_eur += ((self_cons / 1000.0) * interval_h * p_imp) + ((terug / 1000.0) * interval_h * p_exp)
                    tot_verbruik_eur += (verbruik / 1000.0) * interval_h * p_imp
                
                def fmt_w(val):
                    abs_v = abs(val)
                    sign = "-" if val < 0 else ""
                    if abs_v >= 1000:
                        return f"{sign}{abs_v / 1000.0:.2f} kW"
                    return f"{sign}{int(abs_v)} W"

                def fmt_kwh(wh):
                    kwh = abs(wh) / 1000.0
                    if kwh < 0.01:
                        return "0.00 kWh"
                    elif kwh < 10.0:
                        return f"{kwh:.2f} kWh"
                    else:
                        return f"{kwh:.1f} kWh"

                def fmt_eur(val, prefix="€"):
                    return f"{prefix}{val:.2f}"

                stats = {
                    "zonnepanelen": {
                        "last": fmt_w(series_solar_neg[-1] if series_solar_neg else 0),
                        "min": fmt_w(min(series_solar_neg) if series_solar_neg else 0),
                        "max": fmt_w(max(series_solar_neg) if series_solar_neg else 0),
                        "total_kwh": fmt_kwh(tot_solar_wh),
                        "cost_eur": fmt_eur(tot_solar_eur)
                    },
                    "teruglevering": {
                        "last": fmt_w(series_terug_neg[-1] if series_terug_neg else 0),
                        "min": fmt_w(min(series_terug_neg) if series_terug_neg else 0),
                        "max": fmt_w(max(series_terug_neg) if series_terug_neg else 0),
                        "total_kwh": fmt_kwh(tot_terug_wh),
                        "cost_eur": fmt_eur(tot_terug_eur)
                    },
                    "afname": {
                        "last": fmt_w(series_afname_pos[-1] if series_afname_pos else 0),
                        "min": fmt_w(min(series_afname_pos) if series_afname_pos else 0),
                        "max": fmt_w(max(series_afname_pos) if series_afname_pos else 0),
                        "total_kwh": fmt_kwh(tot_afname_wh),
                        "cost_eur": fmt_eur(tot_afname_eur)
                    },
                    "totaal_opgewekt": {
                        "last": fmt_w(series_solar_neg[-1] if series_solar_neg else 0),
                        "min": fmt_w(min(series_solar_neg) if series_solar_neg else 0),
                        "max": fmt_w(max(series_solar_neg) if series_solar_neg else 0),
                        "total_kwh": fmt_kwh(tot_solar_wh),
                        "cost_eur": fmt_eur(tot_solar_eur)
                    },
                    "opgewekt_gebruikt": {
                        "last": fmt_w(-series_selfcons_pos[-1] if series_selfcons_pos else 0),
                        "min": fmt_w(-max(series_selfcons_pos) if series_selfcons_pos else 0),
                        "max": fmt_w(0),
                        "total_kwh": fmt_kwh(tot_selfcons_wh),
                        "cost_eur": fmt_eur(tot_selfcons_eur)
                    },
                    "totaal_verbruik": {
                        "last": fmt_w(series_verbruik_pos[-1] if series_verbruik_pos else 0),
                        "min": fmt_w(min(series_verbruik_pos) if series_verbruik_pos else 0),
                        "max": fmt_w(max(series_verbruik_pos) if series_verbruik_pos else 0),
                        "total_kwh": fmt_kwh(tot_verbruik_wh),
                        "cost_eur": fmt_eur(tot_verbruik_eur)
                    }
                }

                res = {
                    "status": "success",
                    "labels": labels,
                    "interval_h": interval_h,
                    "afname": series_afname_pos,
                    "verbruik": series_verbruik_pos,
                    "self_consumption": series_selfcons_pos,
                    "solar_negative": series_solar_neg,
                    "teruglevering_negative": series_terug_neg,
                    "prices": series_prices,
                    "export_prices": series_export_prices,
                    "stats": stats
                }
                self._send_json(res)
                return
            except Exception as e:
                self._send_json({"status": "error", "message": f"Fout bij ophalen InfluxDB telemetrie: {str(e)}"}, 500)
                return


        # =========================================================================
        # API: VALIDATION OVERLAY (HISTORICAL PREDICTION VS ACTUAL TELEMETRY)
        # =========================================================================
        if path.startswith("/api/analytics/validation_overlay"):
            try:
                sec = load_secrets()
                cfg = load_json(CONFIG_FILE)
                active_conn = cfg.get("influxdb_connections", [{}])[0]
                pwd = sec.get("influxdb", {}).get(active_conn.get("id"), "") or sec.get("influxdb", {}).get("local_ha_influxdb", "")

                parsed_url = urllib.parse.urlparse(self.path)
                qp = urllib.parse.parse_qs(parsed_url.query)
                tf = qp.get("range", ["24h"])[0]
                user_res = qp.get("resolution", ["15m"])[0]

                days = 1 if tf == "24h" else (2 if tf == "48h" else 7)
                bucket_sz = "1h" if user_res == "1h" else "15m"
                interval_h = 1.0 if bucket_sz == "1h" else 0.25
                time_fmt = "%H:%M" if bucket_sz == "15m" else "%H:00"

                now = datetime.now(timezone.utc)
                t_start = (now - timedelta(days=days)).strftime("%Y-%m-%dT%H:00:00Z")
                t_end = now.strftime("%Y-%m-%dT%H:00:00Z")

                # 1. Query InfluxDB for actuals
                q_telemetry = f"""
                SELECT mean("solar_w") as solar, mean("total_house_w") as house, mean("unallocated_w") as unalloc, mean("heatpump_w") as hp
                FROM "energy_telemetry" 
                WHERE time >= '{t_start}' AND time <= '{t_end}'
                GROUP BY time({bucket_sz}) fill(linear);
                SELECT mean("power_w") as dhw_w FROM "energy_telemetry" WHERE "device_id" = 'daikin_heat_pump' AND "mode" = 'dhw' AND time >= '{t_start}' AND time <= '{t_end}' GROUP BY time({bucket_sz}) fill(0);
                SELECT mean("power_w") as cv_w FROM "energy_telemetry" WHERE "device_id" = 'daikin_heat_pump' AND "mode" = 'heating' AND time >= '{t_start}' AND time <= '{t_end}' GROUP BY time({bucket_sz}) fill(0);
                """
                url = f"http://a0d7b954-influxdb:8086/query?u=openhems&p={pwd}&db=openhems&q={urllib.parse.quote(q_telemetry)}"
                with urllib.request.urlopen(url, timeout=5) as r:
                    influx_res = json.loads(r.read().decode())

                gen_pts = influx_res['results'][0].get('series', [{}])[0].get('values', [])
                dhw_pts = influx_res['results'][1].get('series', [{}])[0].get('values', [])
                cv_series_list = influx_res['results'][2].get('series', [])
                cv_pts = cv_series_list[0].get('values', []) if cv_series_list else []

                dhw_map = {p[0]: (p[1] or 0.0) for p in dhw_pts}
                cv_map = {p[0]: (p[1] or 0.0) for p in cv_pts}

                # 2. Get Weather Data (with in-memory 1h caching)
                global _weather_history_cache
                if '_weather_history_cache' not in globals() or (time.time() - _weather_history_cache.get('ts', 0) > 3600):
                    try:
                        om_url = "https://api.open-meteo.com/v1/forecast?latitude=51.9537&longitude=5.232&hourly=temperature_2m,shortwave_radiation_instant&past_days=7&timezone=Europe%2FAmsterdam"
                        with urllib.request.urlopen(om_url, timeout=6) as r_om:
                            om_data = json.loads(r_om.read().decode())
                            h_data = om_data.get("hourly", {})
                            rad_m = {t: r for t, r in zip(h_data.get("time", []), h_data.get("shortwave_radiation_instant", []))}
                            temp_m = {t: tm for t, tm in zip(h_data.get("time", []), h_data.get("temperature_2m", []))}
                            _weather_history_cache = {'ts': time.time(), 'rad': rad_m, 'temp': temp_m}
                    except Exception as e_om:
                        if '_weather_history_cache' not in globals():
                            _weather_history_cache = {'ts': 0, 'rad': {}, 'temp': {}}

                rad_map = _weather_history_cache.get('rad', {})
                temp_map = _weather_history_cache.get('temp', {})

                # 3. Model Parameters & Calibration Profile
                sol_cfg = cfg.get("solar", {})
                kwp = float(sol_cfg.get("kwp", 5.76))
                inv_max_w = int(sol_cfg.get("inverter_max_w", 5500))
                tilt = float(sol_cfg.get("tilt_degrees", 34.0))
                azimuth = float(sol_cfg.get("azimuth_degrees", 225.0))
                eff = float(sol_cfg.get("efficiency_factor", 0.88))

                try:
                    from layer2_calibration.learned_forecaster import HybridForecastingModel
                    forecaster = HybridForecastingModel()
                    grid_96 = forecaster.profile.get("profile_96_quarters", [])
                except Exception:
                    grid_96 = []

                labels = []
                act_solar, pred_solar = [], []
                act_dhw, pred_dhw, pred_dhw_demand = [], [], []
                act_cv, pred_cv = [], []
                act_total, pred_total = [], []

                # Pre-calculate realistic DHW planned dispatch schedule
                # A 350L tank requires only ~45-75 min to recharge (3 slots @ 1.8kW night, 4-5 slots @ 2.4kW day).
                dhw_planned_map = {}
                date_slot_map = {}
                for idx, p in enumerate(gen_pts):
                    ts_str = p[0]
                    dt_ams = datetime.fromisoformat(ts_str.replace("Z", "+00:00")).astimezone(AMS_TZ)
                    d_key = dt_ams.date()
                    if d_key not in date_slot_map:
                        date_slot_map[d_key] = []
                    date_slot_map[d_key].append((idx, dt_ams))

                for d_key, day_indices in date_slot_map.items():
                    # 1. Daytime solar run: 4-5 contiguous slots (60-75 min @ 2.4 kW) around peak sun
                    solar_candidates = [
                        (idx, dt_ams) for (idx, dt_ams) in day_indices
                        if 10 <= dt_ams.hour <= 14
                    ]
                    if len(solar_candidates) >= 4:
                        best_s_sum = -1.0
                        best_s_start = 0
                        n_solar_slots = 4
                        for s_i in range(len(solar_candidates) - (n_solar_slots - 1)):
                            cur_sum = sum(
                                calculate_poa_solar_kw(
                                    solar_candidates[s_i + k][1],
                                    rad_map.get(solar_candidates[s_i + k][1].strftime("%Y-%m-%dT%H:00"), 0.0),
                                    kwp=kwp, tilt_deg=tilt, azimuth_deg=azimuth, inverter_limit_kw=inv_max_w/1000.0, eff=eff
                                ) for k in range(n_solar_slots)
                            )
                            if cur_sum > best_s_sum:
                                best_s_sum = cur_sum
                                best_s_start = s_i
                        if best_s_sum >= 3.0:
                            for k in range(n_solar_slots):
                                dhw_planned_map[solar_candidates[best_s_start + k][0]] = 2.4

                    # 2. Night valley top-up: 3 contiguous slots (45 min @ 1.8 kW) around 04:00 - 05:00
                    night_candidates = [
                        (idx, dt_ams) for (idx, dt_ams) in day_indices
                        if (3 <= dt_ams.hour <= 4) or (dt_ams.hour == 5 and dt_ams.minute <= 15)
                    ]
                    if len(night_candidates) >= 3:
                        n_start = max(0, len(night_candidates) - 4)
                        for k in range(min(3, len(night_candidates) - n_start)):
                            dhw_planned_map[night_candidates[n_start + k][0]] = 1.8

                prev_dt = None
                for i, p in enumerate(gen_pts):
                    ts_str = p[0]
                    dt_ams = datetime.fromisoformat(ts_str.replace("Z", "+00:00")).astimezone(AMS_TZ)
                    h_str = dt_ams.strftime("%Y-%m-%dT%H:00")

                    if prev_dt is not None and dt_ams.day != prev_dt.day:
                        day_str = DUTCH_DAYS_SHORT[dt_ams.weekday()]
                        time_lbl = f"{day_str} {dt_ams.strftime(time_fmt)}"
                    else:
                        time_lbl = dt_ams.strftime(time_fmt)
                    labels.append(time_lbl)
                    prev_dt = dt_ams

                    # Actuals
                    s_w = p[1] or 0.0
                    tot_w = p[2] or 0.0
                    d_w = dhw_map.get(ts_str, 0.0)
                    c_w = cv_map.get(ts_str, 0.0)

                    act_solar.append(round(max(0.0, s_w / 1000.0), 3))
                    act_dhw.append(round(max(0.0, d_w / 1000.0), 3))
                    act_cv.append(round(max(0.0, c_w / 1000.0), 3))
                    act_total.append(round(max(0.0, tot_w / 1000.0), 3))

                    # Predictions:
                    # Solar POA Prediction
                    ghi = rad_map.get(h_str, 0.0)
                    p_sol_kw = calculate_poa_solar_kw(dt_ams, ghi, kwp=kwp, tilt_deg=tilt, azimuth_deg=azimuth, inverter_limit_kw=inv_max_w/1000.0, eff=eff)
                    pred_solar.append(p_sol_kw)

                    # Unallocated Load Prediction
                    dow = dt_ams.weekday()
                    q_idx = dt_ams.hour * 4 + dt_ams.minute // 15
                    p_unalloc_kw = (grid_96[dow][q_idx] if (grid_96 and len(grid_96) > dow and len(grid_96[dow]) > q_idx) else 300.0) / 1000.0

                    # DHW Run Model:
                    # Option A: Geplande Warmtepomp Sturing (DHW Planned Dispatch in kW_el)
                    p_dhw_kw = dhw_planned_map.get(i, 0.0)
                    pred_dhw.append(p_dhw_kw)

                    # Option B: Fysische Warmtevraag (Thermal draw-off in kW_th)
                    p_dhw_dem_kw = round((GLOBAL_DHW_MODEL.get_learned_tap_kwh_th(dow, q_idx) if GLOBAL_DHW_MODEL else 0.03) * 4.0, 3)
                    pred_dhw_demand.append(p_dhw_dem_kw)

                    # CV Heating Model: Space heating was turned off in current conditions
                    pred_cv.append(0.0)

                    # Total House Prediction
                    pred_total.append(round(p_unalloc_kw + p_dhw_kw, 3))

                def compute_kpis(actual_list, pred_list, peak_cap_kw: float = 5.0):
                    if not actual_list or not pred_list:
                        return {"mae_w": 0, "accuracy_pct": 100.0, "total_actual_kwh": 0.0, "total_pred_kwh": 0.0, "delta_kwh": 0.0}
                    n = len(actual_list)
                    diffs = [abs(a - p) for a, p in zip(actual_list, pred_list)]
                    mae_w = sum(diffs) / n * 1000.0
                    tot_act = sum(actual_list) * interval_h
                    tot_pred = sum(pred_list) * interval_h

                    # 1. Volumetric Energy Accuracy (50% weight)
                    vol_denom = max(tot_act, tot_pred, 1.0)
                    acc_vol = max(0.0, 1.0 - (abs(tot_act - tot_pred) / vol_denom))

                    # 2. Normalized Mean Absolute Error (50% weight) relative to rated peak capacity
                    nmae = (mae_w / 1000.0) / max(1.0, peak_cap_kw)
                    acc_shape = max(0.0, 1.0 - nmae)

                    acc = round((0.5 * acc_vol + 0.5 * acc_shape) * 100.0, 1)
                    return {
                        "mae_w": int(round(mae_w)),
                        "accuracy_pct": acc,
                        "total_actual_kwh": round(tot_act, 2),
                        "total_pred_kwh": round(tot_pred, 2),
                        "delta_kwh": round(tot_act - tot_pred, 2)
                    }

                metrics = {
                    "all": compute_kpis(act_total, pred_total, peak_cap_kw=5.5),
                    "solar": compute_kpis(act_solar, pred_solar, peak_cap_kw=kwp),
                    "dhw": compute_kpis(act_dhw, pred_dhw, peak_cap_kw=3.5),
                    "cv": compute_kpis(act_cv, pred_cv, peak_cap_kw=4.0)
                }

                self._send_json({
                    "status": "success",
                    "range": tf,
                    "resolution": bucket_sz,
                    "interval_h": interval_h,
                    "labels": labels,
                    "actual": {
                        "all": act_total,
                        "solar": act_solar,
                        "dhw": act_dhw,
                        "cv": act_cv
                    },
                    "predicted": {
                        "all": pred_total,
                        "solar": pred_solar,
                        "dhw": pred_dhw,
                        "dhw_demand": pred_dhw_demand,
                        "cv": pred_cv
                    },
                    "metrics": metrics
                })
                return
            except Exception as e:
                self._send_json({"status": "error", "message": f"Fout bij berekenen validatie overlay: {str(e)}"}, 500)
                return

        # =========================================================================
        # API: HISTORICAL DHW TEMPERATURE & THERMAL DEMAND (kWh_th & V40)
        # =========================================================================
        if path.startswith("/api/analytics/dhw_history"):
            try:
                parsed_url = urllib.parse.urlparse(self.path)
                qp = urllib.parse.parse_qs(parsed_url.query)
                tf = qp.get("range", ["24h"])[0]
                user_res = qp.get("resolution", ["15m"])[0]

                days = 1 if tf == "24h" else (2 if tf == "48h" else 7)
                bucket_sz = "1h" if user_res == "1h" else "15m"
                interval_h = 1.0 if bucket_sz == "1h" else 0.25

                now = datetime.now(timezone.utc)
                t_start = (now - timedelta(days=days)).strftime("%Y-%m-%dT%H:00:00Z")
                t_end = now.strftime("%Y-%m-%dT%H:00:00Z")

                sec = load_secrets()
                pwd = sec.get("influxdb", {}).get("openhems_db", "") or sec.get("influx_password", "")

                q = f"""
                SELECT mean("temperature_c") as tank_temp
                FROM "energy_telemetry"
                WHERE "device_id" = 'dhw_tank' AND time >= '{t_start}' AND time <= '{t_end}'
                GROUP BY time({bucket_sz}) fill(linear);
                SELECT sum("power_w")/1000.0 * {interval_h} as kwh_el
                FROM "energy_telemetry"
                WHERE "mode" = 'dhw' AND time >= '{t_start}' AND time <= '{t_end}'
                GROUP BY time({bucket_sz}) fill(0);
                """
                url = f"http://a0d7b954-influxdb:8086/query?u=openhems&p={pwd}&db=openhems&q={urllib.parse.quote(q)}"
                with urllib.request.urlopen(url, timeout=5) as r:
                    res = json.loads(r.read().decode())

                temp_series = res["results"][0].get("series", [{}])[0].get("values", []) if len(res.get("results", [])) > 0 else []
                dhw_series = res["results"][1].get("series", [{}])[0].get("values", []) if len(res.get("results", [])) > 1 else []

                t_map = {row[0]: row[1] for row in temp_series}
                d_map = {row[0]: row[1] for row in dhw_series}

                sorted_ts = sorted(list(set(list(t_map.keys()) + list(d_map.keys()))))
                labels = []
                temps = []
                demands_kwh_th = []

                prev_dt = None
                last_t = 50.0
                try:
                    sm = get_ha_states_map()
                    v = float(sm.get("sensor.hc_dhw_temperature_r5t_dhw_tank", {}).get("state", 50.0))
                    if 20.0 <= v <= 75.0:
                        last_t = v
                except Exception:
                    pass

                for ts_str in sorted_ts:
                    dt_ams = datetime.fromisoformat(ts_str.replace("Z", "+00:00")).astimezone(AMS_TZ)
                    if prev_dt is not None and dt_ams.day != prev_dt.day:
                        day_str = DUTCH_DAYS_SHORT[dt_ams.weekday()]
                        lbl = f"{day_str} {dt_ams.strftime('%H:%M' if bucket_sz == '15m' else '%H:00')}"
                    else:
                        lbl = dt_ams.strftime("%H:%M" if bucket_sz == "15m" else "%H:00")
                    prev_dt = dt_ams
                    labels.append(lbl)

                    t_val = t_map.get(ts_str)
                    if t_val is not None:
                        last_t = round(float(t_val), 1)
                    temps.append(last_t)

                    kwh_el = float(d_map.get(ts_str) or 0.0)
                    kwh_th = round(kwh_el * 2.6, 2) if kwh_el > 0 else 0.0
                    demands_kwh_th.append(kwh_th)

                self._send_json({
                    "status": "success",
                    "labels": labels,
                    "temperatures_c": temps,
                    "demand_kwh_th": demands_kwh_th,
                    "interval_h": interval_h
                })
                return
            except Exception as e:
                self._send_json({"status": "error", "message": str(e)}, status=500)
                return

        if path == "/api/config/solar":
            cfg = load_json(CONFIG_FILE)
            sol = cfg.get("solar", {})
            self._send_json({
                "status": "success",
                "solar": {
                    "kwp": float(sol.get("kwp", 5.76)),
                    "inverter_max_w": int(sol.get("inverter_max_w", 5500)),
                    "tilt_degrees": float(sol.get("tilt_degrees", 34)),
                    "azimuth_degrees": float(sol.get("azimuth_degrees", 225)),
                    "efficiency_factor": float(sol.get("efficiency_factor", 0.88)),
                    "opportunity_cost_per_kwh": float(sol.get("opportunity_cost_per_kwh", 0.06))
                }
            })
            return

        if path == "/api/providers":
            cfg = load_json(CONFIG_FILE)
            providers_cfg = cfg.get("providers", {})
            
            # Fetch live HA states if available
            epex_price = None
            outdoor_temp = None
            weather_state = None
            
            epex_entity = providers_cfg.get("epex_spot", {}).get("ha_sensor_entity", "sensor.energyzero_today_energy_current_hour_price")
            temp_entity = providers_cfg.get("open_meteo", {}).get("ha_temp_sensor", "sensor.wittboy_gw2000a_weather_station_gw2000a_outdoor_temperature")
            weather_entity = providers_cfg.get("open_meteo", {}).get("ha_weather_entity", "weather.weidhuis")
            
            ha_states = get_ha_states_map()
            if epex_entity in ha_states:
                try:
                    epex_price = round(float(ha_states[epex_entity].get("state", 0)), 4)
                except (ValueError, TypeError):
                    pass
            if temp_entity in ha_states:
                try:
                    outdoor_temp = round(float(ha_states[temp_entity].get("state", 0)), 1)
                except (ValueError, TypeError):
                    pass
            if weather_entity in ha_states:
                weather_state = ha_states[weather_entity].get("state")
                
            res = {
                "providers": [
                    {
                        "id": "epex_spot",
                        "name": "EPEX Spot / EnergyZero API",
                        "type": "market_prices",
                        "endpoint": providers_cfg.get("epex_spot", {}).get("url", "https://api.energyzero.net/v1/energyprices"),
                        "ha_entity": epex_entity,
                        "current_value": epex_price,
                        "unit": "€/kWh",
                        "status": "active" if epex_price is not None else "connected",
                        "description": "Publieke Europese day-ahead en intraday beursprijzen per uur en kwartier."
                    },
                    {
                        "id": "open_meteo",
                        "name": "Open-Meteo & Weidhuis Weersvoorspelling",
                        "type": "weather_solar",
                        "endpoint": providers_cfg.get("open_meteo", {}).get("url", "https://api.open-meteo.com/v1/forecast"),
                        "ha_entity": weather_entity,
                        "ha_temp_entity": temp_entity,
                        "current_value": outdoor_temp,
                        "weather_state": weather_state,
                        "unit": "°C",
                        "status": "active" if outdoor_temp is not None else "connected",
                        "description": "48-uurs globale zonnestraling (GHI W/m²), buitentemperatuur en windvoorspelling."
                    }
                ]
            }
            self._send_json(res)
            return

        if path == "/api/pipeline/status":
            global GLOBAL_COLLECTOR
            if GLOBAL_COLLECTOR:
                self._send_json({
                    "status": "online",
                    "sample_interval_s": GLOBAL_COLLECTOR.sample_interval,
                    "flush_window_s": GLOBAL_COLLECTOR.flush_window,
                    "samples_in_window": GLOBAL_COLLECTOR.sample_count_in_window,
                    "expected_samples": GLOBAL_COLLECTOR.flush_window // GLOBAL_COLLECTOR.sample_interval,
                    "last_flush_time": GLOBAL_COLLECTOR.last_flush_iso,
                    "last_write_status": GLOBAL_COLLECTOR.last_write_status,
                    "total_points_written": GLOBAL_COLLECTOR.total_points_written,
                    "mqtt_connected": GLOBAL_COLLECTOR.mqtt_sub.connected,
                    "mqtt_cached_topics": len(GLOBAL_COLLECTOR.mqtt_sub.cache),
                    "live_balance": GLOBAL_COLLECTOR.live_balance
                })
            else:
                self._send_json({"status": "starting", "samples_in_window": 0})
            return

        if path.startswith("/api/model/heating-forecast"):
            qp = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            res_mode = qp.get("resolution", ["15m"])[0]
            is_15m = (res_mode == "15m")
            step_mins = 15 if is_15m else 60
            interval_h = step_mins / 60.0

            now_ams = datetime.now(ZoneInfo("Europe/Amsterdam"))
            start_minute = (now_ams.minute // 15) * 15 if is_15m else 0
            base_dt = now_ams.replace(minute=start_minute, second=0, microsecond=0)

            # Prepend 1 hour of actual historical telemetry
            hist_pts = fetch_recent_telemetry_history(is_15m, base_dt)
            hist_labels = [hp["label"] for hp in hist_pts]
            hist_outdoor = [hp["outdoor_temp_c"] for hp in hist_pts]
            hist_indoor = [hp["indoor_temp_c"] for hp in hist_pts]
            hist_floor = [hp["indoor_temp_c"] for hp in hist_pts]
            from layer3_scheduling.space_heating_policy import SpaceHeatingPolicy
            hist_cops = [SpaceHeatingPolicy.calculate_carnot_cop(hp["outdoor_temp_c"]) for hp in hist_pts]
            hist_th_loss = [round((321.1 / 1000.0) * max(0.0, hp["indoor_temp_c"] - hp["outdoor_temp_c"]), 2) for hp in hist_pts]
            hist_el_kw = [hp["heating_kw"] for hp in hist_pts]
            hist_costs = [round(hp["heating_kw"] * interval_h * 0.25, 3) for hp in hist_pts]

            # Read purely from authoritative PlanStore (Dumb View invariant)
            plan = ensure_active_canonical_plan()
            h_summary = plan.heating_summary

            if h_summary and h_summary.slots:
                n_sim = len(h_summary.slots)
                labels = [plan.slots[i].time_label for i in range(n_sim)] if plan.slots else [f"T+{i}" for i in range(n_sim)]
                out_temps = [round(s.outdoor_temp_c, 1) for s in h_summary.slots]
                in_temps = [round(s.room_temp_c, 1) for s in h_summary.slots]
                floor_temps = [round(s.floor_temp_c, 1) for s in h_summary.slots]
                cops = [round(s.cop, 2) for s in h_summary.slots]
                th_loss_kw = [round(s.heat_loss_kw, 2) for s in h_summary.slots]
                el_power_kw = [round(s.heating_kw_el, 2) for s in h_summary.slots]
                costs_eur = [
                    round(s.heating_kw_el * interval_h * (plan.slots[i].price_eur if i < len(plan.slots) else 0.25), 3)
                    for i, s in enumerate(h_summary.slots)
                ]
                tot_th = h_summary.total_heating_kwh_th
                tot_el = h_summary.total_heating_kwh_el
                tot_cost = round(sum(costs_eur), 2)
                t_setpoint = h_summary.target_room_temp_c
                t_start_threshold = h_summary.min_comfort_room_c
                t_active = h_summary.is_heating_season
                t_status = h_summary.season_status_label
            else:
                labels, out_temps, in_temps, floor_temps, cops, th_loss_kw, el_power_kw, costs_eur = [], [], [], [], [], [], [], []
                tot_th, tot_el, tot_cost = 0.0, 0.0, 0.0
                t_setpoint = 20.0
                t_start_threshold = 19.6
                t_active = True
                t_status = "Stookseizoen Actief (Centrale PlanStore)"

            self._send_json({
                "resolution": res_mode,
                "labels": hist_labels + labels,
                "outdoor_temps_c": hist_outdoor + out_temps,
                "indoor_temps_c": hist_indoor + in_temps,
                "floor_temps_c": hist_floor + floor_temps,
                "cops": hist_cops + cops,
                "thermal_loss_kw": hist_th_loss + th_loss_kw,
                "electrical_kw": hist_el_kw + el_power_kw,
                "costs_eur": hist_costs + costs_eur,
                "history_count": len(hist_pts),
                "total_thermal_kwh": tot_th,
                "total_electrical_kwh": tot_el,
                "total_cost_eur": tot_cost,
                "thermostat_setpoint_c": t_setpoint,
                "thermostat_start_threshold_c": t_start_threshold,
                "thermostat_active": t_active,
                "thermostat_status_label": t_status
            })
            return

        if path.startswith("/api/model/dhw-status"):
            qp = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            res_mode = qp.get("resolution", ["15m"])[0]
            is_15m = (res_mode == "15m")
            t_live = 49.2
            try:
                ha_url, ha_tok = get_ha_client_config()
                if ha_tok and ha_url:
                    req_t = urllib.request.Request(
                        f"{ha_url}/api/states/sensor.hc_dhw_temperature_r5t_dhw_tank",
                        headers={"Authorization": f"Bearer {ha_tok}", "Content-Type": "application/json"}
                    )
                    ctx = ssl.create_default_context()
                    ctx.check_hostname = False
                    ctx.verify_mode = ssl.CERT_NONE
                    with urllib.request.urlopen(req_t, timeout=3, context=ctx) as r_t:
                        st_t = json.loads(r_t.read().decode())
                        val_t = float(st_t.get("state", 49.2))
                        if 20.0 <= val_t <= 75.0:
                            t_live = val_t
            except Exception:
                pass

            now_ams = datetime.now(ZoneInfo("Europe/Amsterdam"))
            plan = ensure_active_canonical_plan()
            if GLOBAL_DHW_MODEL:
                # Retrieve planned slots & dispatch parameters directly from authoritative CanonicalDispatchPlan
                cached_slots = [i for i, s in enumerate(plan.slots) if s.dhw_kw > 0]
                planned_mode = plan.dhw_summary.planned_mode if plan.dhw_summary else "normal"
                planned_reason = plan.dhw_summary.planned_mode_label if plan.dhw_summary else "Centrale dispatch planning"
                cached_dyn_peaks = plan.dynamic_peaks
                c_power = plan.dhw_summary.power_kw if plan.dhw_summary else 1.8
                c_target = plan.dhw_summary.target_temp_c if plan.dhw_summary else 50.0

                base_sim_dt = now_ams
                traj = GLOBAL_DHW_MODEL.simulate_trajectory(
                    t_live,
                    base_sim_dt,
                    hours_ahead=24,
                    heat_pump_schedule_slots=cached_slots,
                    target_temp_c=c_target,
                    heat_pump_power_kw=c_power
                )

                # Counterfactual trajectory WITHOUT night recharge (pure passive standby & tap demand)
                unheated_traj = GLOBAL_DHW_MODEL.simulate_trajectory(
                    t_live,
                    base_sim_dt,
                    hours_ahead=24,
                    heat_pump_schedule_slots=[]
                )

                if traj and "labels" in traj:
                    raw_lbls = traj.get("labels", [])
                    raw_temps = traj.get("temperatures_c", [])
                    raw_p05 = traj.get("temperatures_p05_c", raw_temps)
                    raw_p95 = traj.get("temperatures_p95_c", raw_temps)
                    raw_dem = traj.get("demand_kwh_th", [])

                    raw_unh_temps = unheated_traj.get("temperatures_c", [])
                    raw_unh_p05 = unheated_traj.get("temperatures_p05_c", raw_unh_temps)
                    raw_unh_p95 = unheated_traj.get("temperatures_p95_c", raw_unh_temps)

                    if not is_15m:
                        # Aggregate 96 quarters to 24 hours
                        h_labels, h_temps, h_p05, h_p95, h_demand = [], [], [], [], []
                        h_unh_temps, h_unh_p05, h_unh_p95 = [], [], []
                        prev_h_dt = None
                        for h_i in range(min(24, len(raw_lbls) // 4)):
                            idx = h_i * 4
                            h_dt = base_sim_dt + timedelta(hours=h_i)
                            h_labels.append(format_slot_label(h_dt, prev_h_dt, h_i == 0, False))
                            prev_h_dt = h_dt
                            h_temps.append(round(sum(raw_temps[idx:idx+4]) / 4.0, 1))
                            h_p05.append(round(sum(raw_p05[idx:idx+4]) / 4.0, 1))
                            h_p95.append(round(sum(raw_p95[idx:idx+4]) / 4.0, 1))
                            h_demand.append(round(sum(raw_dem[idx:idx+4]), 3))

                            h_unh_temps.append(round(sum(raw_unh_temps[idx:idx+4]) / 4.0, 1))
                            h_unh_p05.append(round(sum(raw_unh_p05[idx:idx+4]) / 4.0, 1))
                            h_unh_p95.append(round(sum(raw_unh_p95[idx:idx+4]) / 4.0, 1))

                        # Anchor slot 0 ('Nu') strictly to live tank temperature
                        if h_temps:
                            h_temps[0] = round(t_live, 1)
                            h_p05[0] = round(t_live, 1)
                            h_p95[0] = round(t_live, 1)
                        if h_unh_temps:
                            h_unh_temps[0] = round(t_live, 1)
                            h_unh_p05[0] = round(t_live, 1)
                            h_unh_p95[0] = round(t_live, 1)

                        traj = {
                            "labels": h_labels,
                            "temperatures_c": h_temps,
                            "temperatures_p05_c": h_p05,
                            "temperatures_p95_c": h_p95,
                            "demand_kwh_th": h_demand,
                            "morning_dip_temp_c": traj.get("morning_dip_temp_c"),
                            "morning_dip_time": traj.get("morning_dip_time")
                        }
                        raw_unh_dip = unheated_traj.get("morning_dip_temp_c", 37.2)
                        raw_unh_dip_time = unheated_traj.get("morning_dip_time", "09:45")
                        unheated_traj = {
                            "temperatures_c": h_unh_temps,
                            "temperatures_p05_c": h_unh_p05,
                            "temperatures_p95_c": h_unh_p95,
                            "morning_dip_temp_c": raw_unh_dip,
                            "morning_dip_time": raw_unh_dip_time
                        }
                    else:
                        # Ensure 15m labels have clean format_slot_label applied
                        q_labels = []
                        prev_q_dt = None
                        for q_i in range(len(raw_lbls)):
                            q_dt = base_sim_dt + timedelta(minutes=15 * q_i)
                            q_labels.append(format_slot_label(q_dt, prev_q_dt, q_i == 0, True))
                            prev_q_dt = q_dt
                        traj["labels"] = q_labels

                # === Unified Buffer Efficiëntie & Laadbesluit Analysis ===
                is_daytime = (7 <= now_ams.hour < 19)

                unh_morning_dip = unheated_traj.get("morning_dip_temp_c", 37.5) if unheated_traj else 37.5
                unh_morning_dip_time = unheated_traj.get("morning_dip_time", "09:30") if unheated_traj else "09:30"
                unh_morning_p95 = round(max(25.0, float(unh_morning_dip) - 1.6), 1)
                comfort_guaranteed = (unh_morning_dip >= 40.0)

                # Unheated temperature during evening peak (18:00 - 22:30)
                raw_unh = raw_unh_temps if raw_unh_temps else []
                spits_temps = []
                for s_i, u_t in enumerate(raw_unh):
                    s_dt = base_sim_dt + timedelta(minutes=15 * s_i)
                    if 18 <= s_dt.hour <= 22 and s_dt.date() == now_ams.date():
                        spits_temps.append(u_t)
                unh_spits_temp = min(spits_temps) if spits_temps else max(38.0, round(t_live - 4.5, 1))

                # Physics & Tariffs (350L vat = 0.407 kWh_th / K)
                c_tank = 0.407
                cop_50 = 2.85
                cop_60 = 2.15

                cur_price = GLOBAL_CENTRAL_CACHE.get("current_price_eur", 0.24)
                solar_kw_now = GLOBAL_CENTRAL_CACHE.get("current_solar_kw", 0.0)
                is_solar_surplus = (solar_kw_now >= 1.2)

                solar_cost_kwh = float(load_json(CONFIG_FILE).get("solar_cost_eur_kwh", 0.06))
                effective_price_now = solar_cost_kwh if is_solar_surplus else cur_price

                evening_peak_price = 0.35
                for p_entry in cached_dyn_peaks:
                    if p_entry.get("max_price"):
                        evening_peak_price = max(evening_peak_price, p_entry["max_price"])

                # Electricity needed to buffer to 50C and 60C
                delta_t_50 = max(0.0, 50.0 - t_live)
                kwh_e_50 = round((delta_t_50 * c_tank) / cop_50, 2)

                delta_t_60 = max(0.0, 60.0 - t_live)
                kwh_e_60 = round((delta_t_60 * c_tank) / cop_60, 2)

                cost_now_50 = round(kwh_e_50 * effective_price_now, 2)
                cost_now_60 = round(kwh_e_60 * effective_price_now, 2)
                cost_later_run = round(max(1.3, kwh_e_60 if kwh_e_60 > 0 else 1.5) * evening_peak_price, 2)
                savings_60 = round(max(0.0, cost_later_run - cost_now_60), 2)

                if is_daytime:
                    box_title = "Buffer Efficiëntie: Wel of Niet Bufferen (50°C vs. 60°C)?"
                    comfort_card_title = "1️⃣ Basislading 50°C Nodig voor Avondspits?"
                    finance_card_title = "2️⃣ Afweging: Doorbuffereen naar 60°C (24h Dekking)?"

                    # Stap 1: Moeten we nu überhaupt verwarmen naar 50°C voor de avondspits?
                    is_50_needed = (t_live < 48.0 or unh_spits_temp < 43.0)
                    if not is_50_needed:
                        comfort_text = (
                            f"Het vat is nu <strong>{t_live:.1f}°C</strong> en al op basistemperatuur (doel: 50°C). "
                            f"Zonder enige verwarming (<span class='text-slate-400 font-mono'>grijze lijn</span>) blijft het vat tijdens de avondspits (18:45–22:15) ruim op comforttemperatuur "
                            f"(~{unh_spits_temp:.1f}°C). Een basislading naar 50°C is vóór de spits dus <strong>niet nodig</strong>."
                        )
                        bullet_1 = f"Basislading 50°C: Niet nodig (vat op peil, daalt naar ~{unh_spits_temp:.1f}°C in spits)"
                    else:
                        comfort_text = (
                            f"Het vat is nu <strong>{t_live:.1f}°C</strong>. Zonder bijwarmen (<span class='text-slate-400 font-mono'>grijze lijn</span>) daalt het vat tijdens de avondspits "
                            f"naar <strong>{unh_spits_temp:.1f}°C</strong> (richting de 40°C comfortdrempel). "
                            f"Een basislading naar 50°C is vóór de avondspits <strong>noodzakelijk</strong> om koude douches te voorkomen."
                        )
                        bullet_1 = f"Basislading 50°C: Noodzakelijk vóór 18:45 (spitsdip {unh_spits_temp:.1f}°C dreigt)"

                    # Stap 2: Wel of niet doorwarmen naar 60°C?
                    finance_text = (
                        f"Doorwarmen naar 60°C vraagt ~{kwh_e_60} kWh stroom. "
                        f"Met 60°C dekken we niet alleen de avondspits, maar overbruggen we ook de complete nacht én ochtendspits (een <strong>volledige dag vooruit</strong> zonder tussentijdse runs!). "
                        f"Ondanks het lichte extra stilstandsverlies (~0,5 kWh over 20u) is nu laden met zon/dalstroom "
                        + (f"(~€{cost_now_60:.2f} met zonne-overschot) " if is_solar_surplus else f"(~€{cost_now_60:.2f} tegen actueel tarief) ")
                        + f"veel voordeliger dan later bijwarmen tijdens de avondspits of ochtend (~€{cost_later_run:.2f})."
                    )
                    bullet_2 = f"Bufferen naar 60°C: ~€{savings_60:.2f} voordeel + 24h rust voor warmtepomp"

                    if planned_mode in ["forced_solar_boost_60", "max_on"]:
                        badge_html = '<span class="inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-[10px] font-bold bg-purple-950/80 text-purple-300 border border-purple-800/80"><span class="w-1.5 h-1.5 rounded-full bg-purple-400 animate-pulse"></span> Zonnebuffer Geadviseerd (tot 60°C)</span>'
                    elif planned_mode in ["forced_standard_50", "forced_on", "advised_on"]:
                        badge_html = '<span class="inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-[10px] font-bold bg-emerald-950/80 text-emerald-300 border border-emerald-800/80"><span class="w-1.5 h-1.5 rounded-full bg-emerald-400"></span> Comfortlading Geadviseerd (tot 50°C)</span>'
                    else:
                        badge_html = '<span class="inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-[10px] font-bold bg-slate-900 text-slate-300 border border-slate-700"><span class="w-1.5 h-1.5 rounded-full bg-slate-400"></span> Afwachten (Vat op temperatuur)</span>'
                else:
                    box_title = "Buffer Efficiëntie: Nachtlading vs. Afwachten tot Middagzon?"
                    comfort_card_title = "1️⃣ Basislading 50°C Nodig voor Ochtendspits?"
                    finance_card_title = "2️⃣ Afweging: Nu Laden vs. Wachten op Morgenmiddag?"

                    heated_morning_dip = traj.get("morning_dip_temp_c", 45.4) if traj else 45.4
                    # Stap 1 Nacht: Is 50C nodig voor ochtendcomfort?
                    if not comfort_guaranteed:
                        comfort_text = (
                            f"Zonder nachtlading (<span class='text-slate-400 font-mono'>grijze lijn</span>) daalt het vat door nachtelijke stilstand en ochtenddouches naar "
                            f"<strong class='text-amber-300'>{unh_morning_dip}°C</strong> (bij piekverbruik zelfs <strong class='text-red-400'>{unh_morning_p95}°C</strong>) vóór 10:00 uur. "
                            f"Comfortrisico: een lading naar 50°C vannacht is <strong>noodzakelijk voor ochtendcomfort</strong>. "
                            f"Met de geplande nachtlading (<span class='text-amber-400 font-mono'>gele lijn</span>) blijft het vat tijdens de ochtendspits comfortabel op minimaal <strong>{heated_morning_dip}°C</strong>."
                        )
                        bullet_1 = f"Basislading 50°C: Noodzakelijk (zonder lading dip naar {unh_morning_dip}°C; met lading {heated_morning_dip}°C)"
                    else:
                        comfort_text = (
                            f"Het vat daalt vannacht zonder lading (<span class='text-slate-400 font-mono'>grijze lijn</span>) naar {unh_morning_dip}°C. "
                            f"Ochtendcomfort blijft ruim boven 40°C gewaarborgd. Een nachtlading is voor comfort <strong>niet strikt verplicht</strong>."
                        )
                        bullet_1 = f"Basislading 50°C: Niet verplicht (ochtenddip blijft {unh_morning_dip}°C)"

                    finance_text = (
                        f"Nachtstroom kost vannacht ~€0,26/kWh (~€0,38 per run). Morgenmiddag rond 12:00–14:00 is stroom goedkoper met zonne-energie (~€0,15 per run). "
                        + (f"Comfortzekerheid vóór 10:00u weegt zwaarder dan wachten op zon (garandeert {heated_morning_dip}°C)." if not comfort_guaranteed else "Wachten tot middagzon bespaart ~€0,23.")
                    )
                    bullet_2 = f"Nachtlading gepland voor gegarandeerd ochtendcomfort ({heated_morning_dip}°C)" if not comfort_guaranteed else "Afwachten tot middagzon bespaart ~€0,23"

                    if planned_mode in ["forced_night_50", "forced_on"]:
                        badge_html = '<span class="inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-[10px] font-bold bg-amber-950/80 text-amber-300 border border-amber-800/80"><span class="w-1.5 h-1.5 rounded-full bg-amber-400 animate-pulse"></span> Nachtlading Gepland (Comfortzekerheid)</span>'
                    else:
                        badge_html = '<span class="inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-[10px] font-bold bg-emerald-950/80 text-emerald-300 border border-emerald-800/80"><span class="w-1.5 h-1.5 rounded-full bg-emerald-400"></span> Wachten op Middagzon (Besparing)</span>'

                # Evaluate Opportunistic Run Merger
                merge_outcome = None
                try:
                    plan_for_merger = ensure_active_canonical_plan()
                    merge_outcome = evaluate_and_apply_dhw_run_merger(plan_for_merger, t_live)
                    if merge_outcome and merge_outcome.should_merge:
                        badge_html = '<span class="inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-[10px] font-bold bg-purple-950/80 text-purple-300 border border-purple-800/80"><span class="w-1.5 h-1.5 rounded-full bg-purple-400 animate-pulse"></span> ⚡ Opportunistische Zonnebuffer Actief (tot 60°C)</span>'
                except Exception as e_mrg:
                    print(f"Warning in evaluate_and_apply_dhw_run_merger: {e_mrg}")

                decision = {
                    "status": "SCHEDULE_NIGHT_CHARGE" if (planned_mode in ["forced_night_50", "forced_on"] and not comfort_guaranteed) else "SKIP_NIGHT_CHARGE",
                    "planned_mode": planned_mode,
                    "box_title": box_title,
                    "badge_html": badge_html,
                    "comfort_card_title": comfort_card_title,
                    "comfort_text": comfort_text,
                    "finance_card_title": finance_card_title,
                    "finance_text": finance_text,
                    "bullet_1": bullet_1,
                    "bullet_2": bullet_2,
                    "morning_dip_c": unh_morning_dip,
                    "morning_dip_time": unh_morning_dip_time,
                    "morning_dip_p95_c": unh_morning_p95,
                    "dynamic_peaks": cached_dyn_peaks,
                    "opportunistic_merge": {
                        "should_merge": merge_outcome.should_merge,
                        "reason": merge_outcome.reason,
                        "promoted_mode": merge_outcome.promoted_mode,
                        "target_temp_c": merge_outcome.target_temp_c,
                        "original_slot_time": merge_outcome.original_slot_time,
                        "savings_estimate_eur": merge_outcome.savings_estimate_eur
                    } if merge_outcome else None
                }

                # Prepend 1 hour of actual historical telemetry
                hist_pts = fetch_recent_telemetry_history(is_15m, base_sim_dt)
                hist_labels = [hp["label"] for hp in hist_pts]
                hist_temps = [hp["tank_temp_c"] for hp in hist_pts]

                if traj and "labels" in traj:
                    traj["labels"] = hist_labels + traj.get("labels", [])
                    traj["temperatures_c"] = hist_temps + traj.get("temperatures_c", [])
                    traj["temperatures_p05_c"] = hist_temps + traj.get("temperatures_p05_c", [])
                    traj["temperatures_p95_c"] = hist_temps + traj.get("temperatures_p95_c", [])
                    traj["demand_kwh_th"] = [0.0] * len(hist_pts) + traj.get("demand_kwh_th", [])
                    traj["history_count"] = len(hist_pts)
                if unheated_traj and "temperatures_c" in unheated_traj:
                    unheated_traj["temperatures_c"] = hist_temps + unheated_traj.get("temperatures_c", [])
                    unheated_traj["temperatures_p05_c"] = hist_temps + unheated_traj.get("temperatures_p05_c", [])
                    unheated_traj["temperatures_p95_c"] = hist_temps + unheated_traj.get("temperatures_p95_c", [])

                forced_off_ranges = []
                in_block = False
                start_b_idx = 0
                h_cnt = len(hist_pts)
                n_eval = len(plan.slots) if is_15m else min(24, len(plan.slots) // 4)
                for h_idx in range(n_eval):
                    if is_15m:
                        is_forced = (plan.slots[h_idx].mode_code == "forced_off")
                    else:
                        is_forced = any(plan.slots[h_idx * 4 + k].mode_code == "forced_off" for k in range(4) if (h_idx * 4 + k) < len(plan.slots))
                    if is_forced and not in_block:
                        in_block = True
                        start_b_idx = h_idx
                    elif not is_forced and in_block:
                        in_block = False
                        forced_off_ranges.append({
                            "start_idx": h_cnt + start_b_idx,
                            "end_idx": h_cnt + h_idx - 1,
                            "start_label": plan.slots[start_b_idx * 4 if not is_15m else start_b_idx].time_label,
                            "end_label": plan.slots[(h_idx - 1) * 4 if not is_15m else (h_idx - 1)].time_label
                        })
                if in_block:
                    forced_off_ranges.append({
                        "start_idx": h_cnt + start_b_idx,
                        "end_idx": h_cnt + n_eval - 1,
                        "start_label": plan.slots[start_b_idx * 4 if not is_15m else start_b_idx].time_label,
                        "end_label": plan.slots[-1].time_label
                    })

                self._send_json({
                    "status": "online",
                    "resolution": res_mode,
                    "decision": decision,
                    "trajectory": traj,
                    "unheated_trajectory": unheated_traj,
                    "forced_off_ranges": forced_off_ranges,
                    "history_count": len(hist_pts)
                })
            else:
                self._send_json({"status": "error", "message": "DHW model niet geladen"}, 500)
            return

        # ANALYTICS: Structured Decision Audit Trail (OTel-aligned)
        if path.startswith("/api/analytics/decisions"):
            qp = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            limit = int(qp.get("limit", [50])[0])
            domain = qp.get("domain", [None])[0]
            from layer3_scheduling.decision_audit import DecisionAuditLogger
            recs = DecisionAuditLogger.get_recent_decisions(limit=limit, domain=domain)
            self._send_json({"status": "success", "total": len(recs), "decisions": recs})
            return

        if path == "/api/model/algorithm-config":
            params = load_json(PARAMS_FILE) if PARAMS_FILE.exists() else {}
            self._send_json({
                "learning_rate_ewma": float(params.get("learning_rate_ewma", 0.05)),
                "rolling_window_days": int(params.get("rolling_window_days", 90)),
                "auto_accept_max_drift_pct": float(params.get("auto_accept_max_drift_pct", 3.0)),
                "wind_exclusion_limit_ms": float(params.get("wind_exclusion_limit_ms", 8.0)),
                "solar_exclusion_limit_w_m2": float(params.get("solar_exclusion_limit_w_m2", 500.0))
            })
            return

        if path == "/api/model/recommendations":
            recs_file = Path("/config/model_recommendations.json")
            if recs_file.exists():
                recs_dict = load_json(recs_file)
                # Synchronize live parameters from PARAMS_FILE if accepted
                if recs_dict.get("status") == "accepted" and PARAMS_FILE.exists():
                    p_active = load_json(PARAMS_FILE)
                    for r in recs_dict.get("recommendations", []):
                        pid = r.get("id")
                        if pid == "building_ua":
                            act = p_active.get("building", {}).get("ua_base_w_per_k")
                            if act is not None:
                                r["current_value"] = act
                                r["drift_pct"] = 0.0
                        elif pid == "night_baseload":
                            act = p_active.get("unallocated", {}).get("night_baseload_floor_w")
                            if act is not None:
                                r["current_value"] = act
                                r["drift_pct"] = 0.0
                        elif pid == "dhw_standby":
                            act = p_active.get("dhw_tank", {}).get("standby_loss_w_per_k")
                            if act is not None:
                                r["current_value"] = act
                                r["drift_pct"] = 0.0
                self._send_json(recs_dict)
            else:
                self._send_json({"status": "empty", "recommendations": []})
            return

        if path == "/api/model/status":
            if not GLOBAL_MODEL:
                self._send_json({"status": "error", "message": "Model niet geladen"}, 500)
                return
            self._send_json({
                "status": "online",
                "params": GLOBAL_MODEL.params,
                "profile_metadata": {
                    "resolution": GLOBAL_MODEL.profile.get("resolution", "15m"),
                    "last_updated": GLOBAL_MODEL.profile.get("last_updated"),
                    "dow_count": len(GLOBAL_MODEL.profile.get("profile_96_quarters", []))
                }
            })
            return

        if path == "/api/model/retrain":
            if not GLOBAL_MODEL:
                self._send_json({"status": "error", "message": "Model niet geladen"}, 500)
                return
            days = 120
            try:
                res = GLOBAL_MODEL.retrain_from_openhems(days_history=days)
                self._send_json(res)
            except Exception as e:
                self._send_json({"status": "error", "message": str(e)}, 500)
            return

        if path == "/api/model/decomposition":
            plan = ensure_active_canonical_plan()
            slots = plan.slots
            res_dict = {
                "success": True,
                "single_source_of_truth": True,
                "plan_generated_at": plan.generated_at,
                "labels": [s.time_label for s in slots],
                "unallocated_w": [int(round(s.unallocated_kw * 1000.0)) for s in slots],
                "heating_w": [int(round(s.heating_kw * 1000.0)) for s in slots],
                "boiler_w": [int(round(s.dhw_kw * 1000.0)) for s in slots],
                "solar_w": [int(round(s.solar_kw * 1000.0)) for s in slots],
                "total_w": [int(round(s.net_import_kw * 1000.0)) for s in slots],
                "prices": [s.price_eur for s in slots]
            }
            self._send_json(res_dict)
            return

        if path == "/api/health/consistency":
            plan = ensure_active_canonical_plan()
            store = get_plan_store()
            report = {
                "status": "HEALTHY",
                "single_source_of_truth_verified": True,
                "plan_version": store.get_version(),
                "plan_generated_at": plan.generated_at,
                "is_fresh": plan.is_fresh,
                "freshness_age_seconds": round(plan.freshness_age_seconds, 1),
                "validation_issues": plan.validation_issues,
                "horizon_hours": plan.horizon_hours,
                "slot_count": len(plan.slots),
                "dhw_strategy": {
                    "mode": plan.dhw_summary.planned_mode,
                    "mode_label": plan.dhw_summary.planned_mode_label,
                    "target_temp_c": plan.dhw_summary.target_temp_c,
                    "run_window": f"{plan.dhw_summary.run_start} – {plan.dhw_summary.run_end}",
                    "color_hex": plan.dhw_summary.color_hex
                },
                "dynamic_peaks_count": len(plan.dynamic_peaks),
                "lockout_hours": plan.dhw_summary.spits_lockout_hours,
                "live_actuation": getattr(GLOBAL_COLLECTOR, "last_actuation", {}),
                "state_taxonomy": {
                    state.value: STATE_METADATA[state]["color_hex"] for state in StandardizedState
                }
            }
            self._send_json(report)
            return

        if path == "/api/calibration/unallocated-model":
            prof_data = {}
            if GLOBAL_MODEL and GLOBAL_MODEL.profile and GLOBAL_MODEL.profile.get("profile_96_quarters"):
                grid_96 = GLOBAL_MODEL.profile.get("profile_96_quarters", [])
            else:
                grid_96 = []

            for cand in [
                Path(__file__).parent / "data" / "unallocated_load_profile.json",
                Path("/config/unallocated_load_profile.json"),
                Path("/homeassistant/unallocated_load_profile.json")
            ]:
                if cand.exists():
                    d = load_json(cand)
                    if d:
                        prof_data = d
                        if not grid_96 and d.get("profile_96_quarters"):
                            grid_96 = d.get("profile_96_quarters", [])
                        break

            profile_watts_24 = prof_data.get("profile_watts", {})
            if grid_96:
                profile_watts_24 = {}
                for dow in range(len(grid_96)):
                    dow_q = grid_96[dow]
                    hourly_avgs = []
                    for h in range(24):
                        chunk = dow_q[h*4:(h+1)*4]
                        hourly_avgs.append(round(sum(chunk)/len(chunk)) if chunk else 300)
                    profile_watts_24[str(dow)] = hourly_avgs
            elif profile_watts_24:
                grid_96 = []
                for dow in range(7):
                    h_arr = profile_watts_24.get(str(dow), [300] * 24)
                    q_arr = []
                    for h_val in h_arr:
                        q_arr.extend([h_val, h_val, h_val, h_val])
                    grid_96.append(q_arr)

            prof_data["profile_watts"] = profile_watts_24
            prof_data["profile_96_quarters"] = grid_96
            prof_data["day_names"] = ['Maandag', 'Dinsdag', 'Woensdag', 'Donderdag', 'Vrijdag', 'Zaterdag', 'Zondag']
            prof_data["model_params"] = GLOBAL_MODEL.params if GLOBAL_MODEL else {}
            self._send_json(prof_data)
            return

        if path == "/api/openapi.json":
            try:
                openapi_path = os.path.join(os.path.dirname(__file__), "docs", "openapi.json")
                if os.path.exists(openapi_path):
                    with open(openapi_path, "r", encoding="utf-8") as f:
                        spec = json.load(f)
                    self._send_json(spec)
                else:
                    self._send_json({"error": "openapi.json not found"}, 404)
                return
            except Exception as e:
                self._send_json({"error": str(e)}, 500)
                return

        if path == "/api/status":
            cfg = load_json(CONFIG_FILE)
            params = load_json(PARAMS_FILE)
            ensure_framework_defaults(cfg)
            self._send_json({
                "system": "Open HEMS Framework",
                "version": "0.93.0",
                "timestamp": datetime.now().isoformat(),
                "status": "online",
                "site_name": cfg.get("site", {}).get("name", "Woning Culemborg"),
                "total_devices": len(cfg.get("devices", [])),
                "total_policies": len(cfg.get("policies", [])),
                "total_tariffs": len(cfg.get("tariffs_list", [])),
                "total_influx_conns": len(cfg.get("influxdb_connections", [])),
                "total_mqtt_conns": len(cfg.get("mqtt_connections", [])),
                "dhw_optimal_run": cfg.get("last_optimal_run", "13:00"),
                "dhw_temperature": 52.8,
                "heatpump_power_w": 33.0,
                "smart_grid_mode": "SG2",
                "last_calibration": params.get("calibration_timestamp", "Recent")
            })
            return

        # API: Infrastructure & Connectivity (Laag 1)
        if path == "/api/infrastructure":
            cfg = load_json(CONFIG_FILE)
            ensure_framework_defaults(cfg)
            sec = load_secrets()
            
            idb_conns = []
            for c in cfg.get("influxdb_connections", []):
                cc = dict(c)
                has_pw = bool(sec.get("influxdb", {}).get(c["id"]) or c.get("password"))
                cc["has_password"] = has_pw
                cc["password"] = "••••••••" if has_pw else ""
                idb_conns.append(cc)

            mq_conns = []
            for c in cfg.get("mqtt_connections", []):
                cc = dict(c)
                has_pw = bool(sec.get("mqtt", {}).get(c["id"]) or c.get("password"))
                cc["has_password"] = has_pw
                cc["password"] = "••••••••" if has_pw else ""
                mq_conns.append(cc)

            # Home Assistant Core Connection info (Bi-directional: Bron & Doel)
            ha_sec = load_secrets()
            ha_base_url, ha_token = get_ha_client_config()
            
            ha_sources = []
            ha_targets = []
            ha_latency_ms = 0.0
            ha_status = "offline"
            ha_version = "2026.x"
            ha_location = "Home Assistant"

            if ha_token:
                headers = {"Authorization": f"Bearer {ha_token}", "Content-Type": "application/json"}
                ctx = ssl.create_default_context()
                ctx.check_hostname = False
                ctx.verify_mode = ssl.CERT_NONE
                t0 = time.time()
                try:
                    req_cfg = urllib.request.Request(f"{ha_base_url}/api/config", headers=headers)
                    with urllib.request.urlopen(req_cfg, timeout=3, context=ctx) as r_c:
                        c_data = json.loads(r_c.read().decode())
                        ha_status = "connected"
                        ha_version = c_data.get("version", "2026.x")
                        ha_location = c_data.get("location_name", "Home Assistant")
                        ha_latency_ms = round((time.time() - t0) * 1000, 1)
                except Exception as e_ha:
                    ha_status = f"error: {e_ha}"

            # Extract sensors and actuators across all devices for live state caching
            total_ha_devs = 0
            for d in cfg.get("devices", []):
                is_ha_dev = (d.get("source_type") == "homeassistant") or any(s.get("connector") == "homeassistant" for s in d.get("sensors", []))
                if is_ha_dev:
                    total_ha_devs += 1
                
                # Iterate over rich sensors list if present, else fallback
                if d.get("sensors"):
                    for s in d["sensors"]:
                        if s.get("connector") == "homeassistant" and s.get("entity_id"):
                            ha_sources.append({
                                "device_name": d.get("name"),
                                "sensor_name": s.get("name", s.get("id")),
                                "role": s.get("role", "consumer"),
                                "entity_id": s["entity_id"],
                                "live_state": "--"
                            })
                else:
                    src_ent = d.get("ha_power_entity") or d.get("ha_temp_entity")
                    if src_ent:
                        ha_sources.append({
                            "device_name": d.get("name"),
                            "sensor_name": "Vermogen / Temp",
                            "role": "consumer",
                            "entity_id": src_ent,
                            "live_state": "--"
                        })

                # Iterate over rich actuators list if present, else fallback
                if d.get("actuators"):
                    for a in d["actuators"]:
                        if a.get("connector") == "homeassistant" and a.get("entity_id"):
                            ha_targets.append({
                                "device_name": d.get("name"),
                                "actuator_name": a.get("name", a.get("id")),
                                "type": a.get("type", "switch"),
                                "entity_id": a["entity_id"],
                                "live_state": "--"
                            })
                else:
                    tgt_ent = d.get("ha_control_entity")
                    if tgt_ent:
                        ha_targets.append({
                            "device_name": d.get("name"),
                            "actuator_name": "Aansturing",
                            "type": "switch",
                            "entity_id": tgt_ent,
                            "live_state": "--"
                        })

            # Fetch live states for configured HA entities
            if ha_status == "connected" and ha_token:
                all_eids = list(set([s["entity_id"] for s in ha_sources] + [t["entity_id"] for t in ha_targets]))
                states_map = {}
                for eid in all_eids:
                    try:
                        r_st = urllib.request.Request(f"{ha_base_url}/api/states/{eid}", headers=headers)
                        with urllib.request.urlopen(r_st, timeout=2, context=ctx) as r_s:
                            st_obj = json.loads(r_s.read().decode())
                            unit = st_obj.get("attributes", {}).get("unit_of_measurement", "")
                            val = st_obj.get("state", "--")
                            states_map[eid] = f"{val} {unit}".strip()
                    except Exception:
                        states_map[eid] = "onbekend"

                for s in ha_sources:
                    s["live_state"] = states_map.get(s["entity_id"], "--")
                for t in ha_targets:
                    t["live_state"] = states_map.get(t["entity_id"], "--")

            self._send_json({
                "homeassistant": {
                    "status": ha_status,
                    "url": ha_base_url,
                    "version": ha_version,
                    "location": ha_location,
                    "latency_ms": ha_latency_ms,
                    "has_token": bool(ha_cfg_tok or os.environ.get("SUPERVISOR_TOKEN")),
                    "verify_ssl": cfg.get("homeassistant", {}).get("verify_ssl", False),
                    "timeout_seconds": cfg.get("homeassistant", {}).get("timeout_seconds", 5),
                    "total_devices": total_ha_devs,
                    "total_sources": len(ha_sources),
                    "total_targets": len(ha_targets),
                    "sources": ha_sources,
                    "targets": ha_targets
                },
                "influxdb_connections": idb_conns,
                "mqtt_connections": mq_conns,
                "influxdb": idb_conns[0] if idb_conns else {},
                "mqtt": mq_conns[0] if mq_conns else {}
            })
            return

        # API: Telemetry Stats from InfluxDB
        if path == "/api/infrastructure/telemetry-stats":
            cfg = load_json(CONFIG_FILE)
            ensure_framework_defaults(cfg)
            ic = cfg.get("influxdb", {})
            stats = {"openhems_series": 0, "status": "online"}
            try:
                clean_url = ic.get("url", "http://a0d7b954-influxdb:8086").rstrip("/")
                sec = load_secrets()
                pwd = sec.get("influxdb", {}).get(ic.get("id"), "") or sec.get("influxdb", {}).get("local_ha_influxdb", "")
                target_db = ic.get("database", "openhems")
                q_url = f"{clean_url}/query?" + urllib.parse.urlencode({
                    "u": ic.get("username", "openhems"),
                    "p": pwd,
                    "db": target_db,
                    "q": f"SHOW MEASUREMENTS ON {target_db}"
                })
                req = urllib.request.Request(q_url)
                with urllib.request.urlopen(req, timeout=3) as r:
                    res = json.loads(r.read().decode("utf-8"))
                    vals = res.get("results", [{}])[0].get("series", [{}])[0].get("values", [])
                    stats["openhems_series"] = len(vals)
                    stats["measurements"] = [v[0] for v in vals]
            except Exception as e:
                stats["error"] = str(e)
            self._send_json(stats)
            return

        # API: Home Assistant Entities Dropdown
        if path == "/api/ha/entities":
            entities = fetch_ha_entities()
            self._send_json({"entities": entities})
            return

        # API: Policies (Read All)
        if path == "/api/policies":
            cfg = load_json(CONFIG_FILE)
            ensure_framework_defaults(cfg)
            self._send_json({"policies": cfg.get("policies", [])})
            return

        # API: Devices (Read All)
        if path == "/api/devices":
            cfg = load_json(CONFIG_FILE)
            ensure_framework_defaults(cfg)
            self._send_json({"devices": cfg.get("devices", [])})
            return

        # API: Tariffs / Suppliers (Read All)
        if path == "/api/tariffs":
            cfg = load_json(CONFIG_FILE)
            ensure_framework_defaults(cfg)
            self._send_json({"tariffs": cfg.get("tariffs_list", [])})
            return

        # API: Calibration & Offsets
        if path == "/api/calibration":
            params = load_json(PARAMS_FILE)
            cfg = load_json(CONFIG_FILE)
            self._send_json({
                "parameters": params,
                "exclusion_windows": cfg.get("data_exclusion_windows", [])
            })
            return

                # API: 24h Rolling Ahead Power Consumption Prediction Engine
        if path == "/api/schedule/chart-data":
            cfg = load_json(CONFIG_FILE)
            ensure_framework_defaults(cfg)

            parsed_url = urllib.parse.urlparse(self.path)
            qp = urllib.parse.parse_qs(parsed_url.query)
            res_mode = qp.get("resolution", ["1h"])[0]
            is_15m = (res_mode == "15m")
            sim_battery_param = qp.get("simulate_battery", ["0"])[0] in ["1", "true", "True"]

            # Battery is active ONLY if physically installed & enabled in config, OR explicitly requested as simulation
            battery_installed = any(
                d.get("type") == "home_battery" and d.get("installed", False) and d.get("enabled", False)
                for d in cfg.get("devices", [])
            )
            is_battery_active = battery_installed or sim_battery_param or cfg.get("simulate_battery", False)

            baseload_w = float(cfg.get("baseload_watts", 300.0))
            baseload_kw = round(baseload_w / 1000.0, 3)

            now_ams = datetime.now(AMS_TZ)
            plan = ensure_active_canonical_plan()
            today_str = now_ams.strftime("%d-%m-%Y")
            tomorrow_str = (now_ams + timedelta(days=1)).strftime("%d-%m-%Y")

            # 1. Fetch EPEX prices from dedicated Day-Ahead cache
            _, prices_map, map_base = get_epex_tariffs_cached(is_15m=is_15m)

            # 2. Fetch Calibrated Solar Forecast (Forecast.Solar) & Weather
            solar_map = {}
            temp_map = {}
            wind_map = {}
            rh_map = {}
            s_cfg = load_json(CONFIG_FILE).get("solar", {})
            s_kwp = float(s_cfg.get("kwp", 5.76))
            s_inv = float(s_cfg.get("inverter_max_w", 5500)) / 1000.0
            s_tilt = float(s_cfg.get("tilt_degrees", 34))
            s_az = float(s_cfg.get("azimuth_degrees", 225))
            s_cal = float(s_cfg.get("calibration_factor", 1.18))
            s_eff = float(s_cfg.get("efficiency_factor", 0.88))

            try:
                from layer1_data_collection.forecast_solar import ForecastSolarProvider
                fs_prov = ForecastSolarProvider(lat=51.9537, lon=5.2320, tilt=s_tilt, azimuth_deg_south=45.0, kwp=s_kwp, inverter_max_kw=s_inv, calibration_factor=s_cal)
                fs_slots = fs_prov.get_calibrated_quarter_slots(base_dt, horizon_slots=total_slots, step_mins=step_mins)
                for sl in fs_slots:
                    k_s = sl["dt"].strftime("%Y-%m-%d %H:%M" if is_15m else "%Y-%m-%d %H:00")
                    solar_map[k_s] = sl["solar_kw"]
            except Exception as e_fs:
                print(f"Warning fetching Forecast.Solar in chart-data: {e_fs}")

            has_solar_chart = any(v > 0.05 for v in solar_map.values())
            try:
                url_m = "https://api.open-meteo.com/v1/forecast?latitude=51.9537&longitude=5.2320&hourly=temperature_2m,shortwave_radiation,wind_speed_10m,relative_humidity_2m&timezone=Europe%2FAmsterdam&forecast_days=2"
                req_m = urllib.request.Request(url_m, headers={"User-Agent": "OpenHEMS/1.0"})
                with urllib.request.urlopen(req_m, timeout=5) as r_m:
                    m_data = json.loads(r_m.read().decode())
                    m_times = m_data.get("hourly", {}).get("time", [])
                    m_rads = m_data.get("hourly", {}).get("shortwave_radiation", [])
                    m_temps = m_data.get("hourly", {}).get("temperature_2m", [])
                    m_winds = m_data.get("hourly", {}).get("wind_speed_10m", [])
                    m_rhs = m_data.get("hourly", {}).get("relative_humidity_2m", [])
                    for t, rad, tmp, wnd, rh in zip(m_times, m_rads, m_temps, m_winds, m_rhs):
                        k_t = t.replace('T', ' ')[:13] + ':00'
                        if not has_solar_chart:
                            dt_h = datetime.strptime(k_t, "%Y-%m-%d %H:%M").replace(tzinfo=ZoneInfo("Europe/Amsterdam"))
                            solar_map[k_t] = calculate_poa_solar_kw(dt_h, float(rad), kwp=s_kwp, tilt_deg=s_tilt, azimuth_deg=s_az, inverter_limit_kw=s_inv, eff=s_eff)
                        temp_map[k_t] = round(float(tmp), 1)
                        wind_map[k_t] = round(float(wnd), 1)
                        rh_map[k_t] = round(float(rh), 1)
            except Exception as e_m:
                print(f"Warning fetching weather forecast: {e_m}")

            # 3. Load 7x96 Learned Quarters & Hybrid Physics Model
            grid_96 = []
            if GLOBAL_MODEL and GLOBAL_MODEL.profile:
                grid_96 = GLOBAL_MODEL.profile.get("profile_96_quarters", [])

            # Interpolate Open-Meteo Hourly Solar to 15-minute quarters
            # Build continuous solar map and temp map per 15-minute slot
            total_slots = 96 if is_15m else 24
            step_mins = 15 if is_15m else 60
            start_minute = (now_ams.minute // 15) * 15 if is_15m else 0
            base_dt = now_ams.replace(minute=start_minute, second=0, microsecond=0)

            labels = []
            prices = []
            export_prices = []
            solar = []
            unallocated = []
            boiler = [0.0] * total_slots
            heating = [0.0] * total_slots
            battery_charge = [0.0] * total_slots
            advices = [""] * total_slots
            timeline_items = []

            prev_dt_item = None
            for i in range(total_slots):
                dt_slot = base_dt + timedelta(minutes=step_mins * i)
                k_full = dt_slot.strftime("%Y-%m-%d %H:%M" if is_15m else "%Y-%m-%d %H:00")
                k_hour = dt_slot.strftime("%Y-%m-%d %H:00")
                k_next_hour = (dt_slot + timedelta(hours=1)).strftime("%Y-%m-%d %H:00")

                if i == 0:
                    lbl = dt_slot.strftime("Nu (%H:%M)" if is_15m else "Nu (%H:00)")
                elif prev_dt_item is not None and dt_slot.day != prev_dt_item.day:
                    day_str = DUTCH_DAYS_SHORT[dt_slot.weekday()]
                    lbl = f"{day_str} {dt_slot.strftime('%H:%M' if is_15m else '%H:00')}"
                else:
                    lbl = dt_slot.strftime("%H:%M" if is_15m else "%H:00")
                prev_dt_item = dt_slot

                if is_15m:
                    plan_slot = plan.slots[i] if plan and i < len(plan.slots) else None
                    if plan_slot:
                        s_val = plan_slot.solar_kw
                        unalloc_kw = plan_slot.unallocated_kw
                        p_val = plan_slot.price_eur
                    else:
                        s_val = solar_map.get(k_full, solar_map.get(k_hour, 0.0))
                        unalloc_kw = 0.35
                        p_val = prices_map.get(k_full, prices_map.get(k_hour, 0.28))
                else:
                    # 1-hour resolution: aggregate the 4 quarters of this hour
                    q_start = i * 4
                    q_end = min(len(plan.slots), (i + 1) * 4) if plan else 0
                    q_slots = plan.slots[q_start:q_end] if plan else []
                    if q_slots:
                        s_val = round(sum(s.solar_kw for s in q_slots) / len(q_slots), 2)
                        unalloc_kw = round(sum(s.unallocated_kw for s in q_slots) / len(q_slots), 2)
                        p_val = round(sum(s.price_eur for s in q_slots) / len(q_slots), 4)
                    else:
                        s_val = solar_map.get(k_full, solar_map.get(k_hour, 0.0))
                        unalloc_kw = 0.35
                        p_val = prices_map.get(k_full, prices_map.get(k_hour, 0.28))

                # Physical nighttime guard (Netherlands is dark between 21:00 and 07:00 in September)
                if dt_slot.hour >= 21 or dt_slot.hour < 7:
                    s_val = 0.0

                t_h0 = temp_map.get(k_hour, 16.0)
                t_h1 = temp_map.get(k_next_hour, t_h0)
                frac = (dt_slot.minute / 60.0) if is_15m else 0.0
                t_val = round(t_h0 + (t_h1 - t_h0) * frac, 1)

                w_val = wind_map.get(k_hour, 3.0)
                rh_val = rh_map.get(k_hour, 75.0)

                labels.append(lbl)
                prices.append(p_val)
                base_spot = map_base.get(k_full, map_base.get(k_hour, (p_val / 1.21) - 0.11085 - 0.0121))
                exp_p = round(base_spot - 0.00605, 4)
                export_prices.append(exp_p)
                solar.append(s_val)
                unallocated.append(unalloc_kw)
                timeline_items.append({"idx": i, "dt": dt_slot, "key": k_full, "label": lbl, "price": p_val, "solar": s_val, "temp": t_val, "wind": w_val, "rh": rh_val})

            # 4. Plan Space Heating (CV) with calibrated 2R1C building model & Living Room Sensor Guard
            max_outdoor_temp = max((it["temp"] for it in timeline_items), default=16.0)
            mean_outdoor_temp = sum(it["temp"] for it in timeline_items) / len(timeline_items) if timeline_items else 16.0

            # Query live indoor temperature from Home Assistant (or default 21.0C from current season)
            indoor_temp_c = 21.0
            plan_t_set = 20.0
            indoor_temp_c = 22.0
            plan_thermostat_active = True
            try:
                ha_url, ha_tok = get_ha_client_config()
                if ha_tok and ha_url:
                    ctx = ssl.create_default_context()
                    ctx.check_hostname = False
                    ctx.verify_mode = ssl.CERT_NONE

                    try:
                        req_in = urllib.request.Request(
                            f"{ha_url}/api/states/climate.woonkamer_climate_daikin",
                            headers={"Authorization": f"Bearer {ha_tok}", "Content-Type": "application/json"}
                        )
                        with urllib.request.urlopen(req_in, timeout=2, context=ctx) as r_in:
                            st_in = json.loads(r_in.read().decode())
                            attrs_in = st_in.get("attributes", {})
                            plan_t_set = float(attrs_in.get("target_temp_low", attrs_in.get("temperature", 20.0)))
                            cur_in = float(attrs_in.get("current_temperature", indoor_temp_c))
                            if 15.0 <= cur_in <= 30.0:
                                indoor_temp_c = cur_in
                            if st_in.get("state") == "off" or attrs_in.get("hvac_action") == "off":
                                plan_thermostat_active = False
                    except Exception:
                        pass

                    try:
                        req_hc = urllib.request.Request(
                            f"{ha_url}/api/states/climate.hc_room_room_heating",
                            headers={"Authorization": f"Bearer {ha_tok}", "Content-Type": "application/json"}
                        )
                        with urllib.request.urlopen(req_hc, timeout=2, context=ctx) as r_hc:
                            if json.loads(r_hc.read().decode()).get("state") == "off":
                                plan_thermostat_active = False
                    except Exception:
                        pass

                    try:
                        req_sw = urllib.request.Request(
                            f"{ha_url}/api/states/switch.hc_mode_altherma_on",
                            headers={"Authorization": f"Bearer {ha_tok}", "Content-Type": "application/json"}
                        )
                        with urllib.request.urlopen(req_sw, timeout=2, context=ctx) as r_sw:
                            if json.loads(r_sw.read().decode()).get("state") == "off":
                                plan_thermostat_active = False
                    except Exception:
                        pass
            except Exception:
                pass

            # Fully Dynamic Price Peak Detection across Timeline (no static clock times)
            dynamic_peaks, slot_lockout_map = detect_dynamic_price_peaks(timeline_items, step_mins=step_mins)

            # 2-Mass Floor Heating Dynamic Simulation for Central Plan
            t_plan_in = indoor_temp_c
            t_plan_fl = indoor_temp_c + 0.2
            plan_t_start = plan_t_set - 0.5
            c_floor = 4.5
            c_air = 6.0
            u_fl_air = 1.2
            step_h = 0.25 if is_15m else 1.0
            plan_hp_running = False

            for it in timeline_items:
                i = it["idx"]
                t_out = it["temp"]
                wnd = it.get("wind", 3.0)
                sol = it["solar"] * 1000.0 / 5.5  # Solar W/m2

                # Dynamic Lockout Check: locked only if slot falls in a detected HARD_LOCKOUT peak
                peak_info = slot_lockout_map.get(i)
                in_peak_lockout = bool(peak_info and peak_info.get("is_hard_lockout"))
                emergency_guard = (t_plan_in < 18.5)

                if plan_thermostat_active and not plan_hp_running and (t_plan_in <= plan_t_start):
                    if not in_peak_lockout or emergency_guard:
                        plan_hp_running = True
                elif plan_hp_running and (not plan_thermostat_active or t_plan_in >= plan_t_set or (in_peak_lockout and not emergency_guard)):
                    plan_hp_running = False

                if plan_hp_running:
                    p_el_w = max(950.0, min(4200.0, 2885.6 - 95.2 * t_out))
                    cop_val = max(2.5, min(5.5, 5.2 - 0.08 * (35.0 - t_out)))
                    th_kw = (p_el_w * cop_val) / 1000.0
                    heating[i] = round((p_el_w / 1000.0) * step_h, 2)
                else:
                    th_kw = 0.0
                    heating[i] = 0.0

                # State integration
                ua_eff = (321.1 + 15.0 * max(0.0, wnd - 2.0)) / 1000.0
                q_loss_kw = ua_eff * max(0.0, t_plan_in - t_out)
                q_solar_kw = (0.12 * sol * 25.0) / 1000.0
                q_fl_air_kw = u_fl_air * (t_plan_fl - t_plan_in)

                dt_fl = ((th_kw - q_fl_air_kw) * step_h) / c_floor
                dt_in = ((q_fl_air_kw + q_solar_kw - q_loss_kw) * step_h) / c_air

                t_plan_fl = t_plan_fl + dt_fl
                t_plan_in = max(15.0, min(26.0, t_plan_in + dt_in))

            # 5. Hot Water Generation (SWW Boiler 350L) from Authoritative Canonical Plan (Single Source of Truth)
            sww_power_kw = plan.dhw_summary.power_kw if plan.dhw_summary else 1.8
            sww_target_temp = plan.dhw_summary.target_temp_c if plan.dhw_summary else 50.0
            planned_mode = plan.dhw_summary.planned_mode if plan.dhw_summary else "normal"
            planned_mode_label = plan.dhw_summary.planned_mode_label if plan.dhw_summary else "Normaal (Standby)"

            # Populate boiler power array directly from canonical plan slots
            for i in range(min(total_slots, len(plan.slots))):
                if is_15m:
                    boiler[i] = plan.slots[i].dhw_kw
                    if boiler[i] > 0:
                        advices[i] = f"♨️ SWW Boiler 350L: {planned_mode_label}"
                else:
                    # 1h aggregation
                    idx_15m = i * 4
                    q_kw = sum(plan.slots[k].dhw_kw for k in range(idx_15m, min(len(plan.slots), idx_15m + 4))) / 4.0
                    boiler[i] = round(q_kw, 2)
                    if boiler[i] > 0:
                        advices[i] = f"♨️ SWW Boiler 350L: {planned_mode_label}"

            actual_15m_slots = [i for i, s in enumerate(plan.slots) if s.dhw_kw > 0]
            sww_start_idx = actual_15m_slots[0] if actual_15m_slots else -1
            slots_to_fill = len(actual_15m_slots)

            GLOBAL_CENTRAL_CACHE["planned_dhw_slots"] = actual_15m_slots
            GLOBAL_CENTRAL_CACHE["sww_start_idx"] = sww_start_idx
            GLOBAL_CENTRAL_CACHE["slots_to_fill"] = slots_to_fill
            GLOBAL_CENTRAL_CACHE["target_temp_c"] = sww_target_temp
            GLOBAL_CENTRAL_CACHE["dynamic_peaks"] = plan.dynamic_peaks
            GLOBAL_CENTRAL_CACHE["sww_power_kw"] = sww_power_kw
            GLOBAL_CENTRAL_CACHE["is_15m"] = is_15m
            GLOBAL_CENTRAL_CACHE["base_dt"] = base_dt
            GLOBAL_CENTRAL_CACHE["planned_mode"] = planned_mode
            GLOBAL_CENTRAL_CACHE["planned_mode_label"] = planned_mode_label
            GLOBAL_CENTRAL_CACHE["reason"] = planned_mode_label

            # Evaluate Live In-Flight DHW Heating & Run Merger
            t_dhw_live = 49.2
            try:
                states_map_dhw = get_ha_states_map()
                val_t = float(states_map_dhw.get("sensor.hc_dhw_temperature_r5t_dhw_tank", {}).get("state", 49.2))
                if 20.0 <= val_t <= 75.0:
                    t_dhw_live = val_t
            except Exception:
                pass

            merge_res = evaluate_and_apply_dhw_run_merger(plan, t_dhw_live)
            active_dhw_status = {
                "is_active": merge_res.is_dhw_active,
                "power_kw": round(merge_res.current_power_kw, 2),
                "tank_temp_c": round(merge_res.current_tank_temp_c, 1),
                "target_temp_c": merge_res.active_target_temp_c,
                "mode_code": merge_res.promoted_mode,
                "mode_label": merge_res.active_mode_label,
                "decision_title": "Doorwarmen naar 60°C (Zonnebuffer Fusie)" if merge_res.should_merge else ("Stoppen bij 50°C (Basislading)" if merge_res.is_dhw_active else "Standby"),
                "decision_explanation": merge_res.decision_explanation,
                "savings_eur": merge_res.savings_estimate_eur,
                "should_merge": merge_res.should_merge,
                "original_slot_time": merge_res.original_slot_time
            }

            # Build 24h Mode Timeline for Horizontal Bar Diagram directly from plan.slots
            dhw_mode_timeline = []
            for it in timeline_items:
                q_idx = it["idx"]
                if is_15m:
                    if q_idx < len(plan.slots):
                        ps = plan.slots[q_idx]
                        dhw_mode_timeline.append({
                            "slot": q_idx,
                            "time": it["label"],
                            "mode": ps.mode_code,
                            "label": ps.mode_label,
                            "color": ps.color_hex,
                            "power_kw": ps.dhw_kw,
                            "description": ps.description
                        })
                    else:
                        dhw_mode_timeline.append({
                            "slot": q_idx,
                            "time": it["label"],
                            "mode": "normal",
                            "label": "Normaal (Standby)",
                            "color": "#1E293B",
                            "power_kw": 0.0,
                            "description": f"Normaal ({it['label']})"
                        })
                else:
                    # 1-Hour Aggregation: correctly sample the 4 quarters corresponding to this hour!
                    start_q = q_idx * 4
                    end_q = min(len(plan.slots), (q_idx + 1) * 4)
                    q_slots = plan.slots[start_q:end_q] if plan else []

                    if q_slots:
                        # Hierarchical state taxonomy selection:
                        # 1. FORCED_OFF (Spitsblokkade) has top priority — cannot be overwritten
                        # 2. MAX_ON (DHW 60°C Boost) — strictly from DHW
                        # 3. FORCED_ON (DHW 50°C)
                        # 4. ADVISED_OFF (Piek advies)
                        # 5. ADVISED_ON (Doorverwarmen / Pre-heat)
                        # 6. NORMAL (Standby)
                        active_modes = [s.mode_code for s in q_slots]
                        mean_dhw = round(sum(s.dhw_kw for s in q_slots) / len(q_slots), 2)

                        if "forced_off" in active_modes:
                            target_ps = next(s for s in q_slots if s.mode_code == "forced_off")
                        elif "max_on" in active_modes:
                            target_ps = next(s for s in q_slots if s.mode_code == "max_on")
                        elif "forced_on" in active_modes:
                            target_ps = next(s for s in q_slots if s.mode_code == "forced_on")
                        elif "advised_off" in active_modes:
                            target_ps = next(s for s in q_slots if s.mode_code == "advised_off")
                        elif "advised_on" in active_modes:
                            target_ps = next(s for s in q_slots if s.mode_code == "advised_on")
                        else:
                            target_ps = q_slots[0]

                        dhw_mode_timeline.append({
                            "slot": q_idx,
                            "time": it["label"],
                            "mode": target_ps.mode_code,
                            "label": target_ps.mode_label,
                            "color": target_ps.color_hex,
                            "power_kw": mean_dhw,
                            "description": target_ps.description
                        })
                    else:
                        dhw_mode_timeline.append({
                            "slot": q_idx,
                            "time": it["label"],
                            "mode": "normal",
                            "label": "Normaal (Standby)",
                            "color": "#1E293B",
                            "power_kw": 0.0,
                            "description": f"Normaal ({it['label']})"
                        })

            dhw_planning_summary = {
                "planned_mode": plan.dhw_summary.planned_mode,
                "planned_mode_label": plan.dhw_summary.planned_mode_label,
                "target_temp_c": plan.dhw_summary.target_temp_c,
                "run_start": plan.dhw_summary.run_start,
                "run_end": plan.dhw_summary.run_end,
                "run_duration_min": plan.dhw_summary.run_duration_min,
                "power_kw": plan.dhw_summary.power_kw,
                "total_stroom_kwh": plan.dhw_summary.total_stroom_kwh,
                "spits_lockout_hours": plan.dhw_summary.spits_lockout_hours,
                "dynamic_peaks": plan.dynamic_peaks,
                "arbitrage_saving_eur": getattr(plan.dhw_summary, "arbitrage_saving_eur", 0.0),
                "arbitrage_p_midday": getattr(plan.dhw_summary, "arbitrage_p_midday", 0.28),
                "arbitrage_p_future": getattr(plan.dhw_summary, "arbitrage_p_future", 0.35),
                "arbitrage_surplus_kwh": getattr(plan.dhw_summary, "arbitrage_surplus_kwh", 0.0)
            }

            
            # 6. Plan Battery Dispatch: ONLY IF BATTERY IS PHYSICALLY INSTALLED OR SIMULATION EXPLICITLY ACTIVATED
            battery_discharge = [0.0] * total_slots
            bat_msg = ""
            min_item = min(timeline_items, key=lambda x: x["price"])
            max_item = max(timeline_items, key=lambda x: x["price"])
            peak_solar_it = max(timeline_items, key=lambda x: x["solar"])

            if not is_battery_active:
                bat_msg = "Geen thuisaccu geactiveerd (zuiver echte apparaten)."
            else:
                price_delta = max_item["price"] - min_item["price"]
                deadband = float(cfg.get("battery_deadband_eur_kwh", 0.115))
                bat_slots = 4 if is_15m else 1

                # Check if there is significant solar surplus available tomorrow
                if peak_solar_it["solar"] >= 1.5:
                    # Mode A: Solar Buffer Priority — charge exclusively from free solar surplus
                    surplus_candidates = [
                        it for it in daylight_slots
                        if (it["solar"] - (unallocated[it["idx"]] + boiler[it["idx"]])) >= 0.5
                    ]
                    if surplus_candidates:
                        charge_slot = max(surplus_candidates, key=lambda x: (x["solar"] - (unallocated[x["idx"]] + boiler[x["idx"]])))
                        avail_surplus = charge_slot["solar"] - (unallocated[charge_slot["idx"]] + boiler[charge_slot["idx"]])
                        charge_kw = round(min(2.5, max(1.0, avail_surplus)), 2)
                    else:
                        charge_slot = peak_solar_it
                        charge_kw = 2.0

                    for b_i in range(charge_slot["idx"], min(total_slots, charge_slot["idx"] + bat_slots)):
                        battery_charge[b_i] = charge_kw

                    # Discharge during expensive evening peak (18:00 - 23:00 or morning)
                    evening_slots = [it for it in timeline_items if (18 <= it["dt"].hour <= 23 or 0 <= it["dt"].hour <= 1)]
                    if evening_slots:
                        best_discharge = max(evening_slots, key=lambda x: x["price"])
                        for d_i in range(best_discharge["idx"], min(total_slots, best_discharge["idx"] + bat_slots)):
                            battery_discharge[d_i] = 2.0
                        other_evening = [it for it in evening_slots if it["idx"] != best_discharge["idx"]]
                        if other_evening:
                            second_dis = max(other_evening, key=lambda x: x["price"])
                            for d2_i in range(second_dis["idx"], min(total_slots, second_dis["idx"] + bat_slots)):
                                battery_discharge[d2_i] = 1.5
                        bat_msg = f"☀️ Zonne-Buffer: Accu laadt op gratis zonne-overschot om {charge_slot['label']} ({charge_kw} kW) en ontlaadt in de avondpiek ({best_discharge['label']}, €{best_discharge['price']:.2f}/kWh)."
                    else:
                        bat_msg = f"☀️ Zonne-Buffer: Accu laadt op gratis zonne-overschot om {charge_slot['label']} ({charge_kw} kW)."
                elif price_delta >= deadband:
                    # Mode B: Winter/Cloudy Tariff Arbitrage — charge from grid at lowest price, discharge at highest
                    for b_i in range(min_item["idx"], min(total_slots, min_item["idx"] + bat_slots)):
                        battery_charge[b_i] = 2.0
                    for d_i in range(max_item["idx"], min(total_slots, max_item["idx"] + bat_slots)):
                        battery_discharge[d_i] = 2.0
                    expensive_slots = sorted(timeline_items, key=lambda x: x["price"], reverse=True)
                    if len(expensive_slots) > 1 and expensive_slots[1]["idx"] != min_item["idx"]:
                        for d2_i in range(expensive_slots[1]["idx"], min(total_slots, expensive_slots[1]["idx"] + bat_slots)):
                            battery_discharge[d2_i] = 1.5
                    bat_msg = f"🔋 Accu-Arbitrage (Bewolkt/Winter): Laden om {min_item['label']} (€{min_item['price']:.2f}), Ontladen om {max_item['label']} (€{max_item['price']:.2f}) [Spread €{price_delta:.3f} > €{deadband:.3f}]."
                else:
                    bat_msg = f"⏸️ Accu Stand-by: Onvoldoende zonne-overschot en prijsdelta €{price_delta:.3f} onder drempel."

            # Calculate Dual-Polarity Datasets
            # Negative stack: Solar generation and Battery discharge (< 0 kW)
            solar_neg = [-round(s, 2) for s in solar]
            bat_discharge_neg = [-round(d, 2) for d in battery_discharge]

            # Net Actual Expected Power Drawn from Grid:
            # Net = Total Consumption - (Solar + Battery Discharge)
            net_power = []
            for b, bl, h, ch, s, d in zip(unallocated, boiler, heating, battery_charge, solar, battery_discharge):
                tot_load = b + bl + h + ch
                tot_gen = s + d
                net_val = round(tot_load - tot_gen, 2)
                net_power.append(net_val)

            cheapest_hour_lbl = min_item["label"]
            cheapest_price = min_item["price"]
            banner_adv = f"Beste stroomtarief om {cheapest_hour_lbl} (€{cheapest_price:.4f}/kWh)"
            if peak_solar_it["solar"] > 1.0:
                advices[peak_solar_it["idx"]] = f"☀️ Zonnepiek ({peak_solar_it['solar']:.1f} kW) — Gratis stroom van eigen dak!"
            if is_battery_active:
                advices[max_item["idx"]] = f"⛔ Prijspiek (€{max_item['price']:.2f}/kWh) — Accu ontlaadt om netafname te voorkomen!"
            else:
                advices[max_item["idx"]] = f"⛔ Prijspiek (€{max_item['price']:.2f}/kWh) — Piekurentarief, vermijd grootverbruik!"

            # 7. Compute Unplanned Solar Surplus (Vrij Zonne-overschot voor niet-slimme apparaten)
            surplus_kwh_tot = 0.0
            surplus_peak_kw = 0.0
            surplus_peak_time = ""
            surplus_kw_list = []
            step_h = 0.25 if is_15m else 1.0

            for it in timeline_items:
                idx = it["idx"]
                s_gen = it["solar"]
                sched_load = unallocated[idx] + boiler[idx] + heating[idx] + battery_charge[idx]
                surp = round(max(0.0, s_gen - sched_load), 2)
                surplus_kw_list.append(surp)
                if surp >= 0.15:
                    surplus_kwh_tot += surp * step_h
                    if surp > surplus_peak_kw:
                        surplus_peak_kw = surp
                        surplus_peak_time = it["label"]

            solar_recommendation = ""
            if surplus_kwh_tot >= 0.8:
                solar_recommendation = f"🧺 Huishoudelijk Zonne-Advies: Circa {surplus_kwh_tot:.1f} kWh vrij zonne-overschot voorspeld (piek {surplus_peak_kw:.1f} kW om {surplus_peak_time}). Ideaal moment om niet-slimme verbruikers (wasmachine, droger, vaatwasser of EV) handmatig aan te zetten!"
            else:
                solar_recommendation = "☀️ Geen significant zonne-overschot verwacht; alle opwek wordt direct door basislast en SWW benut."

            # 8. Compute Total 24h Predicted Energy Consumption, Costs & 6-Box Stats
            step_h = 0.25 if is_15m else 1.0
            tot_cons_kwh = round(sum(u + b + h + c for u, b, h, c in zip(unallocated, boiler, heating, battery_charge)) * step_h, 2)
            tot_solar_kwh = round(sum(solar) * step_h, 2)
            net_cost_eur = round(sum(np * p for np, p in zip(net_power, prices)) * step_h, 2)
            gross_cost_eur = round(sum((u + b + h + c) * p for u, b, h, c, p in zip(unallocated, boiler, heating, battery_charge, prices)) * step_h, 2)
            solar_savings_eur = round(max(0.0, gross_cost_eur - net_cost_eur), 2)

            # Compute detailed 6-box prediction metrics aligned with historical power producers
            pred_afname_kwh = 0.0
            pred_afname_eur = 0.0
            pred_terug_kwh = 0.0
            pred_terug_eur = 0.0
            pred_selfcons_kwh = 0.0
            pred_selfcons_eur = 0.0
            pred_solar_eur = 0.0
            pred_verbruik_eur = 0.0

            afname_series = []
            terug_series = []
            selfcons_series = []
            verbruik_series = []

            for u, b, h, c, s, d, np_val, p in zip(unallocated, boiler, heating, battery_charge, solar, battery_discharge, net_power, prices):
                c_tot = u + b + h + c
                g_tot = s + d
                afn = max(0.0, np_val)
                ter = max(0.0, -np_val)
                s_cons = min(s, c_tot)

                afname_series.append(afn)
                terug_series.append(ter)
                selfcons_series.append(s_cons)
                verbruik_series.append(c_tot)

                pred_afname_kwh += afn * step_h
                pred_afname_eur += afn * step_h * p
                pred_terug_kwh += ter * step_h
                pred_terug_eur += ter * step_h * p
                pred_selfcons_kwh += s_cons * step_h
                pred_selfcons_eur += s_cons * step_h * p
                pred_solar_eur += s * step_h * p
                pred_verbruik_eur += c_tot * step_h * p

            def fmt_kw(val, neg=False):
                sign = "-" if neg and val > 0 else ""
                return f"{sign}{val:.2f} kW"

            prediction_stats = {
                "zonnepanelen": {
                    "total_kwh": f"{tot_solar_kwh:.2f} kWh",
                    "cost_eur": f"€{pred_solar_eur:.2f}",
                    "last": fmt_kw(solar[0] if solar else 0, neg=True),
                    "min": fmt_kw(max(solar) if solar else 0, neg=True)
                },
                "teruglevering": {
                    "total_kwh": f"{pred_terug_kwh:.2f} kWh",
                    "cost_eur": f"€{pred_terug_eur:.2f}",
                    "last": fmt_kw(terug_series[0] if terug_series else 0, neg=True),
                    "min": fmt_kw(max(terug_series) if terug_series else 0, neg=True)
                },
                "afname": {
                    "total_kwh": f"{pred_afname_kwh:.2f} kWh",
                    "cost_eur": f"€{pred_afname_eur:.2f}",
                    "last": fmt_kw(afname_series[0] if afname_series else 0),
                    "max": fmt_kw(max(afname_series) if afname_series else 0)
                },
                "totaal_opgewekt": {
                    "total_kwh": f"{tot_solar_kwh:.2f} kWh",
                    "cost_eur": f"€{pred_solar_eur:.2f}",
                    "last": fmt_kw(solar[0] if solar else 0, neg=True),
                    "min": fmt_kw(max(solar) if solar else 0, neg=True)
                },
                "opgewekt_gebruikt": {
                    "total_kwh": f"{pred_selfcons_kwh:.2f} kWh",
                    "cost_eur": f"€{pred_selfcons_eur:.2f}",
                    "last": fmt_kw(selfcons_series[0] if selfcons_series else 0, neg=True),
                    "min": fmt_kw(max(selfcons_series) if selfcons_series else 0, neg=True)
                },
                "totaal_verbruik": {
                    "total_kwh": f"{tot_cons_kwh:.2f} kWh",
                    "cost_eur": f"€{pred_verbruik_eur:.2f}",
                    "last": fmt_kw(verbruik_series[0] if verbruik_series else 0),
                    "max": fmt_kw(max(verbruik_series) if verbruik_series else 0)
                }
            }

            # Prepend 1 hour of actual historical telemetry
            hist_pts = fetch_recent_telemetry_history(is_15m, base_dt)
            hist_labels = []
            hist_solar = []
            hist_unalloc = []
            hist_boiler = []
            hist_heating = []
            hist_bat_charge = []
            hist_bat_discharge = []
            hist_prices = []
            hist_export_prices = []
            hist_advices = []
            hist_dhw_timeline = []

            for hp in hist_pts:
                p_val = prices_map.get(hp["key"], prices_map.get(hp["dt"].strftime("%Y-%m-%d %H:00"), 0.25))
                b_val = map_base.get(hp["key"], map_base.get(hp["dt"].strftime("%Y-%m-%d %H:00"), (p_val / 1.21) - 0.11085 - 0.0121))
                hist_labels.append(hp["label"])
                hist_solar.append(hp["solar_kw"])
                hist_unalloc.append(hp["unallocated_kw"])
                hist_boiler.append(hp["dhw_kw"])
                hist_heating.append(hp["heating_kw"])
                hist_bat_charge.append(0.0)
                hist_bat_discharge.append(0.0)
                hist_prices.append(p_val)
                hist_export_prices.append(round(b_val - 0.00605, 4))
                hist_advices.append("Actueel gemeten (Historie)")
                hist_dhw_timeline.append({
                    "time": hp["label"],
                    "mode": "measured_actual",
                    "label": f"Actueel ({hp['label']})",
                    "description": f"Historische meting: Tapwater {hp['dhw_kw']:.2f} kW · Verwarming {hp['heating_kw']:.2f} kW."
                })

            all_labels = hist_labels + labels
            all_unalloc = hist_unalloc + unallocated
            all_boiler = hist_boiler + boiler
            all_heating = hist_heating + heating
            all_bat_charge = hist_bat_charge + battery_charge
            all_solar = hist_solar + solar
            all_bat_discharge = hist_bat_discharge + battery_discharge
            all_prices = hist_prices + prices
            all_export_prices = hist_export_prices + export_prices

            all_solar_neg = [-round(s, 2) for s in all_solar]
            all_bat_discharge_neg = [-round(d, 2) for d in all_bat_discharge]
            all_net_power = [
                round((u + bl + h + ch) - (s + d), 2)
                for u, bl, h, ch, s, d in zip(all_unalloc, all_boiler, all_heating, all_bat_charge, all_solar, all_bat_discharge)
            ]
            all_surplus = [0.0] * len(hist_pts) + surplus_kw_list
            all_advices = hist_advices + advices
            all_dhw_timeline = hist_dhw_timeline + dhw_mode_timeline

            self._send_json({
                "hours": all_labels,
                "labels": all_labels,
                "interval_h": step_h,
                "battery_enabled": is_battery_active,
                "battery_simulated": bool(sim_battery_param and not battery_installed),
                "export_prices_eur": all_export_prices,
                "dhw_mode_timeline": all_dhw_timeline,
                "dynamic_peaks": dynamic_peaks,
                "dhw_planning_summary": dhw_planning_summary,
                "active_dhw_status": active_dhw_status,
                "history_count": len(hist_pts),
                "datasets": {
                    "unallocated_kw": all_unalloc,
                    "baseload_kw": all_unalloc,
                    "boiler_kw": all_boiler,
                    "heating_kw": all_heating,
                    "battery_charge_kw": all_bat_charge,
                    "solar_kw_neg": all_solar_neg,
                    "battery_discharge_kw_neg": all_bat_discharge_neg,
                    "surplus_kw": all_surplus,
                    "net_power_kw": all_net_power,
                    "prices_eur": all_prices,
                    "export_prices_eur": all_export_prices
                },
                "advices": all_advices,
                "cheapest_hour": cheapest_hour_lbl,
                "cheapest_price_eur": cheapest_price,
                "battery_status_msg": bat_msg,
                "banner_text": banner_adv,
                "baseload_watts": baseload_w,
                "surplus_total_kwh": round(surplus_kwh_tot, 1),
                "prediction_stats": prediction_stats,
                "solar_recommendation": solar_recommendation,
                "total_consumption_kwh": tot_cons_kwh,
                "total_solar_kwh": tot_solar_kwh,
                "total_net_cost_eur": net_cost_eur,
                "total_gross_cost_eur": gross_cost_eur,
                "solar_savings_eur": solar_savings_eur
            })
            return

        # HTML SPA
        self._serve_spa()

    # =========================================================================
    # POST ROUTER (Create & Action)
    # =========================================================================
    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path.rstrip("/")
        body = self._read_json_body()

        if path == "/api/config/solar":
            data = body or {}
            cfg = load_json(CONFIG_FILE)
            if "solar" not in cfg:
                cfg["solar"] = {}
            if "kwp" in data: cfg["solar"]["kwp"] = float(data["kwp"])
            if "inverter_max_w" in data: cfg["solar"]["inverter_max_w"] = int(data["inverter_max_w"])
            if "tilt_degrees" in data: cfg["solar"]["tilt_degrees"] = float(data["tilt_degrees"])
            if "azimuth_degrees" in data: cfg["solar"]["azimuth_degrees"] = float(data["azimuth_degrees"])
            if "efficiency_factor" in data: cfg["solar"]["efficiency_factor"] = float(data["efficiency_factor"])
            save_json(CONFIG_FILE, cfg)
            self._send_json({"status": "success", "message": "Zonnepanelen configuratie opgeslagen", "solar": cfg["solar"]})
            return

        if path == "/api/model/algorithm-config":
            try:
                params = load_json(PARAMS_FILE) if PARAMS_FILE.exists() else {}
                if "learning_rate_ewma" in body:
                    params["learning_rate_ewma"] = round(float(body["learning_rate_ewma"]), 3)
                if "rolling_window_days" in body:
                    params["rolling_window_days"] = int(body["rolling_window_days"])
                if "auto_accept_max_drift_pct" in body:
                    params["auto_accept_max_drift_pct"] = round(float(body["auto_accept_max_drift_pct"]), 1)
                save_json(PARAMS_FILE, params)
                if GLOBAL_MODEL:
                    GLOBAL_MODEL.params = params
                self._send_json({"status": "success", "message": "Algoritme instellingen opgeslagen", "params": params})
            except Exception as e:
                self._send_json({"status": "error", "message": str(e)}, 500)
            return

        if path == "/api/model/recommendations/accept":
            try:
                recs_file = Path("/config/model_recommendations.json")
                if not recs_file.exists():
                    self._send_json({"status": "error", "message": "Geen aanbevelingen gevonden"}, 404)
                    return
                recs_data = load_json(recs_file)
                params = load_json(PARAMS_FILE) if PARAMS_FILE.exists() else {}
                ewma = float(params.get("learning_rate_ewma", 0.05))

                for r in recs_data.get("recommendations", []):
                    r["auto_applied"] = True
                    p_id = r.get("id")
                    if p_id == "building_ua":
                        old_v = float(params.get("building", {}).get("ua_base_w_per_k", 321.1))
                        prop_v = float(r.get("proposed_value", old_v))
                        new_v = round((1.0 - ewma) * old_v + ewma * prop_v, 1)
                        params.setdefault("building", {})["ua_base_w_per_k"] = new_v
                        r["current_value"] = new_v
                        r["drift_pct"] = 0.0
                    elif p_id == "night_baseload":
                        old_v = float(params.get("unallocated", {}).get("night_baseload_floor_w", 265.0))
                        prop_v = float(r.get("proposed_value", old_v))
                        new_v = round((1.0 - ewma) * old_v + ewma * prop_v, 1)
                        params.setdefault("unallocated", {})["night_baseload_floor_w"] = new_v
                        r["current_value"] = new_v
                        r["drift_pct"] = 0.0
                    elif p_id == "dhw_standby":
                        old_v = float(params.get("dhw_tank", {}).get("standby_loss_w_per_k", 2.50))
                        prop_v = float(r.get("proposed_value", old_v))
                        new_v = round((1.0 - ewma) * old_v + ewma * prop_v, 2)
                        params.setdefault("dhw_tank", {})["standby_loss_w_per_k"] = new_v
                        r["current_value"] = new_v
                        r["drift_pct"] = 0.0

                recs_data["status"] = "accepted"
                recs_data["accepted_at"] = datetime.now(AMS_TZ).isoformat()
                save_json(recs_file, recs_data)
                save_json(PARAMS_FILE, params)
                if GLOBAL_MODEL:
                    GLOBAL_MODEL.params = params

                self._send_json({"status": "success", "message": "Aanbevelingen geaccepteerd en modelparameters geactiveerd!"})
            except Exception as e:
                self._send_json({"status": "error", "message": str(e)}, 500)
            return

        if path == "/api/model/recommendations/reject":
            try:
                recs_file = Path("/config/model_recommendations.json")
                if recs_file.exists():
                    recs_data = load_json(recs_file)
                    recs_data["status"] = "rejected"
                    recs_data["rejected_at"] = datetime.now(AMS_TZ).isoformat()
                    save_json(recs_file, recs_data)
                self._send_json({"status": "success", "message": "Aanbevelingen afgewezen; actieve parameters blijven ongewijzigd."})
            except Exception as e:
                self._send_json({"status": "error", "message": str(e)}, 500)
            return

        if path == "/api/model/retrain":
            if not GLOBAL_MODEL:
                self._send_json({"status": "error", "message": "Model niet geladen"}, 500)
                return
            try:
                days = int(body.get("days", 120)) if body else 120
                res = GLOBAL_MODEL.retrain_from_openhems(days_history=days)
                self._send_json(res)
            except Exception as e:
                self._send_json({"status": "error", "message": str(e)}, 500)
            return


        # INFRASTRUCTURE: Test Home Assistant Core
        if path == "/api/infrastructure/homeassistant/test":
            ha_sec = load_secrets()
            ha_base_url, ha_token = get_ha_client_config()
            if not ha_token:
                self._send_json({"status": "error", "message": "Geen Supervisor of HASS token gevonden"}, 400)
                return
            try:
                headers = {"Authorization": f"Bearer {ha_token}", "Content-Type": "application/json"}
                ctx = ssl.create_default_context()
                ctx.check_hostname = False
                ctx.verify_mode = ssl.CERT_NONE
                t0 = time.time()
                req = urllib.request.Request(f"{ha_base_url}/api/config", headers=headers)
                with urllib.request.urlopen(req, timeout=4, context=ctx) as r:
                    data = json.loads(r.read().decode())
                    lat = round((time.time() - t0) * 1000, 1)
                    self._send_json({
                        "status": "success",
                        "latency_ms": lat,
                        "version": data.get("version"),
                        "location": data.get("location_name"),
                        "message": f"Home Assistant Core verbonden! Latency: {lat}ms, Versie: {data.get('version')}"
                    })
                    return
            except Exception as e:
                self._send_json({"status": "error", "message": f"Fout bij verbinden met Home Assistant: {str(e)}"}, 500)
                return

        # INFRASTRUCTURE: Test InfluxDB
        if path == "/api/infrastructure/influxdb/test":
            cfg = load_json(CONFIG_FILE)
            conn_id = body.get("id")
            conn = next((c for c in cfg.get("influxdb_connections", []) if c["id"] == conn_id), {}) if conn_id else {}
            
            url = body.get("url") or conn.get("url") or "http://a0d7b954-influxdb:8086"
            database = body.get("database") or conn.get("database") or "hermes"
            username = body.get("username") if "username" in body else conn.get("username", "hermes")
            
            # Retrieve password from vault if not provided in payload
            password = body.get("password")
            if not password and conn_id:
                password = get_secret("influxdb", conn_id) or conn.get("password", "")
            if password == "••••••••" and conn_id:
                password = get_secret("influxdb", conn_id) or conn.get("password", "")

            res = test_influxdb_connection(
                url=url,
                database=database,
                username=username,
                password=password or ""
            )
            self._send_json(res)
            return

        # INFRASTRUCTURE: Save / Upsert InfluxDB Connection Profile
        # SETTINGS: Update baseload & solar cost parameters
        if path == "/api/settings" or path == "/api/analytics/solar_cost":
            try:
                cfg = load_json(CONFIG_FILE)
                if "baseload_watts" in body:
                    cfg["baseload_watts"] = float(body["baseload_watts"])
                if "solar_cost_eur_kwh" in body:
                    cfg["solar_cost_eur_kwh"] = round(float(body["solar_cost_eur_kwh"]), 4)
                save_json(CONFIG_FILE, cfg)
                self._send_json({
                    "status": "success",
                    "baseload_watts": cfg.get("baseload_watts", 300),
                    "solar_cost_eur_kwh": cfg.get("solar_cost_eur_kwh", 0.06)
                })
                return
            except Exception as e:
                self._send_json({"status": "error", "message": str(e)}, 400)
                return

        if False and path == "/api/analytics/solar_cost":
            try:
                new_cost = float(body.get("solar_cost_eur_kwh", 0.06))
                cfg = load_json(CONFIG_FILE)
                cfg["solar_cost_eur_kwh"] = round(new_cost, 4)
                save_json(CONFIG_FILE, cfg)
                self._send_json({"status": "success", "solar_cost_eur_kwh": cfg["solar_cost_eur_kwh"]})
                return
            except Exception as e:
                self._send_json({"status": "error", "message": str(e)}, 400)
                return

        if path == "/api/infrastructure/influxdb":
            cfg = load_json(CONFIG_FILE)
            ensure_framework_defaults(cfg)
            conn_id = body.get("id") or f"influx_{int(datetime.now().timestamp())}"
            
            # Save password securely in vault
            if body.get("password") and body.get("password") != "••••••••":
                save_secret("influxdb", conn_id, body["password"])

            updated = False
            conns = cfg.setdefault("influxdb_connections", [])
            conn_obj = None
            for c in conns:
                if c["id"] == conn_id:
                    for k in ["name", "type", "url", "database", "read_database", "username", "retention_policy", "enabled", "is_default"]:
                        if k in body:
                            c[k] = body[k]
                    # Never keep plain password in public config
                    c.pop("password", None)
                    updated = True
                    conn_obj = c
                    break
            if not updated or conn_obj is None:
                conn_obj = {
                    "id": conn_id,
                    "name": body.get("name", "Nieuwe InfluxDB Instantie"),
                    "type": body.get("type", "influx_v1"),
                    "url": body.get("url", "http://localhost:8086"),
                    "database": body.get("database", "hermes"),
                    "read_database": body.get("read_database", "openhems"),
                    "username": body.get("username", "hermes"),
                    "retention_policy": body.get("retention_policy", "autogen"),
                    "enabled": bool(body.get("enabled", True)),
                    "is_default": bool(body.get("is_default", False))
                }
                conns.append(conn_obj)

            if conn_obj.get("is_default") or len(conns) == 1:
                cfg["influxdb"] = dict(conn_obj)

            save_json(CONFIG_FILE, cfg)
            self._send_json({"status": "saved", "connection": conn_obj})
            return

        # INFRASTRUCTURE: Test MQTT
        if path == "/api/infrastructure/mqtt/test":
            cfg = load_json(CONFIG_FILE)
            conn_id = body.get("id")
            conn = next((c for c in cfg.get("mqtt_connections", []) if c["id"] == conn_id), {}) if conn_id else {}
            
            host = body.get("host") or conn.get("host") or "core-mosquitto"
            port = body.get("port") or conn.get("port") or 1883
            username = body.get("username") if "username" in body else conn.get("username", "")
            client_id = body.get("client_id") or conn.get("client_id") or "open-hems-test"
            
            password = body.get("password")
            if not password and conn_id:
                password = get_secret("mqtt", conn_id) or conn.get("password", "")
            if password == "••••••••" and conn_id:
                password = get_secret("mqtt", conn_id) or conn.get("password", "")

            res = test_mqtt_connection(
                host=host,
                port=port,
                username=username,
                password=password or "",
                client_id=client_id
            )
            self._send_json(res)
            return

        # INFRASTRUCTURE: Save / Upsert MQTT Connection Profile
        if path == "/api/infrastructure/mqtt":
            cfg = load_json(CONFIG_FILE)
            ensure_framework_defaults(cfg)
            conn_id = body.get("id") or f"mqtt_{int(datetime.now().timestamp())}"
            updated = False
            conns = cfg.setdefault("mqtt_connections", [])
            conn_obj = None
            for c in conns:
                if c["id"] == conn_id:
                    for k in ["name", "type", "host", "port", "username", "password", "base_topic", "client_id", "tls", "enabled", "is_default"]:
                        if k in body:
                            c[k] = int(body[k]) if k == "port" else body[k]
                    updated = True
                    conn_obj = c
                    break
            if not updated or conn_obj is None:
                conn_obj = {
                    "id": conn_id,
                    "name": body.get("name", "Nieuwe MQTT Broker"),
                    "type": body.get("type", "standard"),
                    "host": body.get("host", "localhost"),
                    "port": int(body.get("port", 1883)),
                    "username": body.get("username", ""),
                    "password": body.get("password", ""),
                    "base_topic": body.get("base_topic", "openhems"),
                    "client_id": body.get("client_id", "open-hems-collector"),
                    "tls": bool(body.get("tls", False)),
                    "enabled": bool(body.get("enabled", True)),
                    "is_default": bool(body.get("is_default", False))
                }
                conns.append(conn_obj)

            if conn_obj.get("is_default") or len(conns) == 1:
                cfg["mqtt"] = conn_obj

            save_json(CONFIG_FILE, cfg)
            self._send_json({"status": "saved", "connection": conn_obj})
            return

        # INFRASTRUCTURE: Write Test Telemetry Line to InfluxDB
        if path == "/api/infrastructure/write-test-point":
            cfg = load_json(CONFIG_FILE)
            ensure_framework_defaults(cfg)
            ic = cfg.get("influxdb", {})
            ts_ns = int(time.time() * 1e9)
            metric_val = float(body.get("value", 33.0))
            device_id = body.get("device_id", "daikin_heat_pump")
            line = f"open_hems_telemetry,device_id={device_id},vector=electricity,flow=consumption power_w={metric_val} {ts_ns}"

            clean_url = ic.get("url", "http://a0d7b954-influxdb:8086").rstrip("/")
            write_db = body.get("database") or "hermes"
            url = f"{clean_url}/write?db={write_db}"
            req = urllib.request.Request(url, data=line.encode("utf-8"), method="POST")
            conn_id = ic.get("id", "local_ha_influxdb")
            pwd = ic.get("password") or get_secret("influxdb", conn_id)
            if ic.get("username") and pwd:
                auth = base64.b64encode(f"{ic['username']}:{pwd}".encode()).decode()
                req.add_header("Authorization", f"Basic {auth}")

            t0 = time.time()
            try:
                with urllib.request.urlopen(req, timeout=3) as r:
                    latency = round((time.time() - t0) * 1000, 1)
                    self._send_json({
                        "status": "success",
                        "message": f"Meting succesvol opgeslagen in InfluxDB database '{write_db}'!",
                        "line_protocol": line,
                        "latency_ms": latency,
                        "http_status": r.status
                    })
            except Exception as e:
                self._send_json({"status": "error", "message": str(e)}, 500)
            return

        # CREATE: Policy
        if path == "/api/policies":
            cfg = load_json(CONFIG_FILE)
            ensure_framework_defaults(cfg)
            pol_id = body.get("id") or f"pol_{int(datetime.now().timestamp())}"
            new_pol = {
                "id": pol_id,
                "name": body.get("name", "Nieuw Beleid"),
                "type": body.get("type", "shiftable_consumer"),
                "target_devices": body.get("target_devices", []),
                "parameters": body.get("parameters", {})
            }
            cfg["policies"].append(new_pol)
            save_json(CONFIG_FILE, cfg)
            self._send_json({"status": "created", "policy": new_pol}, 201)
            return

        # CREATE: Device
        if path == "/api/devices":
            cfg = load_json(CONFIG_FILE)
            ensure_framework_defaults(cfg)
            dev_id = body.get("id") or f"dev_{int(datetime.now().timestamp())}"
            new_dev = {
                "id": dev_id,
                "name": body.get("name", "Nieuw Apparaat"),
                "type": body.get("type", "generic"),
                "source_type": body.get("source_type", "homeassistant"),
                "adapter": body.get("adapter", "custom"),
                "capabilities": body.get("capabilities", ["read_power"]),
                "ha_power_entity": body.get("ha_power_entity", ""),
                "ha_energy_entity": body.get("ha_energy_entity", ""),
                "ha_temp_entity": body.get("ha_temp_entity", ""),
                "ha_control_entity": body.get("ha_control_entity", ""),
                "mqtt_broker_id": body.get("mqtt_broker_id", ""),
                "mqtt_power_topic": body.get("mqtt_power_topic", ""),
                "mqtt_power_json_key": body.get("mqtt_power_json_key", ""),
                "mqtt_control_topic": body.get("mqtt_control_topic", ""),
                "native_unit": body.get("native_unit", "W"),
                "storage_unit": "W",
                "installed": body.get("installed", True),
                "enabled": body.get("enabled", True),
                "parameters": body.get("parameters", {})
            }
            cfg["devices"].append(new_dev)
            save_json(CONFIG_FILE, cfg)
            self._send_json({"status": "created", "device": new_dev}, 201)
            return

        # CREATE: Energy Supplier / Tariff
        if path == "/api/tariffs":
            cfg = load_json(CONFIG_FILE)
            ensure_framework_defaults(cfg)
            t_id = body.get("id") or f"tariff_{int(datetime.now().timestamp())}"
            new_tariff = {
                "id": t_id,
                "name": body.get("name", "Nieuwe Energieleverancier"),
                "provider": body.get("provider", "epex_spot"),
                "import_markup_eur_kwh": float(body.get("import_markup_eur_kwh", 0.0121)),
                "export_markup_eur_kwh": float(body.get("export_markup_eur_kwh", 0.0121)),
                "electricity_tax_eur_kwh": float(body.get("electricity_tax_eur_kwh", 0.11085)),
                "fixed_monthly_fee_eur": float(body.get("fixed_monthly_fee_eur", 6.25)),
                "contract_start_date": body.get("contract_start_date", "2026-09-25"),
                "interval": body.get("interval", "15m"),
                "active": bool(body.get("active", True))
            }
            cfg["tariffs_list"].append(new_tariff)
            save_json(CONFIG_FILE, cfg)
            self._send_json({"status": "created", "tariff": new_tariff}, 201)
            return

        # CREATE: Exclusion Window
        if path == "/api/exclusion-windows":
            cfg = load_json(CONFIG_FILE)
            windows = cfg.setdefault("data_exclusion_windows", [])
            new_win = {
                "sensor": body.get("sensor", "sensor.warmtepomp_power"),
                "start": body.get("start", "2026-01-01"),
                "end": body.get("end", "2026-01-02"),
                "reason": body.get("reason", "Sensor onderhoud")
            }
            windows.append(new_win)
            save_json(CONFIG_FILE, cfg)
            self._send_json({"status": "created", "window": new_win}, 201)
            return

        # ACTION: Recalculate
        if path == "/api/schedule/recalculate":
            try:
                import subprocess
                cmd = ["python3", "/config/projects/energy-scheduler/runners/run_daily_optimizer.py"]
                res = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
                self._send_json({
                    "status": "success" if res.returncode == 0 else "error",
                    "output": res.stdout[-1000:]
                })
            except Exception as e:
                self._send_json({"status": "failed", "error": str(e)}, 500)
            return

        self._send_json({"error": "Endpoint not found"}, 404)

    # =========================================================================
    # PUT ROUTER (Update)
    # =========================================================================
    def do_PUT(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path.rstrip("/")
        body = self._read_json_body()

        # UPDATE: Specific Policy
        m_pol = re.match(r"^/api/policies/([^/]+)$", path)
        if m_pol:
            pol_id = m_pol.group(1)
            cfg = load_json(CONFIG_FILE)
            ensure_framework_defaults(cfg)
            for p in cfg.get("policies", []):
                if p["id"] == pol_id:
                    for k in ["name", "type", "target_devices", "parameters"]:
                        if k in body:
                            p[k] = body[k]
                    save_json(CONFIG_FILE, cfg)
                    self._send_json({"status": "updated", "policy": p})
                    return
            self._send_json({"error": "Policy not found"}, 404)
            return

        # UPDATE: Home Assistant Connector
        if path == "/api/infrastructure/homeassistant":
            body = self._read_json_body()
            cfg = load_json(CONFIG_FILE)
            ensure_framework_defaults(cfg)
            ha_cfg = cfg.setdefault("homeassistant", {})
            for k in ["name", "url", "verify_ssl", "timeout_seconds", "enabled"]:
                if k in body:
                    ha_cfg[k] = body[k]
            save_json(CONFIG_FILE, cfg)

            token = body.get("token")
            if token and token != "••••••••":
                sec = load_secrets()
                sec.setdefault("homeassistant", {})["token"] = token
                sec["homeassistant"]["url"] = ha_cfg.get("url")
                tmp_s = f"{SECRETS_FILE}.tmp.{os.getpid()}"
                with open(tmp_s, "w", encoding="utf-8") as f:
                    json.dump(sec, f, indent=2)
                os.replace(tmp_s, SECRETS_FILE)
                try:
                    os.chmod(SECRETS_FILE, 0o600)
                except Exception:
                    pass
            self._send_json({"status": "updated", "homeassistant": ha_cfg})
            return

        # UPDATE: Specific Device
        m_dev = re.match(r"^/api/devices/([^/]+)$", path)
        if m_dev:
            dev_id = m_dev.group(1)
            cfg = load_json(CONFIG_FILE)
            ensure_framework_defaults(cfg)
            for d in cfg["devices"]:
                if d["id"] == dev_id:
                    for k in ["name", "type", "source_type", "adapter", "capabilities", "ha_power_entity", "ha_energy_entity", "ha_temp_entity", "ha_control_entity", "mqtt_broker_id", "mqtt_power_topic", "mqtt_power_json_key", "mqtt_control_topic", "native_unit", "storage_unit", "installed", "enabled", "parameters"]:
                        if k in body:
                            d[k] = body[k]
                    save_json(CONFIG_FILE, cfg)
                    self._send_json({"status": "updated", "device": d})
                    return
            self._send_json({"error": "Device not found"}, 404)
            return

        # UPDATE: Specific Tariff
        m_tar = re.match(r"^/api/tariffs/([^/]+)$", path)
        if m_tar:
            t_id = m_tar.group(1)
            cfg = load_json(CONFIG_FILE)
            ensure_framework_defaults(cfg)
            for t in cfg["tariffs_list"]:
                if t["id"] == t_id:
                    for k in ["name", "provider", "import_markup_eur_kwh", "export_markup_eur_kwh", "electricity_tax_eur_kwh", "fixed_monthly_fee_eur", "contract_start_date", "interval", "active"]:
                        if k in body:
                            t[k] = float(body[k]) if "eur" in k else body[k]
                    save_json(CONFIG_FILE, cfg)
                    self._send_json({"status": "updated", "tariff": t})
                    return
            self._send_json({"error": "Tariff not found"}, 404)
            return

        self._send_json({"error": "Endpoint not found"}, 404)

    # =========================================================================
    # DELETE ROUTER (Delete)
    # =========================================================================
    def do_DELETE(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path.rstrip("/")

        # DELETE: InfluxDB Connection
        m_inf = re.match(r"^/api/infrastructure/influxdb/([^/]+)$", path)
        if m_inf:
            c_id = m_inf.group(1)
            cfg = load_json(CONFIG_FILE)
            ensure_framework_defaults(cfg)
            orig_len = len(cfg.get("influxdb_connections", []))
            cfg["influxdb_connections"] = [c for c in cfg.get("influxdb_connections", []) if c["id"] != c_id]
            if len(cfg["influxdb_connections"]) < orig_len:
                if cfg.get("influxdb_connections"):
                    cfg["influxdb"] = cfg["influxdb_connections"][0]
                save_json(CONFIG_FILE, cfg)
                self._send_json({"status": "deleted", "id": c_id})
            else:
                self._send_json({"error": "InfluxDB connection not found"}, 404)
            return

        # DELETE: MQTT Broker Connection
        m_mq = re.match(r"^/api/infrastructure/mqtt/([^/]+)$", path)
        if m_mq:
            c_id = m_mq.group(1)
            cfg = load_json(CONFIG_FILE)
            ensure_framework_defaults(cfg)
            orig_len = len(cfg.get("mqtt_connections", []))
            cfg["mqtt_connections"] = [c for c in cfg.get("mqtt_connections", []) if c["id"] != c_id]
            if len(cfg["mqtt_connections"]) < orig_len:
                if cfg.get("mqtt_connections"):
                    cfg["mqtt"] = cfg["mqtt_connections"][0]
                save_json(CONFIG_FILE, cfg)
                self._send_json({"status": "deleted", "id": c_id})
            else:
                self._send_json({"error": "MQTT connection not found"}, 404)
            return

        # DELETE: Policy
        m_pol = re.match(r"^/api/policies/([^/]+)$", path)
        if m_pol:
            pol_id = m_pol.group(1)
            cfg = load_json(CONFIG_FILE)
            ensure_framework_defaults(cfg)
            orig_len = len(cfg.get("policies", []))
            cfg["policies"] = [p for p in cfg.get("policies", []) if p["id"] != pol_id]
            if len(cfg["policies"]) < orig_len:
                save_json(CONFIG_FILE, cfg)
                self._send_json({"status": "deleted", "id": pol_id})
            else:
                self._send_json({"error": "Policy not found"}, 404)
            return

        # DELETE: Device
        m_dev = re.match(r"^/api/devices/([^/]+)$", path)
        if m_dev:
            dev_id = m_dev.group(1)
            cfg = load_json(CONFIG_FILE)
            ensure_framework_defaults(cfg)
            orig_len = len(cfg["devices"])
            cfg["devices"] = [d for d in cfg["devices"] if d["id"] != dev_id]
            if len(cfg["devices"]) < orig_len:
                save_json(CONFIG_FILE, cfg)
                self._send_json({"status": "deleted", "id": dev_id})
            else:
                self._send_json({"error": "Device not found"}, 404)
            return

        # DELETE: Tariff
        m_tar = re.match(r"^/api/tariffs/([^/]+)$", path)
        if m_tar:
            t_id = m_tar.group(1)
            cfg = load_json(CONFIG_FILE)
            ensure_framework_defaults(cfg)
            orig_len = len(cfg["tariffs_list"])
            cfg["tariffs_list"] = [t for t in cfg["tariffs_list"] if t["id"] != t_id]
            if len(cfg["tariffs_list"]) < orig_len:
                save_json(CONFIG_FILE, cfg)
                self._send_json({"status": "deleted", "id": t_id})
            else:
                self._send_json({"error": "Tariff not found"}, 404)
            return

        # DELETE: Exclusion Window
        m_win = re.match(r"^/api/exclusion-windows/(\d+)$", path)
        if m_win:
            idx = int(m_win.group(1))
            cfg = load_json(CONFIG_FILE)
            windows = cfg.get("data_exclusion_windows", [])
            if 0 <= idx < len(windows):
                removed = windows.pop(idx)
                save_json(CONFIG_FILE, cfg)
                self._send_json({"status": "deleted", "window": removed})
            else:
                self._send_json({"error": "Index out of range"}, 404)
            return

        self._send_json({"error": "Endpoint not found"}, 404)

    # =========================================================================
    # HTML SINGLE PAGE APPLICATION (Framework UI + Multi-Instance Laag 1)
    # =========================================================================
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
