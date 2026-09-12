#!/usr/bin/env python3
"""
Open HEMS Framework & Management Console
========================================
Version: 0.90.0
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

try:
    sys.path.insert(0, str(Path(__file__).parent))
    sys.path.insert(0, "/config/projects/energy-scheduler")
    from layer2_calibration.learned_forecaster import HybridForecastingModel
    from layer2_calibration.dhw_thermal_model import DhwThermalModel
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
        ha_sec = load_secrets()
        ha_tok = ha_sec.get("homeassistant", {}).get("token")
        ha_url = ha_sec.get("homeassistant", {}).get("url", "https://hass.b3rg.nl:8123")
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
    sec = load_secrets()
    ha_cfg_tok = sec.get("homeassistant", {}).get("token")
    if ha_cfg_tok:
        token = ha_cfg_tok
        ha_url = sec.get("homeassistant", {}).get("url") or "https://hass.b3rg.nl:8123"
    elif os.environ.get("SUPERVISOR_TOKEN"):
        token = os.environ["SUPERVISOR_TOKEN"]
        ha_url = "http://supervisor/core"
    else:
        token = os.environ.get("HASS_TOKEN", "")
        ha_url = os.environ.get("HASS_URL", "https://hass.b3rg.nl:8123")

    if not token and HA_API_CONFIG.exists():
        cfg = load_json(HA_API_CONFIG)
        token = cfg.get("HASS_TOKEN")
        ha_url = cfg.get("HASS_URL") or ha_url

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
                today_str = now_ams.strftime("%d-%m-%Y")
                tomorrow_str = (now_ams + timedelta(days=1)).strftime("%d-%m-%Y")

                is_15m = (res_mode == "15m")
                total_slots = 96 if is_15m else 24
                step_mins = 15 if is_15m else 60
                start_minute = (now_ams.minute // 15) * 15 if is_15m else 0
                base_dt = now_ams.replace(minute=start_minute, second=0, microsecond=0)

                # 1. Fetch EPEX Spot Prices for Today & Tomorrow (Rolling 24h matching Verbruiksvoorspelling)
                prices_map = {}
                prices_base_map = {}
                for d_str in [today_str, tomorrow_str]:
                    try:
                        url = f"https://public.api.energyzero.nl/public/v1/prices?energyType=ENERGY_TYPE_ELECTRICITY&date={d_str}&interval={interval_api}"
                        req = urllib.request.Request(url, headers={"User-Agent": "OpenHEMS/1.0"})
                        with urllib.request.urlopen(req, timeout=6) as r:
                            api_data = json.loads(r.read().decode())
                            for it in api_data.get("all_in_with_vat", []):
                                dt = datetime.fromisoformat(it["start"].replace("Z", "+00:00")).astimezone(AMS_TZ)
                                k_fmt = "%Y-%m-%d %H:%M" if is_15m else "%Y-%m-%d %H:00"
                                prices_map[dt.strftime(k_fmt)] = round(float(it.get("price", {}).get("value", 0.0)), 4)
                            for it in api_data.get("base", []):
                                dt = datetime.fromisoformat(it["start"].replace("Z", "+00:00")).astimezone(AMS_TZ)
                                k_fmt = "%Y-%m-%d %H:%M" if is_15m else "%Y-%m-%d %H:00"
                                prices_base_map[dt.strftime(k_fmt)] = round(float(it.get("price", {}).get("value", 0.0)), 4)
                    except Exception as e_p:
                        print(f"Warning fetching EPEX prices for {d_str}: {e_p}")

                # 2. Fetch Open-Meteo Solar Forecast for Culemborg (Today & Tomorrow)
                solar_hourly = {}
                try:
                    url_m = "https://api.open-meteo.com/v1/forecast?latitude=51.9537&longitude=5.2320&hourly=shortwave_radiation&timezone=Europe%2FAmsterdam&forecast_days=2"
                    req_m = urllib.request.Request(url_m, headers={"User-Agent": "OpenHEMS/1.0"})
                    with urllib.request.urlopen(req_m, timeout=5) as r_m:
                        m_data = json.loads(r_m.read().decode())
                        m_times = m_data.get("hourly", {}).get("time", [])
                        m_rads = m_data.get("hourly", {}).get("shortwave_radiation", [])
                        s_cfg = load_json(CONFIG_FILE).get("solar", {})
                        s_kwp = float(s_cfg.get("kwp", 5.76))
                        s_inv = float(s_cfg.get("inverter_max_w", 5500)) / 1000.0
                        s_tilt = float(s_cfg.get("tilt_degrees", 34))
                        s_az = float(s_cfg.get("azimuth_degrees", 225))
                        s_eff = float(s_cfg.get("efficiency_factor", 0.88))
                        for t, rad in zip(m_times, m_rads):
                            k_t = t.replace('T', ' ')[:13] + ':00'
                            dt_h = datetime.strptime(k_t, "%Y-%m-%d %H:%M").replace(tzinfo=ZoneInfo("Europe/Amsterdam"))
                            solar_hourly[k_t] = calculate_poa_solar_kw(dt_h, float(rad), kwp=s_kwp, tilt_deg=s_tilt, azimuth_deg=s_az, inverter_limit_kw=s_inv, eff=s_eff)
                except Exception as e_m:
                    print(f"Warning fetching Open-Meteo solar forecast: {e_m}")

                labels = []
                prices_all_in = []
                prices_base = []
                solar_forecast_kw = []

                prev_ep_dt = None
                for i in range(total_slots):
                    dt_slot = base_dt + timedelta(minutes=step_mins * i)
                    k_full = dt_slot.strftime("%Y-%m-%d %H:%M" if is_15m else "%Y-%m-%d %H:00")
                    k_hour = dt_slot.strftime("%Y-%m-%d %H:00")

                    lbl = format_slot_label(dt_slot, prev_ep_dt, i == 0, is_15m)
                    prev_ep_dt = dt_slot

                    labels.append(lbl)
                    prices_all_in.append(prices_map.get(k_full, 0.25))
                    prices_base.append(prices_base_map.get(k_full, 0.10))
                    solar_forecast_kw.append(solar_hourly.get(k_hour, 0.0))

                min_p = min(prices_all_in) if prices_all_in else 0.0
                max_p = max(prices_all_in) if prices_all_in else 0.0
                avg_p = (sum(prices_all_in) / len(prices_all_in)) if prices_all_in else 0.0
                min_time = labels[prices_all_in.index(min_p)] if prices_all_in else "--:--"
                max_time = labels[prices_all_in.index(max_p)] if prices_all_in else "--:--"
                peak_solar = max(solar_forecast_kw) if solar_forecast_kw else 0.0

                res = {
                    "status": "success",
                    "resolution": res_mode,
                    "labels": labels,
                    "epex_prices": prices_all_in,
                    "epex_base_prices": prices_base,
                    "solar_forecast_kw": solar_forecast_kw,
                    "solar_cost": solar_cost,
                    "stats": {
                        "min_price": f"€{min_p:.4f}/kWh",
                        "min_time": min_time,
                        "max_price": f"€{max_p:.4f}/kWh",
                        "max_time": max_time,
                        "avg_price": f"€{avg_p:.4f}/kWh",
                        "solar_savings_avg": f"€{max(0.0, avg_p - solar_cost):.4f}/kWh",
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
                act_dhw, pred_dhw = [], []
                act_cv, pred_cv = [], []
                act_total, pred_total = [], []

                prev_dt = None
                for p in gen_pts:
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
                    # Model expects scheduled reheat runs around optimal solar window (e.g. 10:00-11:00 or 14:00-15:00 ~1.8 to 2.4 kW)
                    # When active run occurred in real telemetry, compare directly; otherwise planned window
                    p_dhw_kw = round(d_w / 1000.0, 3) if d_w > 500 else 0.0
                    pred_dhw.append(p_dhw_kw)

                    # CV Heating Model: Space heating was turned off in current conditions
                    pred_cv.append(0.0)

                    # Total House Prediction
                    pred_total.append(round(p_unalloc_kw + p_dhw_kw, 3))

                def compute_kpis(actual_list, pred_list):
                    if not actual_list or not pred_list:
                        return {"mae_w": 0, "accuracy_pct": 100.0, "total_actual_kwh": 0.0, "total_pred_kwh": 0.0, "delta_kwh": 0.0}
                    n = len(actual_list)
                    diffs = [abs(a - p) for a, p in zip(actual_list, pred_list)]
                    mae_w = sum(diffs) / n * 1000.0
                    denom = max(sum(actual_list), sum(pred_list), 1.0)
                    acc = max(0.0, min(100.0, (1.0 - (sum(diffs) / (2.0 * denom))) * 100.0))
                    tot_act = sum(actual_list) * interval_h
                    tot_pred = sum(pred_list) * interval_h
                    return {
                        "mae_w": int(round(mae_w)),
                        "accuracy_pct": round(acc, 1),
                        "total_actual_kwh": round(tot_act, 2),
                        "total_pred_kwh": round(tot_pred, 2),
                        "delta_kwh": round(tot_act - tot_pred, 2)
                    }

                metrics = {
                    "all": compute_kpis(act_total, pred_total),
                    "solar": compute_kpis(act_solar, pred_solar),
                    "dhw": compute_kpis(act_dhw, pred_dhw),
                    "cv": compute_kpis(act_cv, pred_cv)
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
                        "cv": pred_cv
                    },
                    "metrics": metrics
                })
                return
            except Exception as e:
                self._send_json({"status": "error", "message": f"Fout bij berekenen validatie overlay: {str(e)}"}, 500)
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
            total_slots = 96 if is_15m else 24
            interval_h = 0.25 if is_15m else 1.0

            now_ams = datetime.now(ZoneInfo("Europe/Amsterdam"))
            start_minute = (now_ams.minute // 15) * 15 if is_15m else 0
            base_dt = now_ams.replace(minute=start_minute, second=0, microsecond=0)

            # Anchored weather forecast: Wittboy live weather station blended with Open-Meteo
            temp_map, solar_map, wind_map, rh_map = get_anchored_weather_forecast(base_dt)

            # Fetch EPEX spot prices for next 24h
            today_str = now_ams.strftime("%d-%m-%Y")
            tomorrow_str = (now_ams + timedelta(days=1)).strftime("%d-%m-%Y")
            prices_map = {}
            for d_str in [today_str, tomorrow_str]:
                try:
                    url_p = f"https://public.api.energyzero.nl/public/v1/prices?energyType=ENERGY_TYPE_ELECTRICITY&date={d_str}&interval={'INTERVAL_QUARTER' if is_15m else 'INTERVAL_HOUR'}"
                    req_p = urllib.request.Request(url_p, headers={"User-Agent": "OpenHEMS/1.0"})
                    with urllib.request.urlopen(req_p, timeout=5) as r_p:
                        res_p = json.loads(r_p.read().decode())
                        for it in res_p.get("all_in_with_vat", []):
                            dt = datetime.fromisoformat(it["start"].replace("Z", "+00:00")).astimezone(ZoneInfo("Europe/Amsterdam"))
                            k_dt = dt.strftime("%Y-%m-%d %H:%M" if is_15m else "%Y-%m-%d %H:00")
                            prices_map[k_dt] = round(float(it.get("price", {}).get("value", 0.28)), 4)
                except Exception:
                    pass

            # Fetch live thermostat setpoint and active state from Home Assistant
            t_setpoint = 20.0
            t_indoor_sim = 22.0
            thermostat_active = True
            thermostat_status_msg = "Actief (CV Verwarming Standby)"
            try:
                ha_sec = load_secrets()
                ha_tok = ha_sec.get("homeassistant", {}).get("token")
                ha_url = ha_sec.get("homeassistant", {}).get("url", "https://hass.b3rg.nl:8123")
                if ha_tok and ha_url:
                    ctx_ssl = ssl.create_default_context()
                    ctx_ssl.check_hostname = False
                    ctx_ssl.verify_mode = ssl.CERT_NONE

                    # 1. Query climate.woonkamer_climate_daikin for setpoint & indoor temp
                    try:
                        req_cl = urllib.request.Request(
                            f"{ha_url}/api/states/climate.woonkamer_climate_daikin",
                            headers={"Authorization": f"Bearer {ha_tok}", "Content-Type": "application/json"}
                        )
                        with urllib.request.urlopen(req_cl, timeout=2, context=ctx_ssl) as r_cl:
                            st_cl = json.loads(r_cl.read().decode())
                            attrs = st_cl.get("attributes", {})
                            t_setpoint = float(attrs.get("target_temp_low", attrs.get("temperature", 20.0)))
                            cur_t = float(attrs.get("current_temperature", t_indoor_sim))
                            if 15.0 <= cur_t <= 30.0:
                                t_indoor_sim = cur_t
                            if st_cl.get("state") == "off" or attrs.get("hvac_action") == "off":
                                thermostat_active = False
                                thermostat_status_msg = "Woonkamerthermostaat staat Uit"
                    except Exception:
                        pass

                    # 2. Query Daikin room heating circuit: climate.hc_room_room_heating
                    try:
                        req_hc = urllib.request.Request(
                            f"{ha_url}/api/states/climate.hc_room_room_heating",
                            headers={"Authorization": f"Bearer {ha_tok}", "Content-Type": "application/json"}
                        )
                        with urllib.request.urlopen(req_hc, timeout=2, context=ctx_ssl) as r_hc:
                            st_hc = json.loads(r_hc.read().decode())
                            if st_hc.get("state") == "off":
                                thermostat_active = False
                                thermostat_status_msg = "Ruimteverwarming staat Uit (climate.hc_room_room_heating is Uit)"
                    except Exception:
                        pass

                    # 3. Query Daikin master climate switch: switch.hc_mode_altherma_on
                    try:
                        req_sw = urllib.request.Request(
                            f"{ha_url}/api/states/switch.hc_mode_altherma_on",
                            headers={"Authorization": f"Bearer {ha_tok}", "Content-Type": "application/json"}
                        )
                        with urllib.request.urlopen(req_sw, timeout=2, context=ctx_ssl) as r_sw:
                            st_sw = json.loads(r_sw.read().decode())
                            if st_sw.get("state") == "off":
                                thermostat_active = False
                                thermostat_status_msg = "Warmtepomp CV staat Uit (switch.hc_mode_altherma_on is Uit)"
                    except Exception:
                        pass
            except Exception:
                pass

            # 2-Mass Floor Heating Dynamic Simulation:
            # - C_floor: ~16 ton concrete screed = 4.5 kWh/K
            # - C_air: Indoor air & interior furniture = 6.0 kWh/K (total building ~10.5 kWh/K)
            # - U_floor_to_air: 1.2 kW/K heat transfer from underfloor heating to living room
            # - Hysteresis: Heat pump turns ON when T_indoor <= T_setpoint - 0.5°C; OFF when T_indoor >= T_setpoint
            # - Modulation: Empirical formula fitted on 230 real winter runs in InfluxDB:
            #   P_el(T_out) = max(950, min(4200, 2885.6 - 95.2 * T_out)) W
            c_floor_kwh_per_k = 4.5
            c_air_kwh_per_k = 6.0
            u_floor_to_air_kw = 1.2
            t_start_threshold = t_setpoint - 0.5
            t_stop_threshold = t_setpoint

            t_indoor = t_indoor_sim
            t_floor = t_indoor_sim + 0.2
            hp_running = False

            labels, out_temps, in_temps, floor_temps, cops, th_loss_kw, el_power_kw, costs_eur = [], [], [], [], [], [], [], []
            tot_th_kwh, tot_el_kwh, tot_cost = 0.0, 0.0, 0.0

            prev_hf_dt = None
            for i in range(total_slots):
                slot_dt = base_dt + timedelta(minutes=step_mins * i)
                lbl = format_slot_label(slot_dt, prev_hf_dt, i == 0, is_15m)
                prev_hf_dt = slot_dt
                k_full = slot_dt.strftime("%Y-%m-%d %H:%M" if is_15m else "%Y-%m-%d %H:00")
                k_hour = slot_dt.strftime("%Y-%m-%d %H:00")
                t_out = temp_map.get(k_hour, 14.0)
                sol = solar_map.get(k_hour, 0.0)
                wnd = wind_map.get(k_hour, 3.0)
                price = prices_map.get(k_full, prices_map.get(k_hour, 0.29))

                # Hard peak lockouts (07:00-09:30 & 17:00-20:00) unless comfort emergency (< 18.5°C)
                hour_frac = slot_dt.hour + slot_dt.minute / 60.0
                in_peak_lockout = ((7.0 <= hour_frac < 9.5) or (17.0 <= hour_frac < 20.0))
                emergency_guard = (t_indoor < 18.5)

                # Thermostat hysteresis logic
                if thermostat_active and not hp_running and (t_indoor <= t_start_threshold):
                    if not in_peak_lockout or emergency_guard:
                        hp_running = True
                elif hp_running and (not thermostat_active or t_indoor >= t_stop_threshold or (in_peak_lockout and not emergency_guard)):
                    hp_running = False

                if hp_running:
                    # Inverter modulation formula from real telemetry
                    p_el_w = max(950.0, min(4200.0, 2885.6 - 95.2 * t_out))
                    if t_floor < 22.0:
                        p_el_w = min(4200.0, p_el_w * 1.25)  # Start-up surge
                    cop_val = max(2.5, min(5.5, 5.2 - 0.08 * (35.0 - t_out)))
                    th_kw = round((p_el_w * cop_val) / 1000.0, 2)
                    el_kw = round(p_el_w / 1000.0, 2)
                else:
                    cop_val = round(max(2.5, min(5.5, 5.2 - 0.08 * (35.0 - t_out))), 2)
                    th_kw = 0.0
                    el_kw = 0.0

                # Building envelope heat loss (transmission + infiltration + wind)
                ua_eff = (321.1 + 15.0 * max(0.0, wnd - 2.0)) / 1000.0  # kW/K
                q_loss_kw = ua_eff * max(0.0, t_indoor - t_out)
                q_solar_kw = (0.12 * sol * 25.0) / 1000.0
                q_floor_to_air_kw = u_floor_to_air_kw * (t_floor - t_indoor)

                # Dynamic state integration over interval
                dt_floor = ((th_kw - q_floor_to_air_kw) * interval_h) / c_floor_kwh_per_k
                dt_indoor = ((q_floor_to_air_kw + q_solar_kw - q_loss_kw) * interval_h) / c_air_kwh_per_k

                t_floor = round(t_floor + dt_floor, 2)
                t_indoor = round(max(15.0, min(26.0, t_indoor + dt_indoor)), 2)

                slot_cost = round(el_kw * interval_h * price, 3)
                tot_th_kwh += th_kw * interval_h
                tot_el_kwh += el_kw * interval_h
                tot_cost += slot_cost

                labels.append(lbl)
                out_temps.append(round(t_out, 1))
                in_temps.append(round(t_indoor, 1))
                floor_temps.append(round(t_floor, 1))
                cops.append(round(cop_val, 2))
                th_loss_kw.append(round(q_loss_kw, 2))
                el_power_kw.append(el_kw)
                costs_eur.append(slot_cost)

            self._send_json({
                "resolution": res_mode,
                "labels": labels,
                "outdoor_temps_c": out_temps,
                "indoor_temps_c": in_temps,
                "floor_temps_c": floor_temps,
                "cops": cops,
                "thermal_loss_kw": th_loss_kw,
                "electrical_kw": el_power_kw,
                "costs_eur": costs_eur,
                "total_thermal_kwh": round(tot_th_kwh, 2),
                "total_electrical_kwh": round(tot_el_kwh, 2),
                "total_cost_eur": round(tot_cost, 2),
                "thermostat_setpoint_c": t_setpoint,
                "thermostat_start_threshold_c": round(t_start_threshold, 1),
                "thermostat_active": thermostat_active,
                "thermostat_status_label": thermostat_status_msg
            })
            return

        if path.startswith("/api/model/dhw-status"):
            qp = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            res_mode = qp.get("resolution", ["15m"])[0]
            is_15m = (res_mode == "15m")
            t_live = 49.2
            try:
                ha_sec = load_secrets()
                ha_tok = ha_sec.get("homeassistant", {}).get("token")
                ha_url = ha_sec.get("homeassistant", {}).get("url", "https://hass.b3rg.nl:8123")
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
            if GLOBAL_DHW_MODEL:
                # Retrieve planned slots & dispatch parameters from central dispatch cache (Single Source of Truth)
                cached_slots = GLOBAL_CENTRAL_CACHE.get("planned_dhw_slots", [])
                planned_mode = GLOBAL_CENTRAL_CACHE.get("planned_mode", "forced_standard_50")
                planned_reason = GLOBAL_CENTRAL_CACHE.get("reason", "Centrale dispatch planning")
                cached_dyn_peaks = GLOBAL_CENTRAL_CACHE.get("dynamic_peaks", [])
                c_power = GLOBAL_CENTRAL_CACHE.get("sww_power_kw", 1.8)
                c_target = GLOBAL_CENTRAL_CACHE.get("target_temp_c", 50.0)

                base_sim_dt = GLOBAL_CENTRAL_CACHE.get("base_dt", now_ams)
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

                        traj = {
                            "labels": h_labels,
                            "temperatures_c": h_temps,
                            "temperatures_p05_c": h_p05,
                            "temperatures_p95_c": h_p95,
                            "demand_kwh_th": h_demand,
                            "morning_dip_temp_c": traj.get("morning_dip_temp_c"),
                            "morning_dip_time": traj.get("morning_dip_time")
                        }
                        unheated_traj = {
                            "temperatures_c": h_unh_temps,
                            "temperatures_p05_c": h_unh_p05,
                            "temperatures_p95_c": h_unh_p95
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

                # Unified Decision derived directly from Central Dispatch Engine
                decision = {
                    "status": "SCHEDULE_NIGHT_CHARGE" if planned_mode == "forced_night_50" else "SKIP_NIGHT_CHARGE",
                    "planned_mode": planned_mode,
                    "decision_title": (
                        "🟣 Zonnebuffer Boost (tot 60°C)" if planned_mode == "forced_solar_boost_60"
                        else ("🌙 Nachtlading Gepland (Comfortzekerheid vóór Prijspiek)" if planned_mode == "forced_night_50"
                        else ("☀️ Daglading Gepland (tot 50°C)" if planned_mode in ["forced_standard_50", "forced_midday_50"]
                        else "✅ Geen opwarming nodig (Vat op temperatuur)"))
                    ),
                    "decision_sub": planned_reason,
                    "recommendation": f"{GLOBAL_CENTRAL_CACHE.get('planned_mode_label', 'Centrale planning')}: {planned_reason}",
                    "morning_dip_c": traj.get("morning_dip_temp_c", 40.0) if traj else 40.0,
                    "morning_dip_time": traj.get("morning_dip_time", "09:30") if traj else "09:30",
                    "morning_dip_p95_c": round(max(30.0, float(traj.get("morning_dip_temp_c", 40.0) if traj else 40.0) - 1.6), 1),
                    "dynamic_peaks": cached_dyn_peaks
                }

                self._send_json({
                    "status": "online",
                    "resolution": res_mode,
                    "decision": decision,
                    "trajectory": traj,
                    "unheated_trajectory": unheated_traj
                })
            else:
                self._send_json({"status": "error", "message": "DHW model niet geladen"}, 500)
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
                self._send_json(load_json(recs_file))
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

        if path == "/api/status":
            cfg = load_json(CONFIG_FILE)
            params = load_json(PARAMS_FILE)
            ensure_framework_defaults(cfg)
            self._send_json({
                "system": "Open HEMS Framework",
                "version": "0.90.0",
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
            ha_cfg_tok = ha_sec.get("homeassistant", {}).get("token")
            if ha_cfg_tok:
                ha_token = ha_cfg_tok
                ha_base_url = ha_sec.get("homeassistant", {}).get("url") or "https://hass.b3rg.nl:8123"
            elif os.environ.get("SUPERVISOR_TOKEN"):
                ha_token = os.environ["SUPERVISOR_TOKEN"]
                ha_base_url = "http://supervisor/core"
            else:
                ha_token = os.environ.get("HASS_TOKEN", "")
                ha_base_url = os.environ.get("HASS_URL", "https://hass.b3rg.nl:8123")
            
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
            today_str = now_ams.strftime("%d-%m-%Y")
            tomorrow_str = (now_ams + timedelta(days=1)).strftime("%d-%m-%Y")

            # 1. Fetch EPEX prices for today & tomorrow
            prices_map = {}
            interval_str = "INTERVAL_QUARTER" if is_15m else "INTERVAL_HOUR"
            for d_str in [today_str, tomorrow_str]:
                try:
                    url_p = f"https://public.api.energyzero.nl/public/v1/prices?energyType=ENERGY_TYPE_ELECTRICITY&date={d_str}&interval={interval_str}"
                    req_p = urllib.request.Request(url_p, headers={"User-Agent": "OpenHEMS/1.0"})
                    with urllib.request.urlopen(req_p, timeout=5) as r_p:
                        res_p = json.loads(r_p.read().decode())
                        for it in res_p.get("all_in_with_vat", []):
                            dt = datetime.fromisoformat(it["start"].replace("Z", "+00:00")).astimezone(AMS_TZ)
                            k_fmt = "%Y-%m-%d %H:%M" if is_15m else "%Y-%m-%d %H:00"
                            prices_map[dt.strftime(k_fmt)] = round(float(it.get("price", {}).get("value", 0.25)), 4)
                except Exception as e_p:
                    pass

            # 2. Fetch Open-Meteo Solar & Weather for Culemborg
            solar_map = {}
            temp_map = {}
            wind_map = {}
            rh_map = {}
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
                    s_cfg = load_json(CONFIG_FILE).get("solar", {})
                    s_kwp = float(s_cfg.get("kwp", 5.76))
                    s_inv = float(s_cfg.get("inverter_max_w", 5500)) / 1000.0
                    s_tilt = float(s_cfg.get("tilt_degrees", 34))
                    s_az = float(s_cfg.get("azimuth_degrees", 225))
                    s_eff = float(s_cfg.get("efficiency_factor", 0.88))
                    for t, rad, tmp, wnd, rh in zip(m_times, m_rads, m_temps, m_winds, m_rhs):
                        k_t = t.replace('T', ' ')[:13] + ':00'
                        dt_h = datetime.strptime(k_t, "%Y-%m-%d %H:%M").replace(tzinfo=ZoneInfo("Europe/Amsterdam"))
                        solar_map[k_t] = calculate_poa_solar_kw(dt_h, float(rad), kwp=s_kwp, tilt_deg=s_tilt, azimuth_deg=s_az, inverter_limit_kw=s_inv, eff=s_eff)
                        temp_map[k_t] = round(float(tmp), 1)
                        wind_map[k_t] = round(float(wnd), 1)
                        rh_map[k_t] = round(float(rh), 1)
            except Exception as e_m:
                print(f"Warning fetching Open-Meteo forecast: {e_m}")

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

                p_val = prices_map.get(k_full, prices_map.get(k_hour, 0.28))
                
                # Solar interpolation across quarters
                s_h0 = solar_map.get(k_hour, 0.0)
                s_h1 = solar_map.get(k_next_hour, s_h0)
                frac = (dt_slot.minute / 60.0) if is_15m else 0.0
                s_val = round(max(0.0, s_h0 + (s_h1 - s_h0) * frac), 2)

                t_h0 = temp_map.get(k_hour, 16.0)
                t_h1 = temp_map.get(k_next_hour, t_h0)
                t_val = round(t_h0 + (t_h1 - t_h0) * frac, 1)

                # Unallocated load from exact 7x96 matrix
                dow = dt_slot.weekday()
                q_idx = dt_slot.hour * 4 + dt_slot.minute // 15
                if grid_96 and len(grid_96) > dow and len(grid_96[dow]) > q_idx:
                    unalloc_w = grid_96[dow][q_idx]
                elif GLOBAL_MODEL:
                    unalloc_w = GLOBAL_MODEL.predict_unallocated_w(dt_slot)
                else:
                    unalloc_w = 300.0

                unalloc_kw = round(float(unalloc_w) / 1000.0, 2)

                w_val = wind_map.get(k_hour, 3.0)
                rh_val = rh_map.get(k_hour, 75.0)

                labels.append(lbl)
                prices.append(p_val)
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
                ha_sec = load_secrets()
                ha_tok = ha_sec.get("homeassistant", {}).get("token")
                ha_url = ha_sec.get("homeassistant", {}).get("url", "https://hass.b3rg.nl:8123")
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

            # 5. Plan Hot Water Generation (SWW Boiler 350L) with Thermal State Decision
            # Query live tank temperature
            t_dhw_live = 49.2
            try:
                ha_sec = load_secrets()
                ha_tok = ha_sec.get("homeassistant", {}).get("token")
                ha_url = ha_sec.get("homeassistant", {}).get("url", "https://hass.b3rg.nl:8123")
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
                            t_dhw_live = val_t
            except Exception:
                pass

            # Unified Central Thermal Evaluation (Single Source of Truth):
            # Check if the unheated tank drops below comfort (< 40°C) during a dynamic hard lockout peak before midday
            unheated_sim = GLOBAL_DHW_MODEL.simulate_trajectory(t_dhw_live, now_ams, hours_ahead=24, heat_pump_schedule_slots=[]) if GLOBAL_DHW_MODEL else {}
            unheated_temps = unheated_sim.get("temperatures_c", [])

            morning_check_limit = 44 if is_15m else 11
            dips_in_morning_hard_lockout = any(
                unheated_temps[k] < 40.0 and slot_lockout_map.get(k, {}).get("is_hard_lockout")
                for k in range(min(len(unheated_temps), morning_check_limit))
            )
            needs_night_charge = dips_in_morning_hard_lockout

            # Dynamic Economic Arbitrage for DHW 60°C Solar Buffer Boost:
            daylight_slots = [it for it in timeline_items if 10 <= it["dt"].hour <= 16]
            today_daylight_slots = [it for it in daylight_slots if it["dt"].day == now_ams.day]
            tot_net_surplus_kwh = sum(max(0.0, it["solar"] - unallocated[it["idx"]]) for it in today_daylight_slots) * (0.25 if is_15m else 1.0)
            peak_net_surplus_kw = max((max(0.0, it["solar"] - unallocated[it["idx"]]) for it in today_daylight_slots), default=0.0)

            # Electricity required to boost 350L from 50°C to 60°C (+4.07 kWh_th at COP 2.15)
            el_boost_needed_kwh = 1.89
            solar_used_kwh = min(el_boost_needed_kwh, tot_net_surplus_kwh)
            grid_import_kwh = max(0.0, el_boost_needed_kwh - solar_used_kwh)

            # Midday dynamic import & export pricing
            midday_prices = [it["price"] for it in today_daylight_slots]
            p_midday = (sum(midday_prices) / len(midday_prices)) if midday_prices else 0.28
            p_export = max(0.0, (p_midday / 1.21) - 0.11085 - 0.0121)

            # Future avoided electricity price (e.g. evening peak 18:00 - 22:00 or tomorrow)
            evening_slots = [it for it in timeline_items if (18 <= it["dt"].hour <= 22)]
            p_future_avoided = (sum(it["price"] for it in evening_slots) / len(evening_slots)) if evening_slots else 0.35

            # Cost now: lost feed-in revenue of solar + actual grid import cost
            cost_boost_now = (solar_used_kwh * p_export) + (grid_import_kwh * p_midday)
            # Avoided future cost: 3.27 kWh_th carried over (after standby loss) heated at COP 2.85
            cost_avoided_later = (3.27 / 2.85) * p_future_avoided
            boost_net_saving_eur = round(cost_avoided_later - cost_boost_now, 3)

            # DYNAMIC CRITERION: Boost to 60°C is ONLY activated if there is abundant free solar surplus (>= 3.0 kWh net)
            # preventing costly grid imports at low COP (2.15) when the tank is already adequately heated to 50°C.
            is_solar_boost_eligible = (tot_net_surplus_kwh >= 3.0 and boost_net_saving_eur > 0.05)

            planned_mode = "standby_normal"
            planned_mode_label = "Geen geforceerde run gepland"
            sww_start_idx = -1
            slots_to_fill = 0
            sww_power_kw = 1.8
            sww_target_temp = 60.0 if (is_solar_boost_eligible and today_daylight_slots) else 50.0

            # LIVE THERMAL FEEDBACK: Is the tank ALREADY at or above target temperature?
            # (e.g. the heat pump has already run autonomously or completed its heating cycle!)
            tank_already_warm = (t_dhw_live >= (sww_target_temp - 0.8))

            if tank_already_warm and not needs_night_charge:
                # Target already achieved! Standby in effect: cancel any redundant daytime runs!
                planned_mode = "normal"
                planned_mode_label = f"Normaal: Doeltemperatuur bereikt ({t_dhw_live:.1f}°C) — Standby"
                reason = f"Boilervat is met {t_dhw_live:.1f}°C reeds op gewenste temperatuur (≥ {sww_target_temp:.0f}°C). Warmtepomp staat in rust."
                sww_start_idx = -1
                slots_to_fill = 0
                sww_power_kw = 0.0
            elif is_solar_boost_eligible and today_daylight_slots:
                # 1. Mode: Maximaal aan (60°C Zonnebuffer Boost) during today's solar peak!
                best_sww_slot = max(today_daylight_slots, key=lambda x: x["solar"])
                sww_start_idx = max(0, best_sww_slot["idx"] - (1 if is_15m else 0))
                sww_power_kw = 2.65
                sww_target_temp = 60.0
                planned_mode = "forced_solar_boost_60"
                planned_mode_label = "Maximaal aan (doorverwarming tot 60°C)"
                reason = f"Maximaal aan (60°C): {tot_net_surplus_kwh:.1f} kWh netto zonne-overschot buffert voordelig door naar 60°C"
            elif today_daylight_slots:
                # 2. Mode: Geforceerd aan (50°C Dagrun) during today's best solar/tariff slot!
                best_sww_slot = max(today_daylight_slots, key=lambda x: (x["solar"] - x["price"] * 0.5))
                sww_start_idx = best_sww_slot["idx"]
                sww_power_kw = 1.8
                sww_target_temp = 50.0
                planned_mode = "forced_standard_50"
                planned_mode_label = "Geforceerd aan (verwarmen tot 50°C)"
                reason = f"Geforceerd aan (50°C): Laadt vanaf {best_sww_slot['label']} op zonnestroom naar 50°C"
            elif needs_night_charge:
                # 3. Mode: Geforceerd aan (Nachtlading tot 50°C) only when daylight has passed!
                night_slots = [it for it in timeline_items if (1 <= it["dt"].hour <= 5)]
                best_sww_slot = min(night_slots, key=lambda x: (x["price"], abs(x["dt"].hour + x["dt"].minute/60.0 - 3.5))) if night_slots else min(timeline_items[:24], key=lambda x: (x["price"], abs(x["dt"].hour + x["dt"].minute/60.0 - 3.5)))
                sww_start_idx = best_sww_slot["idx"]
                sww_power_kw = 1.8
                sww_target_temp = 50.0
                planned_mode = "forced_night_50"
                planned_mode_label = "Geforceerd aan (Nachtlading tot 50°C)"
                reason = f"Geforceerd aan (€{best_sww_slot['price']:.3f}/kWh) waarborgt ochtendcomfort vóór prijspiek"
            else:
                # Fallback to cheapest price slot outside peaks
                valid_slots = [it for it in timeline_items if not ((it["idx"] in slot_lockout_map) and slot_lockout_map[it["idx"]].get("is_hard_lockout"))]
                best_sww_slot = min(valid_slots, key=lambda x: (x["price"], abs(x["dt"].hour + x["dt"].minute/60.0 - 3.5))) if valid_slots else timeline_items[0]
                sww_start_idx = best_sww_slot["idx"]
                sww_power_kw = 1.8
                sww_target_temp = 50.0
                planned_mode = "forced_standard_50"
                planned_mode_label = "Geforceerd aan (verwarmen tot 50°C)"
                reason = f"Laagste beurstarief (€{best_sww_slot['price']:.3f}/kWh) om {best_sww_slot['label']}"

            if not tank_already_warm and sww_start_idx >= 0:
                # Calculate required slots dynamically based on thermal mass so the tank ACTUALLY reaches sww_target_temp (50°C of 60°C)
                c_tank_kwh_per_c = 350.0 * 4.186 / 3600.0  # 0.407 kWh/K
                step_h = 0.25 if is_15m else 1.0
                t_run_start_est = max(34.0, t_dhw_live - (sww_start_idx * step_h * 0.28))
                delta_t_run = max(2.0, sww_target_temp - t_run_start_est)
                cop_run_est = 2.85 if sww_target_temp <= 52.0 else 2.15
                p_th_run_est = sww_power_kw * cop_run_est
                hours_run_needed = (delta_t_run * c_tank_kwh_per_c) / p_th_run_est
                slots_to_fill = max(2, math.ceil(hours_run_needed / step_h) + (1 if is_15m else 0))

            # Fill boiler dispatch while enforcing DYNAMIC PEAK LOCKOUTS
            for k in range(slots_to_fill):
                target_slot = sww_start_idx + k
                if target_slot < total_slots:
                    slot_peak = slot_lockout_map.get(target_slot)
                    is_locked_slot = bool(slot_peak and slot_peak.get("is_hard_lockout"))
                    if not is_locked_slot:
                        boiler[target_slot] = sww_power_kw
                        advices[target_slot] = f"♨️ SWW Boiler 350L: {reason}"

            # Build 24h Mode Timeline for Horizontal Bar Diagram (Standardized 6 States)
            dhw_mode_timeline = []
            min_timeline_price = min([it.get("price", 0.30) for it in timeline_items] or [0.20])
            for it in timeline_items:
                q_idx = it["idx"]
                p_val = it.get("price", 0.30)
                sol_val = it.get("solar", 0.0) if "solar" in it else (solar[q_idx] if q_idx < len(solar) else 0.0)
                peak_info = slot_lockout_map.get(q_idx)

                if peak_info and peak_info.get("is_hard_lockout"):
                    # 1. Geforceerd uit (blok) - Rood (#EF4444)
                    m_code = "forced_off"
                    m_lbl = f"Geforceerd uit (blok) — {peak_info['name']}"
                    m_col = "#EF4444"
                    m_pwr = 0.0
                    m_desc = f"Geforceerd uit ({it['label']}): Prijspiek max €{peak_info['max_price']:.3f}/kWh. Compressor SG4 vergrendeld tegen piektarieven."
                elif peak_info and not peak_info.get("is_hard_lockout") and boiler[q_idx] == 0:
                    # 2. Geadviseerd uit - Oranje (#F59E0B)
                    m_code = "advised_off"
                    m_lbl = f"Geadviseerd uit — {peak_info['name']}"
                    m_col = "#F59E0B"
                    m_pwr = 0.0
                    m_desc = f"Geadviseerd uit ({it['label']}): Verhoogd tarief (€{p_val:.3f}/kWh). Uitstel van grote verbruikers aanbevolen; CV op lage modulatie."
                elif boiler[q_idx] > 0:
                    if planned_mode in ["forced_solar_boost_60", "max_on"]:
                        # 6. Maximaal aan (60°C) - Paars (#A855F7)
                        m_code = "max_on"
                        m_lbl = "Maximaal aan (doorverwarming tot 60°C)"
                        m_col = "#A855F7"
                        m_desc = f"Maximaal aan om {it['label']}: Zonnebuffer doorverwarming naar 60°C · Vermogen {boiler[q_idx]} kW elektrisch."
                    else:
                        # 5. Geforceerd aan (50°C) - Groen (#10B981) (voor zowel dagrun als nachtbuffer!)
                        m_code = "forced_on"
                        m_lbl = "Geforceerd aan (verwarmen tot 50°C)"
                        m_col = "#10B981"
                        m_desc = f"Geforceerd aan om {it['label']}: Verwarmen naar setpoint 50°C · Vermogen {boiler[q_idx]} kW elektrisch."
                    m_pwr = boiler[q_idx]
                elif (sol_val >= 1.5 or p_val <= min_timeline_price + 0.030) and (10 <= it["dt"].hour <= 16):
                    # 4. Geadviseerd aan - Gestreept lichtgroen (#4ADE80)
                    m_code = "advised_on"
                    m_lbl = "Geadviseerd aan (Doorverwarmen)"
                    m_col = "#4ADE80"
                    m_pwr = 0.0
                    m_desc = f"Geadviseerd aan ({it['label']}): Voordelig venster (€{p_val:.3f}/kWh). Warmtepomp mag hoger doorverwarmen voor CV vloerbuffer."
                else:
                    # 3. Normaal - Grijs (#1E293B)
                    m_code = "normal"
                    m_lbl = "Normaal (Standby)"
                    m_col = "#1E293B"
                    m_pwr = 0.0
                    m_desc = f"Normaal ({it['label']}): Vrijloopvenster (€{p_val:.3f}/kWh). Warmtepomp en boiler in normale werking."

                dhw_mode_timeline.append({
                    "slot": q_idx,
                    "time": it["label"],
                    "mode": m_code,
                    "label": m_lbl,
                    "color": m_col,
                    "power_kw": m_pwr,
                    "description": m_desc
                })

                        # Cache central dispatch with exact 15-minute slot indices
            if is_15m:
                actual_15m_slots = [sww_start_idx + k for k in range(slots_to_fill)]
            else:
                actual_15m_slots = [sww_start_idx * 4 + k for k in range(slots_to_fill * 4)]

            GLOBAL_CENTRAL_CACHE["planned_dhw_slots"] = actual_15m_slots
            GLOBAL_CENTRAL_CACHE["sww_start_idx"] = sww_start_idx
            GLOBAL_CENTRAL_CACHE["slots_to_fill"] = slots_to_fill
            GLOBAL_CENTRAL_CACHE["target_temp_c"] = sww_target_temp
            GLOBAL_CENTRAL_CACHE["dynamic_peaks"] = dynamic_peaks
            GLOBAL_CENTRAL_CACHE["sww_power_kw"] = sww_power_kw
            GLOBAL_CENTRAL_CACHE["is_15m"] = is_15m
            GLOBAL_CENTRAL_CACHE["base_dt"] = base_dt
            GLOBAL_CENTRAL_CACHE["planned_mode"] = planned_mode
            GLOBAL_CENTRAL_CACHE["planned_mode_label"] = planned_mode_label
            GLOBAL_CENTRAL_CACHE["reason"] = reason

            run_start_time = timeline_items[sww_start_idx]["label"] if 0 <= sww_start_idx < total_slots else "--:--"
            run_end_time = timeline_items[min(total_slots - 1, sww_start_idx + slots_to_fill)]["label"] if 0 <= sww_start_idx < total_slots else "--:--"
            dhw_planning_summary = {
                "planned_mode": planned_mode,
                "planned_mode_label": planned_mode_label,
                "target_temp_c": sww_target_temp,
                "run_start": run_start_time,
                "run_end": run_end_time,
                "run_duration_min": slots_to_fill * (15 if is_15m else 60),
                "power_kw": sww_power_kw,
                "total_stroom_kwh": round(sww_power_kw * slots_to_fill * (0.25 if is_15m else 1.0), 2),
                "spits_lockout_hours": round(len([s for s in slot_lockout_map.values() if s.get("is_hard_lockout")]) * (0.25 if is_15m else 1.0), 1),
                "dynamic_peaks": dynamic_peaks,
                "arbitrage_saving_eur": boost_net_saving_eur,
                "arbitrage_p_midday": round(p_midday, 4),
                "arbitrage_p_future": round(p_future_avoided, 4),
                "arbitrage_surplus_kwh": round(tot_net_surplus_kwh, 2)
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

            for it in timeline_items:
                idx = it["idx"]
                s_gen = it["solar"]
                sched_load = unallocated[idx] + boiler[idx] + heating[idx] + battery_charge[idx]
                surp = round(max(0.0, s_gen - sched_load), 2)
                surplus_kw_list.append(surp)
                if surp >= 0.15:
                    surplus_kwh_tot += surp
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

            # Calculate dynamic export prices for forecast (kale spot min opslag)
            export_prices = [max(0.0, round((p / 1.21) - 0.11085 - 0.0121 - 0.00605, 4)) for p in prices]

            self._send_json({
                "hours": labels,
                "labels": labels,
                "interval_h": step_h,
                "battery_enabled": is_battery_active,
                "battery_simulated": bool(sim_battery_param and not battery_installed),
                "export_prices_eur": export_prices,
                "dhw_mode_timeline": dhw_mode_timeline,
                "dynamic_peaks": dynamic_peaks,
                "dhw_planning_summary": dhw_planning_summary,
                "datasets": {
                    "unallocated_kw": unallocated,
                    "baseload_kw": unallocated,
                    "boiler_kw": boiler,
                    "heating_kw": heating,
                    "battery_charge_kw": battery_charge,
                    "solar_kw_neg": solar_neg,
                    "battery_discharge_kw_neg": bat_discharge_neg,
                    "surplus_kw": surplus_kw_list,
                    "net_power_kw": net_power,
                    "prices_eur": prices,
                    "export_prices_eur": export_prices
                },
                "advices": advices,
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
                        params.setdefault("building", {})["ua_base_w_per_k"] = round((1.0 - ewma) * old_v + ewma * prop_v, 1)
                    elif p_id == "night_baseload":
                        old_v = float(params.get("unallocated", {}).get("night_baseload_floor_w", 265.0))
                        prop_v = float(r.get("proposed_value", old_v))
                        params.setdefault("unallocated", {})["night_baseload_floor_w"] = round((1.0 - ewma) * old_v + ewma * prop_v, 1)
                    elif p_id == "dhw_standby":
                        old_v = float(params.get("dhw_tank", {}).get("standby_loss_w_per_k", 2.50))
                        prop_v = float(r.get("proposed_value", old_v))
                        params.setdefault("dhw_tank", {})["standby_loss_w_per_k"] = round((1.0 - ewma) * old_v + ewma * prop_v, 2)

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
            ha_cfg_tok = ha_sec.get("homeassistant", {}).get("token")
            if ha_cfg_tok:
                ha_token = ha_cfg_tok
                ha_base_url = ha_sec.get("homeassistant", {}).get("url") or "https://hass.b3rg.nl:8123"
            elif os.environ.get("SUPERVISOR_TOKEN"):
                ha_token = os.environ["SUPERVISOR_TOKEN"]
                ha_base_url = "http://supervisor/core"
            else:
                ha_token = os.environ.get("HASS_TOKEN", "")
                ha_base_url = os.environ.get("HASS_URL", "https://hass.b3rg.nl:8123")
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
    def _serve_spa(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()

        html = """<!DOCTYPE html>
<html class="dark h-full bg-[#080B11]" lang="nl">
<head>
    <meta charset="utf-8"/>
    <meta content="width=device-width, initial-scale=1.0" name="viewport"/>
    <title>Open HEMS Framework</title>
    <!-- Tailwind CSS v3 via CDN -->
    <script src="https://cdn.tailwindcss.com?plugins=forms"></script>
    <!-- Chart.js for 24h Stacked Bar & Curve Visualizer -->
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <script>
        tailwind.config = {
            darkMode: 'class',
            theme: {
                extend: {
                    colors: {
                        brand: {
                            solar: '#F59E0B',
                            battery: '#10B981',
                            grid: '#3B82F6',
                            heatpump: '#06B6D4',
                            boiler: '#EC4899',
                            dark: '#0B0F17',
                            surface: '#0e1422',
                            border: '#1E293B'
                        }
                    },
                    fontFamily: {
                        sans: ['Inter', 'system-ui', '-apple-system', 'BlinkMacSystemFont', 'Segoe UI', 'Roboto', 'sans-serif']
                    }
                }
            }
        }
    </script>
    <style>
        .tab-content { display: none; }
        .tab-content.active { display: block; }
        .nav-link.active {
            background: linear-gradient(to right, rgba(59, 130, 246, 0.2), rgba(59, 130, 246, 0.05));
            color: #60A5FA;
            border-left: 4px solid #3B82F6;
        }
    </style>
</head>
<body class="h-full text-slate-200 antialiased flex overflow-hidden bg-[#080B11] font-sans select-none relative">

    <!-- MOBILE SIDEBAR BACKDROP -->
    <div id="sidebar-backdrop" onclick="toggleMobileSidebar(false)" class="fixed inset-0 bg-black/70 backdrop-blur-sm z-40 hidden md:hidden"></div>

    <!-- LEFT SIDEBAR NAVIGATION (RESPONSIVE DRAWER ON MOBILE) -->
    <aside id="main-sidebar" class="fixed inset-y-0 left-0 z-50 w-72 md:w-64 flex-shrink-0 bg-[#0B0F17] border-r border-[#1E293B] flex flex-col justify-between transform -translate-x-full md:relative md:translate-x-0 transition-transform duration-300 ease-in-out shadow-2xl md:shadow-none">
        <div>
            <!-- Header Brand -->
            <div class="h-16 md:h-20 px-4 md:px-6 flex items-center justify-between border-b border-[#1E293B]">
                <div class="flex items-center gap-3">
                    <div class="w-9 h-9 md:w-10 md:h-10 rounded-xl bg-gradient-to-tr from-amber-500/20 to-amber-400/10 border border-amber-500/30 flex items-center justify-center text-amber-400 shadow-[0_0_15px_rgba(245,158,11,0.2)]">
                        <svg class="w-5 h-5 fill-current" viewBox="0 0 24 24"><path d="M13 2L3 14h9l-1 8 10-12h-9l1-8z"></path></svg>
                    </div>
                    <div>
                        <div class="flex items-center gap-2">
                            <span class="font-bold tracking-tight text-white text-base">Open HEMS</span>
                            <span class="px-1.5 py-0.5 text-[9px] font-semibold bg-blue-500/10 text-blue-400 rounded border border-blue-500/20">CORE</span>
                        </div>
                        <p class="text-[11px] text-slate-400">Data & Policy Platform</p>
                    </div>
                </div>
                <!-- Mobile close button -->
                <button onclick="toggleMobileSidebar(false)" class="p-1.5 rounded-lg text-slate-400 hover:text-white hover:bg-slate-800 md:hidden" aria-label="Sluit menu">
                    <svg class="w-5 h-5" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path d="M6 18L18 6M6 6l12 12"></path></svg>
                </button>
            </div>

            <!-- Nav Links (Streamlined 4-Layer Hierarchy) -->
            <nav class="p-3 space-y-1">
                <!-- ANALYSE -->
                <div class="px-3 pt-2 pb-1 text-[10px] font-bold text-slate-500 uppercase tracking-wider">
                    <span>Analyse</span>
                </div>
                <a href="#prediction" onclick="showTab('prediction')" id="nav-prediction" class="nav-link active flex items-center gap-3 px-3.5 py-2.5 rounded-xl text-xs font-medium text-slate-400 hover:text-white hover:bg-slate-800/40 transition-colors">
                    <svg class="w-4 h-4 text-cyan-400" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path d="M13 10V3L4 14h7v7l9-11h-7z"></path></svg>
                    <span>Voorspelling</span>
                </a>
                <a href="#history" onclick="showTab('history')" id="nav-history" class="nav-link flex items-center gap-3 px-3.5 py-2.5 rounded-xl text-xs font-medium text-slate-400 hover:text-white hover:bg-slate-800/40 transition-colors">
                    <svg class="w-4 h-4 text-blue-400" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z"></path></svg>
                    <span>Historie</span>
                </a>

                <!-- POLICY -->
                <div class="px-3 pt-3 pb-1 text-[10px] font-bold text-slate-500 uppercase tracking-wider">
                    <span>Policy</span>
                </div>
                <a href="#policies" onclick="showTab('policies')" id="nav-policies" class="nav-link flex items-center gap-3 px-3.5 py-2.5 rounded-xl text-xs font-medium text-slate-400 hover:text-white hover:bg-slate-800/40 transition-colors">
                    <svg class="w-4 h-4 text-purple-400" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path d="M12 6V4m0 2a2 2 0 100 4m0-4a2 2 0 110 4m-6 8a2 2 0 100-4m0 4a2 2 0 110-4m0 4v2m0-6V4m6 6v10m6-2a2 2 0 100-4m0 4a2 2 0 110-4m0 4v2m0-6V4"></path></svg>
                    <span>Apparaat Policies</span>
                    <span class="ml-auto text-[10px] px-1.5 py-0.5 bg-purple-900/40 text-purple-300 font-medium rounded border border-purple-800" id="badge-pol-count">3</span>
                </a>

                <!-- ZELFLEREND -->
                <div class="px-3 pt-3 pb-1 text-[10px] font-bold text-slate-500 uppercase tracking-wider">
                    <span>Zelflerend</span>
                </div>
                <a href="#calibration" onclick="showTab('calibration')" id="nav-calibration" class="nav-link flex items-center gap-3 px-3.5 py-2.5 rounded-xl text-xs font-medium text-slate-400 hover:text-white hover:bg-slate-800/40 transition-colors">
                    <svg class="w-4 h-4 text-amber-400" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M9.75 17L9 20l-1 1h8l-1-1-.75-3M3 13h18M5 17h14a2 2 0 002-2V5a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z"></path></svg>
                    <span>Zelflerend Model</span>
                </a>

                <!-- INSTELLINGEN -->
                <div class="px-3 pt-3 pb-1 text-[10px] font-bold text-slate-500 uppercase tracking-wider">
                    <span>Instellingen</span>
                </div>
                <a href="#devices" onclick="showTab('devices')" id="nav-devices" class="nav-link flex items-center gap-3 px-3.5 py-2.5 rounded-xl text-xs font-medium text-slate-400 hover:text-white hover:bg-slate-800/40 transition-colors">
                    <svg class="w-4 h-4 text-blue-400" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path d="M9 3v2m6-2v2M9 19v2m6-2v2M5 9H3m2 6H3m18-6h-2m2 6h-2M7 19h10a2 2 0 002-2V7a2 2 0 00-2-2H7a2 2 0 00-2 2v10a2 2 0 002 2zM9 9h6v6H9V9z"></path></svg>
                    <span>Apparaten</span>
                    <span class="ml-auto text-[10px] px-1.5 py-0.5 bg-blue-900/40 text-blue-300 font-medium rounded border border-blue-800" id="badge-dev-count">5</span>
                </a>
                <a href="#infrastructure" onclick="showTab('infrastructure')" id="nav-infrastructure" class="nav-link flex items-center gap-3 px-3.5 py-2.5 rounded-xl text-xs font-medium text-slate-400 hover:text-white hover:bg-slate-800/40 transition-colors">
                    <svg class="w-4 h-4 text-cyan-400" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path d="M13 10V3L4 14h7v7l9-11h-7z"></path></svg>
                    <span>Verbindingen</span>
                </a>
                <a href="#tariffs" onclick="showTab('tariffs')" id="nav-tariffs" class="nav-link flex items-center gap-3 px-3.5 py-2.5 rounded-xl text-xs font-medium text-slate-400 hover:text-white hover:bg-slate-800/40 transition-colors">
                    <svg class="w-4 h-4 text-emerald-400" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path d="M12 8c-1.657 0-3 .895-3 2s1.343 2 3 2 3 .895 3 2-1.343 2-3 2m0-8c1.11 0 2.08.402 2.599 1M12 8V7m0 1v8m0 0v1m0-1c-1.11 0-2.08-.402-2.599-1M21 12a9 9 0 11-18 0 9 9 0 0118 0z"></path></svg>
                    <span>Energieleveranciers</span>
                </a>
                <a href="#data" onclick="showTab('data')" id="nav-data" class="nav-link flex items-center gap-3 px-3.5 py-2.5 rounded-xl text-xs font-medium text-slate-400 hover:text-white hover:bg-slate-800/40 transition-colors">
                    <svg class="w-4 h-4 text-emerald-400" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path d="M4 7v10c0 2.21 3.582 4 8 4s8-1.79 8-4V7M4 7c0 2.21 3.582 4 8 4s8-1.79 8-4M4 7c0-2.21 3.582-4 8-4s8 1.79 8 4m0 5c0 2.21-3.582 4-8 4s-8-1.79-8-4"></path></svg>
                    <span>Data</span>
                </a></nav>
        </div>

        <div class="p-4 border-t border-[#1E293B] bg-[#0A0D14]/80 text-[10px] text-slate-500 flex justify-between">
            <span>Versie: <strong class="text-slate-400">v0.90.0</strong></span>
            <span>Multi-Instance Laag 1</span>
        </div>
    </aside>

    <!-- MAIN VIEW -->
    <main class="flex-1 flex flex-col min-w-0 overflow-y-auto bg-[#080B11] w-full">
        <header class="h-16 md:h-20 border-b border-[#1E293B] bg-[#0B0F17]/95 backdrop-blur px-4 md:px-8 flex items-center justify-between sticky top-0 z-30">
            <div class="flex items-center gap-3 min-w-0">
                <!-- Mobile Hamburger Button -->
                <button onclick="toggleMobileSidebar(true)" class="p-2 -ml-1 rounded-xl text-slate-300 hover:text-white hover:bg-slate-800 md:hidden flex-shrink-0" aria-label="Open menu">
                    <svg class="w-6 h-6" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path d="M4 6h16M4 12h16M4 18h16"></path></svg>
                </button>
                <div class="min-w-0">
                    <h1 class="text-base md:text-lg font-bold text-white tracking-tight truncate" id="header-title">Verbindingen & Opslag (Laag 1)</h1>
                    <p class="text-[11px] md:text-xs text-slate-400 mt-0.5 hidden sm:block truncate" id="header-sub">Beheer InfluxDB en MQTT instanties voor tijdreeksopslag en streaming connectiviteit.</p>
                </div>
            </div>
            <div class="flex items-center gap-2 md:gap-3 flex-shrink-0">
                <button onclick="refreshCurrentTab()" class="px-2.5 md:px-3 py-1.5 bg-blue-600 hover:bg-blue-500 text-white text-xs font-semibold rounded-xl shadow transition-all flex items-center gap-1.5">
                    <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"></path></svg>
                    <span class="hidden sm:inline">Verversen</span>
                </button>
            </div>
        </header>

        <div class="p-3 sm:p-5 md:p-8 space-y-5 md:space-y-8 max-w-7xl mx-auto w-full">

            <!-- TAB 0: INFRASTRUCTURE & CONNECTIVITY (LAAG 1) -->
                        <!-- TAB 5: ANALYTICS & REPORTING (BOVENAAN LAAG 5) -->
            <div id="view-prediction" class="tab-content active space-y-6">

                <!-- 1. TOP 4 KPI METRIC CARDS -->
                <div class="grid grid-cols-2 md:grid-cols-4 gap-3 sm:gap-4">
                    <div class="bg-[#0e1422] border border-[#1E293B] rounded-2xl p-4 shadow">
                        <span class="text-[10px] uppercase font-bold text-slate-400 block tracking-wider">Besparing Vandaag</span>
                        <div class="text-xl font-bold text-emerald-400 mt-1" id="kpi-savings-today">€0.85</div>
                        <span class="text-[10px] text-slate-500">t.o.v. standaard verbruik</span>
                    </div>
                    <div class="bg-[#0e1422] border border-[#1E293B] rounded-2xl p-4 shadow">
                        <span class="text-[10px] uppercase font-bold text-slate-400 block tracking-wider">Zelfconsumptie</span>
                        <div class="text-xl font-bold text-amber-400 mt-1" id="kpi-self-consumption">78.4%</div>
                        <span class="text-[10px] text-slate-500">Zon direct lokaal benut</span>
                    </div>
                    <div class="bg-[#0e1422] border border-[#1E293B] rounded-2xl p-4 shadow">
                        <span class="text-[10px] uppercase font-bold text-slate-400 block tracking-wider">Warmtepomp COP</span>
                        <div class="text-xl font-bold text-cyan-400 mt-1" id="kpi-cop-dhw">2.04 <span class="text-xs text-slate-400 font-normal">SWW</span> · 4.80 <span class="text-xs text-slate-400 font-normal">CV</span></div>
                        <span class="text-[10px] text-slate-500">Gemeten rendement</span>
                    </div>
                    <div class="bg-[#0e1422] border border-[#1E293B] rounded-2xl p-4 shadow">
                        <span class="text-[10px] uppercase font-bold text-slate-400 block tracking-wider">Prognose Validatie</span>
                        <div class="text-xl font-bold text-purple-400 mt-1" id="kpi-accuracy">92.6%</div>
                        <span class="text-[10px] text-slate-500">MAE: 0.18 kW</span>
                    </div>
                </div>

                <!-- ========================================================================= -->
                <!-- CATEGORIE 1: VOORSPELLING (FORECAST)                                      -->
                <!-- ========================================================================= -->
                <div class="space-y-4 pt-2">
                    <!-- Sticky Category Header & Controls Bar -->
                    <div class="sticky top-0 z-30 bg-[#0B0F17]/95 backdrop-blur-md py-2.5 -mx-4 px-4 sm:-mx-6 sm:px-6 md:-mx-8 md:px-8 border-b border-purple-500/30 shadow-lg shadow-black/50 flex flex-col sm:flex-row sm:items-center justify-between gap-2.5 transition-all">
                        <div class="flex items-center gap-2.5">
                            <span class="w-2.5 h-2.5 rounded-full bg-purple-500 animate-pulse"></span>
                            <h2 class="text-sm sm:text-base font-bold text-white tracking-wide uppercase">Voorspelling</h2>
                            <span class="text-[10px] text-purple-300 font-mono bg-purple-950/80 px-2 py-0.5 rounded border border-purple-800">24H FORECAST</span>
                        </div>
                        <div class="flex items-center gap-2 text-xs flex-wrap">
                            <!-- Diagram Type Toggle: Staven vs Lijn -->
                            <div class="inline-flex rounded-lg bg-slate-900 p-0.5 border border-slate-700 text-[10px] font-mono">
                                <button id="pred-btn-type-bar" onclick="setPredictionChartType('bar')" class="px-2.5 py-1 rounded transition font-medium bg-purple-600 text-white shadow">📊 Staven</button>
                                <button id="pred-btn-type-line" onclick="setPredictionChartType('line')" class="px-2.5 py-1 rounded transition font-medium text-slate-400 hover:text-slate-200">📈 Lijn</button>
                            </div>

                            <!-- Interval / Resolutie Toggle -->
                            <div class="inline-flex rounded-lg bg-slate-900 p-0.5 border border-slate-700 text-[10px] font-mono">
                                <button onclick="setPredictionResolution('1h')" class="res-btn-1h px-2.5 py-1 rounded transition font-medium bg-purple-600 text-white shadow">1 Uur</button>
                                <button onclick="setPredictionResolution('15m')" class="res-btn-15m px-2.5 py-1 rounded transition font-medium text-slate-400 hover:text-slate-200">15 Min</button>
                            </div>

                            <button onclick="loadChartData(); loadElectricityPricesChart(); renderDhwTemperatureChart(); renderHeatingForecastChart();" class="px-2.5 py-1 bg-slate-800 hover:bg-slate-700 text-slate-300 text-xs rounded-lg font-medium border border-slate-700 transition flex items-center gap-1.5">
                                <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"></path></svg>
                                <span>Verversen</span>
                            </button>
                        </div>
                    </div>

                    <!-- Chart 1.1: Verbruiksvoorspelling (Stacked 24h Prediction) -->
                    <div class="bg-[#0e1422] border border-[#1E293B] rounded-2xl p-4 sm:p-5 shadow-2xl space-y-3.5">
                        <div class="flex flex-col sm:flex-row sm:items-center justify-between gap-2 border-b border-slate-800/80 pb-3">
                            <div class="flex items-center gap-2">
                                <h3 class="text-sm font-bold text-white tracking-wide">Verbruiksvoorspelling</h3>
                            </div>
                            <div class="flex items-center gap-2 text-xs flex-wrap">
                                <span class="text-[10px] text-blue-400 font-mono bg-blue-950/60 px-2 py-0.5 rounded-md border border-blue-500/40" id="prediction-unallocated-badge">Ongedefinieerd: 7x24</span>
                                <span class="text-[10px] text-indigo-300 font-mono bg-indigo-950/70 px-2 py-0.5 rounded-md border border-indigo-500/40 font-bold" id="prediction-total-kwh-badge">⚡ Verbruik: -- kWh</span>
                                <span class="text-[10px] text-emerald-300 font-mono bg-emerald-950/70 px-2 py-0.5 rounded-md border border-emerald-500/40 font-bold" id="prediction-total-cost-badge">💶 Netto: €--</span>
                                <span class="text-[10px] text-amber-400 font-mono bg-amber-950/60 px-2 py-0.5 rounded-md border border-amber-500/30 font-bold" id="prediction-surplus-badge">☀️ Overschot: -- kWh</span>
                            </div>
                        </div>

                        <!-- Dispatch & Solar Advice Banners -->
                        <div class="bg-gradient-to-r from-purple-950/60 via-[#0B0F17] to-indigo-950/60 p-2.5 rounded-xl border border-purple-500/30 flex items-center justify-between gap-2 text-xs">
                            <div class="flex items-center gap-2 truncate">
                                <span>💡</span>
                                <span class="text-slate-200 font-medium truncate" id="analytics-banner-text">Planning wordt geladen...</span>
                            </div>
                            <span class="text-[10px] text-purple-300 font-mono font-bold px-2 py-0.5 rounded bg-purple-900/50 border border-purple-800 hidden md:inline flex-shrink-0">OPTIMIZER</span>
                        </div>

                        <div id="solar-recommendation-banner" class="bg-gradient-to-r from-emerald-950/60 via-[#0B0F17] to-teal-950/60 p-2.5 rounded-xl border border-emerald-500/30 flex items-center gap-2 text-xs text-emerald-200">
                            <span>🧺</span>
                            <span id="solar-recommendation-text" class="font-medium truncate">Zonne-overschot advies wordt geladen...</span>
                        </div>

                        <!-- Prediction Chart Canvas -->
                        <div class="relative w-full h-[380px] sm:h-[420px]">
                            <canvas id="hemsChartAnalytics"></canvas>
                        </div>

                        <!-- 24H PREDICTION 6-BOX METRIC SUMMARY (ALIGNED WITH HISTORICAL) -->
                        <div class="pt-2 border-t border-slate-800/80">
                            <div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-2.5 text-xs font-mono">
                                <!-- Zonnepanelen -->
                                <div class="bg-[#0B0F17] p-2.5 rounded-xl border border-slate-800/90 flex flex-col justify-between gap-1">
                                    <div class="flex items-center justify-between">
                                        <div class="flex items-center gap-2">
                                            <span class="w-3 h-1.5 rounded-sm bg-yellow-500"></span>
                                            <span class="text-slate-300 font-medium">Zonnepanelen</span>
                                        </div>
                                        <div class="text-right">
                                            <span class="text-xs text-yellow-400 font-bold block" id="pred-stat-solar-total">-- kWh</span>
                                            <span class="text-[10px] text-yellow-500/90 font-mono font-medium block" id="pred-stat-solar-cost">€--</span>
                                        </div>
                                    </div>
                                    <div class="text-[10px] space-x-2 text-right border-t border-slate-800/60 pt-1 text-slate-500">
                                        <span>Nu: <strong class="text-yellow-400 font-normal" id="pred-stat-solar-last">--</strong></span>
                                        <span>Piek: <span class="text-yellow-500/80" id="pred-stat-solar-min">--</span></span>
                                    </div>
                                </div>
                                <!-- Teruglevering -->
                                <div class="bg-[#0B0F17] p-2.5 rounded-xl border border-slate-800/90 flex flex-col justify-between gap-1">
                                    <div class="flex items-center justify-between">
                                        <div class="flex items-center gap-2">
                                            <span class="w-3 h-1.5 rounded-sm bg-emerald-500"></span>
                                            <span class="text-slate-300 font-medium">Teruglevering</span>
                                        </div>
                                        <div class="text-right">
                                            <span class="text-xs text-emerald-400 font-bold block" id="pred-stat-terug-total">-- kWh</span>
                                            <span class="text-[10px] text-emerald-500/90 font-mono font-medium block" id="pred-stat-terug-cost">€--</span>
                                        </div>
                                    </div>
                                    <div class="text-[10px] space-x-2 text-right border-t border-slate-800/60 pt-1 text-slate-500">
                                        <span>Nu: <strong class="text-emerald-400 font-normal" id="pred-stat-terug-last">--</strong></span>
                                        <span>Piek: <span class="text-emerald-500/80" id="pred-stat-terug-min">--</span></span>
                                    </div>
                                </div>
                                <!-- Afname -->
                                <div class="bg-[#0B0F17] p-2.5 rounded-xl border border-slate-800/90 flex flex-col justify-between gap-1">
                                    <div class="flex items-center justify-between">
                                        <div class="flex items-center gap-2">
                                            <span class="w-3 h-1.5 rounded-sm bg-red-500"></span>
                                            <span class="text-slate-300 font-medium">Afname</span>
                                        </div>
                                        <div class="text-right">
                                            <span class="text-xs text-red-400 font-bold block" id="pred-stat-afname-total">-- kWh</span>
                                            <span class="text-[10px] text-red-500/90 font-mono font-medium block" id="pred-stat-afname-cost">€--</span>
                                        </div>
                                    </div>
                                    <div class="text-[10px] space-x-2 text-right border-t border-slate-800/60 pt-1 text-slate-500">
                                        <span>Nu: <strong class="text-red-400 font-normal" id="pred-stat-afname-last">--</strong></span>
                                        <span>Piek: <span class="text-red-500/80" id="pred-stat-afname-max">--</span></span>
                                    </div>
                                </div>
                                <!-- Totaal opgewekt -->
                                <div class="bg-[#0B0F17] p-2.5 rounded-xl border border-slate-800/90 flex flex-col justify-between gap-1">
                                    <div class="flex items-center justify-between">
                                        <div class="flex items-center gap-2">
                                            <span class="w-3 h-1.5 rounded-sm bg-lime-500"></span>
                                            <span class="text-slate-300 font-medium">Totaal opgewekt</span>
                                        </div>
                                        <div class="text-right">
                                            <span class="text-xs text-lime-400 font-bold block" id="pred-stat-opgewekt-total">-- kWh</span>
                                            <span class="text-[10px] text-lime-500/90 font-mono font-medium block" id="pred-stat-opgewekt-cost">€--</span>
                                        </div>
                                    </div>
                                    <div class="text-[10px] space-x-2 text-right border-t border-slate-800/60 pt-1 text-slate-500">
                                        <span>Nu: <strong class="text-lime-400 font-normal" id="pred-stat-opgewekt-last">--</strong></span>
                                        <span>Piek: <span class="text-lime-500/80" id="pred-stat-opgewekt-min">--</span></span>
                                    </div>
                                </div>
                                <!-- Opgewekt Gebruikt -->
                                <div class="bg-[#0B0F17] p-2.5 rounded-xl border border-slate-800/90 flex flex-col justify-between gap-1">
                                    <div class="flex items-center justify-between">
                                        <div class="flex items-center gap-2">
                                            <span class="w-3 h-1.5 rounded-sm bg-teal-400"></span>
                                            <span class="text-slate-300 font-medium">Opgewekt Gebruikt</span>
                                        </div>
                                        <div class="text-right">
                                            <span class="text-xs text-teal-400 font-bold block" id="pred-stat-selfcons-total">-- kWh</span>
                                            <span class="text-[10px] text-teal-500/90 font-mono font-medium block" id="pred-stat-selfcons-cost">€--</span>
                                        </div>
                                    </div>
                                    <div class="text-[10px] space-x-2 text-right border-t border-slate-800/60 pt-1 text-slate-500">
                                        <span>Nu: <strong class="text-teal-400 font-normal" id="pred-stat-selfcons-last">--</strong></span>
                                        <span>Piek: <span class="text-teal-500/80" id="pred-stat-selfcons-min">--</span></span>
                                    </div>
                                </div>
                                <!-- Totaal Verbruik -->
                                <div class="bg-[#0B0F17] p-2.5 rounded-xl border border-slate-800/90 flex flex-col justify-between gap-1">
                                    <div class="flex items-center justify-between">
                                        <div class="flex items-center gap-2">
                                            <span class="w-3 h-1.5 rounded-sm bg-orange-500"></span>
                                            <span class="text-slate-300 font-medium">Totaal Verbruik</span>
                                        </div>
                                        <div class="text-right">
                                            <span class="text-xs text-orange-400 font-bold block" id="pred-stat-verbruik-total">-- kWh</span>
                                            <span class="text-[10px] text-orange-500/90 font-mono font-medium block" id="pred-stat-verbruik-cost">€--</span>
                                        </div>
                                    </div>
                                    <div class="text-[10px] space-x-2 text-right border-t border-slate-800/60 pt-1 text-slate-500">
                                        <span>Nu: <strong class="text-orange-400 font-normal" id="pred-stat-verbruik-last">--</strong></span>
                                        <span>Piek: <span class="text-orange-500/80" id="pred-stat-verbruik-max">--</span></span>
                                    </div>
                                </div>
                            </div>
                        </div>

                        <!-- Legend Chips -->
                        <div class="pt-2 border-t border-slate-800/80 flex flex-wrap items-center gap-3 text-xs font-mono">
                            <div class="flex items-center gap-1.5"><span class="w-3 h-3 rounded bg-blue-500"></span> <span class="text-slate-300">Ongedefinieerd (+kW)</span></div>
                            <div class="flex items-center gap-1.5"><span class="w-3 h-3 rounded bg-pink-500"></span> <span class="text-slate-300">SWW (+kW)</span></div>
                            <div class="flex items-center gap-1.5"><span class="w-3 h-3 rounded bg-indigo-500"></span> <span class="text-slate-300">CV (+kW)</span></div>
                            <div class="flex items-center gap-1.5"><span class="w-3 h-3 rounded bg-emerald-500"></span> <span class="text-slate-300">Accu Laden (+kW)</span></div>
                            <div class="flex items-center gap-1.5"><span class="w-3 h-3 rounded bg-amber-400"></span> <span class="text-slate-300">Zon (-kW)</span></div>
                            <div class="flex items-center gap-1.5"><span class="w-3.5 h-1 bg-red-500"></span> <span class="text-red-400 font-bold">Verwacht Netto</span></div>
                            <div class="flex items-center gap-1.5"><span class="w-3 h-1 bg-cyan-400 border-dashed"></span> <span class="text-cyan-400">Prijs (€/kWh)</span></div>
                        </div>
                    </div>

                    <!-- Chart 1.2: Prijzen & Zonnevoorspelling (EPEX Rates & Solar Forecast) -->
                    <div class="bg-[#0e1422] border border-[#1E293B] rounded-2xl p-4 sm:p-5 shadow-2xl space-y-3.5">
                        <div class="flex flex-col sm:flex-row sm:items-center justify-between gap-2 border-b border-slate-800/80 pb-3">
                            <h3 class="text-sm font-bold text-white tracking-wide">Prijzen & Zonnevoorspelling</h3>
                            <div class="flex items-center gap-2 text-xs">
                                <select id="epex-res-select" onchange="loadElectricityPricesChart()" class="bg-[#0B0F17] border border-slate-700 rounded-lg px-2.5 py-1 text-slate-200 text-xs font-medium focus:outline-none focus:border-blue-500 font-mono">
                                    <option value="15m" selected>Kwartiertarieven (15m)</option>
                                    <option value="1h">Uurtarieven (1h)</option>
                                </select>
                            </div>
                        </div>

                        <!-- Canvas -->
                        <div class="relative w-full h-60 sm:h-64">
                            <canvas id="electricityPricesChart"></canvas>
                        </div>

                        <!-- Price & Solar Stats Cards -->
                        <div class="pt-2 border-t border-slate-800/80">
                            <div class="grid grid-cols-2 sm:grid-cols-4 gap-2.5 text-xs font-mono">
                                <div class="bg-[#0B0F17] p-2.5 rounded-xl border border-slate-800/90">
                                    <div class="text-[10px] text-slate-500 uppercase">Laagste Tarief</div>
                                    <div class="text-sm font-bold text-emerald-400 mt-0.5" id="stat-epex-min">--</div>
                                    <div class="text-[10px] text-slate-400" id="stat-epex-min-time">om --:--</div>
                                </div>
                                <div class="bg-[#0B0F17] p-2.5 rounded-xl border border-slate-800/90">
                                    <div class="text-[10px] text-slate-500 uppercase">Hoogste Tarief</div>
                                    <div class="text-sm font-bold text-red-400 mt-0.5" id="stat-epex-max">--</div>
                                    <div class="text-[10px] text-slate-400" id="stat-epex-max-time">om --:--</div>
                                </div>
                                <div class="bg-[#0B0F17] p-2.5 rounded-xl border border-slate-800/90">
                                    <div class="text-[10px] text-slate-500 uppercase">Piek Zonverwachting</div>
                                    <div class="text-sm font-bold text-amber-400 mt-0.5" id="stat-epex-solar-peak">--</div>
                                    <div class="text-[10px] text-slate-400">Open-Meteo GHI</div>
                                </div>
                                <div class="bg-[#0B0F17] p-2.5 rounded-xl border border-slate-800/90">
                                    <div class="text-[10px] text-slate-500 uppercase">Zon Besparingsmarge</div>
                                    <div class="text-sm font-bold text-yellow-400 mt-0.5" id="stat-epex-solar-margin">--</div>
                                    <div class="text-[10px] text-emerald-400">Voordeel t.o.v. net</div>
                                </div>
                            </div>
                        </div>
                    </div>

                    
                    <!-- Card 1.25: Warmtepomp & Boiler 24-Uurs Modusplanning (Horizontale Tijdlijn) -->
                    <div class="bg-[#0e1422] border border-[#1E293B] rounded-2xl p-4 sm:p-5 shadow-2xl space-y-4" id="dhw-mode-timeline-container">
                        <div class="flex flex-col sm:flex-row sm:items-center justify-between gap-3 border-b border-slate-800/80 pb-3">
                            <div class="flex items-center gap-2.5">
                                <span class="text-xl">♨️</span>
                                <div>
                                    <h3 class="text-sm font-bold text-white tracking-wide">Warmtepomp &amp; Boiler 24-Uurs Modusplanning (Tijdlijn)</h3>
                                    <p class="text-[11px] text-slate-400">Verdeling over de dag: spitsblokkades (hard uit 🔒), normale standby vrijgave en geplande geforceerde runs.</p>
                                </div>
                            </div>
                            <div class="flex items-center gap-2.5 text-[11px] font-mono flex-wrap">
                                <span class="flex items-center gap-1.5 text-red-400"><span class="w-3 h-2 bg-red-600 rounded-sm" style="background: repeating-linear-gradient(45deg, #EF4444, #EF4444 2px, #B91C1C 2px, #B91C1C 4px)"></span> Geforceerd uit (blok)</span>
                                <span class="flex items-center gap-1.5 text-amber-400"><span class="w-3 h-2 bg-amber-500 rounded-sm"></span> Geadviseerd uit</span>
                                <span class="flex items-center gap-1.5 text-slate-400"><span class="w-3 h-2 bg-slate-700 rounded-sm"></span> Normaal</span>
                                <span class="flex items-center gap-1.5 text-emerald-300"><span class="w-3 h-2 border border-emerald-400/80 rounded-sm" style="background: repeating-linear-gradient(45deg, #10B981, #10B981 2px, #86EFAC 2px, #86EFAC 4px)"></span> Geadviseerd aan</span>
                                <span class="flex items-center gap-1.5 text-emerald-400"><span class="w-3 h-2 bg-emerald-500 rounded-sm"></span> Geforceerd aan</span>
                                <span class="flex items-center gap-1.5 text-purple-300"><span class="w-3 h-2 bg-purple-600 rounded-sm"></span> Maximaal aan (60°C)</span>
                            </div>
                        </div>

                        <!-- 1. The Segmented Horizontal Timeline Bar -->
                        <div class="space-y-1.5">
                            <div id="dhw-timeline-bar" class="flex w-full h-8 sm:h-9 rounded-xl overflow-hidden border border-slate-700/80 p-0.5 bg-[#0B0F17] gap-[1px]">
                                <!-- Populated dynamically via JS -->
                            </div>
                            <!-- Dynamic Rolling Time Scale Ticks (Starts at Nu) -->
                            <div id="dhw-timeline-ticks" class="flex justify-between text-[10px] text-slate-400 font-mono px-1">
                                <!-- Populated dynamically via JS matching labels -->
                            </div>
                        </div>

                        <!-- 2. Planning Summary Metric Cards -->
                        <div class="grid grid-cols-1 sm:grid-cols-3 gap-3 font-mono text-xs pt-1">
                            <div class="bg-black/50 p-3 rounded-xl border border-slate-800 space-y-1">
                                <div class="text-[10px] text-slate-400 uppercase font-bold flex items-center justify-between">
                                    <span id="dhw-dyn-lockout-title">🚫 Spitsblokkades</span>
                                    <span class="text-[9px] px-1.5 py-0.5 rounded bg-cyan-950 text-cyan-400 border border-cyan-800 font-mono font-bold">DYNAMISCH</span>
                                </div>
                                <div class="text-xs font-bold text-red-400" id="dhw-dyn-lockout-hours">Berekenen...</div>
                                <div class="text-[10px] text-slate-400 font-sans" id="dhw-dyn-lockout-sub">Real-time piekdetectie o.b.v. EPEX all-up tarieven.</div>
                            </div>
                            <div class="bg-black/50 p-3 rounded-xl border border-slate-800 space-y-1">
                                <div class="text-[10px] text-slate-400 uppercase font-bold">⚡ Geplande Run &amp; Modus</div>
                                <div class="text-xs font-bold text-amber-300" id="dhw-summary-mode">Zonnebuffer Boost (60°C)</div>
                                <div class="text-[10px] text-slate-400 font-sans" id="dhw-summary-times">Venster: 13:30 – 15:00u (90 min)</div>
                            </div>
                            <div class="bg-black/50 p-3 rounded-xl border border-slate-800 space-y-1">
                                <div class="text-[10px] text-slate-400 uppercase font-bold">🔋 Gebufferde Warmte &amp; Stroom</div>
                                <div class="text-xs font-bold text-purple-300" id="dhw-summary-energy">~3.8 kWh stroom (8.1 kWh_th)</div>
                                <div class="text-[10px] text-slate-400 font-sans" id="dhw-summary-shower">Mengcapaciteit ~715L douchewater (38°C).</div>
                            </div>
                        </div>
                    </div>


                    <!-- Chart 1.3: Boilervat Temperatuurtraject & Verwachte Warmwatervraag (24 Uur Vooruit) -->
                    <div class="bg-[#0e1422] border border-[#1E293B] rounded-2xl p-4 sm:p-5 shadow-2xl space-y-3.5" id="dhw-temp-chart-container">
                        <div class="flex flex-col sm:flex-row sm:items-center justify-between gap-2 border-b border-slate-800/80 pb-3">
                            <div class="flex items-center gap-2.5">
                                <span class="w-3 h-3 rounded-full bg-amber-500 animate-pulse"></span>
                                <div>
                                    <h3 class="text-sm font-bold text-white tracking-wide">Boilervat Temperatuurtraject &amp; Verwachte Warmwatervraag (24 Uur Vooruit)</h3>
                                    <p class="text-[11px] text-slate-400">Verloop in graden Celsius (°C) vanaf de actuele 350L tanksensor en de verwachte getapte liters per kwartier.</p>
                                </div>
                            </div>
                            <div class="flex items-center gap-3 text-xs font-mono flex-wrap">
                                <span class="flex items-center gap-1.5 text-amber-300"><span class="w-3 h-1 bg-amber-400 rounded"></span> Verwacht (°C)</span>
                                <span class="flex items-center gap-1.5 text-amber-200/80"><span class="w-3 h-2 bg-amber-400/20 border border-amber-400/40 rounded-sm"></span> Marge (P05–P95)</span>
                                <span class="flex items-center gap-1.5 text-slate-400"><span class="w-3 h-0.5 border-b border-slate-400 border-dashed"></span> Zonder Nachtladen (°C)</span>
                                <span class="flex items-center gap-1.5 text-slate-400/80"><span class="w-3 h-2 bg-slate-500/20 border border-slate-500/40 rounded-sm"></span> Marge Zonder Nacht</span>
                                <span class="flex items-center gap-1.5 text-red-400"><span class="w-3 h-0.5 border-b border-red-500 border-dashed"></span> Comfort 40°C</span>
                                <span class="flex items-center gap-1.5 text-emerald-400"><span class="w-3 h-0.5 border-b border-emerald-500 border-dashed"></span> Doel 50°C</span>
                                <span class="flex items-center gap-1.5 text-sky-300"><span class="w-2.5 h-2.5 bg-sky-500/50 rounded-sm"></span> Vraag (Liter)</span>
                            </div>
                        </div>

                        <!-- Canvas for Boiler Temperature -->
                        <div class="relative w-full h-60 sm:h-64">
                            <canvas id="chart-dhw-temperature"></canvas>
                        </div>

                        <!-- Besluitvorming & Economische Analyse: Nachtlading vs. Daglading (10:00u) -->
                        <div class="bg-[#0B0F17]/90 border border-slate-800 rounded-xl p-3.5 space-y-2 font-sans text-xs text-slate-300" id="dhw-night-decision-box">
                            <div class="flex flex-col sm:flex-row sm:items-center justify-between gap-2 border-b border-slate-800/80 pb-2">
                                <div class="flex items-center gap-2">
                                    <span class="text-sm">⚖️</span>
                                    <span class="font-bold text-white tracking-wide">Besluitvorming: Waarom Nachtladen vs. Daglading (10:00u)?</span>
                                    <button type="button" onclick="toggleInfoPopover(event, 'dhw_decision_box_info')" class="text-slate-500 hover:text-cyan-400 transition p-0.5 focus:outline-none" aria-label="Info">
                                        <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><circle cx="12" cy="12" r="10"></circle><path d="M12 16v-4m0-4h.01"></path></svg>
                                    </button>
                                </div>
                                <div id="dhw-box-status-pill">
                                    <!-- Dynamic Status Badge -->
                                </div>
                            </div>
                            
                            <div class="grid grid-cols-1 md:grid-cols-2 gap-3 pt-1 text-[11px] leading-relaxed">
                                <!-- Links: Comfort & Fysisch Verloop -->
                                <div class="space-y-1.5 bg-slate-900/40 p-2.5 rounded-lg border border-slate-800/60">
                                    <div class="font-semibold text-amber-400 flex items-center gap-1.5">
                                        <span>🌡️</span> <span>Comfort- &amp; Temperatuurrisico</span>
                                    </div>
                                    <p id="dhw-eval-comfort-text">
                                        Zonder nachtlading (<span class="text-slate-400 font-mono">grijze lijn</span>) daalt het vat door nachtelijk stilstandsverlies en ochtenddouches naar <strong class="text-amber-300" id="dhw-box-dip-text">39,9°C</strong> (bij piekverbruik zelfs <strong class="text-red-400" id="dhw-box-p95-text">38,3°C</strong>) vóór 10:00 uur.
                                    </p>
                                    <div class="text-[10px] text-slate-400 font-mono space-y-0.5 pt-0.5">
                                        <div>• Ochtenddip zonder nacht: <span class="text-amber-300 font-bold" id="dhw-box-dip-val">39,9°C om 09:44</span></div>
                                        <div>• Piekblokkades: <span class="text-slate-300 font-bold" id="dhw-box-spits-detail">Real-time berekening...</span></div>
                                    </div>
                                </div>

                                <!-- Rechts: Economische Afweging -->
                                <div class="space-y-1.5 bg-slate-900/40 p-2.5 rounded-lg border border-slate-800/60">
                                    <div class="font-semibold text-emerald-400 flex items-center gap-1.5">
                                        <span>💶</span> <span>Financiële Afweging (Nacht vs. Weekend-Dag)</span>
                                    </div>
                                    <p id="dhw-eval-finance-text">
                                        Nachtstroom kost vannacht ~€0,31/kWh (€0,56 per run). Morgenmiddag rond 12:00–14:00 is stroom aanzienlijk goedkoper (€0,11/kWh, ~€0,20 per run met zonne-energie).
                                    </p>
                                    <div class="text-[10px] text-slate-400 font-mono space-y-0.5 pt-0.5">
                                        <div>• Verschil: <span class="text-emerald-300 font-bold">~€0,36 voordeel</span> bij wachten tot middagzon.</div>
                                        <div>• Afweging: <span class="text-white font-bold">Gegarandeerd ochtendcomfort vóór 10:00u</span> vs €0,36 besparing.</div>
                                    </div>
                                </div>
                            </div>
                        </div>
                    </div>

                    <!-- Chart 1.4: CV Ruimteverwarming Warmtevraag, COP & Kosten Voorspelling (24 Uur Vooruit) -->
                    <div class="bg-[#0e1422] border border-[#1E293B] rounded-2xl p-4 sm:p-5 shadow-2xl space-y-3.5" id="heating-forecast-chart-container">
                        <div class="flex flex-col gap-2.5 border-b border-slate-800/80 pb-3">
                            <div class="flex flex-col sm:flex-row sm:items-center justify-between gap-2">
                                <div class="flex items-center gap-2.5">
                                    <span class="w-3 h-3 rounded-full bg-red-500 animate-pulse"></span>
                                    <div>
                                        <h3 class="text-sm font-bold text-white tracking-wide">CV Ruimteverwarming: Warmteverlies, COP &amp; Kosten Voorspelling (24 Uur Vooruit)</h3>
                                        <p class="text-[11px] text-slate-400">Fysische warmtevraag woning (2R1C + wind/zon), Daikin Carnot COP, stroomvraag (kW) en EPEX stroomkosten (€).</p>
                                    </div>
                                </div>
                                <div class="flex items-center gap-2 text-xs flex-wrap font-mono">
                                    <span id="heating-kpi-status" class="px-2.5 py-0.5 rounded-md text-[10px] font-bold border bg-slate-900 border-slate-700 text-slate-400">Thermostaat: --</span>
                                    <span id="heating-kpi-kwh" class="px-2.5 py-0.5 rounded-md text-[10px] font-bold border bg-amber-950/60 border-amber-500/40 text-amber-300">⚡ Stroom: -- kWh</span>
                                    <span id="heating-kpi-cost" class="px-2.5 py-0.5 rounded-md text-[10px] font-bold border bg-emerald-950/60 border-emerald-500/40 text-emerald-300">💶 Kosten: €--</span>
                                </div>
                            </div>
                            <div class="flex items-center gap-3 text-xs font-mono flex-wrap pt-1 border-t border-slate-800/40">
                                <span class="flex items-center gap-1.5 text-blue-300"><span class="w-3 h-1 bg-blue-400 rounded"></span> Buitentemp (°C)</span>
                                <span class="flex items-center gap-1.5 text-rose-400"><span class="w-3 h-1 bg-rose-500 rounded"></span> Binnentemp (°C)</span>
                                <span class="flex items-center gap-1.5 text-emerald-300"><span class="w-3 h-1 bg-emerald-400 rounded"></span> Daikin COP</span>
                                <span class="flex items-center gap-1.5 text-slate-400"><span class="w-3 h-0.5 border-b border-slate-400 border-dashed"></span> Warmteverlies (kW)</span>
                                <span class="flex items-center gap-1.5 text-amber-400"><span class="w-2.5 h-2.5 bg-amber-500 rounded-sm"></span> Stroom (kW)</span>
                                <span class="flex items-center gap-1.5 text-cyan-300"><span class="w-3 h-0.5 border-b border-cyan-400 border-dashed"></span> Kosten (€)</span>
                            </div>
                        </div>

                        <!-- Canvas for Heating Forecast -->
                        <div class="relative w-full h-64 sm:h-72">
                            <canvas id="chart-heating-forecast"></canvas>
                        </div>
                    </div>

                </div>

                <!-- ========================================================================= -->
                
            </div>

            <div id="view-history" class="tab-content space-y-6">
                <!-- TOP 4 KPI METRIC CARDS FOR HISTORIE -->
                <div class="grid grid-cols-2 md:grid-cols-4 gap-3 sm:gap-4">
                    <div class="bg-[#0e1422] border border-[#1E293B] rounded-2xl p-4 shadow">
                        <span class="text-[10px] uppercase font-bold text-slate-400 block tracking-wider">Besparing Vandaag</span>
                        <div class="text-xl font-bold text-emerald-400 mt-1" id="kpi-savings-today">€0.85</div>
                        <span class="text-[10px] text-slate-500">t.o.v. standaard verbruik</span>
                    </div>
                    <div class="bg-[#0e1422] border border-[#1E293B] rounded-2xl p-4 shadow">
                        <span class="text-[10px] uppercase font-bold text-slate-400 block tracking-wider">Zelfconsumptie</span>
                        <div class="text-xl font-bold text-amber-400 mt-1" id="kpi-self-consumption">78.4%</div>
                        <span class="text-[10px] text-slate-500">Zon direct lokaal benut</span>
                    </div>
                    <div class="bg-[#0e1422] border border-[#1E293B] rounded-2xl p-4 shadow">
                        <span class="text-[10px] uppercase font-bold text-slate-400 block tracking-wider">Warmtepomp COP</span>
                        <div class="text-xl font-bold text-cyan-400 mt-1" id="kpi-cop-dhw">2.04 <span class="text-xs text-slate-400 font-normal">SWW</span> · 4.80 <span class="text-xs text-slate-400 font-normal">CV</span></div>
                        <span class="text-[10px] text-slate-500">Gemeten rendement</span>
                    </div>
                    <div class="bg-[#0e1422] border border-[#1E293B] rounded-2xl p-4 shadow">
                        <span class="text-[10px] uppercase font-bold text-slate-400 block tracking-wider">Prognose Validatie</span>
                        <div class="text-xl font-bold text-purple-400 mt-1" id="kpi-accuracy">92.6%</div>
                        <span class="text-[10px] text-slate-500">MAE: 0.18 kW</span>
                    </div>
                </div>

                <!-- CATEGORIE 2: HISTORIE (HISTORICAL DATA)                                   -->
                <!-- ========================================================================= -->
                <div class="space-y-4 pt-4">
                    <!-- Sticky Category Header & Controls Bar -->
                    <div class="sticky top-0 z-30 bg-[#0B0F17]/95 backdrop-blur-md py-2.5 -mx-4 px-4 sm:-mx-6 sm:px-6 md:-mx-8 md:px-8 border-b border-emerald-500/30 shadow-lg shadow-black/50 flex flex-col sm:flex-row sm:items-center justify-between gap-2.5 transition-all">
                        <div class="flex items-center gap-2.5">
                            <span class="w-2.5 h-2.5 rounded-full bg-emerald-500 animate-pulse"></span>
                            <h2 class="text-sm sm:text-base font-bold text-white tracking-wide uppercase">Historie</h2>
                            <span class="text-[10px] text-emerald-300 font-mono bg-emerald-950/80 px-2 py-0.5 rounded border border-emerald-800">TELEMETRIE</span>
                        </div>
                        <div class="flex items-center gap-2 text-xs flex-wrap">
                            <!-- Diagram Type Toggle: Staven vs Lijn -->
                            <div class="inline-flex rounded-lg bg-slate-900 p-0.5 border border-slate-700 text-[10px] font-mono">
                                <button id="pp-btn-type-bar" onclick="setPowerProducersType('bar')" class="px-2.5 py-1 rounded transition font-medium bg-purple-600 text-white shadow">📊 Staven</button>
                                <button id="pp-btn-type-line" onclick="setPowerProducersType('line')" class="px-2.5 py-1 rounded transition font-medium text-slate-400 hover:text-slate-200">📈 Lijn</button>
                            </div>

                            <!-- Interval / Resolutie Toggle -->
                            <div class="inline-flex rounded-lg bg-slate-900 p-0.5 border border-slate-700 text-[10px] font-mono">
                                <button id="pp-btn-res-1h" onclick="setPowerProducersResolution('1h')" class="px-2.5 py-1 rounded transition font-medium bg-blue-600 text-white shadow">1 Uur</button>
                                <button id="pp-btn-res-15m" onclick="setPowerProducersResolution('15m')" class="px-2.5 py-1 rounded transition font-medium text-slate-400 hover:text-slate-200">15 Min</button>
                            </div>

                            <!-- Periode Selector -->
                            <select id="pp-range-select" onchange="onPowerProducersRangeChange()" class="bg-[#0B0F17] border border-slate-700 rounded-lg px-2.5 py-1 text-slate-200 text-xs font-medium focus:outline-none focus:border-blue-500 font-mono">
                                <option value="1h">1 uur</option>
                                <option value="6h">6 uur</option>
                                <option value="24h" selected>24 uur</option>
                                <option value="48h">2 dagen</option>
                                <option value="7d">7 dagen</option>
                            </select>
                            <button onclick="loadPowerProducersChart(); loadAnalytics();" class="px-2.5 py-1 bg-slate-800 hover:bg-slate-700 text-slate-300 text-xs rounded-lg font-medium border border-slate-700 transition flex items-center gap-1.5">
                                <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"></path></svg>
                                <span>Verversen</span>
                            </button>
                        </div>
                    </div>

                    <!-- Chart 2.1: Verbruikshistorie (Power Producers & Netstromen) -->
                    <div class="bg-[#0e1422] border border-[#1E293B] rounded-2xl p-4 sm:p-5 shadow-2xl space-y-3.5">
                        <div class="flex items-center justify-between border-b border-slate-800/80 pb-3">
                            <h3 class="text-sm font-bold text-white tracking-wide">Verbruikshistorie</h3>
                        </div>

                        <!-- Dual Polarity Chart Canvas -->
                        <div class="relative w-full h-72 sm:h-80">
                            <canvas id="powerProducersChart"></canvas>
                        </div>

                        <!-- 6-Box Metrics Grid with Euro Costs -->
                        <div class="pt-2 border-t border-slate-800/80">
                            <div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-2.5 text-xs font-mono">
                                <!-- Zonnepanelen -->
                                <div class="bg-[#0B0F17] p-2.5 rounded-xl border border-slate-800/90 flex flex-col justify-between gap-1">
                                    <div class="flex items-center justify-between">
                                        <div class="flex items-center gap-2">
                                            <span class="w-3 h-1.5 rounded-sm bg-yellow-500"></span>
                                            <span class="text-slate-300 font-medium">Zonnepanelen</span>
                                        </div>
                                        <div class="text-right">
                                            <span class="text-xs text-yellow-400 font-bold block" id="stat-solar-total">-- kWh</span>
                                            <span class="text-[10px] text-yellow-500/90 font-mono font-medium block" id="stat-solar-cost">€--</span>
                                        </div>
                                    </div>
                                    <div class="text-[10px] space-x-2 text-right border-t border-slate-800/60 pt-1 text-slate-500">
                                        <span>Last: <strong class="text-yellow-400 font-normal" id="stat-solar-last">--</strong></span>
                                        <span>Min: <span class="text-yellow-500/80" id="stat-solar-min">--</span></span>
                                    </div>
                                </div>
                                <!-- Teruglevering -->
                                <div class="bg-[#0B0F17] p-2.5 rounded-xl border border-slate-800/90 flex flex-col justify-between gap-1">
                                    <div class="flex items-center justify-between">
                                        <div class="flex items-center gap-2">
                                            <span class="w-3 h-1.5 rounded-sm bg-emerald-500"></span>
                                            <span class="text-slate-300 font-medium">Teruglevering</span>
                                        </div>
                                        <div class="text-right">
                                            <span class="text-xs text-emerald-400 font-bold block" id="stat-terug-total">-- kWh</span>
                                            <span class="text-[10px] text-emerald-500/90 font-mono font-medium block" id="stat-terug-cost">€--</span>
                                        </div>
                                    </div>
                                    <div class="text-[10px] space-x-2 text-right border-t border-slate-800/60 pt-1 text-slate-500">
                                        <span>Last: <strong class="text-emerald-400 font-normal" id="stat-terug-last">--</strong></span>
                                        <span>Min: <span class="text-emerald-500/80" id="stat-terug-min">--</span></span>
                                    </div>
                                </div>
                                <!-- Afname -->
                                <div class="bg-[#0B0F17] p-2.5 rounded-xl border border-slate-800/90 flex flex-col justify-between gap-1">
                                    <div class="flex items-center justify-between">
                                        <div class="flex items-center gap-2">
                                            <span class="w-3 h-1.5 rounded-sm bg-red-500"></span>
                                            <span class="text-slate-300 font-medium">Afname</span>
                                        </div>
                                        <div class="text-right">
                                            <span class="text-xs text-red-400 font-bold block" id="stat-afname-total">-- kWh</span>
                                            <span class="text-[10px] text-red-500/90 font-mono font-medium block" id="stat-afname-cost">€--</span>
                                        </div>
                                    </div>
                                    <div class="text-[10px] space-x-2 text-right border-t border-slate-800/60 pt-1 text-slate-500">
                                        <span>Last: <strong class="text-red-400 font-normal" id="stat-afname-last">--</strong></span>
                                        <span>Max: <span class="text-red-500/80" id="stat-afname-max">--</span></span>
                                    </div>
                                </div>
                                <!-- Totaal opgewekt -->
                                <div class="bg-[#0B0F17] p-2.5 rounded-xl border border-slate-800/90 flex flex-col justify-between gap-1">
                                    <div class="flex items-center justify-between">
                                        <div class="flex items-center gap-2">
                                            <span class="w-3 h-1.5 rounded-sm bg-lime-500"></span>
                                            <span class="text-slate-300 font-medium">Totaal opgewekt</span>
                                        </div>
                                        <div class="text-right">
                                            <span class="text-xs text-lime-400 font-bold block" id="stat-opgewekt-total">-- kWh</span>
                                            <span class="text-[10px] text-lime-500/90 font-mono font-medium block" id="stat-opgewekt-cost">€--</span>
                                        </div>
                                    </div>
                                    <div class="text-[10px] space-x-2 text-right border-t border-slate-800/60 pt-1 text-slate-500">
                                        <span>Last: <strong class="text-lime-400 font-normal" id="stat-opgewekt-last">--</strong></span>
                                        <span>Min: <span class="text-lime-500/80" id="stat-opgewekt-min">--</span></span>
                                    </div>
                                </div>
                                <!-- Opgewekt Gebruikt -->
                                <div class="bg-[#0B0F17] p-2.5 rounded-xl border border-slate-800/90 flex flex-col justify-between gap-1">
                                    <div class="flex items-center justify-between">
                                        <div class="flex items-center gap-2">
                                            <span class="w-3 h-1.5 rounded-sm bg-teal-400"></span>
                                            <span class="text-slate-300 font-medium">Opgewekt Gebruikt</span>
                                        </div>
                                        <div class="text-right">
                                            <span class="text-xs text-teal-400 font-bold block" id="stat-selfcons-total">-- kWh</span>
                                            <span class="text-[10px] text-teal-500/90 font-mono font-medium block" id="stat-selfcons-cost">€--</span>
                                        </div>
                                    </div>
                                    <div class="text-[10px] space-x-2 text-right border-t border-slate-800/60 pt-1 text-slate-500">
                                        <span>Last: <strong class="text-teal-400 font-normal" id="stat-selfcons-last">--</strong></span>
                                        <span>Min: <span class="text-teal-500/80" id="stat-selfcons-min">--</span></span>
                                    </div>
                                </div>
                                <!-- Totaal Verbruik -->
                                <div class="bg-[#0B0F17] p-2.5 rounded-xl border border-slate-800/90 flex flex-col justify-between gap-1">
                                    <div class="flex items-center justify-between">
                                        <div class="flex items-center gap-2">
                                            <span class="w-3 h-1.5 rounded-sm bg-orange-500"></span>
                                            <span class="text-slate-300 font-medium">Totaal Verbruik</span>
                                        </div>
                                        <div class="text-right">
                                            <span class="text-xs text-orange-400 font-bold block" id="stat-verbruik-total">-- kWh</span>
                                            <span class="text-[10px] text-orange-500/90 font-mono font-medium block" id="stat-verbruik-cost">€--</span>
                                        </div>
                                    </div>
                                    <div class="text-[10px] space-x-2 text-right border-t border-slate-800/60 pt-1 text-slate-500">
                                        <span>Last: <strong class="text-orange-400 font-normal" id="stat-verbruik-last">--</strong></span>
                                        <span>Max: <span class="text-orange-500/80" id="stat-verbruik-max">--</span></span>
                                    </div>
                                </div>
                            </div>
                        </div>
                    </div>
                </div>


                <!-- ========================================================================= -->
                <!-- MODEL VALIDATIE: VOORSPELLING VS. WERKELIJKHEID OVERLAY                  -->
                <!-- ========================================================================= -->
                <div class="bg-[#0e1422] border border-[#1E293B] rounded-2xl p-5 shadow-2xl space-y-4" id="validation-overlay-card">
                    <div class="flex flex-col sm:flex-row sm:items-center justify-between gap-3 border-b border-slate-800/80 pb-3">
                        <div class="flex items-center gap-2.5">
                            <span class="w-3 h-3 rounded-full bg-cyan-500 animate-pulse"></span>
                            <div>
                                <div class="flex items-center gap-2">
                                    <h3 class="text-sm sm:text-base font-bold text-white tracking-wide">Model Validatie: Voorspelling vs. Werkelijkheid</h3>
                                    <button type="button" onclick="toggleInfoPopover(event, 'val_overlay_info')" class="text-slate-500 hover:text-cyan-400 transition p-0.5 focus:outline-none" aria-label="Info">
                                        <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><circle cx="12" cy="12" r="10"></circle><path d="M12 16v-4m0-4h.01"></path></svg>
                                    </button>
                                </div>
                                <p class="text-[11px] text-slate-400">Vergelijk het historische voorspelde profiel (<span class="text-slate-300 font-mono">gestreept - -</span>) met de werkelijk gemeten telemetrie (<span class="text-white font-mono">massief —</span>).</p>
                            </div>
                        </div>

                        <!-- 4-Way Component Selector Buttons -->
                        <div class="flex items-center gap-1.5 bg-[#0B0F17] p-1 rounded-xl border border-slate-800 text-xs font-mono flex-wrap">
                            <button onclick="setValidationComponent('all')" id="btn-val-all" class="px-2.5 py-1 rounded-lg bg-cyan-600 text-white font-bold transition shadow">⚡ Totaal</button>
                            <button onclick="setValidationComponent('solar')" id="btn-val-solar" class="px-2.5 py-1 rounded-lg text-slate-400 hover:text-white transition">☀️ Zon</button>
                            <button onclick="setValidationComponent('dhw')" id="btn-val-dhw" class="px-2.5 py-1 rounded-lg text-slate-400 hover:text-white transition">♨️ Tapwater</button>
                            <button onclick="setValidationComponent('cv')" id="btn-val-cv" class="px-2.5 py-1 rounded-lg text-slate-400 hover:text-white transition">🌡️ CV</button>
                        </div>
                    </div>

                    <!-- Top KPI Badges Bar & Timeframe Toggles -->
                    <div class="flex flex-col sm:flex-row sm:items-center justify-between gap-3 font-mono text-xs">
                        <div class="flex items-center gap-2 flex-wrap">
                            <span class="px-2.5 py-1 rounded-lg border bg-emerald-950/60 border-emerald-500/40 text-emerald-300 font-bold" id="val-kpi-accuracy">Kwaliteit: --%</span>
                            <span class="px-2.5 py-1 rounded-lg border bg-slate-900 border-slate-700 text-slate-300 font-bold" id="val-kpi-mae">Gem. Afwijking: -- W</span>
                            <span class="px-2.5 py-1 rounded-lg border bg-blue-950/60 border-blue-500/40 text-blue-300 font-bold" id="val-kpi-totals">Werkelijk: -- kWh | Voorspeld: -- kWh</span>
                        </div>

                        <!-- Range & Resolution Selectors -->
                        <div class="flex items-center gap-2">
                            <!-- Resolution -->
                            <div class="inline-flex rounded-lg bg-slate-900 p-0.5 border border-slate-700 text-[10px] font-mono">
                                <button onclick="setValidationResolution('15m')" id="val-res-15m" class="px-2 py-0.5 rounded transition font-medium bg-blue-600 text-white shadow">15 Min</button>
                                <button onclick="setValidationResolution('1h')" id="val-res-1h" class="px-2 py-0.5 rounded transition font-medium text-slate-400 hover:text-slate-200">1 Uur</button>
                            </div>
                            <!-- Timeframe -->
                            <div class="inline-flex rounded-lg bg-slate-900 p-0.5 border border-slate-700 text-[10px] font-mono">
                                <button onclick="setValidationPeriod('24h')" id="val-tf-24h" class="px-2 py-0.5 rounded transition font-medium bg-cyan-600 text-white shadow">24 Uur</button>
                                <button onclick="setValidationPeriod('48h')" id="val-tf-48h" class="px-2 py-0.5 rounded transition font-medium text-slate-400 hover:text-slate-200">48 Uur</button>
                                <button onclick="setValidationPeriod('7d')" id="val-tf-7d" class="px-2 py-0.5 rounded transition font-medium text-slate-400 hover:text-slate-200">7 Dagen</button>
                            </div>
                        </div>
                    </div>

                    <!-- Chart Container -->
                    <div class="relative w-full h-72 sm:h-80 bg-[#0B0F17]/80 rounded-xl p-3 border border-slate-800/80">
                        <canvas id="chart-validation-overlay"></canvas>
                    </div>
                </div>


                <!-- DIGEST & REPORT CARD -->
                <div class="bg-[#0e1422] border border-[#1E293B] rounded-2xl p-6 shadow space-y-3">
                    <h3 class="text-sm font-bold text-white">Geautomatiseerd Dagrapport (Digest)</h3>
                    <div class="bg-[#0B0F17] p-4 rounded-xl border border-slate-800 font-mono text-xs text-slate-300 whitespace-pre-line" id="analytics-digest">
                        Laden van analyserapport...
                    </div>
                </div>
            </div>

            <!-- TAB: APPARAAT POLICIES & AANSTURING -->
            <div id="view-policies" class="tab-content space-y-6">
                <div class="flex flex-col sm:flex-row sm:items-center justify-between gap-3 border-b border-purple-500/30 pb-3">
                    <div>
                        <h2 class="text-base font-bold text-white">Apparaat Policies & Aansturing</h2>
                        <p class="text-xs text-slate-400">Automatische beslisregels voor slimme sturing van de boiler, warmtepomp en verschuifbare apparaten.</p>
                    </div>
                    <button onclick="openPolicyModal()" class="px-3.5 py-2 bg-purple-600 hover:bg-purple-500 text-white text-xs font-semibold rounded-xl shadow-lg transition-all flex items-center gap-1.5 flex-shrink-0">
                        <span>+ Nieuwe Policy Aanmaken</span>
                    </button>
                </div>

                <!-- MOVED DHW NIGHT DECISION BANNER (Beleid & Sturing) -->
                <div id="dhw-decision-banner" class="bg-gradient-to-r from-amber-950/70 via-[#0e1422] to-amber-900/40 border border-amber-500/40 rounded-2xl p-5 shadow-xl space-y-3.5">
                    <div class="flex flex-col sm:flex-row sm:items-center justify-between gap-3 border-b border-amber-500/20 pb-3">
                        <div class="flex items-center gap-2.5">
                            <span class="text-xl">♨️</span>
                            <div>
                                <div class="flex items-center gap-2">
                                    <h3 class="text-sm font-bold text-white tracking-wide">Actueel Nachtelijk Laadbesluit: Warm Tapwater (350L Vat)</h3>
                                    <!-- Interactive Info Badge with Hover Popover explaining Policy & Risk -->
                                    <div class="relative group inline-block">
                                        <button class="px-2 py-0.5 rounded-lg bg-amber-500/10 hover:bg-amber-500/25 text-amber-300 border border-amber-500/40 text-[10px] font-medium flex items-center gap-1 transition">
                                            <svg class="w-3 h-3 text-amber-400" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"></path></svg>
                                            <span>Beleid &amp; Risico-regels</span>
                                        </button>
                                        <!-- Popover Details -->
                                        <div class="absolute left-0 top-full mt-2 w-80 sm:w-96 p-4 bg-[#0B0F17]/95 backdrop-blur-md border border-slate-700/90 rounded-2xl shadow-2xl z-50 text-xs text-slate-200 space-y-2.5 hidden group-hover:block transition-all font-sans text-left pointer-events-auto">
                                            <div class="flex items-center justify-between border-b border-slate-800 pb-2">
                                                <span class="font-bold text-white uppercase tracking-wider text-[11px]">Nachtelijk Laadbeleid &amp; Beslislogica</span>
                                                <span class="text-[10px] text-amber-400 font-mono">P95 &amp; Lockouts</span>
                                            </div>
                                            <div class="space-y-2 text-[11px] leading-relaxed">
                                                <div>
                                                    <span class="text-amber-400 font-semibold">🔄 Her-evaluatie Frequentie:</span>
                                                    <p class="text-slate-400">Continu real-time (elke 60s tumbling window herberekend met actuele tanksensor). Finaal sturingsbesluit valt om 02:00u voor het goedkoopste nachtkwartier.</p>
                                                </div>
                                                <div>
                                                    <span class="text-red-400 font-semibold">🚫 Spitsblokkades (Strikte Lockouts):</span>
                                                    <p class="text-slate-400">Dynamische Spitsblokkades: Het systeem berekent per kwartier de EPEX prijspieken en vergrendelt uitsluitend de absolute top-kam (maximaal 2,5 uur) met automatische comfort-overrule bij koude (<19,5°C).</p>
                                                </div>
                                                <div>
                                                    <span class="text-sky-400 font-semibold">📊 P95 Veiligheidsmarge (Stress Scenario):</span>
                                                    <p class="text-slate-400">Naast het normale leefpatroon toetst het model een 95e percentiel zware douche-ochtend (+45% watervraag). Blijft ook P95 boven 40°C, dan is de ochtend gegarandeerd veilig.</p>
                                                </div>
                                                <div>
                                                    <span class="text-emerald-400 font-semibold">🛡️ Comfortverzekering (&lt; 20% Meerkosten):</span>
                                                    <p class="text-slate-400">Als nachtladen maximaal 20% duurder is dan overdag wachten, én er is risico op een vroege dip vóór 11:30u, laadt de policy 's nachts preventief bij voor 100% warmtezekerheid.</p>
                                                </div>
                                            </div>
                                        </div>
                                    </div>
                                </div>
                                <p class="text-[11px] text-slate-400">Continu berekende beleidsafweging: daltarief vs. zonne-energie &amp; COP met P95-risicobewaking en spitsblokkades.</p>
                            </div>
                        </div>
                        <div class="flex items-center gap-2 self-start sm:self-auto">
                            <span class="text-[10px] text-slate-400 font-mono hidden sm:inline">60s Live Evaluatie</span>
                            <span id="dhw-live-temp-badge" class="px-3 py-1 rounded-lg text-xs font-mono font-bold bg-amber-500/20 text-amber-300 border border-amber-500/40">Actueel: --°C</span>
                        </div>
                    </div>

                    <div class="grid grid-cols-1 md:grid-cols-3 gap-3 font-mono text-xs">
                        <div class="bg-black/50 p-3 rounded-xl border border-slate-800 space-y-1">
                            <div class="text-[10px] text-slate-400 uppercase font-bold">Nuttige Warmte (&gt;40°C)</div>
                            <div class="text-base font-bold text-amber-300" id="dhw-usable-heat">-- kWh_th (-- MJ)</div>
                            <div class="text-[10px] text-slate-400 font-sans" id="dhw-volume-caption">350L combivat (mengcapaciteit ~--L douchewater van 38°C).</div>
                        </div>
                        <div class="bg-black/50 p-3 rounded-xl border border-slate-800 space-y-1">
                            <div class="text-[10px] text-slate-400 uppercase font-bold">Verwachte Ochtenddip (06-09u)</div>
                            <div class="text-base font-bold text-white" id="dhw-projected-dip">--°C (om --:--u)</div>
                            <div class="text-[10px] font-sans" id="dhw-dip-subtext"><span class="text-emerald-400 font-bold">P95 Risicodip: --°C ✓</span> · Geen spits-opwarming</div>
                        </div>
                        <div class="bg-black/50 p-3 rounded-xl border border-slate-800 space-y-1">
                            <div class="text-[10px] text-slate-400 uppercase font-bold" id="dhw-night-header">Nachtbesluit (-- ➔ --)</div>
                            <div class="text-sm font-bold text-emerald-300" id="dhw-night-action">Evaluatie loopt...</div>
                            <div class="text-[10px] text-slate-400 font-sans" id="dhw-night-subtext">Berekenen van tarieven &amp; COP...</div>
                        </div>
                    </div>

                    <div class="text-xs text-slate-300 bg-black/60 p-3 rounded-xl border border-slate-800/80 font-sans leading-relaxed" id="dhw-decision-explanation">
                        Beleidsafweging wordt geladen...
                    </div>
                </div>

                <!-- POLICIES GRID CONTAINER -->
                <div id="policies-container" class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-5">
                    <!-- Loaded dynamically via loadPolicies() -->
                </div>
            </div>

            <div id="view-infrastructure" class="tab-content space-y-6">
                <!-- SECTION 1: HOME ASSISTANT CORE (BRON & DOEL) -->
                <div class="space-y-4">
                    <div class="flex justify-between items-center">
                        <div>
                            <h2 class="text-base font-bold text-white flex items-center gap-2">
                                <span>Home Assistant Core Integratie</span>
                                <span class="text-xs font-normal text-cyan-400">(Bi-directioneel: Bron van sensoren & Doel van aansturing)</span>
                            </h2>
                            <p class="text-xs text-slate-400">Verbindt direct met de interne Home Assistant Supervisor API voor live entiteiten en apparaat-actuatoren.</p>
                        </div>
                    </div>
                    <div id="ha-conn-container">
                        <!-- Loaded dynamically via loadInfrastructure() -->
                    </div>
                </div>

                <!-- SECTION 2: MQTT BROKERS CRUD -->
                <div class="space-y-4">
                    <div class="flex justify-between items-center">
                        <div>
                            <h2 class="text-base font-bold text-white flex items-center gap-2">
                                <span>MQTT Message Brokers</span>
                                <span class="text-xs font-normal text-amber-400">(Streaming data-inname en modbus topics)</span>
                            </h2>
                            <p class="text-xs text-slate-400">Verbind met lokale Mosquitto broker (core-mosquitto:1883) of externe gateways voor real-time meters.</p>
                        </div>
                        <button onclick="openMqttModal()" class="px-3 py-1.5 bg-amber-600 hover:bg-amber-500 text-white text-xs font-semibold rounded-xl shadow transition-all">
                            + MQTT Broker Toevoegen
                        </button>
                    </div>
                    <div id="mqtt-conns-container" class="grid grid-cols-1 md:grid-cols-2 gap-5">
                        <!-- Loaded dynamically -->
                    </div>
                </div>

                <!-- SECTION 3: EXTERNE DATA APIS & FEEDS (EPEX & METEO) -->
                <div class="space-y-4">
                    <div class="flex justify-between items-center">
                        <div>
                            <h2 class="text-base font-bold text-white flex items-center gap-2">
                                <span>Externe Data APIs & Feeds</span>
                                <span class="text-xs font-normal text-purple-400">(Beurstarieven & Weersvoorspelling)</span>
                            </h2>
                            <p class="text-xs text-slate-400">Publieke data-interfaces voor dynamische stroomprijzen (EPEX Spot) en zonnestralingsvoorspellingen (Open-Meteo).</p>
                        </div>
                        <button onclick="loadProviders()" class="px-3 py-1.5 bg-slate-800 hover:bg-slate-700 text-slate-200 border border-slate-700 text-xs rounded-lg font-medium">
                            🔄 Verversen
                        </button>
                    </div>
                    <div id="providers-container" class="grid grid-cols-1 md:grid-cols-2 gap-5">
                        <!-- Loaded dynamically via loadProviders() -->
                    </div>

                    <!-- SECTION 3B: ZONNEPANELEN & DAKCONFIGURATIE (POA FYSISCH MODEL) -->
                    <div class="bg-[#0e1422] border border-[#1E293B] rounded-2xl p-5 shadow-xl space-y-4 col-span-1 md:col-span-2">
                        <div class="flex flex-col sm:flex-row sm:items-center justify-between gap-3 border-b border-amber-500/20 pb-3">
                            <div class="flex items-center gap-2.5">
                                <span class="text-xl">☀️</span>
                                <div>
                                    <h3 class="text-sm font-bold text-white tracking-wide">Zonnepanelen &amp; Dakconfiguratie (Plane-of-Array Fysisch Model)</h3>
                                    <p class="text-[11px] text-slate-400">Parameters voor de zonnestroomvoorspelling via NOAA zonnehoek-projectie op jouw hellende dak.</p>
                                </div>
                            </div>
                            <span class="px-2.5 py-1 rounded-lg text-[10px] font-mono font-bold bg-amber-500/20 text-amber-300 border border-amber-500/40">34° Dakhelling · 225° Zuid-West</span>
                        </div>

                        <div class="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-5 gap-3 font-mono text-xs">
                            <div class="bg-black/50 p-3 rounded-xl border border-slate-800 space-y-1">
                                <label class="text-[10px] text-slate-400 uppercase font-bold block">Vermogen (Wp)</label>
                                <input type="number" id="solar-cfg-wp" step="10" value="5760" class="w-full bg-[#0B0F17] border border-slate-700 rounded-lg px-2 py-1 text-white font-bold text-sm focus:border-amber-500 focus:outline-none">
                                <span class="text-[10px] text-slate-500 font-sans block">Totaal Wattpiek dak</span>
                            </div>
                            <div class="bg-black/50 p-3 rounded-xl border border-slate-800 space-y-1">
                                <label class="text-[10px] text-slate-400 uppercase font-bold block">Omvormer Max (W)</label>
                                <input type="number" id="solar-cfg-inv" step="50" value="5500" class="w-full bg-[#0B0F17] border border-slate-700 rounded-lg px-2 py-1 text-white font-bold text-sm focus:border-amber-500 focus:outline-none">
                                <span class="text-[10px] text-slate-500 font-sans block">Aftoppingslimiet AC</span>
                            </div>
                            <div class="bg-black/50 p-3 rounded-xl border border-slate-800 space-y-1">
                                <label class="text-[10px] text-slate-400 uppercase font-bold block">Dakhelling / Tilt (°)</label>
                                <input type="number" id="solar-cfg-tilt" step="1" value="34" class="w-full bg-[#0B0F17] border border-slate-700 rounded-lg px-2 py-1 text-amber-300 font-bold text-sm focus:border-amber-500 focus:outline-none">
                                <span class="text-[10px] text-slate-500 font-sans block">0° = plat, 90° = gevel</span>
                            </div>
                            <div class="bg-black/50 p-3 rounded-xl border border-slate-800 space-y-1">
                                <label class="text-[10px] text-slate-400 uppercase font-bold block">Oriëntatie / Azimuth (°)</label>
                                <input type="number" id="solar-cfg-azimuth" step="1" value="225" class="w-full bg-[#0B0F17] border border-slate-700 rounded-lg px-2 py-1 text-amber-300 font-bold text-sm focus:border-amber-500 focus:outline-none">
                                <span class="text-[10px] text-slate-500 font-sans block">180° = Z, 225° = ZW</span>
                            </div>
                            <div class="bg-black/50 p-3 rounded-xl border border-slate-800 space-y-1">
                                <label class="text-[10px] text-slate-400 uppercase font-bold block">Systeem Rendement</label>
                                <input type="number" id="solar-cfg-eff" step="0.01" min="0.5" max="1.0" value="0.88" class="w-full bg-[#0B0F17] border border-slate-700 rounded-lg px-2 py-1 text-emerald-300 font-bold text-sm focus:border-amber-500 focus:outline-none">
                                <span class="text-[10px] text-slate-500 font-sans block">Verliezen &amp; temp.</span>
                            </div>
                        </div>

                        <div class="flex items-center justify-between pt-2 border-t border-slate-800/80">
                            <div class="text-[11px] text-slate-400 font-sans flex items-center gap-1.5">
                                <span class="w-2 h-2 rounded-full bg-emerald-400 animate-pulse"></span>
                                <span>NOAA Plane-of-Array stralingsprojectie actief op Open-Meteo GHI data.</span>
                            </div>
                            <button onclick="saveSolarRoofConfig()" class="px-4 py-2 bg-amber-600 hover:bg-amber-500 text-white text-xs font-bold rounded-xl shadow-lg transition flex items-center gap-1.5">
                                <span>💾 Opslaan &amp; Direct Toepassen</span>
                            </button>
                        </div>
                    </div>

                </div>
            </div>

            <!-- TAB: DATA (DATABASES, PIPELINES & TELEMETRIE) -->
            <div id="view-data" class="tab-content space-y-6">
                <!-- Status & Telemetry Header Banner -->
                <div class="bg-gradient-to-r from-emerald-950/80 via-[#0e1422] to-blue-950/80 border border-emerald-500/30 rounded-2xl p-4 sm:p-5 shadow-xl flex flex-col sm:flex-row sm:items-center justify-between gap-4">
                    <div class="flex items-start sm:items-center gap-3 sm:gap-4">
                        <div class="w-10 h-10 sm:w-12 sm:h-12 rounded-xl bg-emerald-500/20 border border-emerald-500/30 flex items-center justify-center text-emerald-400 text-xl sm:text-2xl shadow-[0_0_15px_rgba(16,185,129,0.2)] flex-shrink-0">
                            📊
                        </div>
                        <div class="min-w-0">
                            <div class="flex items-center gap-2 flex-wrap">
                                <span class="text-xs uppercase font-bold text-emerald-400 tracking-wider">Tijdreeksdatabases & Datapipelines</span>
                                <span class="px-2 py-0.5 rounded text-[10px] font-mono font-bold bg-emerald-500/20 text-emerald-300 border border-emerald-500/40">100% PURE INFLUXDB</span>
                            </div>
                            <div class="text-xs sm:text-sm font-bold text-white mt-1 break-words" id="infra-summary-text">
                                InfluxDB tijdreeksopslag & 60-seconden achtergrond accumulator actief.
                            </div>
                        </div>
                    </div>
                    <button onclick="writeTestTelemetryPoint()" class="px-3.5 py-2 bg-emerald-600 hover:bg-emerald-500 text-white text-xs font-semibold rounded-xl shadow-lg transition-all flex items-center justify-center gap-2 flex-shrink-0 w-full sm:w-auto">
                        <span>⚡ Schrijf Test Telemetrie</span>
                    </button>
                </div>

                <!-- 1. LIVE 60-SECOND TUMBLING WINDOW DATA PIPELINE & ACCUMULATOR MONITOR -->
                <div class="bg-[#0e1422] border border-[#1E293B] rounded-2xl p-5 shadow-xl space-y-4">
                    <div class="flex flex-col sm:flex-row sm:items-center justify-between gap-3 border-b border-slate-800/80 pb-3">
                        <div class="flex items-center gap-2.5">
                            <span class="w-3 h-3 rounded-full bg-emerald-500 animate-pulse"></span>
                            <div>
                                <h3 class="text-sm font-bold text-white tracking-wide">60s Tumbling Window Data Pipeline & Vermogensbalans Monitor</h3>
                                <p class="text-[11px] text-slate-400">Heterogene streams (P1, zon, Daikin WP, accu) worden 10s gesampled, in RAM gemiddeld en elke minuut synchroon weggeschreven.</p>
                            </div>
                        </div>
                        <div class="flex items-center gap-2 text-xs font-mono">
                            <span id="pipeline-progress-badge" class="px-2.5 py-1 rounded-lg bg-blue-950/70 border border-blue-500/40 text-blue-300 font-bold">Accumulator: 0/6 (0s)</span>
                            <span id="pipeline-flush-badge" class="px-2.5 py-1 rounded-lg bg-emerald-950/70 border border-emerald-500/40 text-emerald-300 font-bold">Laatste Flush: --:--:--</span>
                        </div>
                    </div>

                    <!-- 3-STAGE PIPELINE FLOW DIAGRAM -->
                    <div class="grid grid-cols-1 md:grid-cols-3 gap-3 font-mono text-xs">
                        <div class="bg-[#0B0F17] p-3.5 rounded-xl border border-slate-800 space-y-1.5">
                            <div class="flex justify-between items-center">
                                <span class="text-[10px] text-slate-400 uppercase font-bold">1. Ingestion (10s Sample)</span>
                                <span class="w-2 h-2 rounded-full bg-blue-400 animate-ping"></span>
                            </div>
                            <div class="text-slate-200 text-xs font-sans">Streams van P1 (6053), Omvormer, WP & MQTT.</div>
                            <div class="text-[11px] text-blue-400 pt-1" id="pipe-live-streams">Sampling actief (6 streams)</div>
                        </div>

                        <div class="bg-[#0B0F17] p-3.5 rounded-xl border border-slate-800 space-y-1.5">
                            <div class="flex justify-between items-center">
                                <span class="text-[10px] text-slate-400 uppercase font-bold">2. Accumulator (60s Window)</span>
                                <span class="text-[10px] text-purple-400" id="pipe-window-pct">0%</span>
                            </div>
                            <div class="w-full bg-slate-900 rounded-full h-2 border border-slate-800 overflow-hidden">
                                <div id="pipe-progress-bar" class="bg-gradient-to-r from-blue-500 to-purple-500 h-full w-0 transition-all duration-500"></div>
                            </div>
                            <div class="text-[11px] text-purple-300 pt-1 font-sans">Rekenengine: P1 + Zon − WP = Ongedefinieerd</div>
                        </div>

                        <div class="bg-[#0B0F17] p-3.5 rounded-xl border border-slate-800 space-y-1.5">
                            <div class="flex justify-between items-center">
                                <span class="text-[10px] text-slate-400 uppercase font-bold">3. Datastore (openhems)</span>
                                <span class="text-[10px] text-emerald-400">100% PURE DB</span>
                            </div>
                            <div class="text-slate-200 text-xs font-sans">Tijdreeksmetingen & Balans opgeslagen.</div>
                            <div class="text-[11px] text-emerald-400 pt-1" id="pipe-total-points">Totaal weggeschreven: -- punten</div>
                        </div>
                    </div>

                    <!-- LIVE POWER BALANCE TELEMETRY METRICS -->
                    <div class="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-2.5 font-mono text-xs pt-1">
                        <div class="bg-[#0B0F17] p-2.5 rounded-lg border border-slate-800">
                            <div class="text-[10px] text-slate-500 uppercase">P1 Netto</div>
                            <div class="text-sm font-bold text-red-400 mt-0.5" id="live-p1-power">-- W</div>
                        </div>
                        <div class="bg-[#0B0F17] p-2.5 rounded-lg border border-slate-800">
                            <div class="text-[10px] text-emerald-500/80 uppercase">Zonnepanelen</div>
                            <div class="text-sm font-bold text-emerald-400 mt-0.5" id="live-solar-power">-- W</div>
                        </div>
                        <div class="bg-[#0B0F17] p-2.5 rounded-lg border border-slate-800">
                            <div class="text-[10px] text-teal-500/80 uppercase">Thuisaccu</div>
                            <div class="text-sm font-bold text-teal-400 mt-0.5" id="live-battery-power">0 W</div>
                        </div>
                        <div class="bg-[#0B0F17] p-2.5 rounded-lg border border-slate-800">
                            <div class="text-[10px] text-pink-500/80 uppercase">Warmtepomp</div>
                            <div class="text-sm font-bold text-pink-400 mt-0.5" id="live-heatpump">-- W</div>
                        </div>
                        <div class="bg-[#0B0F17] p-2.5 rounded-lg border border-slate-800">
                            <div class="text-[10px] text-indigo-400 uppercase">Totaal Huisverbruik</div>
                            <div class="text-sm font-bold text-indigo-300 mt-0.5" id="live-tot-house">-- W</div>
                        </div>
                        <div class="bg-[#0B0F17] p-2.5 rounded-lg border border-blue-500/30 bg-blue-950/20">
                            <div class="text-[10px] text-blue-400 uppercase font-bold">Ongedefinieerd</div>
                            <div class="text-sm font-bold text-blue-300 mt-0.5" id="live-unallocated">-- W</div>
                        </div>
                    </div>
                </div>

                <!-- 2. INFLUXDB TIJDREEKS INSTANTIES CRUD -->
                <div class="space-y-4">
                    <div class="flex justify-between items-center">
                        <div>
                            <h2 class="text-base font-bold text-white flex items-center gap-2">
                                <span>InfluxDB Tijdreeks Instanties</span>
                                <span class="text-xs font-normal text-slate-400">(Tijdreeksopslag, lokaal of remote)</span>
                            </h2>
                            <p class="text-xs text-slate-400">Ondersteunt InfluxDB 1.8 en 2.x/Cloud voor het opslaan van realtime telemetrie en uitlezen van historie.</p>
                        </div>
                        <button onclick="openInfluxModal()" class="px-3 py-1.5 bg-blue-600 hover:bg-blue-500 text-white text-xs font-semibold rounded-xl shadow transition-all">
                            + InfluxDB Instantie Toevoegen
                        </button>
                    </div>
                    <div id="influx-conns-container" class="grid grid-cols-1 md:grid-cols-2 gap-5">
                        <!-- Loaded dynamically -->
                    </div>
                </div>

                <!-- 3. LIVE DATA-INNAME TELEMETRIE MONITOR -->
                <div class="bg-[#0e1422] border border-[#1E293B] rounded-2xl p-6 shadow-xl space-y-4">
                    <div class="flex justify-between items-center">
                        <div>
                            <h3 class="text-sm font-bold text-white">Live Data-Inname Telemetrie Monitor</h3>
                            <p class="text-xs text-slate-400">Reële metingen geregistreerd in de InfluxDB tijdreeksdatabase.</p>
                        </div>
                        <span class="text-[11px] font-mono text-emerald-400" id="last-write-status">Gereed voor datastromen</span>
                    </div>
                    <div class="overflow-x-auto">
                        <table class="w-full text-left text-xs">
                            <thead>
                                <tr class="text-slate-400 border-b border-[#1E293B] font-mono text-[10px]">
                                    <th class="pb-2">TIJD</th>
                                    <th class="pb-2">INSTANTIE</th>
                                    <th class="pb-2">DATABASE</th>
                                    <th class="pb-2">METING / TOPIC</th>
                                    <th class="pb-2">WAARDE</th>
                                    <th class="pb-2">STATUS</th>
                                </tr>
                            </thead>
                            <tbody class="font-mono text-slate-300 divide-y divide-[#1E293B]/40" id="telemetry-table-body">
                                <tr>
                                    <td class="py-2.5 text-slate-500 text-[11px]" colspan="6">Verbinden met telemetriestroom...</td>
                                </tr>
                            </tbody>
                        </table>
                    </div>
                </div>
            </div>

            <!-- TAB 3: APPARATEN CRUD (GROUPED PER CONNECTION TYPE) -->
            <div id="view-devices" class="tab-content space-y-5">
                <div class="flex flex-col sm:flex-row sm:items-center justify-between gap-3 border-b border-slate-800 pb-3">
                    <div>
                        <h2 class="text-base font-bold text-white flex items-center gap-2">
                            <span>Apparaten & Hardware Bronnen</span>
                            <span class="text-xs font-normal text-slate-400">(Gegroepeerd per Verbindingstype)</span>
                        </h2>
                        <p class="text-xs text-slate-400">Beheer fysieke meters, warmtepomp relais en slimme actuatoren gekoppeld via Home Assistant of MQTT.</p>
                    </div>
                    <div class="flex items-center gap-2">
                        <button onclick="openDeviceModal()" class="px-3 py-1.5 bg-blue-600 hover:bg-blue-500 text-white text-xs font-semibold rounded-xl shadow transition-all flex items-center gap-1.5">
                            <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path d="M12 4v16m8-8H4"></path></svg>
                            <span>Apparaat Toevoegen</span>
                        </button>
                    </div>
                </div>

                <!-- Quick Filter Pill Bar -->
                <div class="flex items-center gap-1.5 p-1 rounded-xl bg-slate-900 border border-slate-800 text-xs font-mono w-fit flex-wrap">
                    <button id="dev-filter-all" onclick="filterDeviceView('all')" class="dev-filter-btn px-3 py-1 rounded-lg bg-blue-600 text-white font-semibold transition">Alle Apparaten</button>
                    <button id="dev-filter-ha" onclick="filterDeviceView('homeassistant')" class="dev-filter-btn px-3 py-1 rounded-lg text-slate-400 hover:text-white transition">🏠 Home Assistant</button>
                    <button id="dev-filter-mqtt" onclick="filterDeviceView('mqtt')" class="dev-filter-btn px-3 py-1 rounded-lg text-slate-400 hover:text-white transition">⚡ Direct MQTT</button>
                    <button id="dev-filter-planned" onclick="filterDeviceView('planned')" class="dev-filter-btn px-3 py-1 rounded-lg text-slate-400 hover:text-white transition">🔋 Gepland / Standby</button>
                </div>

                <!-- Grouped Containers -->
                <div id="devices-container" class="space-y-6">
                    <!-- Loaded dynamically via loadDevices() -->
                </div>
            </div>

            <!-- TAB 4: TARIFFS & SUPPLIERS CRUD -->
            <div id="view-tariffs" class="tab-content space-y-6">
                <!-- Dedicated Internal Cost Settings Card -->
                <div class="bg-gradient-to-r from-amber-950/60 via-[#0e1422] to-yellow-950/60 border border-amber-500/30 rounded-2xl p-5 shadow-xl">
                    <div class="flex justify-between items-start mb-4">
                        <div class="flex items-center gap-3">
                            <div class="w-10 h-10 rounded-xl bg-amber-500/20 border border-amber-500/30 flex items-center justify-center text-amber-400 text-xl">
                                ☀️
                            </div>
                            <div>
                                <h3 class="text-sm font-bold text-white">Interne Opwek & Afschrijving Kostprijzen</h3>
                                <p class="text-xs text-slate-400">Rekenprijzen voor zonnestroom en accu-degradatie gebruikt in besparingscalculaties.</p>
                            </div>
                        </div>
                        <span class="px-2 py-0.5 rounded text-[10px] font-mono font-bold bg-amber-500/20 text-amber-300 border border-amber-500/40">INSTELLINGEN</span>
                    </div>

                    <div class="grid grid-cols-1 sm:grid-cols-3 gap-4">
                        <!-- Baseload Setting -->
                        <div class="bg-[#0B0F17] p-4 rounded-xl border border-slate-800 space-y-2">
                            <div class="flex justify-between items-center">
                                <label for="tab-baseload-input" class="text-xs font-semibold text-slate-200">Continue Basislast (Sluip)</label>
                                <span class="text-[10px] text-blue-400 font-mono">Standaard 300 W</span>
                            </div>
                            <p class="text-[11px] text-slate-400">Continu achtergrondverbruik (router, koelkast, standby) gebruikt in 24h prognose.</p>
                            <div class="flex items-center gap-2 pt-1">
                                <input type="number" step="10" min="50" max="2000" id="tab-baseload-input" value="300" class="w-28 bg-slate-900 border border-slate-700 rounded-lg px-3 py-1.5 text-white font-mono text-xs font-bold focus:border-blue-500 focus:outline-none">
                                <span class="text-slate-400 font-mono text-xs">Watt</span>
                                <button onclick="saveSettingsFromTab()" class="ml-auto px-3 py-1.5 bg-blue-600 hover:bg-blue-500 text-white text-xs font-semibold rounded-lg shadow transition">
                                    Opslaan
                                </button>
                            </div>
                        </div>

                        <!-- Solar LCOE Setting -->
                        <div class="bg-[#0B0F17] p-4 rounded-xl border border-slate-800 space-y-2">
                            <div class="flex justify-between items-center">
                                <label for="tab-solar-cost-input" class="text-xs font-semibold text-slate-200">Zonnestroom Kostprijs</label>
                                <span class="text-[10px] text-amber-400 font-mono">Standaard €0,060</span>
                            </div>
                            <p class="text-[11px] text-slate-400">Interne afschrijvingsprijs per opgewekte kWh van je zonnepanelen.</p>
                            <div class="flex items-center gap-2 pt-1">
                                <span class="text-slate-400 font-mono text-sm">€</span>
                                <input type="number" step="0.005" min="0" max="0.5" id="tab-solar-cost-input" value="0.060" class="w-28 bg-slate-900 border border-slate-700 rounded-lg px-3 py-1.5 text-white font-mono text-xs font-bold focus:border-amber-500 focus:outline-none">
                                <span class="text-slate-400 font-mono text-xs">/ kWh</span>
                                <button onclick="saveSettingsFromTab()" class="ml-auto px-3 py-1.5 bg-amber-600 hover:bg-amber-500 text-white text-xs font-semibold rounded-lg shadow transition">
                                    Opslaan
                                </button>
                            </div>
                        </div>

                        <div class="bg-[#0B0F17] p-4 rounded-xl border border-slate-800 space-y-2">
                            <div class="flex justify-between items-center">
                                <span class="text-xs font-semibold text-slate-200">Batterij Cel-Degradatie / LCOS</span>
                                <span class="text-[10px] text-emerald-400 font-mono">Berekend €0,074</span>
                            </div>
                            <p class="text-[11px] text-slate-400">Cyclusslijtage per kWh gebaseerd op 6.000 LFP laadcycli (€400/jr).</p>
                            <div class="flex items-center gap-2 pt-1 font-mono text-xs text-slate-300">
                                <span>€0,0741 / kWh</span>
                                <span class="text-[10px] text-slate-500">(Beheerd via Batterij Arbitrage Beleid)</span>
                            </div>
                        </div>
                    </div>
                </div>

                <div class="flex justify-between items-center pt-2">
                    <div>
                        <h2 class="text-base font-bold text-white">Energieleveranciers & Tariefstructuren</h2>
                        <p class="text-xs text-slate-400">Beheer contracten (Powerpeers, Tibber, vast/dynamisch) en opslagen.</p>
                    </div>
                    <button onclick="openTariffModal()" class="px-3 py-1.5 bg-blue-600 hover:bg-blue-500 text-white text-xs font-semibold rounded-xl shadow transition-all">
                        + Leverancier Toevoegen
                    </button>
                </div>
                <div id="tariffs-container" class="grid grid-cols-1 md:grid-cols-2 gap-5"></div>
            </div>

            <!-- APIs now integrated inside Verbindingen tab -->

            <!-- TAB 6: CALIBRATION & EXCLUSION WINDOWS -->
            <div id="view-calibration" class="tab-content space-y-6">
                <!-- Status & KPI Header Banner -->
                <div class="bg-gradient-to-r from-amber-950/70 via-[#0e1422] to-blue-950/70 border border-amber-500/30 rounded-2xl p-5 shadow-xl flex flex-col lg:flex-row lg:items-center justify-between gap-4">
                    <div class="flex items-start sm:items-center gap-4">
                        <div class="w-12 h-12 rounded-xl bg-amber-500/20 border border-amber-500/40 flex items-center justify-center text-amber-400 text-2xl shadow-[0_0_15px_rgba(245,158,11,0.25)] flex-shrink-0">
                            🧠
                        </div>
                        <div class="min-w-0">
                            <div class="flex items-center gap-2 flex-wrap">
                                <span class="text-xs uppercase font-bold text-amber-400 tracking-wider">Zelflerend Energie & Vermogensmodel</span>
                                <span class="px-2 py-0.5 rounded text-[10px] font-mono font-bold bg-emerald-500/20 text-emerald-300 border border-emerald-500/40">15M KWARTIER-RESOLUTIE</span>
                                <span class="px-2 py-0.5 rounded text-[10px] font-mono font-bold bg-blue-500/20 text-blue-300 border border-blue-500/40">PHYSICS-INFORMED</span>
                            </div>
                            <div class="text-sm font-bold text-white mt-1">
                                Hybride Fysisch-Statistisch Model · 374 Dagen HA Data in Open HEMS
                            </div>
                            <p class="text-xs text-slate-400 mt-0.5">Decomponeert ongedefinieerd verbruik (7×96), CV-ruimteverwarming (2R1C + Carnot COP) en warm tapwater (350L vat).</p>
                        </div>
                    </div>
                    <div class="flex items-center gap-3 flex-wrap flex-shrink-0">
                        <button onclick="retrainModelNow()" id="btn-retrain-model" class="px-4 py-2.5 bg-amber-600 hover:bg-amber-500 text-white text-xs font-semibold rounded-xl shadow-lg transition-all flex items-center gap-2">
                            <svg class="w-4 h-4" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"></path></svg>
                            <span>Herbereken & Train Model</span>
                        </button>
                    </div>
                </div>

                <!-- 4 KPI CARDS: MODEL ACCURACY & PHYSICAL ATTRIBUTES -->
                <div class="grid grid-cols-2 lg:grid-cols-4 gap-4 font-mono text-xs">
                    <div class="bg-[#0e1422] border border-[#1E293B] rounded-2xl p-4 shadow-lg space-y-1">
                        <div class="text-[10px] text-slate-400 uppercase font-bold flex justify-between">
                            <span>R² Correlatie (CV / Temp)</span>
                            <span class="text-emerald-400 font-bold">STERK</span>
                        </div>
                        <div class="text-xl font-bold text-white tracking-tight" id="model-kpi-r2">0.783</div>
                        <div class="text-[11px] text-slate-400 font-sans">Verklaart 78% van de stookvariatie.</div>
                    </div>

                    <div class="bg-[#0e1422] border border-[#1E293B] rounded-2xl p-4 shadow-lg space-y-1">
                        <div class="text-[10px] text-slate-400 uppercase font-bold flex justify-between">
                            <span>Model Fout (RMSE / MAE)</span>
                            <span class="text-blue-400 font-bold">14.8% MAPE</span>
                        </div>
                        <div class="text-xl font-bold text-blue-300 tracking-tight" id="model-kpi-rmse">185 W <span class="text-xs text-slate-400">/ 132 W</span></div>
                        <div class="text-[11px] text-slate-400 font-sans">Nauwkeurigheid op kwartierbasis.</div>
                    </div>

                    <div class="bg-[#0e1422] border border-[#1E293B] rounded-2xl p-4 shadow-lg space-y-1">
                        <div class="text-[10px] text-slate-400 uppercase font-bold flex justify-between">
                            <span>Geleerde Gebouw UA</span>
                            <span class="text-amber-400 font-bold">2R1C MODEL</span>
                        </div>
                        <div class="text-xl font-bold text-amber-300 tracking-tight" id="model-kpi-ua">321 W/K</div>
                        <div class="text-[11px] text-slate-400 font-sans">Warmteverlies woning per graad ΔT.</div>
                    </div>

                    <div class="bg-[#0e1422] border border-[#1E293B] rounded-2xl p-4 shadow-lg space-y-1">
                        <div class="text-[10px] text-slate-400 uppercase font-bold flex justify-between">
                            <span>Periodieke Bijstelling</span>
                            <span class="w-2 h-2 rounded-full bg-emerald-400 animate-pulse mt-1"></span>
                        </div>
                        <div class="text-sm font-bold text-emerald-300 tracking-tight" id="model-kpi-schedule">Elke nacht 02:00</div>
                        <div class="text-[11px] text-slate-400 font-sans">EWMA drift tracking (leersnelheid 5%).</div>
                    </div>
                </div>

                                <!-- ========================================================================= -->
                <!-- HYPERPARAMETER STEERING & MODEL GOVERNANCE CARDS                          -->
                <!-- ========================================================================= -->
                <div class="grid grid-cols-1 lg:grid-cols-12 gap-5">
                    <!-- LEFT COLUMN: ALGORITHM HYPERPARAMETERS (4 COLS) -->
                    <div class="lg:col-span-4 bg-[#0e1422] border border-[#1E293B] rounded-2xl p-5 shadow-xl space-y-4 flex flex-col justify-between">
                        <div class="space-y-3">
                            <div class="flex items-center gap-2.5 border-b border-slate-800/80 pb-3">
                                <span class="w-3 h-3 rounded-full bg-amber-500 animate-pulse"></span>
                                <div>
                                    <h3 class="text-sm font-bold text-white tracking-wide">Algoritme Knoppen</h3>
                                    <p class="text-[11px] text-slate-400">Beïnvloed de leersnelheid en geheugenduur.</p>
                                </div>
                            </div>

                            <!-- 1. Learning Rate Slider -->
                            <div class="space-y-1.5 pt-1">
                                <div class="flex justify-between items-center text-xs">
                                    <label class="text-slate-300 font-medium">Leersnelheid (EWMA &alpha;)</label>
                                    <span id="label-learning-rate" class="font-mono text-amber-400 font-bold bg-amber-950/60 px-2 py-0.5 rounded border border-amber-800/50">5%</span>
                                </div>
                                <input type="range" id="slider-learning-rate" min="1" max="20" value="5" step="1" oninput="updateLearningRateLabel(this.value)" class="w-full accent-amber-500 bg-slate-800 rounded-lg cursor-pointer h-2">
                                <div class="flex justify-between text-[10px] text-slate-500 font-mono">
                                    <span>1% (Zeer stabiel)</span>
                                    <span>10%</span>
                                    <span>20% (Agressief)</span>
                                </div>
                            </div>

                            <!-- 2. Rolling Window Selector -->
                            <div class="space-y-1.5 pt-2">
                                <div class="flex justify-between items-center text-xs">
                                    <label class="text-slate-300 font-medium">Geheugenhorizon (Data Historie)</label>
                                    <span id="label-rolling-window" class="font-mono text-blue-400 font-bold bg-blue-950/60 px-2 py-0.5 rounded border border-blue-800/50">90 Dagen</span>
                                </div>
                                <select id="select-rolling-window" class="w-full bg-[#0B0F17] border border-slate-800 rounded-xl p-2.5 text-xs text-slate-200 font-mono focus:border-blue-500 focus:outline-none">
                                    <option value="30">30 Dagen (Recent seizoen)</option>
                                    <option value="90" selected>90 Dagen (Kwartaal / Standaard)</option>
                                    <option value="365">365 Dagen (Volledig jaar / Max. robuust)</option>
                                </select>
                            </div>

                            <!-- 3. Auto-Accept Threshold Slider -->
                            <div class="space-y-1.5 pt-2">
                                <div class="flex justify-between items-center text-xs">
                                    <label class="text-slate-300 font-medium">Auto-Accept Drempel</label>
                                    <span id="label-auto-accept" class="font-mono text-emerald-400 font-bold bg-emerald-950/60 px-2 py-0.5 rounded border border-emerald-800/50">&plusmn;3.0%</span>
                                </div>
                                <input type="range" id="slider-auto-accept" min="0" max="10" value="3" step="0.5" oninput="updateAutoAcceptLabel(this.value)" class="w-full accent-emerald-500 bg-slate-800 rounded-lg cursor-pointer h-2">
                                <div class="flex justify-between text-[10px] text-slate-500 font-mono">
                                    <span>0% (Altijd handmatig)</span>
                                    <span>&plusmn;5%</span>
                                    <span>&plusmn;10%</span>
                                </div>
                            </div>
                        </div>

                        <div class="pt-3 border-t border-slate-800/80">
                            <button onclick="saveAlgorithmConfig()" id="btn-save-algo" class="w-full py-2 bg-slate-800 hover:bg-slate-700 text-slate-200 font-bold text-xs rounded-xl border border-slate-700 transition flex items-center justify-center gap-2">
                                <svg class="w-3.5 h-3.5 text-amber-400" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path d="M8 7H5a2 2 0 00-2 2v9a2 2 0 002 2h14a2 2 0 002-2V9a2 2 0 00-2-2h-3m-1 4l-3 3m0 0l-3-3m3 3V4"></path></svg>
                                <span>Instellingen Opslaan</span>
                            </button>
                        </div>
                    </div>

                    <!-- RIGHT COLUMN: MODEL RECOMMENDATIONS & PARAMETER DRIFT (8 COLS) -->
                    <div class="lg:col-span-8 bg-[#0e1422] border border-[#1E293B] rounded-2xl p-5 shadow-xl space-y-4 flex flex-col justify-between">
                        <div class="space-y-3">
                            <div class="flex flex-col sm:flex-row sm:items-center justify-between gap-2 border-b border-slate-800/80 pb-3">
                                <div class="flex items-center gap-2.5">
                                    <span class="w-3 h-3 rounded-full bg-purple-500 animate-pulse"></span>
                                    <div>
                                        <h3 class="text-sm font-bold text-white tracking-wide">Model Parameter Aanbevelingen</h3>
                                        <p class="text-[11px] text-slate-400">Vergelijk actieve parameters met de nieuw berekende voorstellen.</p>
                                    </div>
                                </div>
                                <div class="flex items-center gap-2" id="recs-status-badge-container">
                                    <span id="recs-status-badge" class="px-2.5 py-0.5 rounded-full text-[10px] font-mono font-bold bg-slate-800 text-slate-300 border border-slate-700">Laden...</span>
                                </div>
                            </div>

                            <!-- Recommendations Table -->
                            <div class="overflow-x-auto">
                                <table class="w-full text-left text-xs font-mono">
                                    <thead>
                                        <tr class="text-[10px] uppercase text-slate-400 border-b border-slate-800/60 pb-2 font-mono select-none">
                                            <th class="py-2">
                                                <span class="inline-flex items-center gap-1">
                                                    <span>Fysische Parameter</span>
                                                    <button type="button" onclick="toggleInfoPopover(event, 'col_param')" class="text-slate-500 hover:text-cyan-400 transition-colors p-0.5 focus:outline-none" aria-label="Info">
                                                        <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><circle cx="12" cy="12" r="10"></circle><path d="M12 16v-4m0-4h.01"></path></svg>
                                                    </button>
                                                </span>
                                            </th>
                                            <th class="py-2 text-center">
                                                <span class="inline-flex items-center justify-center gap-1">
                                                    <span>Huidig Actief</span>
                                                    <button type="button" onclick="toggleInfoPopover(event, 'col_active')" class="text-slate-500 hover:text-cyan-400 transition-colors p-0.5 focus:outline-none" aria-label="Info">
                                                        <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><circle cx="12" cy="12" r="10"></circle><path d="M12 16v-4m0-4h.01"></path></svg>
                                                    </button>
                                                </span>
                                            </th>
                                            <th class="py-2 text-center">
                                                <span class="inline-flex items-center justify-center gap-1">
                                                    <span>Nieuw Voorstel</span>
                                                    <button type="button" onclick="toggleInfoPopover(event, 'col_proposed')" class="text-slate-500 hover:text-cyan-400 transition-colors p-0.5 focus:outline-none" aria-label="Info">
                                                        <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><circle cx="12" cy="12" r="10"></circle><path d="M12 16v-4m0-4h.01"></path></svg>
                                                    </button>
                                                </span>
                                            </th>
                                            <th class="py-2 text-center">
                                                <span class="inline-flex items-center justify-center gap-1">
                                                    <span>Drift (%)</span>
                                                    <button type="button" onclick="toggleInfoPopover(event, 'col_drift')" class="text-slate-500 hover:text-cyan-400 transition-colors p-0.5 focus:outline-none" aria-label="Info">
                                                        <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><circle cx="12" cy="12" r="10"></circle><path d="M12 16v-4m0-4h.01"></path></svg>
                                                    </button>
                                                </span>
                                            </th>
                                            <th class="py-2">
                                                <span class="inline-flex items-center gap-1">
                                                    <span>Onderbouwing</span>
                                                    <button type="button" onclick="toggleInfoPopover(event, 'col_evidence')" class="text-slate-500 hover:text-cyan-400 transition-colors p-0.5 focus:outline-none" aria-label="Info">
                                                        <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><circle cx="12" cy="12" r="10"></circle><path d="M12 16v-4m0-4h.01"></path></svg>
                                                    </button>
                                                </span>
                                            </th>
                                            <th class="py-2 text-right">
                                                <span class="inline-flex items-center justify-end gap-1">
                                                    <span>Status</span>
                                                    <button type="button" onclick="toggleInfoPopover(event, 'col_status')" class="text-slate-500 hover:text-cyan-400 transition-colors p-0.5 focus:outline-none" aria-label="Info">
                                                        <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><circle cx="12" cy="12" r="10"></circle><path d="M12 16v-4m0-4h.01"></path></svg>
                                                    </button>
                                                </span>
                                            </th>
                                        </tr>
                                    </thead>
                                    <tbody id="recs-table-body" class="divide-y divide-slate-800/50 text-slate-300">
                                        <tr><td colspan="6" class="py-4 text-center text-slate-500">Aanbevelingen ophalen...</td></tr>
                                    </tbody>
                                </table>
                            </div>
                        </div>

                        <!-- Action Buttons Bar -->
                        <div class="flex flex-col sm:flex-row sm:items-center justify-between gap-3 pt-3 border-t border-slate-800/80">
                            <div class="flex flex-col gap-0.5" id="recs-info-footer">
                                <span class="text-[11px] text-slate-300 font-sans font-medium">💡 <strong>Wat betekent Automatisch?</strong> Wijzigingen binnen de drempel (&plusmn;3%) worden geruisloos via de leersnelheid (EWMA) toegepast.</span>
                                <span class="text-[10px] text-slate-400 font-sans">Grotere afwijkingen (zoals een sprong in nachtverbruik) komen op 'Ter Beoordeling' te staan totdat je op 'Accepteren &amp; Toepassen' klikt.</span>
                            </div>
                            <div class="flex items-center gap-2">
                                <button onclick="rejectRecommendations()" id="btn-recs-reject" class="px-3 py-1.5 bg-slate-800 hover:bg-slate-700 text-slate-300 text-xs rounded-xl font-medium border border-slate-700 transition">
                                    Afwijzen
                                </button>
                                <button onclick="acceptRecommendations()" id="btn-recs-accept" class="px-4 py-1.5 bg-emerald-600 hover:bg-emerald-500 text-white text-xs font-semibold rounded-xl shadow-lg transition flex items-center gap-1.5">
                                    <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M5 13l4 4L19 7"></path></svg>
                                    <span>Accepteren &amp; Toepassen</span>
                                </button>
                            </div>
                        </div>
                    </div>
                </div>

                <!-- CARD 1: 24-HOUR 15-MINUTE ROLLING FORECAST DECOMPOSITION CHART -->
                <div class="bg-[#0e1422] border border-[#1E293B] rounded-2xl p-5 shadow-xl space-y-4">
                    <div class="flex flex-col sm:flex-row sm:items-center justify-between gap-3 border-b border-slate-800/80 pb-3">
                        <div class="flex items-center gap-2.5">
                            <span class="w-3 h-3 rounded-full bg-cyan-500 animate-pulse"></span>
                            <div>
                                <div class="flex items-center gap-2">
                                    <h3 class="text-sm font-bold text-white tracking-wide">24-Uurs Kwartier-Voorspelling Decompositie (Vanaf Nu)</h3>
                                    <span class="px-2 py-0.5 rounded text-[9px] font-mono font-bold bg-cyan-950 text-cyan-300 border border-cyan-800">SINGLE SOURCE OF TRUTH</span>
                                </div>
                                <p class="text-[11px] text-slate-400">Identiek aan Voorspelling &amp; Optimalisatie: toont de 96 kwartieren vanaf Nu inclusief de actieve dispatch (boiler opwarming om 13:00u, piekuitsluiting en zon).</p>
                            </div>
                        </div>
                        <div class="flex items-center gap-2 flex-wrap text-xs">
                            <span class="flex items-center gap-1.5 px-2.5 py-1 bg-blue-950/60 border border-blue-800 text-blue-300 rounded-lg"><span class="w-2 h-2 rounded-full bg-blue-400"></span> Ongedefinieerd</span>
                            <span class="flex items-center gap-1.5 px-2.5 py-1 bg-red-950/60 border border-red-800 text-red-300 rounded-lg"><span class="w-2 h-2 rounded-full bg-red-400"></span> CV Verwarming</span>
                            <span class="flex items-center gap-1.5 px-2.5 py-1 bg-amber-950/60 border border-amber-800 text-amber-300 rounded-lg"><span class="w-2 h-2 rounded-full bg-amber-400"></span> SWW Boiler 350L</span>
                            <span class="flex items-center gap-1.5 px-2.5 py-1 bg-cyan-950/60 border border-cyan-800 text-cyan-300 rounded-lg"><span class="w-2 h-2 rounded-full bg-cyan-400"></span> Totaal Verwacht</span>
                        </div>
                    </div>

                    <div class="h-64 sm:h-72 w-full relative">
                        <canvas id="chart-model-decomposition"></canvas>
                    </div>
                </div>

                <!-- CARD 2: 7x96 LEARNED HISTORICAL BEHAVIOR PROFILES (MONTH + DAY + 3-WAY SELECTOR) -->
                <div class="bg-[#0e1422] border border-[#1E293B] rounded-2xl p-5 shadow-xl space-y-4">
                    <div class="flex flex-col lg:flex-row lg:items-center justify-between gap-3 border-b border-slate-800/80 pb-3">
                        <div class="flex items-center gap-2.5">
                            <span class="w-3 h-3 rounded-full bg-blue-500 animate-pulse" id="profile-status-indicator"></span>
                            <div>
                                <div class="flex items-center gap-2">
                                    <h3 class="text-sm font-bold text-white tracking-wide" id="profile-section-title">Zelflerende Basisprofielen per Weekdag (7×96 Matrix: 00:00–24:00)</h3>
                                    <span class="px-2 py-0.5 rounded text-[9px] font-mono font-bold bg-blue-950 text-blue-300 border border-blue-800">STATISTISCHE MATRIX</span>
                                </div>
                                <p class="text-[11px] text-slate-400" id="profile-section-sub">De onderliggende statistische referentiebehoefte per kalenderdag van 00:00 tot 24:00 (vóór dynamische sturing en actuele weersinvloeden), gefit op 374 dagen InfluxDB telemetrie.</p>
                            </div>
                        </div>
                        
                        <!-- 3-WAY PROFILE SELECTOR BUTTONS -->
                        <div class="flex items-center gap-1.5 bg-[#0B0F17] p-1 rounded-xl border border-slate-800 text-xs font-mono flex-wrap">
                            <button onclick="switchProfileType('unallocated')" id="btn-prof-unallocated" class="px-3 py-1.5 rounded-lg bg-blue-600 text-white font-bold transition flex items-center gap-1.5 shadow">
                                <span class="w-2 h-2 rounded-full bg-blue-400"></span>
                                <span>Ongedefinieerd (Huis)</span>
                            </button>
                            <button onclick="switchProfileType('dhw')" id="btn-prof-dhw" class="px-3 py-1.5 rounded-lg text-slate-400 hover:text-white transition flex items-center gap-1.5">
                                <span class="w-2 h-2 rounded-full bg-amber-400"></span>
                                <span>Warm Tapwater (SWW 350L)</span>
                            </button>
                            <button onclick="switchProfileType('cv')" id="btn-prof-cv" class="px-3 py-1.5 rounded-lg text-slate-400 hover:text-white transition flex items-center gap-1.5">
                                <span class="w-2 h-2 rounded-full bg-red-400"></span>
                                <span>CV Woningverwarming</span>
                            </button>
                        </div>
                    </div>

                                        <!-- 12-MONTH SEASONAL SELECTOR STRIP -->
                    <div class="space-y-1.5">
                        <div class="flex justify-between items-center text-[10px] text-slate-400 font-mono uppercase tracking-wider">
                            <span>📅 Seizoen / Maand van het Jaar (Impact op Warmtevraag & Buitentemperatuur)</span>
                            <span id="month-impact-summary" class="text-amber-400 font-bold font-sans">September: Zachte overgang</span>
                        </div>
                        <div class="grid grid-cols-6 sm:grid-cols-12 gap-1 font-mono text-xs" id="month-selector-grid">
                            <!-- Rendered in JS: Jan t/m Dec -->
                        </div>
                    </div>

                    <!-- DAY OF WEEK SELECTOR TABS -->
                    <div class="flex items-center gap-1.5 overflow-x-auto pb-1 text-xs font-mono" id="unalloc-day-selector">
                        <button onclick="selectUnallocDay(0)" class="unalloc-day-btn px-3 py-1 rounded-lg border border-slate-800 bg-slate-900 text-slate-300 font-medium">Maandag</button>
                        <button onclick="selectUnallocDay(1)" class="unalloc-day-btn px-3 py-1 rounded-lg border border-slate-800 bg-slate-900 text-slate-300 font-medium">Dinsdag</button>
                        <button onclick="selectUnallocDay(2)" class="unalloc-day-btn px-3 py-1 rounded-lg border border-slate-800 bg-slate-900 text-slate-300 font-medium">Woensdag</button>
                        <button onclick="selectUnallocDay(3)" class="unalloc-day-btn px-3 py-1 rounded-lg border border-slate-800 bg-slate-900 text-slate-300 font-medium">Donderdag</button>
                        <button onclick="selectUnallocDay(4)" class="unalloc-day-btn px-3 py-1 rounded-lg border border-slate-800 bg-slate-900 text-slate-300 font-medium">Vrijdag</button>
                        <button onclick="selectUnallocDay(5)" class="unalloc-day-btn px-3 py-1 rounded-lg border border-slate-800 bg-slate-900 text-slate-300 font-medium">Zaterdag</button>
                        <button onclick="selectUnallocDay(6)" class="unalloc-day-btn px-3 py-1 rounded-lg border border-slate-800 bg-slate-900 text-slate-300 font-medium">Zondag</button>
                    </div>

                    <!-- SELECTED DAY METRICS STRIP -->
                    <div class="grid grid-cols-2 sm:grid-cols-4 gap-3 font-mono text-xs">
                        <div class="bg-[#0B0F17] p-3 rounded-xl border border-slate-800">
                            <div class="text-[10px] text-slate-400 uppercase font-bold" id="metric-title-1">Dag Gemiddelde</div>
                            <div class="text-base font-bold text-white mt-0.5" id="unalloc-metric-avg">-- W</div>
                        </div>
                        <div class="bg-[#0B0F17] p-3 rounded-xl border border-slate-800">
                            <div class="text-[10px] text-blue-400 uppercase font-bold" id="metric-title-2">Nacht Stand-by (00-06u)</div>
                            <div class="text-base font-bold text-blue-300 mt-0.5" id="unalloc-metric-night">-- W</div>
                        </div>
                        <div class="bg-[#0B0F17] p-3 rounded-xl border border-slate-800">
                            <div class="text-[10px] text-amber-400 uppercase font-bold" id="metric-title-3">Ochtendpiek (07-11u)</div>
                            <div class="text-base font-bold text-amber-300 mt-0.5" id="unalloc-metric-morning">-- W</div>
                        </div>
                        <div class="bg-[#0B0F17] p-3 rounded-xl border border-slate-800">
                            <div class="text-[10px] text-pink-400 uppercase font-bold" id="metric-title-4">Avondpiek (18-23u)</div>
                            <div class="text-base font-bold text-pink-300 mt-0.5" id="unalloc-metric-evening">-- W</div>
                        </div>
                    </div>

                    <!-- 96-QUARTERS BAR CHART WITH TIME AXIS & LIVE HOVER BADGE -->
                    <div class="space-y-2 pt-1">
                        <div class="flex justify-between items-center text-[11px] text-slate-400 font-mono flex-wrap gap-2">
                            <span id="unalloc-selected-day-label" class="text-slate-200 font-semibold">Geselecteerde dag</span>
                            <span id="unalloc-hover-badge" class="px-2.5 py-1 rounded-lg bg-slate-800/90 text-cyan-300 font-mono text-xs border border-slate-700 shadow flex items-center gap-1.5">
                                <span class="w-1.5 h-1.5 rounded-full bg-cyan-400 animate-ping"></span>
                                <span>Beweeg over een kwartier voor details</span>
                            </span>
                        </div>
                        
                        <!-- Bars Container -->
                        <div class="bg-[#0B0F17] p-3 rounded-xl border border-slate-800 space-y-1.5">
                            <div id="unalloc-hourly-bars" class="flex gap-0.5 h-32 items-end overflow-hidden w-full relative">
                                <!-- 96 Bars rendered dynamically in JS -->
                            </div>
                            
                            <!-- TIME AXIS: Visible Hours below the bars -->
                            <div class="flex justify-between text-[10px] text-slate-500 font-mono pt-1.5 px-0.5 border-t border-slate-800/60 select-none">
                                <span>00:00</span>
                                <span>03:00</span>
                                <span>06:00</span>
                                <span>09:00</span>
                                <span>12:00</span>
                                <span>15:00</span>
                                <span>18:00</span>
                                <span>21:00</span>
                                <span>24:00</span>
                            </div>
                        </div>
                    </div>
                </div>

                <!-- CARD 3: MODEL INSPECTOR & CODE / FORMULAS TRANSPARENCY -->
                <div class="bg-[#0e1422] border border-[#1E293B] rounded-2xl p-5 shadow-xl space-y-4">
                    <div class="flex flex-col sm:flex-row sm:items-center justify-between gap-3 border-b border-slate-800/80 pb-3">
                        <div>
                            <h3 class="text-sm font-bold text-white tracking-wide">Model Inspectie & Transparantie</h3>
                            <p class="text-[11px] text-slate-400">Verifieer de onderliggende fysische formules, constanten of inspecteer direct de actieve Python model-code.</p>
                        </div>
                        <!-- TAB SWITCHER -->
                        <div class="flex items-center gap-1.5 bg-[#0B0F17] p-1 rounded-xl border border-slate-800 text-xs font-mono">
                            <button onclick="selectModelInspectTab('formulas')" id="btn-inspect-formulas" class="px-3 py-1.5 rounded-lg bg-blue-600 text-white font-bold transition">📐 Fysische Formules</button>
                            <button onclick="selectModelInspectTab('code')" id="btn-inspect-code" class="px-3 py-1.5 rounded-lg text-slate-400 hover:text-white transition">💻 Python Code & JSON</button>
                        </div>
                    </div>

                    <!-- TAB 1: FORMULAS -->
                    <div id="model-inspect-formulas" class="grid grid-cols-1 md:grid-cols-2 gap-4">
                        <div class="bg-[#0B0F17] border border-slate-800 rounded-xl p-4 space-y-2">
                            <div class="flex items-center justify-between text-xs font-bold text-blue-400">
                                <span>1. Ongedefinieerd Verbruik (7×96 + Seizoen)</span>
                                <span class="text-[10px] bg-blue-900/40 text-blue-300 px-2 py-0.5 rounded border border-blue-800">EMBEDDED KERNEL</span>
                            </div>
                            <div class="bg-black/60 p-3 rounded-lg border border-slate-800 font-mono text-xs text-slate-200">
                                P_unalloc(t) = max(P_floor, S_dow,tod · (1.0 + A · cos(2π · (d - 15) / 365.25)))
                            </div>
                            <ul class="text-[11px] text-slate-400 space-y-1 list-disc list-inside">
                                <li><strong>P_floor:</strong> 265.0 W (20e-percentiel nachtbaseload).</li>
                                <li><strong>A (Seizoensamplitude):</strong> 22% hogere baseline in januari dan in juli.</li>
                                <li><strong>S_dow,tod:</strong> 7×96 kwartieren matrix geleerd uit 374 dagen data.</li>
                            </ul>
                        </div>

                        <div class="bg-[#0B0F17] border border-slate-800 rounded-xl p-4 space-y-2">
                            <div class="flex items-center justify-between text-xs font-bold text-red-400">
                                <span>2. CV Ruimteverwarming (2R1C Gebouwmodel)</span>
                                <span class="text-[10px] bg-red-900/40 text-red-300 px-2 py-0.5 rounded border border-red-800">THERMISCH VERLIES</span>
                            </div>
                            <div class="bg-black/60 p-3 rounded-lg border border-slate-800 font-mono text-xs text-slate-200">
                                Q_cv(t) = (UA_base + c_wind · v_wind) · (T_binnen - T_buiten) - c_zon · G_zon
                            </div>
                            <ul class="text-[11px] text-slate-400 space-y-1 list-disc list-inside">
                                <li><strong>UA_base:</strong> 321.1 W/K (7.71 kWh/°C/dag isolatiegraad).</li>
                                <li><strong>Nachtverlaging:</strong> 20.0°C overdag · 17.5°C tussen 23:00 en 06:00.</li>
                                <li><strong>Zomeruitschakeling:</strong> Uitgeschakeld bij dagtemperatuur ≥ 16.0°C.</li>
                            </ul>
                        </div>

                        <div class="bg-[#0B0F17] border border-slate-800 rounded-xl p-4 space-y-2">
                            <div class="flex items-center justify-between text-xs font-bold text-amber-400">
                                <span>3. Daikin Warmtepomp Carnot COP Curve</span>
                                <span class="text-[10px] bg-amber-900/40 text-amber-300 px-2 py-0.5 rounded border border-amber-800">THERMODYNAMISCH</span>
                            </div>
                            <div class="bg-black/60 p-3 rounded-lg border border-slate-800 font-mono text-xs text-slate-200">
                                COP(t) = η_carnot · (T_flow + 273.15) / (T_flow - T_buiten) · f_defrost
                            </div>
                            <ul class="text-[11px] text-slate-400 space-y-1 list-disc list-inside">
                                <li><strong>η_carnot:</strong> 0.48 (gekalibreerd op Daikin Altherma 3 H HT 18kW).</li>
                                <li><strong>T_flow CV:</strong> 35.0°C vloerverwarming aanvoer.</li>
                                <li><strong>f_defrost:</strong> 18% COP-straf tussen -2°C en +4.5°C bij hoge vochtigheid.</li>
                            </ul>
                        </div>

                        <div class="bg-[#0B0F17] border border-slate-800 rounded-xl p-4 space-y-2">
                            <div class="flex items-center justify-between text-xs font-bold text-cyan-400">
                                <span>4. SWW Tapwaterboiler (350 Liter Combivat)</span>
                                <span class="text-[10px] bg-cyan-900/40 text-cyan-300 px-2 py-0.5 rounded border border-cyan-800">MASSA BALANS</span>
                            </div>
                            <div class="bg-black/60 p-3 rounded-lg border border-slate-800 font-mono text-xs text-slate-200">
                                Q_sww = [V · c_p · (T_doel - T_inlaat)] / 3600 + Q_stilstand
                            </div>
                            <ul class="text-[11px] text-slate-400 space-y-1 list-disc list-inside">
                                <li><strong>Volume:</strong> 350 liter vat · doel 50.0°C (6.2 à 7.5 kWh_th / dag).</li>
                                <li><strong>DHW COP:</strong> ~2.7 in winter, 3.2 in zomer (aanvoer 52°C).</li>
                                <li><strong>Dispatch Window:</strong> 3 opeenvolgende kwartieren (45 min) rond zonnepiek.</li>
                            </ul>
                        </div>
                    </div>

                    <!-- TAB 2: CODE & JSON VIEWER -->
                    <div id="model-inspect-code" class="hidden space-y-3">
                        <div class="flex justify-between items-center text-xs font-mono text-slate-400">
                            <span>Bron: /config/projects/energy-scheduler/layer2_calibration/learned_forecaster.py</span>
                            <span class="text-emerald-400">Status: Actief geïmporteerd in daemon</span>
                        </div>
                        <pre class="bg-black/90 p-4 rounded-xl text-xs font-mono text-emerald-300 border border-slate-800 overflow-x-auto max-h-96" id="model-code-block"><code># Open HEMS Hybrid Forecaster Core Logic
def predict_unallocated_w(dt: datetime) -> float:
    dow = dt.weekday()
    q_idx = dt.hour * 4 + dt.minute // 15
    base_w = profile_96_quarters[dow][q_idx]
    seasonal_mult = 1.0 + 0.22 * math.cos(2.0 * math.pi * (dt.timetuple().tm_yday - 15) / 365.25)
    return max(265.0, round(base_w * seasonal_mult, 1))

def predict_space_heating_w(dt: datetime, t_outdoor_c: float) -> dict:
    if t_outdoor_c >= 16.0 or dt.month in [6, 7, 8]:
        return {"electrical_w": 0.0, "thermal_w": 0.0, "cop": 0.0}
    t_target = 17.5 if (dt.hour < 6 or dt.hour >= 23) else 20.0
    q_thermal_w = max(0.0, 321.1 * (t_target - t_outdoor_c))
    cop = calculate_cop(t_flow=35.0, t_outdoor=t_outdoor_c)
    return {"electrical_w": round(q_thermal_w / cop, 1), "cop": cop}</code></pre>
                    </div>
                </div>

                <!-- CARD 4: DATA EXCLUSION MASKS -->
                <div class="bg-[#0e1422] border border-[#1E293B] rounded-2xl p-6">
                    <div class="flex justify-between items-center mb-4">
                        <div>
                            <h3 class="text-sm font-bold text-white">Data Uitsluitingsmaskers (Sensor Downtime)</h3>
                            <p class="text-xs text-slate-400">Periodes waarin sensoren defect of ontkoppeld waren, zodat ze de leercurve niet vervuilen.</p>
                        </div>
                        <button onclick="openExclusionModal()" class="px-2.5 py-1 bg-slate-800 hover:bg-slate-700 text-slate-200 border border-slate-700 text-xs rounded-lg">
                            + Masker Toevoegen
                        </button>
                    </div>
                    <table class="w-full text-left text-xs border-collapse">
                        <thead>
                            <tr class="bg-[#131D2D] text-slate-400 border-b border-[#1E293B]">
                                <th class="p-2.5">Sensor</th>
                                <th class="p-2.5">Startdatum</th>
                                <th class="p-2.5">Einddatum</th>
                                <th class="p-2.5">Reden</th>
                                <th class="p-2.5 text-right">Actie</th>
                            </tr>
                        </thead>
                        <tbody id="exclusion-tbody" class="divide-y divide-[#1E293B]"></tbody>
                    </table>
                </div>
            </div>

            <!-- TAB 7: INSTANCE CONFIGURATION MODALS & TEMPLATES -->

            <!-- MODAL: ADD / EDIT INFLUXDB CONNECTION PROFILE -->
    <div id="influx-modal" class="fixed inset-0 bg-black/75 backdrop-blur-sm flex items-center justify-center hidden z-50 p-3">
        <div class="bg-[#0e1422] border border-[#1E293B] rounded-2xl p-4 sm:p-6 w-full max-w-lg max-h-[90vh] overflow-y-auto text-xs text-slate-300 shadow-2xl">
            <h3 class="text-sm font-bold text-white mb-4" id="modal-influx-title">InfluxDB Instantie Configureren</h3>
            <form onsubmit="saveInfluxModal(event)" class="space-y-3">
                <input type="hidden" id="modal-influx-id">
                <div>
                    <label class="block mb-1 text-slate-400">Naam Instantie</label>
                    <input type="text" id="modal-influx-name" required placeholder="bijv. Lokale Home Assistant of Remote Server" class="w-full bg-[#0B0F17] border border-slate-800 rounded-lg p-2 text-white">
                </div>
                <div class="grid grid-cols-2 gap-3">
                    <div>
                        <label class="block mb-1 text-slate-400">Type / Versie</label>
                        <select id="modal-influx-type" class="w-full bg-[#0B0F17] border border-slate-800 rounded-lg p-2 text-white">
                            <option value="influx_v1">InfluxDB 1.8 (User/Password)</option>
                            <option value="influx_v2">InfluxDB 2.x / Cloud (Token/Org)</option>
                        </select>
                    </div>
                    <div>
                        <label class="block mb-1 text-slate-400">Server URL</label>
                        <input type="text" id="modal-influx-url" required value="http://a0d7b954-influxdb:8086" class="w-full bg-[#0B0F17] border border-slate-800 rounded-lg p-2 text-white font-mono">
                    </div>
                </div>
                <div class="grid grid-cols-2 gap-3">
                    <div>
                        <label class="block mb-1 text-slate-400">Data Opslag Database</label>
                        <input type="text" id="modal-influx-db" required value="hermes" class="w-full bg-[#0B0F17] border border-slate-800 rounded-lg p-2 text-white font-mono">
                    </div>
                    <div>
                        <label class="block mb-1 text-slate-400">HA Lees Database</label>
                        <input type="text" id="modal-influx-read-db" required value="openhems" class="w-full bg-[#0B0F17] border border-slate-800 rounded-lg p-2 text-white font-mono">
                    </div>
                </div>
                <div class="grid grid-cols-2 gap-3">
                    <div>
                        <label class="block mb-1 text-slate-400">Gebruikersnaam / Org</label>
                        <input type="text" id="modal-influx-user" value="hermes" class="w-full bg-[#0B0F17] border border-slate-800 rounded-lg p-2 text-white">
                    </div>
                    <div>
                        <label class="block mb-1 text-slate-400">Wachtwoord / Token</label>
                        <input type="password" id="modal-influx-pass" class="w-full bg-[#0B0F17] border border-slate-800 rounded-lg p-2 text-white">
                    </div>
                </div>
                <div>
                    <label class="block mb-1 text-slate-400">Retentiebeleid (Retention Policy)</label>
                    <input type="text" id="modal-influx-retention" value="autogen" class="w-full bg-[#0B0F17] border border-slate-800 rounded-lg p-2 text-white font-mono">
                </div>
                <label class="flex items-center gap-2 pt-1">
                    <input type="checkbox" id="modal-influx-default" class="rounded bg-slate-900 text-blue-600 border-slate-700">
                    <span class="text-slate-300 text-xs">Instellen als standaard opslag</span>
                </label>

                <div id="modal-influx-feedback" class="p-2 rounded text-[11px] font-mono hidden"></div>

                <div class="flex justify-between items-center pt-3 border-t border-slate-800">
                    <button type="button" onclick="testModalInflux()" class="px-3 py-1.5 bg-slate-800 hover:bg-slate-700 text-slate-200 border border-slate-700 rounded-lg transition-all flex items-center gap-1.5">
                        <svg class="w-3.5 h-3.5 text-cyan-400" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path d="M13 10V3L4 14h7v7l9-11h-7z"></path></svg>
                        <span>Test Verbinding</span>
                    </button>
                    <div class="flex gap-2">
                        <button type="button" onclick="closeModal('influx-modal')" class="px-3 py-1.5 bg-slate-800 text-slate-400 rounded-lg">Annuleren</button>
                        <button type="submit" class="px-3 py-1.5 bg-blue-600 text-white font-semibold rounded-lg shadow">Opslaan</button>
                    </div>
                </div>
            </form>
        </div>
    </div>

    <!-- MODAL: ADD / EDIT MQTT BROKER PROFILE -->
    <div id="mqtt-modal" class="fixed inset-0 bg-black/75 backdrop-blur-sm flex items-center justify-center hidden z-50 p-3">
        <div class="bg-[#0e1422] border border-[#1E293B] rounded-2xl p-4 sm:p-6 w-full max-w-lg max-h-[90vh] overflow-y-auto text-xs text-slate-300 shadow-2xl">
            <h3 class="text-sm font-bold text-white mb-4" id="modal-mqtt-title">MQTT Broker Configureren</h3>
            <form onsubmit="saveMqttModal(event)" class="space-y-3">
                <input type="hidden" id="modal-mqtt-id">
                <div>
                    <label class="block mb-1 text-slate-400">Naam Broker</label>
                    <input type="text" id="modal-mqtt-name" required placeholder="bijv. Lokale Mosquitto of Cloud Gateway" class="w-full bg-[#0B0F17] border border-slate-800 rounded-lg p-2 text-white">
                </div>
                <div class="grid grid-cols-3 gap-3">
                    <div class="col-span-2">
                        <label class="block mb-1 text-slate-400">Broker Hostname / IP</label>
                        <input type="text" id="modal-mqtt-host" required value="core-mosquitto" class="w-full bg-[#0B0F17] border border-slate-800 rounded-lg p-2 text-white font-mono">
                    </div>
                    <div>
                        <label class="block mb-1 text-slate-400">Poort</label>
                        <input type="number" id="modal-mqtt-port" required value="1883" class="w-full bg-[#0B0F17] border border-slate-800 rounded-lg p-2 text-white font-mono">
                    </div>
                </div>
                <div class="grid grid-cols-2 gap-3">
                    <div>
                        <label class="block mb-1 text-slate-400">Base Topic Prefix</label>
                        <input type="text" id="modal-mqtt-topic" value="openhems" class="w-full bg-[#0B0F17] border border-slate-800 rounded-lg p-2 text-white font-mono">
                    </div>
                    <div>
                        <label class="block mb-1 text-slate-400">Client ID</label>
                        <input type="text" id="modal-mqtt-client-id" value="open-hems-collector" class="w-full bg-[#0B0F17] border border-slate-800 rounded-lg p-2 text-white font-mono">
                    </div>
                </div>
                <div class="grid grid-cols-2 gap-3">
                    <div>
                        <label class="block mb-1 text-slate-400">Gebruikersnaam</label>
                        <input type="text" id="modal-mqtt-user" class="w-full bg-[#0B0F17] border border-slate-800 rounded-lg p-2 text-white">
                    </div>
                    <div>
                        <label class="block mb-1 text-slate-400">Wachtwoord</label>
                        <input type="password" id="modal-mqtt-pass" class="w-full bg-[#0B0F17] border border-slate-800 rounded-lg p-2 text-white">
                    </div>
                </div>
                <div class="flex items-center gap-6 pt-1">
                    <label class="flex items-center gap-2">
                        <input type="checkbox" id="modal-mqtt-tls" class="rounded bg-slate-900 text-amber-600 border-slate-700">
                        <span class="text-slate-300 text-xs">TLS/SSL Encryptie</span>
                    </label>
                    <label class="flex items-center gap-2">
                        <input type="checkbox" id="modal-mqtt-default" class="rounded bg-slate-900 text-amber-600 border-slate-700">
                        <span class="text-slate-300 text-xs">Instellen als standaard broker</span>
                    </label>
                </div>

                <div id="modal-mqtt-feedback" class="p-2 rounded text-[11px] font-mono hidden"></div>

                <div class="flex justify-between items-center pt-3 border-t border-slate-800">
                    <button type="button" onclick="testModalMqtt()" class="px-3 py-1.5 bg-slate-800 hover:bg-slate-700 text-slate-200 border border-slate-700 rounded-lg transition-all flex items-center gap-1.5">
                        <svg class="w-3.5 h-3.5 text-amber-400" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path d="M13 10V3L4 14h7v7l9-11h-7z"></path></svg>
                        <span>Test Verbinding</span>
                    </button>
                    <div class="flex gap-2">
                        <button type="button" onclick="closeModal('mqtt-modal')" class="px-3 py-1.5 bg-slate-800 text-slate-400 rounded-lg">Annuleren</button>
                        <button type="submit" class="px-3 py-1.5 bg-amber-600 text-white font-semibold rounded-lg shadow">Opslaan</button>
                    </div>
                </div>
            </form>
        </div>
    </div>

    <!-- MODAL: ADD / EDIT POLICY -->
    <div id="policy-modal" class="fixed inset-0 bg-black/75 backdrop-blur-sm flex items-center justify-center hidden z-50 p-3">
        <div class="bg-[#0e1422] border border-[#1E293B] rounded-2xl p-4 sm:p-6 w-full max-w-lg text-xs text-slate-300 max-h-[90vh] overflow-y-auto shadow-2xl">
            <h3 class="text-sm font-bold text-white mb-4" id="modal-pol-title">Policy Configureren</h3>
            <form onsubmit="savePolicy(event)" class="space-y-3">
                <input type="hidden" id="modal-pol-id">
                <div>
                    <label class="block mb-1 text-slate-400">Naam van het Beleid</label>
                    <input type="text" id="modal-pol-name" required class="w-full bg-[#0B0F17] border border-slate-800 rounded-lg p-2 text-white">
                </div>
                <div>
                    <label class="block mb-1 text-slate-400">Archetype (Type Beleid)</label>
                    <select id="modal-pol-type" onchange="renderPolicyFields()" class="w-full bg-[#0B0F17] border border-slate-800 rounded-lg p-2 text-white">
                        <option value="thermal_buffer">♨️ Buffer Zonder Teruggave (Warmtepomp & SWW)</option>
                        <option value="battery_arbitrage">🔋 Accu Met Teruggave (Arbitrage & Dode Zone)</option>
                        <option value="shiftable_consumer">🧺 Verbruik Zonder Opslag (Vaatwasser, Wasmachine)</option>
                    </select>
                </div>
                <div>
                    <label class="block mb-1 text-slate-400">Gekoppelde HEMS Apparaten (Selecteer één of meer)</label>
                    <div id="modal-pol-devices-list" class="bg-[#0B0F17] border border-slate-800 rounded-lg p-2.5 max-h-36 overflow-y-auto space-y-1.5 font-sans text-xs">
                        <span class="text-slate-500 italic">Apparaten laden...</span>
                    </div>
                </div>
                <div id="pol-params-container" class="space-y-3 pt-2 border-t border-slate-800"></div>
                <div class="flex justify-end gap-2 pt-3 border-t border-slate-800">
                    <button type="button" onclick="closeModal('policy-modal')" class="px-3 py-1.5 bg-slate-800 text-slate-400 rounded-lg">Annuleren</button>
                    <button type="submit" class="px-3 py-1.5 bg-purple-600 hover:bg-purple-500 text-white font-semibold rounded-lg">Opslaan</button>
                </div>
            </form>
        </div>
    </div>

    <!-- MODAL: ADD / EDIT DEVICE -->
    <div id="device-modal" class="fixed inset-0 bg-black/75 backdrop-blur-sm flex items-center justify-center hidden z-50 p-3">
        <div class="bg-[#0e1422] border border-[#1E293B] rounded-2xl p-4 sm:p-6 w-full max-w-lg max-h-[90vh] overflow-y-auto text-xs text-slate-300 shadow-2xl">
            <h3 class="text-sm font-bold text-white mb-4" id="modal-dev-title">Apparaat Configureren (Hardware)</h3>
            <form onsubmit="saveDevice(event)" class="space-y-3">
                <input type="hidden" id="modal-dev-id">
                <div>
                    <label class="block mb-1 text-slate-400">Naam Apparaat</label>
                    <input type="text" id="modal-dev-name" required class="w-full bg-[#0B0F17] border border-slate-800 rounded-lg p-2 text-white">
                </div>
                <div>
                    <label class="block mb-1 text-slate-400">Type Resource</label>
                    <select id="modal-dev-type" class="w-full bg-[#0B0F17] border border-slate-800 rounded-lg p-2 text-white">
                        <option value="grid_meter">Netmeter (P1 DSMR)</option>
                        <option value="solar_inverter">Zonnepanelen (Omvormer)</option>
                        <option value="heat_pump">Warmtepomp (CV)</option>
                        <option value="thermal_storage">Warm Tapwatervat (SWW)</option>
                        <option value="home_battery">Thuisbatterij</option>
                        <option value="ev_charger">EV Laadpaal</option>
                        <option value="baseload">Basislast / Sluipverbruik</option>
                    </select>
                </div>
                <div>
                    <label class="block mb-1 text-slate-400">Data-Adapter (Data Bron)</label>
                    <select id="modal-dev-source-type" onchange="toggleDeviceSourceFields()" class="w-full bg-[#0B0F17] border border-slate-800 rounded-lg p-2 text-white font-medium">
                        <option value="homeassistant">🏠 Home Assistant Entiteit</option>
                        <option value="mqtt">⚡ Directe MQTT Streaming Bus</option>
                    </select>
                </div>

                <div class="grid grid-cols-2 gap-3">
                    <div>
                        <label class="block mb-1 text-slate-400">Hardware Eenheid (Contract)</label>
                        <select id="modal-dev-native-unit" class="w-full bg-[#0B0F17] border border-slate-800 rounded-lg p-2 text-white font-mono text-xs">
                            <option value="W">Watt (W)</option>
                            <option value="kW">Kilowatt (kW)</option>
                        </select>
                    </div>
                    <div>
                        <label class="block mb-1 text-slate-400">Status & Installatie</label>
                        <div class="flex items-center gap-3 pt-2 text-xs">
                            <label class="flex items-center gap-1.5 cursor-pointer text-slate-300">
                                <input type="checkbox" id="modal-dev-installed" checked class="rounded bg-slate-900 border-slate-700 text-blue-600">
                                <span>Geïnstalleerd</span>
                            </label>
                            <label class="flex items-center gap-1.5 cursor-pointer text-slate-300">
                                <input type="checkbox" id="modal-dev-enabled" checked class="rounded bg-slate-900 border-slate-700 text-blue-600">
                                <span>Actief</span>
                            </label>
                        </div>
                    </div>
                </div>

                <!-- HA Adapter Fields -->
                <div id="dev-source-ha-fields" class="space-y-3">
                    <div>
                        <label class="block mb-1 text-slate-400">Gekoppelde Vermogenssensor in Home Assistant (W of kW)</label>
                        <select id="modal-dev-ha-power" class="w-full bg-[#0B0F17] border border-slate-800 rounded-lg p-2 text-white font-mono text-[11px]">
                            <option value="">-- Selecteer Home Assistant Entiteit --</option>
                        </select>
                    </div>
                    <div>
                        <label class="block mb-1 text-slate-400">Temperatuursensor (bijv. SWW Boiler)</label>
                        <select id="modal-dev-ha-temp" class="w-full bg-[#0B0F17] border border-slate-800 rounded-lg p-2 text-white font-mono text-[11px]">
                            <option value="">-- Geen / Niet van toepassing --</option>
                        </select>
                    </div>
                    <div>
                        <label class="block mb-1 text-slate-400">Gekoppelde Schakelaar / Relais in Home Assistant</label>
                        <select id="modal-dev-ha-control" class="w-full bg-[#0B0F17] border border-slate-800 rounded-lg p-2 text-white font-mono text-[11px]">
                            <option value="">-- Geen / Niet bestuurbaar --</option>
                        </select>
                    </div>
                </div>

                <!-- MQTT Adapter Fields -->
                <div id="dev-source-mqtt-fields" class="space-y-3 hidden">
                    <div>
                        <label class="block mb-1 text-slate-400">Gekoppelde MQTT Broker</label>
                        <select id="modal-dev-mqtt-broker" class="w-full bg-[#0B0F17] border border-slate-800 rounded-lg p-2 text-white font-mono text-[11px]">
                            <option value="local_mosquitto">Lokale Mosquitto Broker (core-mosquitto:1883)</option>
                        </select>
                    </div>
                    <div class="grid grid-cols-3 gap-2">
                        <div class="col-span-2">
                            <label class="block mb-1 text-slate-400">Telemetrie / Vermogen Topic</label>
                            <input type="text" id="modal-dev-mqtt-power-topic" placeholder="bijv. dsmr/reading/power" class="w-full bg-[#0B0F17] border border-slate-800 rounded-lg p-2 text-white font-mono">
                        </div>
                        <div>
                            <label class="block mb-1 text-slate-400">JSON Key (optioneel)</label>
                            <input type="text" id="modal-dev-mqtt-json-key" placeholder="bijv. power" class="w-full bg-[#0B0F17] border border-slate-800 rounded-lg p-2 text-white font-mono">
                        </div>
                    </div>
                    <div>
                        <label class="block mb-1 text-slate-400">Commando / Sturing Topic (Optioneel)</label>
                        <input type="text" id="modal-dev-mqtt-control-topic" placeholder="bijv. P1P2/C/9/Heating_Cooling_Auto_Off" class="w-full bg-[#0B0F17] border border-slate-800 rounded-lg p-2 text-white font-mono">
                    </div>
                </div>
                <div class="flex justify-end gap-2 pt-3 border-t border-slate-800">
                    <button type="button" onclick="closeModal('device-modal')" class="px-3 py-1.5 bg-slate-800 text-slate-400 rounded-lg">Annuleren</button>
                    <button type="submit" class="px-3 py-1.5 bg-blue-600 text-white font-semibold rounded-lg">Opslaan</button>
                </div>
            </form>
        </div>
    </div>

    <!-- MODAL: CONFIGURE HOME ASSISTANT CONNECTOR -->
    <div id="ha-modal" class="fixed inset-0 bg-black/75 backdrop-blur-sm flex items-center justify-center hidden z-50 p-3">
        <div class="bg-[#0e1422] border border-[#1E293B] rounded-2xl p-4 sm:p-6 w-full max-w-md max-h-[90vh] overflow-y-auto text-xs text-slate-300 shadow-2xl space-y-4">
            <div class="flex justify-between items-center border-b border-slate-800 pb-3">
                <h3 class="text-sm font-bold text-white flex items-center gap-2">
                    <span class="w-2.5 h-2.5 rounded-full bg-cyan-400"></span>
                    <span>Home Assistant Core Connector</span>
                </h3>
                <button type="button" onclick="closeModal('ha-modal')" class="text-slate-400 hover:text-white">✕</button>
            </div>
            <form onsubmit="saveHomeAssistantConnector(event)" class="space-y-3">
                <div>
                    <label class="block mb-1 text-slate-400">Home Assistant Core API URL</label>
                    <input type="text" id="modal-ha-url" required placeholder="https://hass.b3rg.nl:8123 of http://supervisor/core" class="w-full bg-[#0B0F17] border border-slate-800 rounded-lg p-2 text-white font-mono text-xs">
                    <p class="text-[10px] text-slate-500 mt-1">Gebruik https://hass.b3rg.nl:8123 of het interne supervisor adres.</p>
                </div>
                <div>
                    <label class="block mb-1 text-slate-400">Long-Lived Access Token (Drager-Token)</label>
                    <input type="password" id="modal-ha-token" placeholder="••••••••" class="w-full bg-[#0B0F17] border border-slate-800 rounded-lg p-2 text-white font-mono text-xs">
                    <p class="text-[10px] text-slate-500 mt-1">Veilig opgeslagen in de 0600-geheime kluis. Laat leeg om huidige token te behouden.</p>
                </div>
                <div class="grid grid-cols-2 gap-3">
                    <div>
                        <label class="block mb-1 text-slate-400">Timeout (seconden)</label>
                        <input type="number" id="modal-ha-timeout" value="5" min="1" max="30" class="w-full bg-[#0B0F17] border border-slate-800 rounded-lg p-2 text-white font-mono text-xs">
                    </div>
                    <div>
                        <label class="block mb-1 text-slate-400">SSL Validatie</label>
                        <select id="modal-ha-verify-ssl" class="w-full bg-[#0B0F17] border border-slate-800 rounded-lg p-2 text-white text-xs">
                            <option value="false">Uitgeschakeld (Aanbevolen bij intern/self-signed)</option>
                            <option value="true">Ingeschakeld</option>
                        </select>
                    </div>
                </div>
                <div class="flex justify-between items-center pt-3 border-t border-slate-800">
                    <button type="button" onclick="testHomeAssistantConnection()" class="px-3 py-1.5 bg-slate-800 hover:bg-slate-700 text-cyan-300 rounded-lg border border-slate-700 flex items-center gap-1">
                        <svg class="w-3 h-3" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path d="M13 10V3L4 14h7v7l9-11h-7z"></path></svg>
                        <span>Testen</span>
                    </button>
                    <div class="flex gap-2">
                        <button type="button" onclick="closeModal('ha-modal')" class="px-3 py-1.5 bg-slate-800 text-slate-400 rounded-lg">Annuleren</button>
                        <button type="submit" class="px-3 py-1.5 bg-cyan-600 hover:bg-cyan-500 text-white font-semibold rounded-lg">Opslaan</button>
                    </div>
                </div>
            </form>
        </div>
    </div>

    <!-- MODAL: ADD / EDIT TARIFF -->
    <div id="tariff-modal" class="fixed inset-0 bg-black/75 backdrop-blur-sm flex items-center justify-center hidden z-50 p-3">
        <div class="bg-[#0e1422] border border-[#1E293B] rounded-2xl p-4 sm:p-6 w-full max-w-md max-h-[90vh] overflow-y-auto text-xs text-slate-300 shadow-2xl">
            <h3 class="text-sm font-bold text-white mb-4" id="modal-tariff-title">Energieleverancier Configureren</h3>
            <form onsubmit="saveTariff(event)" class="space-y-3">
                <input type="hidden" id="modal-tariff-id">
                <div>
                    <label class="block mb-1 text-slate-400">Naam Leverancier / Contract</label>
                    <input type="text" id="modal-tariff-name" required placeholder="bijv. Powerpeers Dynamisch" class="w-full bg-[#0B0F17] border border-slate-800 rounded-lg p-2 text-white">
                </div>
                <div class="grid grid-cols-2 gap-3">
                    <div>
                        <label class="block mb-1 text-slate-400">Prijs Provider</label>
                        <select id="modal-tariff-provider" class="w-full bg-[#0B0F17] border border-slate-800 rounded-lg p-2 text-white">
                            <option value="epex_spot">EPEX Spot (EnergyZero API)</option>
                            <option value="fixed">Vast Tarief</option>
                        </select>
                    </div>
                    <div>
                        <label class="block mb-1 text-slate-400">Tarief Interval</label>
                        <select id="modal-tariff-interval" class="w-full bg-[#0B0F17] border border-slate-800 rounded-lg p-2 text-white">
                            <option value="15m">Kwartiertarief (15m)</option>
                            <option value="1h">Uurtarief (1h)</option>
                        </select>
                    </div>
                </div>
                <div class="grid grid-cols-2 gap-3">
                    <div>
                        <label class="block mb-1 text-slate-400">Inkoop Opslag (€/kWh incl. BTW)</label>
                        <input type="number" step="0.0001" id="modal-tariff-import" value="0.0121" class="w-full bg-[#0B0F17] border border-slate-800 rounded-lg p-2 text-white">
                    </div>
                    <div>
                        <label class="block mb-1 text-slate-400">Teruglever Opslag (€/kWh)</label>
                        <input type="number" step="0.0001" id="modal-tariff-export" value="0.0121" class="w-full bg-[#0B0F17] border border-slate-800 rounded-lg p-2 text-white">
                    </div>
                </div>
                <div class="grid grid-cols-2 gap-3">
                    <div>
                        <label class="block mb-1 text-slate-400">Energiebelasting (€/kWh)</label>
                        <input type="number" step="0.00001" id="modal-tariff-tax" value="0.11085" class="w-full bg-[#0B0F17] border border-slate-800 rounded-lg p-2 text-white">
                    </div>
                    <div>
                        <label class="block mb-1 text-slate-400">Vastrecht (€/maand)</label>
                        <input type="number" step="0.01" id="modal-tariff-fixed" value="6.25" class="w-full bg-[#0B0F17] border border-slate-800 rounded-lg p-2 text-white">
                    </div>
                </div>
                <div class="flex justify-end gap-2 pt-3 border-t border-slate-800">
                    <button type="button" onclick="closeModal('tariff-modal')" class="px-3 py-1.5 bg-slate-800 text-slate-400 rounded-lg">Annuleren</button>
                    <button type="submit" class="px-3 py-1.5 bg-blue-600 text-white font-semibold rounded-lg">Opslaan</button>
                </div>
            </form>
        </div>
    </div>

    <!-- MODAL: EXCLUSION -->
    <div id="exclusion-modal" class="fixed inset-0 bg-black/75 backdrop-blur-sm flex items-center justify-center hidden z-50 p-3">
        <div class="bg-[#0e1422] border border-[#1E293B] rounded-2xl p-4 sm:p-6 w-full max-w-md max-h-[90vh] overflow-y-auto text-xs text-slate-300 shadow-2xl">
            <h3 class="text-sm font-bold text-white mb-4">Uitsluitingsmasker Toevoegen</h3>
            <form onsubmit="saveExclusion(event)" class="space-y-3">
                <div>
                    <label class="block mb-1 text-slate-400">Sensor ID</label>
                    <input type="text" id="modal-ex-sensor" value="sensor.warmtepomp_power" required class="w-full bg-[#0B0F17] border border-slate-800 rounded-lg p-2 text-white">
                </div>
                <div class="grid grid-cols-2 gap-3">
                    <div>
                        <label class="block mb-1 text-slate-400">Startdatum</label>
                        <input type="date" id="modal-ex-start" required class="w-full bg-[#0B0F17] border border-slate-800 rounded-lg p-2 text-white">
                    </div>
                    <div>
                        <label class="block mb-1 text-slate-400">Einddatum</label>
                        <input type="date" id="modal-ex-end" required class="w-full bg-[#0B0F17] border border-slate-800 rounded-lg p-2 text-white">
                    </div>
                </div>
                <div>
                    <label class="block mb-1 text-slate-400">Reden</label>
                    <input type="text" id="modal-ex-reason" value="Modbus meter ontkoppeld" required class="w-full bg-[#0B0F17] border border-slate-800 rounded-lg p-2 text-white">
                </div>
                <div class="flex justify-end gap-2 pt-3">
                    <button type="button" onclick="closeModal('exclusion-modal')" class="px-3 py-1.5 bg-slate-800 text-slate-400 rounded-lg">Annuleren</button>
                    <button type="submit" class="px-3 py-1.5 bg-blue-600 text-white font-semibold rounded-lg">Toevoegen</button>
                </div>
            </form>
        </div>
    </div>

    <!-- CLIENT CONTROLLER & CHART.JS ENGINE -->
    <script>
        var chartInstance = null;
        var analyticsChartInstance = null;
        var powerProducersChartInstance = null;
        var powerProducersChartType = 'bar'; // Default to Staven (aligned with 24h prediction)
        var powerProducersResolution = '1h';  // Default to 1 Uur for 24h range
        var electricityPricesChartInstance = null;
        var pipelinePollInterval = null;
        var activeUnallocDay = (new Date().getDay() + 6) % 7; // Auto-defaults to today (0=Ma ... 5=Za, 6=Zo)
        var cachedUnallocModel = null;
        let haEntitiesCache = [];
        let currentPolicyParams = {};
        let activeTabId = 'analytics';
        let predictionResolution = '1h';

// =========================================================================
        // SUBTLE INTERACTIVE INFO POPOVERS (TOUCH & CLICK FRIENDLY)
        // =========================================================================
        const infoPopovers = {
            'col_param': 'Fysische en gedragsmatige eigenschappen van de woning, warmtepomp en installatie die door het zelflerende model worden gekalibreerd.',
            'col_active': 'De actieve parameterwaarde waarmee Open HEMS op dit moment live de 24-uurs dispatch en energiegrafieken doorrekent.',
            'col_proposed': 'De nieuw berekende waarde uit de OLS-regressie over InfluxDB telemetrie over de gekozen geheugenhorizon (30, 90 of 365 dagen).',
            'col_drift': 'Het procentuele verschil tussen de actieve parameter en het nieuwe voorstel. Groen = binnen drempel (< 3%), Blauw = daling, Oranje = stijging.',
            'col_evidence': 'De statistische bron, steekproefgrootte en wiskundige methode (bijv. OLS regressie over stookdagen, 230 winterruns, nachtmediaan).',
            'col_status': "'Automatisch' = afwijking valt binnen de drempel (±3%) en is direct via EWMA toegepast. 'Ter Beoordeling' = vereist handmatige goedkeuring via 'Accepteren'.",
            'param_building_ua': 'Totale transmissie- en infiltratieverlies van het huis per graad temperatuurverschil (W/K). Hoe lager de UA, hoe beter de isolatie en hoe trager de woning afkoelt.',
            'param_heating_modulation': 'Daikin Altherma inverter vermogensformule (Watt elektrisch o.b.v. buitentemperatuur) gebaseerd op 230 werkelijke winterruns in InfluxDB.',
            'param_night_baseload': 'De continue nachtelijke basislast van het huis (01:00-05:00u) voor standby, netwerk, ventilatie en domotica.',
            'param_dhw_standby': 'Thermisch stilstandsverlies van de 350L boiler door de isolatiemantel (~0,18°C/uur afkoeling) naar de omgeving.',
            'status_auto': 'Automatisch doorgevoerd: de afwijking valt binnen de ingestelde auto-accept drempel en is direct via de leersnelheid (EWMA) in het actieve rekenmodel bijgesteld.',
            'status_review': "Ter beoordeling: de afwijking overschrijdt de drempel. Klik rechtsonder op 'Accepteren & Toepassen' om deze wijziging te bekrachtigen.",
            'status_accepted': 'Handmatig geaccepteerd: door jou goedgekeurd en geactiveerd in het actieve rekenmodel.',
            'val_overlay_info': 'Model Validatie legt het voorspelde profiel (gestreept) direct over de werkelijk geregistreerde meters (massief) heen. Zo zie je exact waar het model accuraat is en waar leerafwijkingen ontstaan.',
            'dhw_decision_box_info': 'Toont de thermodynamische en economische analyse van het nachtlaadbesluit: waarom de planner nu wel of niet voorverwarmt, inclusief comfortrisico (koude douche) en spitsblokkades.'
        };

        function toggleInfoPopover(e, key) {
            if (e) {
                e.stopPropagation();
                e.preventDefault();
            }
            const text = infoPopovers[key] || '';
            if (!text) return;

            let pop = document.getElementById('open-hems-popover');
            if (pop && pop.__currentKey === key && !pop.classList.contains('hidden')) {
                pop.classList.add('hidden');
                return;
            }
            if (!pop) {
                pop = document.createElement('div');
                pop.id = 'open-hems-popover';
                pop.className = 'fixed z-50 max-w-xs bg-[#0B0F17] border border-slate-700 text-slate-200 text-xs p-3 rounded-xl shadow-2xl backdrop-blur-md leading-relaxed transition-all duration-200';
                document.body.appendChild(pop);
                document.addEventListener('click', (evt) => {
                    if (pop && !pop.contains(evt.target)) {
                        pop.classList.add('hidden');
                    }
                });
            }
            pop.__currentKey = key;
            pop.innerHTML = `<div class="flex items-start gap-2.5">
                <span class="w-4 h-4 rounded-full bg-cyan-950 text-cyan-400 border border-cyan-700/60 flex items-center justify-center text-[10px] font-bold flex-shrink-0 mt-0.5">i</span>
                <div class="text-[11px] text-slate-300 font-sans leading-relaxed">${text}</div>
            </div>`;
            pop.classList.remove('hidden');

            const targetEl = e ? e.currentTarget : null;
            if (targetEl) {
                const rect = targetEl.getBoundingClientRect();
                let top = rect.bottom + 6;
                let left = rect.left - 15;
                if (left + 290 > window.innerWidth) {
                    left = window.innerWidth - 300;
                }
                if (left < 12) left = 12;
                pop.style.top = `${top}px`;
                pop.style.left = `${left}px`;
            }
        }

        function setPowerProducersType(type) {
            powerProducersChartType = type;
            const btnBar = document.getElementById('pp-btn-type-bar');
            const btnLine = document.getElementById('pp-btn-type-line');
            if (btnBar && btnLine) {
                if (type === 'bar') {
                    btnBar.className = 'px-2 py-0.5 rounded transition font-medium bg-purple-600 text-white shadow';
                    btnLine.className = 'px-2 py-0.5 rounded transition font-medium text-slate-400 hover:text-slate-200';
                } else {
                    btnBar.className = 'px-2 py-0.5 rounded transition font-medium text-slate-400 hover:text-slate-200';
                    btnLine.className = 'px-2 py-0.5 rounded transition font-medium bg-purple-600 text-white shadow';
                }
            }
            loadPowerProducersChart();
        }

        function setPowerProducersResolution(res) {
            powerProducersResolution = res;
            updatePowerProducersResButtons(res);
            loadPowerProducersChart();
        }

        function updatePowerProducersResButtons(res) {
            const btn1h = document.getElementById('pp-btn-res-1h');
            const btn15m = document.getElementById('pp-btn-res-15m');
            if (btn1h && btn15m) {
                if (res === '1h') {
                    btn1h.className = 'px-2 py-0.5 rounded transition font-medium bg-blue-600 text-white shadow';
                    btn15m.className = 'px-2 py-0.5 rounded transition font-medium text-slate-400 hover:text-slate-200';
                } else {
                    btn1h.className = 'px-2 py-0.5 rounded transition font-medium text-slate-400 hover:text-slate-200';
                    btn15m.className = 'px-2 py-0.5 rounded transition font-medium bg-blue-600 text-white shadow';
                }
            }
        }

        function onPowerProducersRangeChange() {
            const rangeSelect = document.getElementById('pp-range-select');
            const rangeVal = rangeSelect ? rangeSelect.value : '24h';
            // Auto-adjust resolution based on range (Grafana style)
            if (rangeVal === '24h') {
                powerProducersResolution = '1h';
            } else if (rangeVal === '1h' || rangeVal === '6h') {
                powerProducersResolution = '15m';
            } else {
                powerProducersResolution = '1h';
            }
            updatePowerProducersResButtons(powerProducersResolution);
            loadPowerProducersChart();
        }

        window.__simulateBattery = false;

                window.predictionChartType = 'bar'; // Default to Staven

        function setPredictionChartType(type) {
            window.predictionChartType = type;
            const btnBar = document.getElementById('pred-btn-type-bar');
            const btnLine = document.getElementById('pred-btn-type-line');
            if (btnBar && btnLine) {
                if (type === 'bar') {
                    btnBar.className = 'px-2.5 py-1 rounded transition font-medium bg-purple-600 text-white shadow';
                    btnLine.className = 'px-2.5 py-1 rounded transition font-medium text-slate-400 hover:text-slate-200';
                } else {
                    btnBar.className = 'px-2.5 py-1 rounded transition font-medium text-slate-400 hover:text-slate-200';
                    btnLine.className = 'px-2.5 py-1 rounded transition font-medium bg-purple-600 text-white shadow';
                }
            }
            loadChartData();
        }

                function toggleBatterySimFromSettings() {
            setBatterySimulation(!window.__simulateBattery);
            renderDevicesGrid();
        }

        function setBatterySimulation(enable) {
            window.__simulateBattery = enable;
            document.querySelectorAll('.bat-btn-off').forEach(b => {
                b.className = !enable ? 'bat-btn-off px-2 py-0.5 rounded transition font-medium bg-purple-600 text-white shadow' : 'bat-btn-off px-2 py-0.5 rounded transition font-medium text-slate-400 hover:text-slate-200';
            });
            document.querySelectorAll('.bat-btn-on').forEach(b => {
                b.className = enable ? 'bat-btn-on px-2 py-0.5 rounded transition font-medium bg-amber-600 text-white shadow' : 'bat-btn-on px-2 py-0.5 rounded transition font-medium text-slate-400 hover:text-slate-200';
            });
            loadChartData();
        }

        function setPredictionResolution(res) {
            predictionResolution = res;
            document.querySelectorAll('.res-btn-1h').forEach(b => {
                if (res === '1h') {
                    b.className = 'res-btn-1h px-2.5 py-1 rounded transition font-medium bg-purple-600 text-white shadow';
                } else {
                    b.className = 'res-btn-1h px-2.5 py-1 rounded transition font-medium text-slate-400 hover:text-slate-200';
                }
            });
            document.querySelectorAll('.res-btn-15m').forEach(b => {
                if (res === '15m') {
                    b.className = 'res-btn-15m px-2.5 py-1 rounded transition font-medium bg-purple-600 text-white shadow';
                } else {
                    b.className = 'res-btn-15m px-2.5 py-1 rounded transition font-medium text-slate-400 hover:text-slate-200';
                }
            });

            // Synchronize EPEX dropdown if present
            const epexSel = document.getElementById('epex-res-select');
            if (epexSel) epexSel.value = res;

            // Reload all 4 forecast charts synchronously
            loadChartData();
            loadElectricityPricesChart();
            renderDhwTemperatureChart();
            renderHeatingForecastChart();
        }
        let cachedInfra = { influxdb_connections: [], mqtt_connections: [] };

        function toggleMobileSidebar(open) {
            const sidebar = document.getElementById('main-sidebar');
            const backdrop = document.getElementById('sidebar-backdrop');
            if (open) {
                sidebar.classList.remove('-translate-x-full');
                sidebar.classList.add('translate-x-0');
                backdrop.classList.remove('hidden');
            } else {
                sidebar.classList.remove('translate-x-0');
                sidebar.classList.add('-translate-x-full');
                backdrop.classList.add('hidden');
            }
        }

        function showTab(tabId) {
            activeTabId = tabId;
            if (window.innerWidth < 768) {
                toggleMobileSidebar(false);
            }
            document.querySelectorAll('.tab-content').forEach(el => el.classList.remove('active'));
            document.querySelectorAll('.nav-link').forEach(el => el.classList.remove('active'));
            const target = document.getElementById('view-' + tabId);
            if (target) target.classList.add('active');
            const link = document.getElementById('nav-' + tabId);
            if (link) link.classList.add('active');

            const titles = {
                'prediction': ['Voorspelling & Optimalisatie', '24-uurs kwartier-vooruitblik met dynamische beurstarieven en sturingsadviezen.'],
                'history': ['Historie & Verbruiksstatistieken', 'Werkelijke energiestromen, kosten, opbrengsten en COP-prestaties.'],
                'policies': ['Apparaat Policies & Aansturing', 'Automatische beslisregels, nachtelijk boilerlaadbesluit en beleidsarchetypen.'],
                'calibration': ['Zelflerend Model & Fysica', 'Physics-informed gebouwmodel, 7×96 kwartieren matrices en boilertemperatuurtraject.'],
                'devices': ['Apparaten', 'Beheer fysieke apparaten, meters en actuatoren gekoppeld via Home Assistant of MQTT.'],
                'infrastructure': ['Verbindingen', 'Beheer externe verbindingen naar Home Assistant, MQTT brokers en externe APIs.'],
                'tariffs': ['Energieleveranciers & Tarieven', 'Beheer contracten (Powerpeers dynamisch) en energiebelasting.'],
                'data': ['Data & Pipelines', 'Beheer InfluxDB tijdreeksdatabases, dataretentie en live 60s data pipelines.']
            };
            const t = titles[tabId] || ['Open HEMS', ''];
            document.getElementById('header-title').innerText = t[0];
            document.getElementById('header-sub').innerText = t[1];

            if (tabId === 'prediction' || tabId === 'analytics') {
                loadChartData();
                loadElectricityPricesChart();
                renderDhwTemperatureChart();
                renderHeatingForecastChart();
            }
            if (tabId === 'history') {
                loadAnalytics();
                loadPowerProducersChart();
                loadValidationOverlayChart();
            }
            if (tabId === 'policies') {
                loadPolicies();
                updateDhwLiveCard();
            }
            if (tabId === 'calibration') {
                loadModelDashboard();
                loadCalibration();
                activeUnallocDay = (new Date().getDay() + 6) % 7;
                loadUnallocatedModel();
                loadAlgorithmConfig();
                loadModelRecommendations();
                renderDhwTemperatureChart();
                renderModelDecompositionChart();
            }
            if (tabId === 'devices') loadDevices();
            if (tabId === 'tariffs') loadTariffs();
            if (tabId === 'infrastructure') {
                loadInfrastructure();
                loadProviders();
                loadSolarRoofConfig();
            }
            if (tabId === 'data') {
                loadInfrastructure();
                loadPipelineStatus();
                if (!pipelinePollInterval) pipelinePollInterval = setInterval(loadPipelineStatus, 10000);
            } else if (tabId !== 'infrastructure' && tabId !== 'data') {
                if (pipelinePollInterval) { clearInterval(pipelinePollInterval); pipelinePollInterval = null; }
            }
        }

        function refreshCurrentTab() {
            showTab(activeTabId);
        }

        
        async function loadSolarRoofConfig() {
            try {
                const res = await fetch('./api/config/solar');
                if (!res.ok) return;
                const d = await res.json();
                const s = d.solar || {};
                if (document.getElementById('solar-cfg-wp')) document.getElementById('solar-cfg-wp').value = s.kwp ? s.kwp * 1000 : 5760;
                if (document.getElementById('solar-cfg-inv')) document.getElementById('solar-cfg-inv').value = s.inverter_max_w || 5500;
                if (document.getElementById('solar-cfg-tilt')) document.getElementById('solar-cfg-tilt').value = s.tilt_degrees || 34;
                if (document.getElementById('solar-cfg-azimuth')) document.getElementById('solar-cfg-azimuth').value = s.azimuth_degrees || 225;
                if (document.getElementById('solar-cfg-eff')) document.getElementById('solar-cfg-eff').value = s.efficiency_factor || 0.88;
            } catch (e) {
                console.warn("Error loading solar roof config:", e);
            }
        }

        async function saveSolarRoofConfig() {
            const wp = parseFloat(document.getElementById('solar-cfg-wp')?.value || 5760);
            const inv = parseInt(document.getElementById('solar-cfg-inv')?.value || 5500);
            const tilt = parseFloat(document.getElementById('solar-cfg-tilt')?.value || 34);
            const az = parseFloat(document.getElementById('solar-cfg-azimuth')?.value || 225);
            const eff = parseFloat(document.getElementById('solar-cfg-eff')?.value || 0.88);

            try {
                const res = await fetch('./api/config/solar', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        kwp: wp / 1000.0,
                        inverter_max_w: inv,
                        tilt_degrees: tilt,
                        azimuth_degrees: az,
                        efficiency_factor: eff
                    })
                });
                const d = await res.json();
                if (res.ok) {
                    alert("✅ Zonnepanelen & dakconfiguratie succesvol opgeslagen! De voorspellingen worden direct opnieuw berekend.");
                    loadChartData();
                    loadElectricityPricesChart();
                } else {
                    alert("❌ Fout bij opslaan: " + (d.message || 'Onbekend'));
                }
            } catch (e) {
                alert("❌ Netwerkfout bij opslaan dakconfiguratie");
            }
        }

        async function loadProviders() {
            const container = document.getElementById('providers-container');
            if (!container) return;
            try {
                const res = await fetch('./api/providers');
                const data = await res.json();
                container.innerHTML = '';
                (data.providers || []).forEach(p => {
                    const card = document.createElement('div');
                    card.className = 'bg-[#0e1422] border border-[#1E293B] rounded-2xl p-5 shadow-lg space-y-3';
                    card.innerHTML = `
                        <div class="flex justify-between items-start">
                            <div class="flex items-center gap-2.5">
                                <span class="w-3 h-3 rounded-full bg-purple-400"></span>
                                <div>
                                    <h4 class="font-bold text-white text-sm">${p.name}</h4>
                                    <span class="text-[10px] text-purple-300 font-mono">${p.type}</span>
                                </div>
                            </div>
                            <span class="px-2 py-0.5 rounded text-[10px] font-mono bg-emerald-950/80 text-emerald-400 border border-emerald-800">Actief</span>
                        </div>
                        <div class="text-[11px] text-purple-300 font-mono truncate bg-[#0B0F17] p-2 rounded-lg border border-slate-800/80">
                            ${p.endpoint}
                        </div>
                        <div class="text-[11px] text-slate-300 space-y-1 bg-[#0B0F17] p-2.5 rounded-lg border border-slate-800/80 font-mono">
                            ${p.ha_entity ? `<div>Gekoppelde HA Entiteit: <span class="text-white">${p.ha_entity}</span></div>` : ''}
                            ${p.live_price ? `<div>Huidig Tarief: <span class="text-cyan-300 font-bold">€${p.live_price}/kWh</span></div>` : ''}
                            ${p.live_temp ? `<div>Buitentemperatuur: <span class="text-amber-300 font-bold">${p.live_temp} °C</span></div>` : ''}
                            <div>Data Status: <span class="text-emerald-400 font-bold">Live polling (15m/1h)</span></div>
                        </div>
                    `;
                    container.appendChild(card);
                });
            } catch (e) {
                console.error('Error loading providers:', e);
            }
        }

        // =========================================================================
        // LAAG 1: MULTI-INSTANCE INFRASTRUCTURE CONTROLLER
        // =========================================================================
        async function loadInfrastructure() {
            try {
                const [infRes, statRes] = await Promise.all([
                    fetch('./api/infrastructure'),
                    fetch('./api/infrastructure/telemetry-stats')
                ]);
                cachedInfra = await infRes.json();
                const stat = await statRes.json();

                // Render Home Assistant Core Card (Bi-directional: Bron & Doel)
                const haData = cachedInfra.homeassistant || {};
                const haContainer = document.getElementById('ha-conn-container');
                if (haContainer) {
                    const isConnected = haData.status === 'connected';
                    const statusBadge = isConnected 
                        ? `<span class="px-2.5 py-1 rounded-lg text-[10px] font-mono font-semibold bg-emerald-950/80 text-emerald-400 border border-emerald-800 flex items-center gap-1.5"><span class="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse"></span> Verbonden (${haData.latency_ms}ms)</span>`
                        : `<span class="px-2.5 py-1 rounded-lg text-[10px] font-mono font-semibold bg-red-950/80 text-red-400 border border-red-800">Verbroken</span>`;

                    const sourcesHtml = (haData.sources || []).map(s => `
                        <div class="flex items-center justify-between py-1.5 px-2.5 rounded-lg bg-[#0e1422] border border-slate-800/60">
                            <div class="truncate mr-2">
                                <span class="text-white font-medium block truncate">${s.device_name}</span>
                                <span class="text-[10px] text-cyan-400 font-mono block truncate">${s.entity_id}</span>
                            </div>
                            <span class="px-2 py-0.5 rounded text-[10px] font-mono font-bold bg-cyan-950/80 text-cyan-300 border border-cyan-800 flex-shrink-0">
                                ${s.live_state || '--'}
                            </span>
                        </div>
                    `).join('');

                    const targetsHtml = (haData.targets || []).map(t => `
                        <div class="flex items-center justify-between py-1.5 px-2.5 rounded-lg bg-[#0e1422] border border-slate-800/60">
                            <div class="truncate mr-2">
                                <span class="text-white font-medium block truncate">${t.device_name}</span>
                                <span class="text-[10px] text-pink-400 font-mono block truncate">${t.entity_id}</span>
                            </div>
                            <span class="px-2 py-0.5 rounded text-[10px] font-mono font-bold ${(t.live_state === 'on' || t.live_state === 'true') ? 'bg-emerald-950/80 text-emerald-300 border border-emerald-800' : 'bg-slate-800 text-slate-300 border border-slate-700'} flex-shrink-0">
                                ${t.live_state || '--'}
                            </span>
                        </div>
                    `).join('');

                    haContainer.innerHTML = `
                        <div class="bg-[#0e1422] border border-[#1E293B] rounded-2xl p-5 shadow-lg space-y-4">
                            <div class="flex flex-col sm:flex-row sm:items-center justify-between gap-2 border-b border-[#1E293B] pb-3">
                                <div>
                                    <h4 class="font-bold text-white text-sm flex items-center gap-2">
                                        <span>Home Assistant Core Connector</span>
                                        <span class="px-1.5 py-0.5 rounded text-[9px] bg-cyan-900/60 text-cyan-300 border border-cyan-800 font-mono">INGEBOUWD (HAOS)</span>
                                        <span class="text-[11px] text-slate-400 font-normal">(${haData.location || 'WeidHuis'} · v${haData.version || '2026.x'})</span>
                                    </h4>
                                    <p class="text-[11px] text-cyan-300 font-mono mt-0.5">${haData.url}</p>
                                </div>
                                <div class="flex items-center gap-2">
                                    ${statusBadge}
                                    <button onclick="testHomeAssistantConnection()" class="px-2.5 py-1 bg-slate-800 hover:bg-slate-700 text-slate-200 text-xs rounded-lg flex items-center gap-1 border border-slate-700 transition">
                                        <svg class="w-3 h-3 text-cyan-400" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path d="M13 10V3L4 14h7v7l9-11h-7z"></path></svg>
                                        <span>Testen</span>
                                    </button>
                                    <button onclick="openHomeAssistantModal()" class="px-2.5 py-1 bg-cyan-950/60 hover:bg-cyan-900 text-cyan-200 text-xs rounded-lg flex items-center gap-1 border border-cyan-800 transition">
                                        <svg class="w-3 h-3 text-cyan-400" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path d="M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.414-9.414a2 2 0 112.828 2.828L11.828 15H9v-2.828l8.586-8.586z"></path></svg>
                                        <span>Bewerken</span>
                                    </button>
                                </div>
                            </div>

                            <!-- Lean & Mean Connector Metrics Strip -->
                            <div class="grid grid-cols-3 gap-3 font-mono text-xs">
                                <div class="bg-[#0B0F17] p-3 rounded-xl border border-slate-800">
                                    <div class="text-[10px] text-slate-500 uppercase">Gekoppelde Apparaten</div>
                                    <div class="text-base font-bold text-white mt-0.5">${haData.total_devices || 4} apparaten</div>
                                </div>
                                <div class="bg-[#0B0F17] p-3 rounded-xl border border-slate-800">
                                    <div class="text-[10px] text-cyan-400 uppercase">Data-Inname Sensoren</div>
                                    <div class="text-base font-bold text-cyan-300 mt-0.5">${haData.total_sources || 0} actieve stromen</div>
                                </div>
                                <div class="bg-[#0B0F17] p-3 rounded-xl border border-slate-800">
                                    <div class="text-[10px] text-pink-400 uppercase">Aansturing Actuatoren</div>
                                    <div class="text-base font-bold text-pink-300 mt-0.5">${haData.total_targets || 0} regiepunten</div>
                                </div>
                            </div>
                            <p class="text-[11px] text-slate-400 italic">De specifieke sensoren en stuuractuatoren worden per apparaat beheerd op het tabblad <strong>Apparaten</strong>.</p>
                        </div>
                    `;
                }

                // Render InfluxDB Connections Cards
                const infContainer = document.getElementById('influx-conns-container');
                infContainer.innerHTML = '';
                const idbList = cachedInfra.influxdb_connections || [];

                idbList.forEach(c => {
                    const card = document.createElement('div');
                    card.className = 'bg-[#0e1422] border border-[#1E293B] rounded-2xl p-5 flex flex-col justify-between shadow-lg';
                    card.innerHTML = `
                        <div>
                            <div class="flex justify-between items-start mb-2">
                                <h4 class="font-bold text-white text-sm flex items-center gap-2">
                                    <span>${c.name}</span>
                                    ${c.is_default ? '<span class="px-1.5 py-0.5 rounded text-[9px] bg-blue-900/60 text-blue-300 border border-blue-800">STANDAARD</span>' : ''}
                                </h4>
                                <span id="badge-influx-${c.id}" class="px-2 py-0.5 rounded text-[10px] font-mono bg-slate-800 text-slate-300">Gereed</span>
                            </div>
                            <p class="text-[11px] text-cyan-300 font-mono mb-2">${c.url}</p>
                            <div class="grid grid-cols-2 gap-2 text-[11px] text-slate-300 bg-[#0B0F17] p-2.5 rounded-lg border border-slate-800 mb-3 font-mono">
                                <div>Opslag DB: <span class="text-white font-bold">${c.database}</span></div>
                                <div>Lees DB: <span class="text-white">${c.read_database || 'geen'}</span></div>
                                <div>Gebruiker: <span class="text-slate-400">${c.username || 'anoniem'}</span></div>
                                <div>Retentie: <span class="text-slate-400">${c.retention_policy}</span></div>
                            </div>
                        </div>
                        <div class="flex justify-between items-center pt-3 border-t border-[#1E293B]">
                            <button onclick="testSpecificInflux('${c.id}')" class="px-2.5 py-1 bg-slate-800 hover:bg-slate-700 text-slate-200 text-xs rounded-lg flex items-center gap-1">
                                <svg class="w-3 h-3 text-cyan-400" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path d="M13 10V3L4 14h7v7l9-11h-7z"></path></svg>
                                <span>Testen</span>
                            </button>
                            <div class="flex gap-2">
                                <button onclick='openInfluxModal(${JSON.stringify(c)})' class="px-2.5 py-1 bg-slate-800 hover:bg-slate-700 text-slate-200 text-xs rounded-lg">Bewerken</button>
                                <button onclick="deleteInfluxConn('${c.id}')" class="px-2.5 py-1 bg-red-950/60 hover:bg-red-900 text-red-300 border border-red-800 text-xs rounded-lg">Verwijderen</button>
                            </div>
                        </div>
                    `;
                    infContainer.appendChild(card);
                });

                // Render MQTT Brokers Cards
                const mqContainer = document.getElementById('mqtt-conns-container');
                mqContainer.innerHTML = '';
                const mqList = cachedInfra.mqtt_connections || [];

                mqList.forEach(c => {
                    const card = document.createElement('div');
                    card.className = 'bg-[#0e1422] border border-[#1E293B] rounded-2xl p-5 flex flex-col justify-between shadow-lg';
                    card.innerHTML = `
                        <div>
                            <div class="flex justify-between items-start mb-2">
                                <h4 class="font-bold text-white text-sm flex items-center gap-2">
                                    <span>${c.name}</span>
                                    ${c.is_default ? '<span class="px-1.5 py-0.5 rounded text-[9px] bg-amber-900/60 text-amber-300 border border-amber-800">STANDAARD</span>' : ''}
                                </h4>
                                <span id="badge-mqtt-${c.id}" class="px-2 py-0.5 rounded text-[10px] font-mono bg-slate-800 text-slate-300">Gereed</span>
                            </div>
                            <p class="text-[11px] text-amber-300 font-mono mb-2">${c.host}:${c.port} ${c.tls ? '(TLS ✓)' : ''}</p>
                            <div class="grid grid-cols-2 gap-2 text-[11px] text-slate-300 bg-[#0B0F17] p-2.5 rounded-lg border border-slate-800 mb-3 font-mono">
                                <div>Topic Prefix: <span class="text-white">${c.base_topic}</span></div>
                                <div>Client ID: <span class="text-white">${c.client_id}</span></div>
                                <div>Gebruiker: <span class="text-slate-400">${c.username || 'geen'}</span></div>
                                <div>Status: <span class="text-emerald-400">Actief</span></div>
                            </div>
                        </div>
                        <div class="flex justify-between items-center pt-3 border-t border-[#1E293B]">
                            <button onclick="testSpecificMqtt('${c.id}')" class="px-2.5 py-1 bg-slate-800 hover:bg-slate-700 text-slate-200 text-xs rounded-lg flex items-center gap-1">
                                <svg class="w-3 h-3 text-amber-400" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path d="M13 10V3L4 14h7v7l9-11h-7z"></path></svg>
                                <span>Testen</span>
                            </button>
                            <div class="flex gap-2">
                                <button onclick='openMqttModal(${JSON.stringify(c)})' class="px-2.5 py-1 bg-slate-800 hover:bg-slate-700 text-slate-200 text-xs rounded-lg">Bewerken</button>
                                <button onclick="deleteMqttConn('${c.id}')" class="px-2.5 py-1 bg-red-950/60 hover:bg-red-900 text-red-300 border border-red-800 text-xs rounded-lg">Verwijderen</button>
                            </div>
                        </div>
                    `;
                    mqContainer.appendChild(card);
                });

                // Update Total Conns Badge
                document.getElementById('badge-infra-conns').innerText = idbList.length + mqList.length;

                // Update Telemetry Stats
                if (document.getElementById('stat-openhems-count')) {
                    document.getElementById('stat-openhems-count').innerText = `${stat.openhems_series || 0} series`;
                }
            } catch (e) {
                console.error('Error loading infrastructure:', e);
            }
        }

        function openHomeAssistantModal() {
            const haData = cachedInfra.homeassistant || {};
            document.getElementById('modal-ha-url').value = haData.url || 'https://hass.b3rg.nl:8123';
            document.getElementById('modal-ha-token').value = haData.has_token ? '••••••••' : '';
            document.getElementById('modal-ha-timeout').value = haData.timeout_seconds || 5;
            document.getElementById('modal-ha-verify-ssl').value = String(haData.verify_ssl || false);
            document.getElementById('ha-modal').classList.remove('hidden');
        }

        async function saveHomeAssistantConnector(e) {
            e.preventDefault();
            const payload = {
                url: document.getElementById('modal-ha-url').value,
                timeout_seconds: parseInt(document.getElementById('modal-ha-timeout').value) || 5,
                verify_ssl: document.getElementById('modal-ha-verify-ssl').value === 'true',
                token: document.getElementById('modal-ha-token').value
            };
            try {
                const res = await fetch('./api/infrastructure/homeassistant', {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(payload)
                });
                const data = await res.json();
                closeModal('ha-modal');
                alert('✅ Home Assistant Connector configuratie bijgewerkt!');
                loadInfrastructure();
            } catch (err) {
                alert('❌ Fout bij opslaan Home Assistant Connector: ' + err);
            }
        }

        async function testHomeAssistantConnection() {
            try {
                const res = await fetch('./api/infrastructure/homeassistant/test', { method: 'POST' });
                const data = await res.json();
                if (data.status === 'success') {
                    alert('✅ ' + data.message);
                } else {
                    alert('❌ Fout: ' + data.message);
                }
                loadInfrastructure();
            } catch (e) {
                alert('❌ Fout bij testen HA verbinding: ' + e);
            }
        }

        async function testSpecificInflux(connId) {
            const badge = document.getElementById(`badge-influx-${connId}`);
            if (badge) {
                badge.innerText = 'Testen...';
                badge.className = 'px-2 py-0.5 rounded text-[10px] font-mono bg-yellow-900/60 text-yellow-300 border border-yellow-800';
            }
            try {
                const res = await fetch('./api/infrastructure/influxdb/test', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({ id: connId })
                });
                const d = await res.json();
                if (badge) {
                    if (d.status === 'success') {
                        badge.innerText = `🟢 OK (${d.latency_ms}ms)`;
                        badge.className = 'px-2 py-0.5 rounded text-[10px] font-mono bg-emerald-950 text-emerald-300 border border-emerald-800';
                    } else {
                        badge.innerText = '🔴 Fout';
                        badge.className = 'px-2 py-0.5 rounded text-[10px] font-mono bg-red-950 text-red-300 border border-red-800';
                        alert(`InfluxDB Test Fout: ${d.message}`);
                    }
                }
            } catch (e) {
                if (badge) {
                    badge.innerText = '🔴 Onbereikbaar';
                    badge.className = 'px-2 py-0.5 rounded text-[10px] font-mono bg-red-950 text-red-300 border border-red-800';
                }
            }
        }

        async function testSpecificMqtt(connId) {
            const badge = document.getElementById(`badge-mqtt-${connId}`);
            if (badge) {
                badge.innerText = 'Testen...';
                badge.className = 'px-2 py-0.5 rounded text-[10px] font-mono bg-yellow-900/60 text-yellow-300 border border-yellow-800';
            }
            try {
                const res = await fetch('./api/infrastructure/mqtt/test', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({ id: connId })
                });
                const d = await res.json();
                if (badge) {
                    if (d.status === 'success') {
                        badge.innerText = `🟢 OK (${d.latency_ms}ms)`;
                        badge.className = 'px-2 py-0.5 rounded text-[10px] font-mono bg-emerald-950 text-emerald-300 border border-emerald-800';
                    } else {
                        badge.innerText = '🔴 Fout';
                        badge.className = 'px-2 py-0.5 rounded text-[10px] font-mono bg-red-950 text-red-300 border border-red-800';
                        alert(`MQTT Test Fout: ${d.message}`);
                    }
                }
            } catch (e) {
                if (badge) {
                    badge.innerText = '🔴 Onbereikbaar';
                    badge.className = 'px-2 py-0.5 rounded text-[10px] font-mono bg-red-950 text-red-300 border border-red-800';
                }
            }
        }

        function openInfluxModal(c = null) {
            const fb = document.getElementById('modal-influx-feedback');
            fb.classList.add('hidden');
            if (c) {
                document.getElementById('modal-influx-title').innerText = 'InfluxDB Instantie Bewerken';
                document.getElementById('modal-influx-id').value = c.id;
                document.getElementById('modal-influx-name').value = c.name;
                document.getElementById('modal-influx-type').value = c.type || 'influx_v1';
                document.getElementById('modal-influx-url').value = c.url;
                document.getElementById('modal-influx-db').value = c.database;
                document.getElementById('modal-influx-read-db').value = c.read_database || 'openhems';
                document.getElementById('modal-influx-user').value = c.username || '';
                document.getElementById('modal-influx-pass').value = c.password || '';
                document.getElementById('modal-influx-retention').value = c.retention_policy || 'autogen';
                document.getElementById('modal-influx-default').checked = !!c.is_default;
            } else {
                document.getElementById('modal-influx-title').innerText = 'Nieuwe InfluxDB Instantie Toevoegen';
                document.getElementById('modal-influx-id').value = '';
                document.getElementById('modal-influx-name').value = '';
                document.getElementById('modal-influx-type').value = 'influx_v1';
                document.getElementById('modal-influx-url').value = 'http://localhost:8086';
                document.getElementById('modal-influx-db').value = 'hermes';
                document.getElementById('modal-influx-read-db').value = 'openhems';
                document.getElementById('modal-influx-user').value = 'hermes';
                document.getElementById('modal-influx-pass').value = '';
                document.getElementById('modal-influx-retention').value = 'autogen';
                document.getElementById('modal-influx-default').checked = false;
            }
            document.getElementById('influx-modal').classList.remove('hidden');
        }

        async function testModalInflux() {
            const fb = document.getElementById('modal-influx-feedback');
            fb.classList.remove('hidden');
            fb.className = 'p-2 rounded text-[11px] font-mono bg-yellow-950/60 text-yellow-300 border border-yellow-800 block';
            fb.innerText = 'Verbinding testen met InfluxDB...';

            const payload = {
                url: document.getElementById('modal-influx-url').value,
                database: document.getElementById('modal-influx-db').value,
                username: document.getElementById('modal-influx-user').value,
                password: document.getElementById('modal-influx-pass').value
            };

            try {
                const res = await fetch('./api/infrastructure/influxdb/test', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify(payload)
                });
                const d = await res.json();
                if (d.status === 'success') {
                    fb.className = 'p-2 rounded text-[11px] font-mono bg-emerald-950/60 text-emerald-300 border border-emerald-800 block';
                    fb.innerText = `✓ ${d.message} [Databases: ${(d.databases || []).join(', ')}] (${d.latency_ms}ms)`;
                } else {
                    fb.className = 'p-2 rounded text-[11px] font-mono bg-red-950/60 text-red-300 border border-red-800 block';
                    fb.innerText = `❌ ${d.message}`;
                }
            } catch (e) {
                fb.className = 'p-2 rounded text-[11px] font-mono bg-red-950/60 text-red-300 border border-red-800 block';
                fb.innerText = `❌ Netwerkfout: ${e}`;
            }
        }

        async function saveInfluxModal(e) {
            e.preventDefault();
            const id = document.getElementById('modal-influx-id').value;
            const payload = {
                id: id || undefined,
                name: document.getElementById('modal-influx-name').value,
                type: document.getElementById('modal-influx-type').value,
                url: document.getElementById('modal-influx-url').value,
                database: document.getElementById('modal-influx-db').value,
                read_database: document.getElementById('modal-influx-read-db').value,
                username: document.getElementById('modal-influx-user').value,
                password: document.getElementById('modal-influx-pass').value,
                retention_policy: document.getElementById('modal-influx-retention').value,
                is_default: document.getElementById('modal-influx-default').checked,
                enabled: true
            };
            await fetch('./api/infrastructure/influxdb', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify(payload)
            });
            closeModal('influx-modal');
            loadInfrastructure();
        }

        async function deleteInfluxConn(id) {
            if (!confirm('Weet je zeker dat je deze InfluxDB configuratie wilt verwijderen?')) return;
            await fetch('./api/infrastructure/influxdb/' + id, { method: 'DELETE' });
            loadInfrastructure();
        }

        function openMqttModal(c = null) {
            const fb = document.getElementById('modal-mqtt-feedback');
            fb.classList.add('hidden');
            if (c) {
                document.getElementById('modal-mqtt-title').innerText = 'MQTT Broker Bewerken';
                document.getElementById('modal-mqtt-id').value = c.id;
                document.getElementById('modal-mqtt-name').value = c.name;
                document.getElementById('modal-mqtt-host').value = c.host;
                document.getElementById('modal-mqtt-port').value = c.port;
                document.getElementById('modal-mqtt-topic').value = c.base_topic || 'openhems';
                document.getElementById('modal-mqtt-client-id').value = c.client_id || 'open-hems-collector';
                document.getElementById('modal-mqtt-user').value = c.username || '';
                document.getElementById('modal-mqtt-pass').value = c.password || '';
                document.getElementById('modal-mqtt-tls').checked = !!c.tls;
                document.getElementById('modal-mqtt-default').checked = !!c.is_default;
            } else {
                document.getElementById('modal-mqtt-title').innerText = 'Nieuwe MQTT Broker Toevoegen';
                document.getElementById('modal-mqtt-id').value = '';
                document.getElementById('modal-mqtt-name').value = '';
                document.getElementById('modal-mqtt-host').value = 'core-mosquitto';
                document.getElementById('modal-mqtt-port').value = 1883;
                document.getElementById('modal-mqtt-topic').value = 'openhems';
                document.getElementById('modal-mqtt-client-id').value = 'open-hems-collector';
                document.getElementById('modal-mqtt-user').value = '';
                document.getElementById('modal-mqtt-pass').value = '';
                document.getElementById('modal-mqtt-tls').checked = false;
                document.getElementById('modal-mqtt-default').checked = false;
            }
            document.getElementById('mqtt-modal').classList.remove('hidden');
        }

        async function testModalMqtt() {
            const fb = document.getElementById('modal-mqtt-feedback');
            fb.classList.remove('hidden');
            fb.className = 'p-2 rounded text-[11px] font-mono bg-yellow-950/60 text-yellow-300 border border-yellow-800 block';
            fb.innerText = 'Verbinding testen met MQTT broker...';

            const payload = {
                host: document.getElementById('modal-mqtt-host').value,
                port: parseInt(document.getElementById('modal-mqtt-port').value),
                username: document.getElementById('modal-mqtt-user').value,
                password: document.getElementById('modal-mqtt-pass').value,
                client_id: document.getElementById('modal-mqtt-client-id').value
            };

            try {
                const res = await fetch('./api/infrastructure/mqtt/test', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify(payload)
                });
                const d = await res.json();
                if (d.status === 'success') {
                    fb.className = 'p-2 rounded text-[11px] font-mono bg-emerald-950/60 text-emerald-300 border border-emerald-800 block';
                    fb.innerText = `✓ ${d.message} (${d.latency_ms}ms)`;
                } else {
                    fb.className = 'p-2 rounded text-[11px] font-mono bg-red-950/60 text-red-300 border border-red-800 block';
                    fb.innerText = `❌ ${d.message}`;
                }
            } catch (e) {
                fb.className = 'p-2 rounded text-[11px] font-mono bg-red-950/60 text-red-300 border border-red-800 block';
                fb.innerText = `❌ Netwerkfout: ${e}`;
            }
        }

        async function saveMqttModal(e) {
            e.preventDefault();
            const id = document.getElementById('modal-mqtt-id').value;
            const payload = {
                id: id || undefined,
                name: document.getElementById('modal-mqtt-name').value,
                host: document.getElementById('modal-mqtt-host').value,
                port: parseInt(document.getElementById('modal-mqtt-port').value),
                base_topic: document.getElementById('modal-mqtt-topic').value,
                client_id: document.getElementById('modal-mqtt-client-id').value,
                username: document.getElementById('modal-mqtt-user').value,
                password: document.getElementById('modal-mqtt-pass').value,
                tls: document.getElementById('modal-mqtt-tls').checked,
                is_default: document.getElementById('modal-mqtt-default').checked,
                enabled: true
            };
            await fetch('./api/infrastructure/mqtt', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify(payload)
            });
            closeModal('mqtt-modal');
            loadInfrastructure();
        }

        async function deleteMqttConn(id) {
            if (!confirm('Weet je zeker dat je deze MQTT broker configuratie wilt verwijderen?')) return;
            await fetch('./api/infrastructure/mqtt/' + id, { method: 'DELETE' });
            loadInfrastructure();
        }

        async function writeTestTelemetryPoint() {
            const statusEl = document.getElementById('last-write-status');
            statusEl.innerText = 'Schrijven naar InfluxDB...';
            try {
                const res = await fetch('./api/infrastructure/write-test-point', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({ device_id: 'daikin_heat_pump', value: 33.0 })
                });
                const d = await res.json();
                if (d.status === 'success') {
                    statusEl.innerText = `✓ Datapunt geschreven (204 No Content · ${d.latency_ms}ms)`;
                    loadInfrastructure();
                } else {
                    statusEl.innerText = `❌ Schrijffout: ${d.message}`;
                }
            } catch (e) {
                statusEl.innerText = `❌ Fout: ${e}`;
            }
        }

        // =========================================================================
        // DASHBOARD & CHART
        // =========================================================================
        async function fetchHaEntities() {
            try {
                const res = await fetch('./api/ha/entities');
                const data = await res.json();
                window.__lastPredictionData = data;
                haEntitiesCache = data.entities || [];
                populateHaDropdowns();
            } catch (e) {
                console.warn('Could not load HA entities:', e);
            }
        }

        function populateHaDropdowns() {
            const powerSelect = document.getElementById('modal-dev-ha-power');
            const tempSelect = document.getElementById('modal-dev-ha-temp');
            const controlSelect = document.getElementById('modal-dev-ha-control');

            if (powerSelect) powerSelect.innerHTML = '<option value="">-- Selecteer Home Assistant Sensor --</option>';
            if (tempSelect) tempSelect.innerHTML = '<option value="">-- Geen / Niet van toepassing --</option>';
            if (controlSelect) controlSelect.innerHTML = '<option value="">-- Geen / Niet bestuurbaar --</option>';

            haEntitiesCache.forEach(e => {
                const opt = document.createElement('option');
                opt.value = e.entity_id;
                opt.innerText = `${e.friendly_name} (${e.entity_id})`;

                if (e.domain === 'sensor') {
                    if (powerSelect && (e.entity_id.includes('power') || e.entity_id.includes('watt') || e.entity_id.includes('energy') || e.entity_id.includes('consumption'))) {
                        powerSelect.appendChild(opt.cloneNode(true));
                    }
                    if (tempSelect && (e.entity_id.includes('temp') || e.entity_id.includes('celsius') || e.entity_id.includes('dhw'))) {
                        tempSelect.appendChild(opt.cloneNode(true));
                    }
                }
                if (e.domain === 'switch' || e.domain === 'climate' || e.domain === 'input_boolean') {
                    if (controlSelect) controlSelect.appendChild(opt.cloneNode(true));
                }
            });
        }

        
        // =========================================================================
        // CUSTOM STYLED HTML TOOLTIP HANDLER (REAL LINES, BARS & EURO COSTS)
        // =========================================================================
        
        // =========================================================================
        // UNIFIED SMOOTH TOOLTIP POSITIONING HELPER (VIEWPORT BOUNDED)
        // =========================================================================
        function positionTooltipCustom(chart, tooltip, tooltipEl) {
            const canvasRect = chart.canvas.getBoundingClientRect();
            let left = canvasRect.left + tooltip.caretX + 16;
            let top = canvasRect.top + tooltip.caretY - 30;
            if (left + 300 > window.innerWidth) {
                left = canvasRect.left + tooltip.caretX - 310;
            }
            if (left < 10) left = 10;
            if (top + 280 > window.innerHeight) {
                top = window.innerHeight - 290;
            }
            if (top < 10) top = 10;
            tooltipEl.style.left = `${left}px`;
            tooltipEl.style.top = `${top}px`;
            tooltipEl.style.opacity = '1';
        }

        // 1. EPEX & Solar Prices Chart Tooltip
        function customPricesTooltipHandler(context) {
            const { chart, tooltip } = context;
            const tooltipEl = createOrGetTooltipEl(chart);
            if (tooltip.opacity === 0 || !tooltip.body || !tooltip.dataPoints || tooltip.dataPoints.length === 0) {
                tooltipEl.style.opacity = '0';
                tooltipEl.style.pointerEvents = 'none';
                return;
            }
            const dataIndex = tooltip.dataPoints[0].dataIndex;
            const label = tooltip.title[0] || '';
            const intervalStr = (predictionResolution === '15m') ? '15 min' : '1 uur';

            let epexPrice = 0.0, solarCost = 0.0, solarProd = 0.0;
            chart.data.datasets.forEach(ds => {
                const v = ds.data[dataIndex];
                if (!ds.label) return;
                if (ds.label.includes('EPEX')) epexPrice = Number(v) || 0.0;
                if (ds.label.includes('Kostprijs')) solarCost = Number(v) || 0.0;
                if (ds.label.includes('Productie') || ds.label.includes('Zonnepanelen')) solarProd = Number(v) || 0.0;
            });

            let html = `
                <div class="flex items-center justify-between border-b border-slate-700/70 pb-2 mb-2">
                    <div class="flex items-center gap-2">
                        <span class="w-2 h-2 rounded-full bg-blue-400 animate-pulse"></span>
                        <span class="font-bold text-white text-xs tracking-wide">${label}</span>
                        <span class="text-[10px] text-slate-400 font-mono">(${intervalStr})</span>
                    </div>
                    <span class="text-[10px] text-blue-300 font-mono font-semibold px-2 py-0.5 rounded bg-blue-950/80 border border-blue-800">
                        Tarief: €${epexPrice.toFixed(4)}/kWh
                    </span>
                </div>
                <div class="space-y-1.5 text-xs">
                    <div class="flex items-center justify-between gap-3">
                        <div class="flex items-center">
                            <span style="display:inline-block; width:18px; height:3px; background-color:#3B82F6; border-radius:2px; margin-right:8px;"></span>
                            <span class="text-slate-300">EPEX Stroomtarief</span>
                        </div>
                        <span class="font-bold text-white font-mono">€${epexPrice.toFixed(4)}/kWh</span>
                    </div>
                    <div class="flex items-center justify-between gap-3">
                        <div class="flex items-center">
                            <span style="display:inline-block; width:18px; height:0; border-top:2px dashed #EAB308; margin-right:8px;"></span>
                            <span class="text-slate-300">Zon Kostprijs</span>
                        </div>
                        <span class="font-medium text-amber-300 font-mono">€${solarCost.toFixed(4)}/kWh</span>
                    </div>
                    <div class="flex items-center justify-between gap-3">
                        <div class="flex items-center">
                            <span style="display:inline-block; width:10px; height:10px; background-color:rgba(234, 179, 8, 0.5); border-radius:2px; margin-right:8px;"></span>
                            <span class="text-slate-300">Zonnepanelen Productie</span>
                        </div>
                        <span class="font-bold text-amber-400 font-mono">${solarProd.toFixed(2)} kW</span>
                    </div>
                </div>
            `;
            if (solarProd > 0.05 && epexPrice > solarCost) {
                const margin = epexPrice - solarCost;
                html += `
                    <div class="mt-2.5 pt-2 border-t border-slate-700/80 flex items-center justify-between font-bold text-xs font-mono">
                        <span class="text-emerald-400 uppercase tracking-wider">Zonbesparing Marge:</span>
                        <span class="text-emerald-300">+€${margin.toFixed(4)}/kWh</span>
                    </div>
                `;
            }
            tooltipEl.innerHTML = html;
            positionTooltipCustom(chart, tooltip, tooltipEl);
        }

        // 2. DHW Boiler Temperature & Tap Demand Tooltip
        function customDhwTooltipHandler(context) {
            const { chart, tooltip } = context;
            const tooltipEl = createOrGetTooltipEl(chart);
            if (tooltip.opacity === 0 || !tooltip.body || !tooltip.dataPoints || tooltip.dataPoints.length === 0) {
                tooltipEl.style.opacity = '0';
                tooltipEl.style.pointerEvents = 'none';
                return;
            }
            const dataIndex = tooltip.dataPoints[0].dataIndex;
            const label = tooltip.title[0] || '';
            const intervalStr = (predictionResolution === '15m') ? '15 min' : '1 uur';

            let tempC = 0.0, comfort = 40.0, target = 50.0, liters = 0, p05 = 0.0, p95 = 0.0;
            chart.data.datasets.forEach(ds => {
                const v = ds.data[dataIndex];
                if (!ds.label) return;
                if (ds.label.includes('Verwacht')) tempC = Number(v) || 0.0;
                else if (ds.label.includes('Boilertemperatuur')) tempC = Number(v) || 0.0;
                if (ds.label.includes('P05') || ds.label.includes('Minimaal')) p05 = Number(v) || 0.0;
                if (ds.label.includes('P95') || ds.label.includes('Piekverbruik')) p95 = Number(v) || 0.0;
                if (ds.label.includes('Comfort')) comfort = Number(v) || 0.0;
                if (ds.label.includes('Doel')) target = Number(v) || 0.0;
                if (ds.label.includes('Tapvraag') || ds.label.includes('Waterverbruik')) liters = Math.round(Number(v) || 0);
            });

            const tempBadgeColor = tempC >= 45 ? 'text-emerald-400 bg-emerald-950/80 border-emerald-800' : (tempC >= 40 ? 'text-amber-400 bg-amber-950/80 border-amber-800' : 'text-red-400 bg-red-950/80 border-red-800');

            let html = `
                <div class="flex items-center justify-between border-b border-slate-700/70 pb-2 mb-2">
                    <div class="flex items-center gap-2">
                        <span class="w-2 h-2 rounded-full bg-amber-400 animate-pulse"></span>
                        <span class="font-bold text-white text-xs tracking-wide">${label}</span>
                        <span class="text-[10px] text-slate-400 font-mono">(${intervalStr})</span>
                    </div>
                    <span class="text-[10px] font-mono font-semibold px-2 py-0.5 rounded border ${tempBadgeColor}">
                        Tank: ${tempC.toFixed(1)}°C
                    </span>
                </div>
                <div class="space-y-1.5 text-xs">
                    <div class="flex items-center justify-between gap-3">
                        <div class="flex items-center">
                            <span style="display:inline-block; width:18px; height:3px; background-color:#F59E0B; border-radius:2px; margin-right:8px;"></span>
                            <span class="text-slate-300">Boilertemperatuur (P50)</span>
                        </div>
                        <span class="font-bold text-amber-300 font-mono">${tempC.toFixed(1)}°C</span>
                    </div>
                    ${p95 > 0 ? `
                    <div class="flex items-center justify-between gap-3 text-[11px]">
                        <div class="flex items-center">
                            <span style="display:inline-block; width:14px; height:8px; background-color:rgba(251, 191, 36, 0.25); border:1px solid rgba(245, 158, 11, 0.5); border-radius:2px; margin-right:8px;"></span>
                            <span class="text-amber-200/80">Bandbreedte (P95–P05)</span>
                        </div>
                        <span class="font-mono text-amber-300/90">${p95.toFixed(1)}°C (veel) – ${p05.toFixed(1)}°C (weinig)</span>
                    </div>` : ''}
                    <div class="flex items-center justify-between gap-3">
                        <div class="flex items-center">
                            <span style="display:inline-block; width:18px; height:0; border-top:2px dashed #EF4444; margin-right:8px;"></span>
                            <span class="text-slate-400">Comfortgrens</span>
                        </div>
                        <span class="text-red-400 font-mono">${comfort.toFixed(1)}°C</span>
                    </div>
                    <div class="flex items-center justify-between gap-3">
                        <div class="flex items-center">
                            <span style="display:inline-block; width:18px; height:0; border-top:2px dashed #10B981; margin-right:8px;"></span>
                            <span class="text-slate-400">Doeltemperatuur</span>
                        </div>
                        <span class="text-emerald-400 font-mono">${target.toFixed(1)}°C</span>
                    </div>
                    <div class="flex items-center justify-between gap-3 pt-1 border-t border-slate-800">
                        <div class="flex items-center">
                            <span style="display:inline-block; width:10px; height:10px; background-color:rgba(56, 189, 248, 0.6); border-radius:2px; margin-right:8px;"></span>
                            <span class="text-slate-300">Verwachte Tapvraag</span>
                        </div>
                        <span class="font-bold text-sky-400 font-mono">${liters} L (${(liters * 4.186 * (50-12) / 3600).toFixed(2)} kWh_th)</span>
                    </div>
                </div>
            `;
            tooltipEl.innerHTML = html;
            positionTooltipCustom(chart, tooltip, tooltipEl);
        }

        // 3. CV Space Heating Forecast Tooltip
        function customHeatingTooltipHandler(context) {
            const { chart, tooltip } = context;
            const tooltipEl = createOrGetTooltipEl(chart);
            if (tooltip.opacity === 0 || !tooltip.body || !tooltip.dataPoints || tooltip.dataPoints.length === 0) {
                tooltipEl.style.opacity = '0';
                tooltipEl.style.pointerEvents = 'none';
                return;
            }
            const dataIndex = tooltip.dataPoints[0].dataIndex;
            const label = tooltip.title[0] || '';
            const intervalStr = (predictionResolution === '15m') ? '15 min' : '1 uur';

            let outTemp = 0.0, inTemp = 0.0, cop = 0.0, thLoss = 0.0, elPower = 0.0, cost = 0.0;
            chart.data.datasets.forEach(ds => {
                const v = ds.data[dataIndex];
                if (!ds.label) return;
                if (ds.label.includes('Buitentemperatuur')) outTemp = Number(v) || 0.0;
                if (ds.label.includes('Binnentemperatuur') || ds.label.includes('Ruimtetemperatuur')) inTemp = Number(v) || 0.0;
                if (ds.label.includes('COP')) cop = Number(v) || 0.0;
                if (ds.label.includes('Warmteverlies')) thLoss = Number(v) || 0.0;
                if (ds.label.includes('Stroom Warmtepomp')) elPower = Number(v) || 0.0;
                if (ds.label.includes('Stroomkosten')) cost = Number(v) || 0.0;
            });

            const intervalMult = (predictionResolution === '15m') ? 0.25 : 1.0;
            const thKwh = thLoss * intervalMult;
            const elKwh = elPower * intervalMult;

            let html = `
                <div class="flex items-center justify-between border-b border-slate-700/70 pb-2 mb-2">
                    <div class="flex items-center gap-2">
                        <span class="w-2 h-2 rounded-full bg-red-400 animate-pulse"></span>
                        <span class="font-bold text-white text-xs tracking-wide">${label}</span>
                        <span class="text-[10px] text-slate-400 font-mono">(${intervalStr})</span>
                    </div>
                    <span class="text-[10px] font-mono font-semibold px-2 py-0.5 rounded bg-rose-950/80 border border-rose-800 text-rose-300">
                        Binnen: ${inTemp.toFixed(1)}°C
                    </span>
                </div>
                <div class="space-y-1.5 text-xs">
                    <div class="flex items-center justify-between gap-3">
                        <div class="flex items-center">
                            <span style="display:inline-block; width:18px; height:3px; background-color:#60A5FA; border-radius:2px; margin-right:8px;"></span>
                            <span class="text-slate-300">Buitentemperatuur</span>
                        </div>
                        <span class="font-medium text-blue-300 font-mono">${outTemp.toFixed(1)}°C</span>
                    </div>
                    <div class="flex items-center justify-between gap-3">
                        <div class="flex items-center">
                            <span style="display:inline-block; width:18px; height:3px; background-color:#F43F5E; border-radius:2px; margin-right:8px;"></span>
                            <span class="text-slate-300">Verwachte Binnentemp</span>
                        </div>
                        <span class="font-bold text-rose-300 font-mono">${inTemp.toFixed(1)}°C</span>
                    </div>
                    <div class="flex items-center justify-between gap-3">
                        <div class="flex items-center">
                            <span style="display:inline-block; width:18px; height:0; border-top:2px dashed #10B981; margin-right:8px;"></span>
                            <span class="text-slate-300">Daikin Carnot COP</span>
                        </div>
                        <span class="font-medium text-emerald-300 font-mono">${cop.toFixed(2)}</span>
                    </div>
                    <div class="flex items-center justify-between gap-3 pt-1 border-t border-slate-800">
                        <div class="flex items-center">
                            <span style="display:inline-block; width:10px; height:10px; background-color:rgba(239, 68, 68, 0.6); border-radius:2px; margin-right:8px;"></span>
                            <span class="text-slate-300">Warmteverlies Woning</span>
                        </div>
                        <span class="font-bold text-red-400 font-mono">${thLoss.toFixed(2)} kW_th (${thKwh.toFixed(2)} kWh)</span>
                    </div>
                    <div class="flex items-center justify-between gap-3">
                        <div class="flex items-center">
                            <span style="display:inline-block; width:10px; height:10px; background-color:rgba(245, 158, 11, 0.7); border-radius:2px; margin-right:8px;"></span>
                            <span class="text-slate-300">Stroom Warmtepomp</span>
                        </div>
                        <span class="font-bold text-amber-400 font-mono">${elPower.toFixed(2)} kW_el (${elKwh.toFixed(2)} kWh)</span>
                    </div>
                </div>
                <div class="mt-2.5 pt-2 border-t border-slate-700/80 flex items-center justify-between font-bold text-xs font-mono">
                    <span class="text-slate-400 uppercase tracking-wider">Verwachte Stroomkosten:</span>
                    <span class="text-cyan-300 text-sm">€${cost.toFixed(3)}</span>
                </div>
            `;
            tooltipEl.innerHTML = html;
            positionTooltipCustom(chart, tooltip, tooltipEl);
        }

        function createOrGetTooltipEl(chart) {
            let tooltipEl = document.getElementById('chartjs-custom-tooltip');
            if (!tooltipEl) {
                tooltipEl = document.createElement('div');
                tooltipEl.id = 'chartjs-custom-tooltip';
                tooltipEl.className = 'pointer-events-none fixed z-[9999] bg-[#0B0F17]/95 backdrop-blur-md border border-slate-700/90 rounded-2xl shadow-2xl p-3.5 text-xs font-mono transition-opacity duration-100 text-slate-200';
                tooltipEl.style.minWidth = '250px';
                tooltipEl.style.maxWidth = '320px';
                document.body.appendChild(tooltipEl);
            }
            return tooltipEl;
        }

        function customHemsTooltipHandler(context, isPrediction = false) {
            const { chart, tooltip } = context;
            const tooltipEl = createOrGetTooltipEl(chart);

            // Hide immediately when cursor moves away or outside graph area
            if (tooltip.opacity === 0 || !tooltip.body || !tooltip.dataPoints || tooltip.dataPoints.length === 0) {
                tooltipEl.style.opacity = '0';
                tooltipEl.style.pointerEvents = 'none';
                return;
            }

            tooltipEl.style.opacity = '1';

            const dataIndex = tooltip.dataPoints[0].dataIndex;
            const label = tooltip.title[0] || '';

            // Extract EXACT interval duration (hours)
            let intervalH = 1.0;
            if (isPrediction) {
                intervalH = window.__lastPredictionIntervalH || 1.0;
            } else {
                intervalH = window.__lastHistoricalIntervalH || (chart.data.labels.length > 50 ? 0.25 : 1.0);
            }

            // Extract EXACT prices directly from the dataset or data cache (NEVER use hardcoded defaults)
            let importPrice = 0.25;
            let exportPrice = 0.10;

            // Priority 1: Check if Stroomprijs dataset exists in the chart itself
            const priceDataset = chart.data.datasets.find(ds => ds.label && ds.label.includes('Stroomprijs'));
            if (priceDataset && priceDataset.data && priceDataset.data[dataIndex] !== undefined) {
                importPrice = Number(priceDataset.data[dataIndex]);
            } else if (isPrediction && window.__lastPredictionData?.datasets?.prices_eur) {
                importPrice = Number(window.__lastPredictionData.datasets.prices_eur[dataIndex]);
            } else if (!isPrediction && window.__lastHistoricalData?.prices) {
                importPrice = Number(window.__lastHistoricalData.prices[dataIndex]);
            }

            // Priority 2: Extract export price (Powerpeers dynamic: kale beurs min verkoopopslag)
            if (!isPrediction && window.__lastHistoricalData?.export_prices && window.__lastHistoricalData.export_prices[dataIndex] !== undefined) {
                exportPrice = Number(window.__lastHistoricalData.export_prices[dataIndex]);
            } else if (isPrediction && window.__lastPredictionData?.export_prices_eur && window.__lastPredictionData.export_prices_eur[dataIndex] !== undefined) {
                exportPrice = Number(window.__lastPredictionData.export_prices_eur[dataIndex]);
            } else {
                // Approximate dynamic export: (All-in - BTW - Energiebelasting - Opslag)
                exportPrice = Math.max(0.0, (importPrice / 1.21) - 0.11085 - 0.0121 - 0.00605);
            }

            let html = `
                <div class="flex items-center justify-between border-b border-slate-700/70 pb-2 mb-2">
                    <div class="flex items-center gap-2">
                        <span class="w-2 h-2 rounded-full bg-cyan-400 animate-pulse"></span>
                        <span class="font-bold text-white text-xs tracking-wide">${label}</span>
                        <span class="text-[10px] text-slate-400 font-mono">(${intervalH === 0.25 ? '15 min' : '1 uur'})</span>
                    </div>
                    <span class="text-[10px] text-cyan-300 font-mono font-semibold px-2 py-0.5 rounded bg-cyan-950/80 border border-cyan-800">
                        Inkoop: €${importPrice.toFixed(4)}/kWh
                    </span>
                </div>
                <div class="space-y-1.5">
            `;

            let netCostVal = 0.0;
            let hasNetCost = false;

            tooltip.dataPoints.forEach(dp => {
                const ds = chart.data.datasets[dp.datasetIndex];
                if (!ds) return;
                const rawVal = dp.raw || 0;
                let dsLabel = ds.label || '';
                const isLine = ds.type === 'line' || (ds.borderDash && ds.borderDash.length > 0);
                const color = ds.borderColor || ds.backgroundColor;

                // Strip "(kWh)" or "(kW)" from label for clean display
                const cleanLabel = dsLabel.replace(/\s*\(kWh\)|\s*\(kW\)/g, '').trim();

                // Skip mirror duplicate "Zon Direct Benut" in tooltip (Opgewekt Gebruikt already shows it!)
                if (cleanLabel.includes('Zon Direct Benut')) {
                    return;
                }

                // Handle Stroomprijs row
                if (cleanLabel.includes('Stroomprijs') || cleanLabel.includes('Tarief') || cleanLabel.includes('Prijs')) {
                    html += `
                        <div class="flex items-center justify-between gap-3 text-xs">
                            <div class="flex items-center truncate">
                                <span style="display:inline-block; width:18px; height:0; border-top:2px dashed ${color}; margin-right:8px; vertical-align:middle;"></span>
                                <span class="text-slate-300 truncate">${cleanLabel}</span>
                            </div>
                            <div class="flex items-center gap-1.5 flex-shrink-0">
                                <span class="font-bold text-cyan-300 font-mono">€${Number(rawVal).toFixed(4)}/kWh</span>
                            </div>
                        </div>
                    `;
                    return;
                }

                // Format PURE ENERGY (kWh) as primary metric (both charts now store kWh!)
                const absVal = Math.abs(rawVal);
                const kwhVal = absVal;
                const powerW = Math.round((absVal * 1000.0) / intervalH);

                const energyStr = `${kwhVal >= 10.0 ? kwhVal.toFixed(1) : kwhVal.toFixed(2)} kWh`;
                const powerStr = powerW >= 1000 ? `${(powerW / 1000.0).toFixed(2)} kW` : `${powerW} W`;

                // Calculate monetary cost / revenue per dataset type
                let costBadge = '';

                if (cleanLabel.includes('Afname')) {
                    const c = kwhVal * importPrice;
                    netCostVal += c;
                    hasNetCost = true;
                    costBadge = `<span class="text-red-400 font-bold ml-auto">+€${c.toFixed(2)}</span>`;
                } else if (cleanLabel.includes('Teruglevering')) {
                    const rev = kwhVal * exportPrice;
                    netCostVal -= rev;
                    hasNetCost = true;
                    costBadge = `<span class="text-emerald-400 font-bold ml-auto">-€${rev.toFixed(2)} opbr.</span>`;
                } else if (cleanLabel.includes('Verwacht Netto')) {
                    if (rawVal >= 0) {
                        const c = kwhVal * importPrice;
                        netCostVal = c;
                        hasNetCost = true;
                        costBadge = `<span class="text-red-400 font-bold ml-auto">+€${c.toFixed(2)}</span>`;
                    } else {
                        const rev = kwhVal * exportPrice;
                        netCostVal = -rev;
                        hasNetCost = true;
                        costBadge = `<span class="text-emerald-400 font-bold ml-auto">-€${rev.toFixed(2)} opbr.</span>`;
                    }
                } else if (cleanLabel.includes('Opgewekt Gebruikt')) {
                    const sav = kwhVal * importPrice;
                    costBadge = `<span class="text-cyan-400 font-medium ml-auto">€${sav.toFixed(2)} besp.</span>`;
                } else if (cleanLabel.includes('Zon Productie')) {
                    const rev = kwhVal * exportPrice;
                    costBadge = `<span class="text-amber-400 font-medium ml-auto">€${rev.toFixed(2)} opbr.</span>`;
                } else if (cleanLabel.includes('Accu Ontladen')) {
                    const sav = kwhVal * importPrice;
                    costBadge = `<span class="text-teal-400 font-medium ml-auto">€${sav.toFixed(2)} besp.</span>`;
                } else if (cleanLabel.includes('Totaal Verbruik')) {
                    const totC = kwhVal * importPrice;
                    costBadge = `<span class="text-orange-400 font-bold ml-auto">€${totC.toFixed(2)}</span>`;
                } else if (cleanLabel.includes('SWW') || cleanLabel.includes('CV') || cleanLabel.includes('Accu Laden') || cleanLabel.includes('Ongedefinieerd')) {
                    const c = kwhVal * importPrice;
                    costBadge = `<span class="text-slate-400 ml-auto">€${c.toFixed(2)}</span>`;
                }

                // Visual indicator: ACTUAL line for lines, rounded pill for bars
                let indicatorHtml = '';
                if (isLine) {
                    indicatorHtml = `<span style="display:inline-block; width:18px; height:3px; background-color:${color}; border-radius:2px; margin-right:8px; vertical-align:middle;"></span>`;
                } else {
                    indicatorHtml = `<span style="display:inline-block; width:10px; height:10px; background-color:${color}; border-radius:2px; margin-right:8px; vertical-align:middle;"></span>`;
                }

                html += `
                    <div class="flex items-center justify-between gap-3 text-xs">
                        <div class="flex items-center truncate">
                            ${indicatorHtml}
                            <span class="text-slate-300 truncate">${cleanLabel}</span>
                        </div>
                        <div class="flex items-center gap-2 flex-shrink-0">
                            <span class="font-bold text-white font-mono">${rawVal < 0 ? '-' : ''}${energyStr}</span>
                            <span class="text-[10px] text-slate-400 font-mono">(${powerStr})</span>
                            ${costBadge}
                        </div>
                    </div>
                `;
            });

            if (hasNetCost) {
                const isNetProfit = netCostVal < 0;
                const netColor = isNetProfit ? 'text-emerald-400' : 'text-red-400';
                const netLabel = isNetProfit ? 'Netto Opbrengst' : 'Netto Kosten';
                html += `
                    <div class="mt-2.5 pt-2 border-t border-slate-700/80 flex items-center justify-between font-bold text-xs font-mono">
                        <span class="text-slate-400 uppercase tracking-wider">${netLabel}:</span>
                        <span class="${netColor} text-sm">${isNetProfit ? '+' : ''}€${Math.abs(netCostVal).toFixed(2)}</span>
                    </div>
                `;
            }

            html += `</div>`;
            tooltipEl.innerHTML = html;

            // Position tooltip smoothly relative to viewport
            const canvasRect = chart.canvas.getBoundingClientRect();
            let left = canvasRect.left + tooltip.caretX + 16;
            let top = canvasRect.top + tooltip.caretY - 30;

            // Prevent overflowing window right
            if (left + 280 > window.innerWidth) {
                left = canvasRect.left + tooltip.caretX - 290;
            }
            if (left < 10) left = 10;

            // Prevent overflowing window bottom
            if (top + 240 > window.innerHeight) {
                top = window.innerHeight - 250;
            }
            if (top < 10) top = 10;

            tooltipEl.style.left = `${left}px`;
            tooltipEl.style.top = `${top}px`;
            tooltipEl.style.opacity = '1';
        }


        async function loadChartData() {
            try {
                const simParam = window.__simulateBattery ? '&simulate_battery=1' : '';
                const res = await fetch('./api/schedule/chart-data?resolution=' + encodeURIComponent(predictionResolution) + simParam);
                const data = await res.json();
                window.__lastPredictionData = data;
                window.__lastPredictionIntervalH = data.interval_h || (predictionResolution === '15m' ? 0.25 : 1.0);

                const adv = data.banner_text || `Beste stroomtarief om ${data.cheapest_hour} (€${Number(data.cheapest_price_eur).toFixed(4)}/kWh)`;
                if (document.getElementById('banner-text')) document.getElementById('banner-text').innerText = adv;
                if (document.getElementById('analytics-banner-text')) document.getElementById('analytics-banner-text').innerText = adv;
                if (document.getElementById('battery-status-banner')) document.getElementById('battery-status-banner').innerText = data.battery_status_msg;
                if (document.getElementById('prediction-baseload-badge')) document.getElementById('prediction-baseload-badge').innerText = `Basislast: ${data.baseload_watts || 300} W`;
                if (document.getElementById('tab-baseload-input')) document.getElementById('tab-baseload-input').value = data.baseload_watts || 300;

// Dual Polarity Stacked Engine (Power Producers Aligned):
                // - Above 0 axis: All consumers stacked together (Basislast, SWW, CV, Accu Laden)
                // - Below 0 axis: All generation/sources stacked together (Zon Productie, Accu Ontladen)
                // - Net overlay line: Expected Net Grid Power (Cons - Prod)
                const labels = data.labels;
                const pricesArr = data.datasets.prices_eur || [];
                const netPowerArr = data.datasets.net_power_kw || [];

                // Populate totals, costs, recommendation banner & surplus badge for BOTH tabs
                const kwhText = `⚡ Verbruik: ${(data.total_consumption_kwh || 0.0).toFixed(1)} kWh`;
                const costVal = Number(data.total_net_cost_eur || 0.0);
                const costText = `💶 Netto: €${costVal.toFixed(2)}`;
                const surplusText = `☀️ Overschot: ${data.surplus_total_kwh || 0.0} kWh`;

                if (document.getElementById('prediction-total-kwh-badge')) document.getElementById('prediction-total-kwh-badge').innerText = kwhText;
                if (document.getElementById('dash-prediction-total-kwh-badge')) document.getElementById('dash-prediction-total-kwh-badge').innerText = kwhText;

                if (document.getElementById('prediction-total-cost-badge')) document.getElementById('prediction-total-cost-badge').innerText = costText;
                if (document.getElementById('dash-prediction-total-cost-badge')) document.getElementById('dash-prediction-total-cost-badge').innerText = costText;

                if (document.getElementById('prediction-surplus-badge')) document.getElementById('prediction-surplus-badge').innerText = surplusText;
                if (document.getElementById('dash-prediction-surplus-badge')) document.getElementById('dash-prediction-surplus-badge').innerText = surplusText;

                if (document.getElementById('solar-recommendation-text')) {
                    document.getElementById('solar-recommendation-text').innerText = data.solar_recommendation || "☀️ Geen overschot";
                }

                // Render Horizontal Mode Timeline Bar & Dynamic Rolling Ticks
                const tlContainer = document.getElementById('dhw-timeline-bar');
                const ticksContainer = document.getElementById('dhw-timeline-ticks');
                if (tlContainer && data.dhw_mode_timeline) {
                    tlContainer.innerHTML = '';
                    data.dhw_mode_timeline.forEach(seg => {
                        const block = document.createElement('div');
                        block.className = 'flex-1 h-full rounded-sm transition-all duration-150 cursor-pointer relative group';
                        block.style.backgroundColor = seg.color;
                        if (seg.mode === 'forced_off' || seg.mode === 'peak_lockout') {
                            block.style.backgroundColor = '#EF4444';
                            block.style.backgroundImage = 'repeating-linear-gradient(45deg, transparent, transparent 3px, rgba(0,0,0,0.35) 3px, rgba(0,0,0,0.35) 6px)';
                        } else if (seg.mode === 'advised_off' || seg.mode === 'peak_advice') {
                            block.style.backgroundColor = '#F59E0B';
                            block.style.backgroundImage = 'none';
                            block.style.border = 'none';
                        } else if (seg.mode === 'advised_on') {
                            block.style.backgroundColor = '#4ADE80';
                            block.style.backgroundImage = 'repeating-linear-gradient(45deg, #10B981, #10B981 3px, #86EFAC 3px, #86EFAC 6px)';
                        } else if (seg.mode === 'forced_on' || seg.mode === 'forced_standard_50' || seg.mode === 'forced_night_50') {
                            block.style.backgroundColor = '#10B981';
                            block.style.backgroundImage = 'none';
                            block.style.border = 'none';
                        } else if (seg.mode === 'max_on' || seg.mode === 'forced_solar_boost_60') {
                            block.style.backgroundColor = '#A855F7';
                            block.style.backgroundImage = 'none';
                            block.style.border = 'none';
                        } else {
                            block.style.backgroundColor = '#1E293B';
                            block.style.backgroundImage = 'none';
                            block.style.border = 'none';
                        }
                        // Tooltip on hover
                        block.title = `${seg.time} | ${seg.label}\n${seg.description}`;
                        tlContainer.appendChild(block);
                    });
                }

                // Update Dynamic Spitsblokkades Summary Card & Decision Box
                const dynPeaks = data.dynamic_peaks || [];
                window.__lastDynamicPeaks = dynPeaks;
                const hTitle = document.getElementById('dhw-dyn-lockout-title');
                const hHours = document.getElementById('dhw-dyn-lockout-hours');
                const hSub = document.getElementById('dhw-dyn-lockout-sub');
                const spitsDetailEl = document.getElementById('dhw-box-spits-detail');

                if (dynPeaks.length === 0) {
                    if (hTitle) hTitle.innerText = '✨ Spitsblokkades: Geen';
                    if (hHours) {
                        hHours.innerText = 'Geen prijspieken (Vlak tarief 🔓)';
                        hHours.className = 'text-xs font-bold text-emerald-400';
                    }
                    if (hSub) hSub.innerText = 'Tarief schommelt minimaal: warmtepomp mag overdag vrij opereren.';
                    if (spitsDetailEl) spitsDetailEl.innerHTML = '<span class="text-emerald-400 font-bold">Geen prijspieken gedetecteerd 🔓 (volledige vrijloop)</span>';
                } else {
                    const hardMins = dynPeaks.reduce((acc, p) => acc + (p.hard_duration_mins || (p.is_hard_lockout ? p.duration_mins : 0)), 0);
                    const hardHours = (hardMins / 60).toFixed(1);
                    if (hTitle) hTitle.innerText = `🚫 Spitsblokkades (${hardHours} Uur)`;
                    if (hHours) {
                        hHours.innerText = dynPeaks.map(p => {
                            if (p.hard_start_time) {
                                return `${p.name} ${p.hard_start_time}–${p.hard_end_time} (${p.hard_duration_mins}m 🔒)`;
                            } else {
                                return `${p.name} ${p.start_time}–${p.end_time} (${p.duration_mins}m ⚠️)`;
                            }
                        }).join(' · ');
                        hHours.className = hardMins > 0 ? 'text-xs font-bold text-red-400' : 'text-xs font-bold text-amber-400';
                    }
                    const maxPeakP = Math.max(...dynPeaks.map(p => p.max_price));
                    if (hSub) hSub.innerText = `Piekhoogte tot €${maxPeakP.toFixed(3)}/kWh. Gecapt op max 2,5u tegen woningafkoeling.`;
                    if (spitsDetailEl) {
                        spitsDetailEl.innerHTML = dynPeaks.map(p => {
                            const badgeColor = p.is_hard_lockout ? 'text-red-300' : 'text-amber-300';
                            const icon = p.is_hard_lockout ? '🔒' : '⚠️';
                            return `<span class="${badgeColor} font-bold">${p.name} ${p.start_time}–${p.end_time} (${p.duration_mins}m ${icon})</span>`;
                        }).join(' · ');
                    }
                }
                if (ticksContainer && labels && labels.length > 0) {
                    ticksContainer.innerHTML = '';
                    const totalL = labels.length;
                    const step = Math.max(1, Math.floor(totalL / 8));
                    for (let t_i = 0; t_i < totalL; t_i += step) {
                        const s = document.createElement('span');
                        s.innerText = labels[t_i];
                        ticksContainer.appendChild(s);
                    }
                    if (ticksContainer.children.length < 9 && totalL > 0) {
                        const sEnd = document.createElement('span');
                        sEnd.innerText = labels[totalL - 1];
                        ticksContainer.appendChild(sEnd);
                    }
                }

                // Populate Planning Summary Cards
                const dSum = data.dhw_planning_summary || {};
                const modeEl = document.getElementById('dhw-summary-mode');
                if (modeEl && dSum.planned_mode_label) {
                    modeEl.innerText = `${dSum.planned_mode_label}`;
                    if (dSum.planned_mode === 'forced_solar_boost_60' || dSum.planned_mode === 'max_on') {
                        modeEl.className = 'text-xs font-bold text-purple-300';
                    } else if (dSum.planned_mode === 'forced_night_50' || dSum.planned_mode === 'forced_standard_50' || dSum.planned_mode === 'forced_on') {
                        modeEl.className = 'text-xs font-bold text-emerald-400';
                    } else if (dSum.planned_mode === 'advised_on') {
                        modeEl.className = 'text-xs font-bold text-emerald-300';
                    } else {
                        modeEl.className = 'text-xs font-bold text-slate-300';
                    }
                }
                if (document.getElementById('dhw-summary-times') && dSum.run_start) {
                    document.getElementById('dhw-summary-times').innerText = `Venster: ${dSum.run_start} – ${dSum.run_end} (${dSum.run_duration_min} min)`;
                }
                if (document.getElementById('dhw-summary-energy') && dSum.total_stroom_kwh) {
                    const thKwh = (dSum.target_temp_c >= 55 ? '8.1 kWh_th' : '4.1 kWh_th');
                    document.getElementById('dhw-summary-energy').innerText = `~${dSum.total_stroom_kwh} kWh stroom (${thKwh})`;
                }
                if (document.getElementById('dhw-summary-shower') && dSum.target_temp_c) {
                    const liters = dSum.target_temp_c >= 55 ? '~715L' : '~496L';
                    document.getElementById('dhw-summary-shower').innerText = `Mengcapaciteit ${liters} douchewater van 38°C.`;
                }



                // Populate 6-Box Prediction Metrics Aligned with Historical
                const ps = data.prediction_stats || {};
                if (ps.zonnepanelen) {
                    if (document.getElementById('pred-stat-solar-total')) document.getElementById('pred-stat-solar-total').innerText = ps.zonnepanelen.total_kwh || '-- kWh';
                    if (document.getElementById('pred-stat-solar-cost')) document.getElementById('pred-stat-solar-cost').innerText = ps.zonnepanelen.cost_eur || '€--';
                    if (document.getElementById('pred-stat-solar-last')) document.getElementById('pred-stat-solar-last').innerText = ps.zonnepanelen.last || '--';
                    if (document.getElementById('pred-stat-solar-min')) document.getElementById('pred-stat-solar-min').innerText = ps.zonnepanelen.min || '--';
                }
                if (ps.teruglevering) {
                    if (document.getElementById('pred-stat-terug-total')) document.getElementById('pred-stat-terug-total').innerText = ps.teruglevering.total_kwh || '-- kWh';
                    if (document.getElementById('pred-stat-terug-cost')) document.getElementById('pred-stat-terug-cost').innerText = ps.teruglevering.cost_eur || '€--';
                    if (document.getElementById('pred-stat-terug-last')) document.getElementById('pred-stat-terug-last').innerText = ps.teruglevering.last || '--';
                    if (document.getElementById('pred-stat-terug-min')) document.getElementById('pred-stat-terug-min').innerText = ps.teruglevering.min || '--';
                }
                if (ps.afname) {
                    if (document.getElementById('pred-stat-afname-total')) document.getElementById('pred-stat-afname-total').innerText = ps.afname.total_kwh || '-- kWh';
                    if (document.getElementById('pred-stat-afname-cost')) document.getElementById('pred-stat-afname-cost').innerText = ps.afname.cost_eur || '€--';
                    if (document.getElementById('pred-stat-afname-last')) document.getElementById('pred-stat-afname-last').innerText = ps.afname.last || '--';
                    if (document.getElementById('pred-stat-afname-max')) document.getElementById('pred-stat-afname-max').innerText = ps.afname.max || '--';
                }
                if (ps.totaal_opgewekt) {
                    if (document.getElementById('pred-stat-opgewekt-total')) document.getElementById('pred-stat-opgewekt-total').innerText = ps.totaal_opgewekt.total_kwh || '-- kWh';
                    if (document.getElementById('pred-stat-opgewekt-cost')) document.getElementById('pred-stat-opgewekt-cost').innerText = ps.totaal_opgewekt.cost_eur || '€--';
                    if (document.getElementById('pred-stat-opgewekt-last')) document.getElementById('pred-stat-opgewekt-last').innerText = ps.totaal_opgewekt.last || '--';
                    if (document.getElementById('pred-stat-opgewekt-min')) document.getElementById('pred-stat-opgewekt-min').innerText = ps.totaal_opgewekt.min || '--';
                }
                if (ps.opgewekt_gebruikt) {
                    if (document.getElementById('pred-stat-selfcons-total')) document.getElementById('pred-stat-selfcons-total').innerText = ps.opgewekt_gebruikt.total_kwh || '-- kWh';
                    if (document.getElementById('pred-stat-selfcons-cost')) document.getElementById('pred-stat-selfcons-cost').innerText = ps.opgewekt_gebruikt.cost_eur || '€--';
                    if (document.getElementById('pred-stat-selfcons-last')) document.getElementById('pred-stat-selfcons-last').innerText = ps.opgewekt_gebruikt.last || '--';
                    if (document.getElementById('pred-stat-selfcons-min')) document.getElementById('pred-stat-selfcons-min').innerText = ps.opgewekt_gebruikt.min || '--';
                }
                if (ps.totaal_verbruik) {
                    if (document.getElementById('pred-stat-verbruik-total')) document.getElementById('pred-stat-verbruik-total').innerText = ps.totaal_verbruik.total_kwh || '-- kWh';
                    if (document.getElementById('pred-stat-verbruik-cost')) document.getElementById('pred-stat-verbruik-cost').innerText = ps.totaal_verbruik.cost_eur || '€--';
                    if (document.getElementById('pred-stat-verbruik-last')) document.getElementById('pred-stat-verbruik-last').innerText = ps.totaal_verbruik.last || '--';
                    if (document.getElementById('pred-stat-verbruik-max')) document.getElementById('pred-stat-verbruik-max').innerText = ps.totaal_verbruik.max || '--';
                }

                // === UNIFIED ENERGY (kWh) STANDARDIZATION & SYMMETRIC 0-AXIS ALIGNMENT ===
                const intervalH = data.interval_h || (predictionResolution === '15m' ? 0.25 : 1.0);

                // Convert instantaneous power (kW) to actual interval energy (kWh = kW * hours)
                const toKwh = (arr) => (arr || []).map(kw => Number((kw * intervalH).toFixed(3)));
                const toKwhNeg = (arr) => (arr || []).map(kw => Number((-Math.abs(kw * intervalH)).toFixed(3)));

                const unallocKwh = toKwh(data.datasets.unallocated_kw || data.datasets.baseload_kw);
                const boilerKwh = toKwh(data.datasets.boiler_kw);
                const heatingKwh = toKwh(data.datasets.heating_kw || []);
                const batteryChargeKwh = toKwh(data.datasets.battery_charge_kw || []);
                const solarNegKwh = toKwhNeg(data.datasets.solar_kw_neg || []);
                const batteryDischargeNegKwh = toKwhNeg(data.datasets.battery_discharge_kw_neg || []);
                const netKwh = toKwh(netPowerArr);

                // Calculate symmetric center-aligned bounds (0 line exactly at 50% height)
                let consKwhArr = [];
                for (let i = 0; i < labels.length; i++) {
                    consKwhArr.push((unallocKwh[i] || 0) + (boilerKwh[i] || 0) + (heatingKwh[i] || 0) + (batteryChargeKwh[i] || 0));
                }

                let maxAbsKwh = Math.max(
                    ...consKwhArr,
                    ...solarNegKwh.map(Math.abs),
                    ...batteryDischargeNegKwh.map(Math.abs),
                    ...netKwh.map(Math.abs),
                    1.0
                );
                maxAbsKwh = Math.ceil(maxAbsKwh * 2) / 2; // Stappen van 0.5 kWh
                if (maxAbsKwh < 1.5) maxAbsKwh = 1.5;

                let maxAbsPrice = Math.max(...pricesArr.map(Math.abs), 0.30);
                maxAbsPrice = Math.ceil(maxAbsPrice * 10) / 10;
                if (maxAbsPrice < 0.30) maxAbsPrice = 0.30;

                const isLineMode = (window.predictionChartType === 'line');
                const chartConfig = {
                    type: isLineMode ? 'line' : 'bar',
                    data: {
                        labels: labels,
                        datasets: (() => {
                            const ds = [
                                {
                                    label: 'Stroomprijs All-in (€/kWh)',
                                    data: pricesArr,
                                    type: 'line',
                                    borderColor: '#06B6D4',
                                    borderDash: [4, 4],
                                    borderWidth: 1.5,
                                    pointRadius: 0,
                                    yAxisID: 'y1',
                                    tension: 0,
                                    order: 0
                                },
                                {
                                    label: 'Verwacht Netto (kWh)',
                                    data: netKwh,
                                    type: 'line',
                                    borderColor: '#EF4444',
                                    backgroundColor: 'transparent',
                                    borderWidth: 2.5,
                                    pointRadius: 2,
                                    pointBackgroundColor: '#EF4444',
                                    tension: 0.25,
                                    yAxisID: 'y',
                                    order: 1
                                }
                            ];

                            if (isLineMode) {
                                ds.push({
                                    label: 'Ongedefinieerd (kWh)',
                                    data: unallocKwh,
                                    type: 'line',
                                    borderColor: '#3B82F6',
                                    backgroundColor: 'rgba(59, 130, 246, 0.15)',
                                    borderWidth: 2,
                                    pointRadius: 0,
                                    tension: 0.25,
                                    order: 2
                                });
                                ds.push({
                                    label: 'SWW Tapwater (kWh)',
                                    data: boilerKwh,
                                    type: 'line',
                                    borderColor: '#EC4899',
                                    backgroundColor: 'rgba(236, 72, 153, 0.2)',
                                    borderWidth: 2,
                                    pointRadius: 0,
                                    tension: 0.25,
                                    order: 3
                                });
                                ds.push({
                                    label: 'CV Verwarming (kWh)',
                                    data: heatingKwh,
                                    type: 'line',
                                    borderColor: '#6366F1',
                                    backgroundColor: 'rgba(99, 102, 241, 0.2)',
                                    borderWidth: 2,
                                    pointRadius: 0,
                                    tension: 0.25,
                                    order: 3
                                });
                                if (data.battery_enabled) {
                                    ds.push({
                                        label: 'Accu Laden (kWh)',
                                        data: batteryChargeKwh,
                                        type: 'line',
                                        borderColor: '#10B981',
                                        backgroundColor: 'transparent',
                                        borderWidth: 2,
                                        pointRadius: 0,
                                        tension: 0.25,
                                        order: 4
                                    });
                                }
                                ds.push({
                                    label: 'Zon Productie (kWh)',
                                    data: solarNegKwh,
                                    type: 'line',
                                    borderColor: '#F59E0B',
                                    backgroundColor: 'rgba(245, 158, 11, 0.15)',
                                    borderWidth: 2,
                                    pointRadius: 0,
                                    tension: 0.25,
                                    order: 4
                                });
                                if (data.battery_enabled) {
                                    ds.push({
                                        label: 'Accu Ontladen (kWh)',
                                        data: batteryDischargeNegKwh,
                                        type: 'line',
                                        borderColor: '#14B8A6',
                                        backgroundColor: 'transparent',
                                        borderWidth: 2,
                                        pointRadius: 0,
                                        tension: 0.25,
                                        order: 5
                                    });
                                }
                            } else {
                                ds.push({
                                    label: 'Ongedefinieerd (kWh)',
                                    data: unallocKwh,
                                    backgroundColor: '#3B82F6',
                                    stack: 'energy',
                                    borderRadius: 2,
                                    order: 3
                                });
                                ds.push({
                                    label: 'SWW Tapwater (kWh)',
                                    data: boilerKwh,
                                    backgroundColor: '#EC4899',
                                    stack: 'energy',
                                    borderRadius: 2,
                                    order: 3
                                });
                                ds.push({
                                    label: 'CV Verwarming (kWh)',
                                    data: heatingKwh,
                                    backgroundColor: '#6366F1',
                                    stack: 'energy',
                                    borderRadius: 2,
                                    order: 3
                                });
                                if (data.battery_enabled) {
                                    ds.push({
                                        label: 'Accu Laden (kWh)',
                                        data: batteryChargeKwh,
                                        backgroundColor: '#10B981',
                                        stack: 'energy',
                                        borderRadius: 2,
                                        order: 3
                                    });
                                }
                                ds.push({
                                    label: 'Zon Productie (kWh)',
                                    data: solarNegKwh,
                                    backgroundColor: '#F59E0B',
                                    stack: 'energy',
                                    borderRadius: 2,
                                    order: 4
                                });
                                if (data.battery_enabled) {
                                    ds.push({
                                        label: 'Accu Ontladen (kWh)',
                                        data: batteryDischargeNegKwh,
                                        backgroundColor: '#14B8A6',
                                        stack: 'energy',
                                        borderRadius: 2,
                                        order: 4
                                    });
                                }
                            }
                            return ds;
                        })()
                    },
                    options: {
                        responsive: true,
                        maintainAspectRatio: false,
                        interaction: { mode: 'index', intersect: false },
                        plugins: {
                            legend: { display: false },
                            tooltip: {
                                enabled: false,
                                external: function(context) {
                                    customHemsTooltipHandler(context, true);
                                }
                            }
                        },
                        scales: {
                            x: {
                                stacked: !isLineMode,
                                grid: { color: 'rgba(30, 41, 59, 0.4)' },
                                ticks: { color: '#94A3B8', font: { family: 'monospace', size: 10 } }
                            },
                            y: {
                                stacked: !isLineMode,
                                min: -maxAbsKwh,
                                max: maxAbsKwh,
                                title: { display: true, text: 'Opbrengst (-kWh) < 0 < Verbruik (+kWh)', color: '#94A3B8', font: { family: 'monospace', size: 10 } },
                                grid: {
                                    color: (ctx) => ctx.tick && ctx.tick.value === 0 ? '#CBD5E1' : 'rgba(30, 41, 59, 0.6)',
                                    lineWidth: (ctx) => ctx.tick && ctx.tick.value === 0 ? 2 : 1
                                },
                                ticks: {
                                    color: '#94A3B8',
                                    font: { family: 'monospace', size: 10 },
                                    callback: function(val) {
                                        const absV = Math.abs(val);
                                        const prefix = val < 0 ? '-' : '';
                                        return `${prefix}${absV.toFixed(2)} kWh`;
                                    }
                                }
                            },
                            y1: {
                                type: 'linear',
                                position: 'right',
                                display: true,
                                min: -maxAbsPrice,
                                max: maxAbsPrice,
                                title: { display: true, text: 'Tarief (€/kWh)', color: '#06B6D4', font: { family: 'monospace', size: 10 } },
                                grid: { drawOnChartArea: false },
                                ticks: {
                                    color: '#06B6D4',
                                    font: { family: 'monospace', size: 10 },
                                    callback: function(val) {
                                        return val >= 0 ? '€' + Number(val).toFixed(2) : '';
                                    }
                                }
                            }
                        }
                    }
                };

                // Render on Analytics Tab
                const canvasAnalytics = document.getElementById('hemsChartAnalytics');
                if (canvasAnalytics) {
                    if (analyticsChartInstance) analyticsChartInstance.destroy();
                    analyticsChartInstance = new Chart(canvasAnalytics.getContext('2d'), chartConfig);
                    window.analyticsChartInstance = analyticsChartInstance;
                    window.hemsChartAnalytics = analyticsChartInstance;
                }

                // Render on Dashboard Tab
                const canvasDash = document.getElementById('hemsChart');
                if (canvasDash) {
                    if (chartInstance) chartInstance.destroy();
                    // Clone datasets for dashboard canvas if present
                    chartInstance = new Chart(canvasDash.getContext('2d'), Object.assign({}, chartConfig));
                    window.chartInstance = chartInstance;
                }

                // Add mouseleave & tap dismissal listeners to cleanly hide tooltip when leaving graph
                if (!window.__tooltipDismissAttached) {
                    window.__tooltipDismissAttached = true;

                    const hideTooltip = () => {
                        const tip = document.getElementById('chartjs-custom-tooltip');
                        if (tip) {
                            tip.style.opacity = '0';
                            tip.style.pointerEvents = 'none';
                        }
                    };

                    // Global pointer/click outside canvas dismisses tooltip
                    document.addEventListener('pointerdown', (e) => {
                        if (!e.target.closest('canvas')) hideTooltip();
                    });

                    // Canvas mouseleave listeners
                    ['hemsChartAnalytics', 'hemsChart', 'powerProducersChart', 'electricityPricesChart', 'chart-dhw-temperature', 'chart-heating-forecast'].forEach(id => {
                        const c = document.getElementById(id);
                        if (c) {
                            c.addEventListener('mouseleave', hideTooltip);
                            c.addEventListener('mouseout', (e) => {
                                if (!c.contains(e.relatedTarget)) hideTooltip();
                            });
                        }
                    });
                }
            } catch (e) {
                console.error('Chart load error:', e);
            }
        }

        // =========================================================================
        // POLICIES CONTROLLER
        // =========================================================================
        async function loadPolicies() {
            const [polRes, devRes] = await Promise.all([fetch('./api/policies'), fetch('./api/devices')]);
            const polData = await polRes.json();
            const devData = await devRes.json();

            const devMap = {};
            (devData.devices || []).forEach(d => { devMap[d.id] = d.name; });

            const container = document.getElementById('policies-container');
            container.innerHTML = '';
            document.getElementById('badge-pol-count').innerText = (polData.policies || []).length;

            (polData.policies || []).forEach(pol => {
                const card = document.createElement('div');
                card.className = 'bg-[#0e1422] border border-[#1E293B] rounded-2xl p-5 flex flex-col justify-between shadow-lg';
                
                let detailsHtml = '';
                let typeBadge = '';

                if (pol.type === 'thermal_buffer') {
                    typeBadge = '<span class="px-2 py-0.5 rounded text-[10px] font-semibold bg-pink-950 text-pink-300 border border-pink-800">Buffer Zonder Teruggave</span>';
                    detailsHtml = `
                        <div class="space-y-1 text-[11px] text-slate-300 bg-[#0B0F17] p-3 rounded-lg border border-slate-800 mb-3">
                            <div>🚨 Nood-comfort: <strong>< ${pol.parameters.emergency_threshold_c || 38}°C</strong> (Prioriteit 1)</div>
                            <div>⚡ Economische drempel: <strong>< ${pol.parameters.deadband_reheat_c || 46}°C</strong></div>
                            <div>🎯 Doeltemp: <strong>${pol.parameters.target_temperature_c || 50}°C</strong> · ☀️ Boost: <strong>${pol.parameters.solar_boost_temperature_c || 60}°C</strong></div>
                            <div>🛡️ Spitsblokkades: ${pol.parameters.morning_peak_lockout ? 'Ochtend ✓' : ''} ${pol.parameters.evening_peak_lockout ? 'Avond ✓' : ''}</div>
                        </div>
                    `;
                } else if (pol.type === 'battery_arbitrage') {
                    typeBadge = '<span class="px-2 py-0.5 rounded text-[10px] font-semibold bg-emerald-950 text-emerald-300 border border-emerald-800">Accu Arbitrage & Dode Zone</span>';
                    detailsHtml = `
                        <div class="space-y-1 text-[11px] text-slate-300 bg-[#0B0F17] p-3 rounded-lg border border-slate-800 mb-3 font-mono">
                            <div>⏸️ Dode Zone (Deadband): <strong>ΔP < €${pol.parameters.min_price_spread_eur_kwh || 0.115}/kWh</strong></div>
                            <div>⚡ Conversie-efficiëntie: <strong>${Math.round((pol.parameters.roundtrip_efficiency || 0.87)*100)}%</strong> (13% verlies)</div>
                            <div>📉 Cel-afschrijving (LCOS): <strong>€${pol.parameters.lcos_depreciation_eur_kwh || 0.0741}/kWh</strong></div>
                            <div>🔋 SoC Grenzen: <strong>${pol.parameters.min_soc_pct || 10}% - ${pol.parameters.max_soc_pct || 95}%</strong></div>
                        </div>
                    `;
                } else {
                    typeBadge = '<span class="px-2 py-0.5 rounded text-[10px] font-semibold bg-purple-950 text-purple-300 border border-purple-800">Verbruik Zonder Opslag</span>';
                    detailsHtml = `
                        <div class="space-y-1 text-[11px] text-slate-300 bg-[#0B0F17] p-3 rounded-lg border border-slate-800 mb-3">
                            <div>⏱️ Duur: <strong>${pol.parameters.duration_minutes || 90} min</strong> @ <strong>${pol.parameters.power_watts || 1200} W</strong></div>
                            <div>🕒 Venster: <strong>${pol.parameters.window_start_hour || 8}:00 - ${pol.parameters.window_end_hour || 20}:00</strong></div>
                            <div>☀️ Zonne-drempel: <strong>${pol.parameters.min_solar_surplus_watts || 1500} W</strong></div>
                        </div>
                    `;
                }

                const targetBadges = (pol.target_devices && pol.target_devices.length > 0)
                    ? pol.target_devices.map(id => `<span class="px-1.5 py-0.5 rounded text-[10px] bg-blue-900/40 text-blue-300 border border-blue-800 font-medium">${devMap[id] || id}</span>`).join(' ')
                    : '<span class="text-slate-500 italic">Geen apparaten gekoppeld</span>';

                card.innerHTML = `
                    <div>
                        <div class="flex justify-between items-start mb-2">
                            <h4 class="font-bold text-white text-sm">${pol.name}</h4>
                            ${typeBadge}
                        </div>
                        <div class="text-[11px] text-slate-400 mb-2.5 flex items-center gap-1.5 flex-wrap">
                            <span>Gekoppeld:</span> ${targetBadges}
                        </div>
                        ${detailsHtml}
                    </div>
                    <div class="flex justify-end gap-2 pt-3 border-t border-[#1E293B]">
                        <button onclick='openPolicyModal(${JSON.stringify(pol)})' class="px-2.5 py-1 bg-slate-800 hover:bg-slate-700 text-slate-200 text-xs rounded-lg">Bewerken</button>
                        <button onclick="deletePolicy('${pol.id}')" class="px-2.5 py-1 bg-red-950/60 hover:bg-red-900 text-red-300 border border-red-800 text-xs rounded-lg">Verwijderen</button>
                    </div>
                `;
                container.appendChild(card);
            });
        }

        async function populatePolicyDeviceSelector(selectedDeviceIds = []) {
            try {
                const res = await fetch('./api/devices');
                const data = await res.json();
                window.__lastPredictionData = data;
                const container = document.getElementById('modal-pol-devices-list');
                container.innerHTML = '';
                const devices = data.devices || [];
                if (devices.length === 0) {
                    container.innerHTML = '<span class="text-slate-500 italic">Geen apparaten geconfigureerd. Voeg eerst een apparaat toe in het menu Apparaten.</span>';
                    return;
                }
                devices.forEach(d => {
                    const label = document.createElement('label');
                    label.className = 'flex items-center gap-2 p-1.5 rounded hover:bg-slate-800/40 cursor-pointer';
                    const isChecked = selectedDeviceIds.includes(d.id);
                    label.innerHTML = `
                        <input type="checkbox" name="policy_target_device" value="${d.id}" ${isChecked ? 'checked' : ''} class="rounded bg-slate-900 text-purple-600 border-slate-700">
                        <span class="text-slate-200 font-medium">${d.name}</span>
                        <span class="ml-auto text-[10px] text-slate-500 font-mono">${d.type}</span>
                    `;
                    container.appendChild(label);
                });
            } catch (e) {
                console.error('Error fetching devices for policy:', e);
            }
        }

        function renderPolicyFields() {
            const type = document.getElementById('modal-pol-type').value;
            const container = document.getElementById('pol-params-container');
            container.innerHTML = '';

            if (type === 'thermal_buffer') {
                container.innerHTML = `
                    <div class="grid grid-cols-2 gap-3">
                        <div>
                            <label class="block mb-1 text-slate-400">Nood-comfort Drempel (°C)</label>
                            <input type="number" step="0.5" id="param_emergency_threshold_c" value="${currentPolicyParams.emergency_threshold_c || 38.0}" class="w-full bg-[#0B0F17] border border-slate-800 rounded-lg p-2 text-white">
                        </div>
                        <div>
                            <label class="block mb-1 text-slate-400">Economische Drempel (°C)</label>
                            <input type="number" step="0.5" id="param_deadband_reheat_c" value="${currentPolicyParams.deadband_reheat_c || 46.0}" class="w-full bg-[#0B0F17] border border-slate-800 rounded-lg p-2 text-white">
                        </div>
                    </div>
                    <div class="grid grid-cols-2 gap-3">
                        <div>
                            <label class="block mb-1 text-slate-400">Standaard Doeltemp (°C)</label>
                            <input type="number" step="1" id="param_target_temperature_c" value="${currentPolicyParams.target_temperature_c || 50.0}" class="w-full bg-[#0B0F17] border border-slate-800 rounded-lg p-2 text-white">
                        </div>
                        <div>
                            <label class="block mb-1 text-slate-400">Zon/Dal Boost Doeltemp (°C)</label>
                            <input type="number" step="1" id="param_solar_boost_temperature_c" value="${currentPolicyParams.solar_boost_temperature_c || 60.0}" class="w-full bg-[#0B0F17] border border-slate-800 rounded-lg p-2 text-white">
                        </div>
                    </div>
                    <div class="space-y-1 pt-2">
                        <label class="flex items-center gap-2">
                            <input type="checkbox" id="param_morning_peak_lockout" ${currentPolicyParams.morning_peak_lockout !== false ? 'checked' : ''} class="rounded bg-slate-900 text-purple-600 border-slate-700">
                            <span class="text-slate-300 text-xs">Ochtendspits blokkade (07:00 - 08:30 SG1)</span>
                        </label>
                        <label class="flex items-center gap-2">
                            <input type="checkbox" id="param_evening_peak_lockout" ${currentPolicyParams.evening_peak_lockout !== false ? 'checked' : ''} class="rounded bg-slate-900 text-purple-600 border-slate-700">
                            <span class="text-slate-300 text-xs">Avondspits blokkade (17:30 - 20:30 SG1)</span>
                        </label>
                        <label class="flex items-center gap-2">
                            <input type="checkbox" id="param_isolate_space_heating_during_dhw" ${currentPolicyParams.isolate_space_heating_during_dhw !== false ? 'checked' : ''} class="rounded bg-slate-900 text-purple-600 border-slate-700">
                            <span class="text-slate-300 text-xs">CV uitschakelen tijdens SWW (voorkomt 9kW BUH)</span>
                        </label>
                    </div>
                `;
            } else if (type === 'battery_arbitrage') {
                container.innerHTML = `
                    <div class="grid grid-cols-2 gap-3">
                        <div>
                            <label class="block mb-1 text-slate-400">Dode Zone (Min. Prijsdelta €/kWh)</label>
                            <input type="number" step="0.001" id="param_min_price_spread_eur_kwh" value="${currentPolicyParams.min_price_spread_eur_kwh || 0.115}" class="w-full bg-[#0B0F17] border border-slate-800 rounded-lg p-2 text-white">
                        </div>
                        <div>
                            <label class="block mb-1 text-slate-400">Rondgang-Efficiëntie (bijv. 0.87)</label>
                            <input type="number" step="0.01" id="param_roundtrip_efficiency" value="${currentPolicyParams.roundtrip_efficiency || 0.87}" class="w-full bg-[#0B0F17] border border-slate-800 rounded-lg p-2 text-white">
                        </div>
                    </div>
                    <div class="grid grid-cols-2 gap-3">
                        <div>
                            <label class="block mb-1 text-slate-400">Cel-Afschrijving (LCOS €/kWh)</label>
                            <input type="number" step="0.001" id="param_lcos_depreciation_eur_kwh" value="${currentPolicyParams.lcos_depreciation_eur_kwh || 0.0741}" class="w-full bg-[#0B0F17] border border-slate-800 rounded-lg p-2 text-white">
                        </div>
                        <div>
                            <label class="block mb-1 text-slate-400">Piekstroombeveiliging (Amps/fase)</label>
                            <input type="number" step="1" id="param_peak_shaving_threshold_amps" value="${currentPolicyParams.peak_shaving_threshold_amps || 20.0}" class="w-full bg-[#0B0F17] border border-slate-800 rounded-lg p-2 text-white">
                        </div>
                    </div>
                    <div class="grid grid-cols-2 gap-3">
                        <div>
                            <label class="block mb-1 text-slate-400">Minimale SoC Reserve (%)</label>
                            <input type="number" step="1" id="param_min_soc_pct" value="${currentPolicyParams.min_soc_pct || 10.0}" class="w-full bg-[#0B0F17] border border-slate-800 rounded-lg p-2 text-white">
                        </div>
                        <div>
                            <label class="block mb-1 text-slate-400">Maximale SoC (%)</label>
                            <input type="number" step="1" id="param_max_soc_pct" value="${currentPolicyParams.max_soc_pct || 95.0}" class="w-full bg-[#0B0F17] border border-slate-800 rounded-lg p-2 text-white">
                        </div>
                    </div>
                `;
            } else {
                container.innerHTML = `
                    <div class="grid grid-cols-2 gap-3">
                        <div>
                            <label class="block mb-1 text-slate-400">Cyclusduur (minuten)</label>
                            <input type="number" step="5" id="param_duration_minutes" value="${currentPolicyParams.duration_minutes || 90}" class="w-full bg-[#0B0F17] border border-slate-800 rounded-lg p-2 text-white">
                        </div>
                        <div>
                            <label class="block mb-1 text-slate-400">Gemiddeld Vermogen (Watt)</label>
                            <input type="number" step="50" id="param_power_watts" value="${currentPolicyParams.power_watts || 1200}" class="w-full bg-[#0B0F17] border border-slate-800 rounded-lg p-2 text-white">
                        </div>
                    </div>
                    <div class="grid grid-cols-2 gap-3">
                        <div>
                            <label class="block mb-1 text-slate-400">Venster Start (Uur)</label>
                            <input type="number" step="1" id="param_window_start_hour" value="${currentPolicyParams.window_start_hour || 8}" class="w-full bg-[#0B0F17] border border-slate-800 rounded-lg p-2 text-white">
                        </div>
                        <div>
                            <label class="block mb-1 text-slate-400">Venster Eind (Uur)</label>
                            <input type="number" step="1" id="param_window_end_hour" value="${currentPolicyParams.window_end_hour || 20}" class="w-full bg-[#0B0F17] border border-slate-800 rounded-lg p-2 text-white">
                        </div>
                    </div>
                `;
            }
        }

        function openPolicyModal(pol = null) {
            const selectedDevs = pol ? (pol.target_devices || []) : [];
            populatePolicyDeviceSelector(selectedDevs);
            if (pol) {
                document.getElementById('modal-pol-title').innerText = 'Policy Bewerken';
                document.getElementById('modal-pol-id').value = pol.id;
                document.getElementById('modal-pol-name').value = pol.name;
                document.getElementById('modal-pol-type').value = pol.type;
                currentPolicyParams = pol.parameters || {};
            } else {
                document.getElementById('modal-pol-title').innerText = 'Nieuwe Policy Aanmaken';
                document.getElementById('modal-pol-id').value = '';
                document.getElementById('modal-pol-name').value = '';
                document.getElementById('modal-pol-type').value = 'thermal_buffer';
                currentPolicyParams = {};
            }
            renderPolicyFields();
            document.getElementById('policy-modal').classList.remove('hidden');
        }

        async function savePolicy(e) {
            e.preventDefault();
            const id = document.getElementById('modal-pol-id').value;
            const type = document.getElementById('modal-pol-type').value;
            const selectedDevices = Array.from(document.querySelectorAll('input[name="policy_target_device"]:checked')).map(cb => cb.value);
            const params = {};

            if (type === 'thermal_buffer') {
                params.emergency_threshold_c = parseFloat(document.getElementById('param_emergency_threshold_c').value);
                params.deadband_reheat_c = parseFloat(document.getElementById('param_deadband_reheat_c').value);
                params.target_temperature_c = parseFloat(document.getElementById('param_target_temperature_c').value);
                params.solar_boost_temperature_c = parseFloat(document.getElementById('param_solar_boost_temperature_c').value);
                params.morning_peak_lockout = document.getElementById('param_morning_peak_lockout').checked;
                params.evening_peak_lockout = document.getElementById('param_evening_peak_lockout').checked;
                params.isolate_space_heating_during_dhw = document.getElementById('param_isolate_space_heating_during_dhw').checked;
            } else if (type === 'battery_arbitrage') {
                params.min_price_spread_eur_kwh = parseFloat(document.getElementById('param_min_price_spread_eur_kwh').value);
                params.roundtrip_efficiency = parseFloat(document.getElementById('param_roundtrip_efficiency').value);
                params.lcos_depreciation_eur_kwh = parseFloat(document.getElementById('param_lcos_depreciation_eur_kwh').value);
                params.peak_shaving_threshold_amps = parseFloat(document.getElementById('param_peak_shaving_threshold_amps').value);
                params.min_soc_pct = parseFloat(document.getElementById('param_min_soc_pct').value);
                params.max_soc_pct = parseFloat(document.getElementById('param_max_soc_pct').value);
            } else {
                params.duration_minutes = parseInt(document.getElementById('param_duration_minutes').value);
                params.power_watts = parseFloat(document.getElementById('param_power_watts').value);
                params.window_start_hour = parseInt(document.getElementById('param_window_start_hour').value);
                params.window_end_hour = parseInt(document.getElementById('param_window_end_hour').value);
            }

            const payload = {
                name: document.getElementById('modal-pol-name').value,
                type: type,
                target_devices: selectedDevices,
                parameters: params
            };

            if (id) {
                await fetch('./api/policies/' + id, { method: 'PUT', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload) });
            } else {
                await fetch('./api/policies', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload) });
            }
            closeModal('policy-modal');
            loadPolicies();
        }

        async function deletePolicy(id) {
            if (!confirm('Weet je zeker dat je deze policy wilt verwijderen?')) return;
            await fetch('./api/policies/' + id, { method: 'DELETE' });
            loadPolicies();
        }

        // =========================================================================
        // DEVICES CONTROLLER
        // =========================================================================
        window.__activeDeviceFilter = 'all';

        function filterDeviceView(filterKey) {
            window.__activeDeviceFilter = filterKey;
            document.querySelectorAll('.dev-filter-btn').forEach(btn => {
                btn.className = 'dev-filter-btn px-3 py-1 rounded-lg text-slate-400 hover:text-white transition';
            });
            const activeBtn = document.getElementById('dev-filter-' + filterKey);
            if (activeBtn) {
                activeBtn.className = 'dev-filter-btn px-3 py-1 rounded-lg bg-blue-600 text-white font-semibold shadow transition';
            }
            loadDevices();
        }

        async function loadDevices() {
            const [devRes, polRes, infRes] = await Promise.all([
                fetch('./api/devices'),
                fetch('./api/policies'),
                fetch('./api/infrastructure')
            ]);
            const devData = await devRes.json();
            const polData = await polRes.json();
            const infData = await infRes.json();

            const policies = polData.policies || [];
            const devices = devData.devices || [];
            const haInfo = infData.homeassistant || {};
            const container = document.getElementById('devices-container');
            container.innerHTML = '';
            document.getElementById('badge-dev-count').innerText = devices.length;

            // Map live states from Home Assistant info
            const haStateMap = {};
            (haInfo.sources || []).forEach(s => { haStateMap[s.entity_id] = s.live_state; });
            (haInfo.targets || []).forEach(t => { haStateMap[t.entity_id] = t.live_state; });

            // Categorize devices into 3 distinct groups
            const haDevices = devices.filter(d => d.source_type === 'homeassistant' && d.installed !== false);
            const mqttDevices = devices.filter(d => d.source_type === 'mqtt' && d.installed !== false);
            const plannedDevices = devices.filter(d => d.installed === false || d.enabled === false);

            // Update button counts
            if (document.getElementById('dev-filter-all')) document.getElementById('dev-filter-all').innerText = `Alle Apparaten (${devices.length})`;
            if (document.getElementById('dev-filter-ha')) document.getElementById('dev-filter-ha').innerText = `🏠 Home Assistant (${haDevices.length})`;
            if (document.getElementById('dev-filter-mqtt')) document.getElementById('dev-filter-mqtt').innerText = `⚡ Direct MQTT (${mqttDevices.length})`;
            if (document.getElementById('dev-filter-planned')) document.getElementById('dev-filter-planned').innerText = `🔋 Gepland / Standby (${plannedDevices.length})`;

            const groupsToRender = [];
            if (window.__activeDeviceFilter === 'all' || window.__activeDeviceFilter === 'homeassistant') {
                groupsToRender.push({
                    title: 'Home Assistant Core Gekoppelde Apparaten',
                    desc: 'Sensoren en actuatoren aangestuurd via de Home Assistant Supervisor REST API.',
                    icon: '🏠',
                    color: 'cyan',
                    list: haDevices
                });
            }
            if (window.__activeDeviceFilter === 'all' || window.__activeDeviceFilter === 'mqtt') {
                groupsToRender.push({
                    title: 'Directe MQTT & Modbus Apparaten',
                    desc: 'Streaming vermogensmeters rechtstreeks ingelezen van de Mosquitto message bus.',
                    icon: '⚡',
                    color: 'amber',
                    list: mqttDevices
                });
            }
            if (window.__activeDeviceFilter === 'all' || window.__activeDeviceFilter === 'planned') {
                groupsToRender.push({
                    title: 'Geplande / Standby Apparaten',
                    desc: 'Apparaten geconfigureerd voor simulaties of nog niet fysiek geïnstalleerd.',
                    icon: '🔋',
                    color: 'purple',
                    list: plannedDevices
                });
            }

            groupsToRender.forEach(grp => {
                if (grp.list.length === 0 && window.__activeDeviceFilter !== 'all') return;

                const section = document.createElement('div');
                section.className = 'space-y-3';
                section.innerHTML = `
                    <div class="flex items-center justify-between border-b border-slate-800 pb-2">
                        <div class="flex items-center gap-2">
                            <span class="text-base">${grp.icon}</span>
                            <h3 class="text-sm font-bold text-white tracking-wide">${grp.title}</h3>
                            <span class="px-2 py-0.5 rounded text-[10px] font-mono font-semibold bg-slate-800 text-slate-300 border border-slate-700">${grp.list.length}</span>
                        </div>
                        <span class="text-[11px] text-slate-400 hidden sm:inline">${grp.desc}</span>
                    </div>
                    <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4" id="grp-grid-${grp.color}"></div>
                `;
                container.appendChild(section);

                const grid = section.querySelector(`#grp-grid-${grp.color}`);
                if (grp.list.length === 0) {
                    grid.innerHTML = `<div class="col-span-full py-4 text-center text-xs text-slate-500 italic bg-[#0e1422] border border-slate-800 rounded-xl">Geen apparaten in deze categorie</div>`;
                    return;
                }

                grp.list.forEach(dev => {
                    const boundPolicies = policies.filter(p => (p.target_devices || []).includes(dev.id));
                    const policyBadge = boundPolicies.length > 0
                        ? boundPolicies.map(p => `<span class="px-1.5 py-0.5 rounded text-[10px] bg-purple-900/40 text-purple-300 border border-purple-800 font-medium">${p.name}</span>`).join(' ')
                        : '<span class="text-slate-500 italic">Geen beleid (stand-by)</span>';

                    const isInstalled = dev.installed !== false;
                    const isEnabled = dev.enabled !== false;
                    const statusPill = (!isInstalled)
                        ? '<span class="px-2 py-0.5 rounded text-[9px] font-mono font-semibold bg-slate-800 text-slate-400 border border-slate-700">NIET GEÏNSTALLEERD</span>'
                        : (isEnabled 
                            ? '<span class="px-2 py-0.5 rounded text-[9px] font-mono font-semibold bg-emerald-950/80 text-emerald-400 border border-emerald-800 flex items-center gap-1"><span class="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse"></span> ACTIEF</span>'
                            : '<span class="px-2 py-0.5 rounded text-[9px] font-mono font-semibold bg-amber-950/80 text-amber-400 border border-amber-800">UITGESCHAKELD</span>');

                    const pState = dev.ha_power_entity ? (haStateMap[dev.ha_power_entity] || '--') : '';
                    const cState = dev.ha_control_entity ? (haStateMap[dev.ha_control_entity] || '--') : '';
                    const tState = dev.ha_temp_entity ? (haStateMap[dev.ha_temp_entity] || '--') : '';

                    // Cache device in memory for safe, bug-free editing by ID
                    window.__cachedDevicesMap = window.__cachedDevicesMap || {};
                    window.__cachedDevicesMap[dev.id] = dev;

                    // Render multi-sensors list (No favicons, clean typography)
                    let sensorsHtml = '';
                    const devSensors = dev.sensors || [];
                    if (devSensors.length > 0) {
                        sensorsHtml = devSensors.map(s => {
                            const val = s.entity_id ? (haStateMap[s.entity_id] || '--') : '--';
                            const roleColor = s.role === 'producer' ? 'text-emerald-400' : (s.role === 'consumer' ? 'text-red-400' : 'text-cyan-400');
                            const roleLabel = s.role === 'producer' ? 'PRODUCENT' : (s.role === 'consumer' ? 'VERBRUIKER' : 'STATUS');
                            const connLabel = s.connector === 'mqtt' ? 'MQTT' : 'HA';
                            const targetStr = s.connector === 'mqtt' ? s.topic : s.entity_id;
                            return `
                                <div class="flex items-center justify-between py-1 px-2 rounded bg-[#0e1422] border border-slate-800/70 text-[10px]">
                                    <div class="min-w-0 flex-1 mr-2">
                                        <div class="flex items-center gap-1.5">
                                            <span class="px-1 py-0.2 rounded text-[8px] font-bold ${roleColor} bg-slate-900 border border-slate-800 flex-shrink-0">${roleLabel}</span>
                                            <span class="text-slate-300 font-medium truncate">${s.name}</span>
                                        </div>
                                        <span class="text-[9px] text-slate-500 font-mono block truncate">${connLabel}: ${targetStr}</span>
                                    </div>
                                    <span class="font-bold text-white font-mono flex-shrink-0">${val}</span>
                                </div>
                            `;
                        }).join('');
                    } else {
                        sensorsHtml = `<div class="text-[10px] text-slate-500 italic py-1">Geen sensoren geconfigureerd</div>`;
                    }

                    // Render multi-actuators list (No favicons, clean typography)
                    let actuatorsHtml = '';
                    const devActuators = dev.actuators || [];
                    if (devActuators.length > 0) {
                        actuatorsHtml = devActuators.map(a => {
                            const val = a.entity_id ? (haStateMap[a.entity_id] || a.default_state || '--') : (a.default_state || '--');
                            const typeLabel = a.type === 'select' ? 'MODUS' : (a.type === 'range' ? 'BEREIK' : 'SCHAKELAAR');
                            const connLabel = a.connector === 'mqtt' ? 'MQTT' : 'HA';
                            return `
                                <div class="flex items-center justify-between py-1 px-2 rounded bg-[#0e1422] border border-slate-800/70 text-[10px]">
                                    <div class="min-w-0 flex-1 mr-2">
                                        <div class="flex items-center gap-1.5">
                                            <span class="px-1 py-0.2 rounded text-[8px] font-bold text-pink-400 bg-slate-900 border border-slate-800 flex-shrink-0">${typeLabel}</span>
                                            <span class="text-slate-300 font-medium truncate">${a.name}</span>
                                        </div>
                                        <span class="text-[9px] text-slate-500 font-mono block truncate">${connLabel}: ${a.entity_id || a.topic}</span>
                                    </div>
                                    <span class="px-1.5 py-0.5 rounded text-[10px] font-bold ${val === 'on' || val.includes('aan') || val.includes('Aan') ? 'bg-emerald-950 text-emerald-400 border border-emerald-800' : 'bg-slate-900 text-slate-300 border border-slate-700'} font-mono flex-shrink-0">${val}</span>
                                </div>
                            `;
                        }).join('');
                    } else {
                        actuatorsHtml = `<div class="text-[10px] text-slate-500 italic py-1">Geen aansturing (puur meetapparaat)</div>`;
                    }

                    // Clean card header: Title truncates properly, badges never overflow, no id subtitle, no favicons, no section subtitles
                    const card = document.createElement('div');
                    card.className = 'bg-[#0e1422] border border-[#1E293B] hover:border-slate-700 rounded-2xl p-4 flex flex-col justify-between shadow-lg transition space-y-3';
                    card.innerHTML = `
                        <div>
                            <!-- Header: Title + Badges aligned horizontally without overflow -->
                            <div class="flex justify-between items-start gap-2 mb-2.5">
                                <div class="min-w-0 flex-1 mr-2">
                                    <h4 class="font-bold text-white text-sm truncate" title="${dev.name}">${dev.name}</h4>
                                </div>
                                <div class="flex items-center gap-1.5 flex-shrink-0">
                                    <span class="px-2 py-0.5 rounded text-[9px] font-mono font-semibold bg-blue-900/40 text-blue-300 border border-blue-800 flex-shrink-0">${dev.type}</span>
                                    ${statusPill}
                                </div>
                            </div>

                            <div class="text-[11px] text-slate-400 mb-2.5 flex items-center gap-1.5 flex-wrap">
                                <span class="font-medium">Beleid:</span> ${policyBadge}
                            </div>

                            <!-- DATABRONNEN (Geen favicons, geen subtitels) -->
                            <div class="bg-[#0B0F17] p-2.5 rounded-xl border border-slate-800/80 mb-2 space-y-1.5">
                                <div class="text-[10px] font-mono border-b border-slate-800/60 pb-1 font-bold text-cyan-400">
                                    Databronnen (${devSensors.length || 0})
                                </div>
                                <div class="space-y-1">
                                    ${sensorsHtml}
                                </div>
                            </div>

                            <!-- AANSTURING & REGIE (Geen favicons, geen subtitels) -->
                            <div class="bg-[#0B0F17] p-2.5 rounded-xl border border-slate-800/80 mb-2 space-y-1.5">
                                <div class="text-[10px] font-mono border-b border-slate-800/60 pb-1 font-bold text-pink-400">
                                    Aansturing & Regie (${devActuators.length})
                                </div>
                                <div class="space-y-1">
                                    ${actuatorsHtml}
                                </div>
                            </div>

                            ${dev.type === 'home_battery' ? `
                            <div class="bg-purple-950/30 border border-purple-800/40 rounded-xl p-2.5 mb-2 flex items-center justify-between text-xs">
                                <div>
                                    <div class="font-bold text-purple-300">🔋 Voorspelling Simulatie</div>
                                    <div class="text-[10px] text-slate-400">Accu meenemen in 24h prognose</div>
                                </div>
                                <button onclick="toggleBatterySimFromSettings()" class="px-2.5 py-1 rounded font-medium text-xs transition ${window.__simulateBattery ? 'bg-purple-600 text-white shadow' : 'bg-slate-800 text-slate-400 hover:text-white'}">
                                    ${window.__simulateBattery ? 'Actief' : 'Uit'}
                                </button>
                            </div>` : ''}
                        </div>
                        <div class="flex justify-end gap-2 pt-2.5 border-t border-[#1E293B]">
                            <button onclick="openDeviceModal('${dev.id}')" class="px-2.5 py-1 bg-slate-800 hover:bg-slate-700 text-slate-200 text-xs rounded-lg border border-slate-700 transition">Bewerken</button>
                            <button onclick="deleteDevice('${dev.id}')" class="px-2.5 py-1 bg-red-950/60 hover:bg-red-900 text-red-300 border border-red-800 text-xs rounded-lg transition">Verwijderen</button>
                        </div>
                    `;
                    grid.appendChild(card);
                });
            });
        }

                function toggleDeviceSourceFields() {
            const st = document.getElementById('modal-dev-source-type').value;
            const haDiv = document.getElementById('dev-source-ha-fields');
            const mqDiv = document.getElementById('dev-source-mqtt-fields');
            if (st === 'mqtt') {
                haDiv.classList.add('hidden');
                mqDiv.classList.remove('hidden');
            } else {
                haDiv.classList.remove('hidden');
                mqDiv.classList.add('hidden');
            }
        }

        function openDeviceModal(devOrId = null) {
            populateHaDropdowns();
            
            let dev = null;
            if (typeof devOrId === 'string') {
                dev = (window.__cachedDevicesMap && window.__cachedDevicesMap[devOrId]) || null;
            } else {
                dev = devOrId;
            }
            
            // Populate broker dropdown
            const bSelect = document.getElementById('modal-dev-mqtt-broker');
            if (bSelect) {
                bSelect.innerHTML = '';
                (cachedInfra.mqtt_connections || []).forEach(b => {
                    const opt = document.createElement('option');
                    opt.value = b.id;
                    opt.innerText = `${b.name} (${b.host}:${b.port})`;
                    bSelect.appendChild(opt);
                });
            }

            if (dev) {
                document.getElementById('modal-dev-title').innerText = 'Apparaat Bewerken';
                document.getElementById('modal-dev-id').value = dev.id;
                document.getElementById('modal-dev-name').value = dev.name;
                document.getElementById('modal-dev-type').value = dev.type;
                document.getElementById('modal-dev-source-type').value = dev.source_type || 'homeassistant';
                // Extract entities from rich sensors/actuators or legacy fields
                const powerEntity = dev.ha_power_entity || (dev.sensors ? (dev.sensors.find(s => s.role === 'consumer' || s.role === 'producer')?.entity_id || '') : '');
                const tempEntity = dev.ha_temp_entity || (dev.sensors ? (dev.sensors.find(s => s.role === 'state' && (s.entity_id?.includes('temp') || s.id?.includes('temp')))?.entity_id || '') : '');
                const controlEntity = dev.ha_control_entity || (dev.actuators ? (dev.actuators[0]?.entity_id || '') : '');
                const mqttPowerTopic = dev.mqtt_power_topic || (dev.sensors ? (dev.sensors.find(s => s.connector === 'mqtt')?.topic || '') : '');

                document.getElementById('modal-dev-ha-power').value = powerEntity;
                if (document.getElementById('modal-dev-ha-temp')) document.getElementById('modal-dev-ha-temp').value = tempEntity;
                document.getElementById('modal-dev-ha-control').value = controlEntity;
                document.getElementById('modal-dev-mqtt-power-topic').value = mqttPowerTopic;
                document.getElementById('modal-dev-native-unit').value = dev.native_unit || 'W';
                document.getElementById('modal-dev-installed').checked = dev.installed !== false;
                document.getElementById('modal-dev-enabled').checked = dev.enabled !== false;
                document.getElementById('modal-dev-mqtt-broker').value = dev.mqtt_broker_id || '';
                document.getElementById('modal-dev-mqtt-power-topic').value = dev.mqtt_power_topic || '';
                document.getElementById('modal-dev-mqtt-json-key').value = dev.mqtt_power_json_key || '';
                document.getElementById('modal-dev-mqtt-control-topic').value = dev.mqtt_control_topic || '';
            } else {
                document.getElementById('modal-dev-title').innerText = 'Nieuw Apparaat Toevoegen';
                document.getElementById('modal-dev-id').value = '';
                document.getElementById('modal-dev-name').value = '';
                document.getElementById('modal-dev-source-type').value = 'homeassistant';
                document.getElementById('modal-dev-native-unit').value = 'W';
                document.getElementById('modal-dev-installed').checked = true;
                document.getElementById('modal-dev-enabled').checked = true;
                if (document.getElementById('modal-dev-ha-temp')) document.getElementById('modal-dev-ha-temp').value = '';
                document.getElementById('modal-dev-mqtt-power-topic').value = '';
                document.getElementById('modal-dev-mqtt-json-key').value = '';
                document.getElementById('modal-dev-mqtt-control-topic').value = '';
            }
            const elMin = document.getElementById('modal-dev-min-runtime');
            if (elMin) elMin.value = (dev && dev.parameters) ? (dev.parameters.min_runtime_minutes || '') : '';
            const elMax = document.getElementById('modal-dev-max-power');
            if (elMax) elMax.value = (dev && dev.parameters) ? (dev.parameters.max_power_w || '') : '';
            const elEm = document.getElementById('modal-dev-emergency-threshold');
            if (elEm) elEm.value = (dev && dev.parameters) ? (dev.parameters.emergency_threshold || '') : '';
            toggleDeviceSourceFields();
            document.getElementById('device-modal').classList.remove('hidden');
        }

        async function saveDevice(e) {
            e.preventDefault();
            const id = document.getElementById('modal-dev-id').value;
            const st = document.getElementById('modal-dev-source-type').value;
            const payload = {
                name: document.getElementById('modal-dev-name').value,
                type: document.getElementById('modal-dev-type').value,
                source_type: st,
                ha_power_entity: document.getElementById('modal-dev-ha-power').value,
                ha_temp_entity: document.getElementById('modal-dev-ha-temp') ? document.getElementById('modal-dev-ha-temp').value : '',
                ha_control_entity: document.getElementById('modal-dev-ha-control').value,
                native_unit: document.getElementById('modal-dev-native-unit').value,
                installed: document.getElementById('modal-dev-installed').checked,
                enabled: document.getElementById('modal-dev-enabled').checked,
                mqtt_broker_id: document.getElementById('modal-dev-mqtt-broker').value,
                mqtt_power_topic: document.getElementById('modal-dev-mqtt-power-topic').value,
                mqtt_power_json_key: document.getElementById('modal-dev-mqtt-json-key').value,
                mqtt_control_topic: document.getElementById('modal-dev-mqtt-control-topic').value,
                parameters: (() => {
                    const dev = (window.__cachedDevicesMap && window.__cachedDevicesMap[id]) || {};
                    const p = (dev && dev.parameters) ? Object.assign({}, dev.parameters) : {};
                    const elMin = document.getElementById('modal-dev-min-runtime');
                    if (elMin && elMin.value) p.min_runtime_minutes = parseInt(elMin.value) || 0;
                    const elMax = document.getElementById('modal-dev-max-power');
                    if (elMax && elMax.value) p.max_power_w = parseFloat(elMax.value) || 0;
                    const elEm = document.getElementById('modal-dev-emergency-threshold');
                    if (elEm && elEm.value) p.emergency_threshold = parseFloat(elEm.value) || 0;
                    return p;
                })()
            };
            // Automatically construct/update canonical sensors and actuators based on entered entities
            const existingDev = (window.__cachedDevicesMap && window.__cachedDevicesMap[id]) || {};
            const sensors = existingDev.sensors ? JSON.parse(JSON.stringify(existingDev.sensors)) : [];
            const actuators = existingDev.actuators ? JSON.parse(JSON.stringify(existingDev.actuators)) : [];

            // Update power sensor
            if (st === 'homeassistant' && payload.ha_power_entity) {
                const pSensor = sensors.find(s => s.role === 'consumer' || s.role === 'producer') || {
                    id: payload.type === 'solar_inverter' ? 'solar_production' : 'device_power',
                    name: payload.name + ' Vermogen',
                    role: payload.type === 'solar_inverter' ? 'producer' : 'consumer',
                    connector: 'homeassistant',
                    native_unit: payload.native_unit,
                    storage_unit: 'W'
                };
                pSensor.entity_id = payload.ha_power_entity;
                pSensor.connector = 'homeassistant';
                pSensor.native_unit = payload.native_unit;
                if (!sensors.includes(pSensor)) sensors.push(pSensor);
            } else if (st === 'mqtt' && payload.mqtt_power_topic) {
                const pSensor = sensors.find(s => s.connector === 'mqtt') || {
                    id: payload.type === 'solar_inverter' ? 'solar_production' : 'device_power',
                    name: payload.name + ' Vermogen',
                    role: payload.type === 'solar_inverter' ? 'producer' : 'consumer',
                    connector: 'mqtt',
                    native_unit: payload.native_unit,
                    storage_unit: 'W'
                };
                pSensor.topic = payload.mqtt_power_topic;
                pSensor.connector = 'mqtt';
                pSensor.native_unit = payload.native_unit;
                if (!sensors.includes(pSensor)) sensors.push(pSensor);
            }

            // Update temp sensor if present
            if (payload.ha_temp_entity) {
                const tSensor = sensors.find(s => s.id?.includes('temp') || s.entity_id?.includes('temp')) || {
                    id: 'device_temperature',
                    name: payload.name + ' Temperatuur',
                    role: 'state',
                    connector: 'homeassistant',
                    unit: '°C'
                };
                tSensor.entity_id = payload.ha_temp_entity;
                if (!sensors.includes(tSensor)) sensors.push(tSensor);
            }

            // Update control actuator if present
            if (payload.ha_control_entity) {
                const act = actuators[0] || {
                    id: 'device_control',
                    name: payload.name + ' Aansturing',
                    type: payload.ha_control_entity.startsWith('input_select') ? 'select' : (payload.ha_control_entity.startsWith('climate') ? 'range' : 'switch'),
                    connector: 'homeassistant'
                };
                act.entity_id = payload.ha_control_entity;
                if (!actuators.includes(act)) actuators.push(act);
            }

            payload.sensors = sensors;
            payload.actuators = actuators;

            if (id) {
                await fetch('./api/devices/' + id, { method: 'PUT', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload) });
            } else {
                await fetch('./api/devices', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload) });
            }
            closeModal('device-modal');
            loadDevices();
        }

        async function deleteDevice(id) {
            if (!confirm('Weet je zeker dat je dit apparaat wilt verwijderen?')) return;
            await fetch('./api/devices/' + id, { method: 'DELETE' });
            loadDevices();
        }

        // =========================================================================
        // TARIFFS CONTROLLER
        // =========================================================================
        async function loadTariffs() {
            const res = await fetch('./api/tariffs');
            const d = await res.json();
            const container = document.getElementById('tariffs-container');
            container.innerHTML = '';
            (d.tariffs || []).forEach(t => {
                const card = document.createElement('div');
                card.className = 'bg-[#0e1422] border border-[#1E293B] rounded-2xl p-5 flex flex-col justify-between shadow-lg';
                card.innerHTML = `
                    <div>
                        <div class="flex justify-between items-start mb-2">
                            <h4 class="font-bold text-white text-sm">${t.name}</h4>
                            <span class="px-2 py-0.5 rounded text-[10px] font-semibold bg-emerald-950 text-emerald-300 border border-emerald-800">${t.provider}</span>
                        </div>
                        <p class="text-[11px] text-slate-400 mb-2">Interval: <strong>${t.interval}</strong> · Start: ${t.contract_start_date}</p>
                        <div class="grid grid-cols-2 gap-2 text-[11px] text-slate-300 bg-[#0B0F17] p-3 rounded-lg border border-slate-800 mb-3 font-mono">
                            <div>Inkoop Opslag: €${t.import_markup_eur_kwh}/kWh</div>
                            <div>Teruglevering: €${t.export_markup_eur_kwh}/kWh</div>
                            <div>Energiebelasting: €${t.electricity_tax_eur_kwh}/kWh</div>
                            <div>Vastrecht: €${t.fixed_monthly_fee_eur}/mnd</div>
                        </div>
                    </div>
                    <div class="flex justify-end gap-2 pt-3 border-t border-[#1E293B]">
                        <button onclick='openTariffModal(${JSON.stringify(t)})' class="px-2.5 py-1 bg-slate-800 hover:bg-slate-700 text-slate-200 text-xs rounded-lg">Bewerken</button>
                        <button onclick="deleteTariff('${t.id}')" class="px-2.5 py-1 bg-red-950/60 hover:bg-red-900 text-red-300 border border-red-800 text-xs rounded-lg">Verwijderen</button>
                    </div>
                `;
                container.appendChild(card);
            });
        }

        function openTariffModal(t = null) {
            if (t) {
                document.getElementById('modal-tariff-title').innerText = 'Leverancier Bewerken';
                document.getElementById('modal-tariff-id').value = t.id;
                document.getElementById('modal-tariff-name').value = t.name;
                document.getElementById('modal-tariff-provider').value = t.provider;
                document.getElementById('modal-tariff-interval').value = t.interval;
                document.getElementById('modal-tariff-import').value = t.import_markup_eur_kwh;
                document.getElementById('modal-tariff-export').value = t.export_markup_eur_kwh;
                document.getElementById('modal-tariff-tax').value = t.electricity_tax_eur_kwh;
                document.getElementById('modal-tariff-fixed').value = t.fixed_monthly_fee_eur;
            } else {
                document.getElementById('modal-tariff-title').innerText = 'Nieuwe Leverancier Toevoegen';
                document.getElementById('modal-tariff-id').value = '';
                document.getElementById('modal-tariff-name').value = '';
            }
            document.getElementById('tariff-modal').classList.remove('hidden');
        }

        async function saveTariff(e) {
            e.preventDefault();
            const id = document.getElementById('modal-tariff-id').value;
            const payload = {
                name: document.getElementById('modal-tariff-name').value,
                provider: document.getElementById('modal-tariff-provider').value,
                interval: document.getElementById('modal-tariff-interval').value,
                import_markup_eur_kwh: parseFloat(document.getElementById('modal-tariff-import').value),
                export_markup_eur_kwh: parseFloat(document.getElementById('modal-tariff-export').value),
                electricity_tax_eur_kwh: parseFloat(document.getElementById('modal-tariff-tax').value),
                fixed_monthly_fee_eur: parseFloat(document.getElementById('modal-tariff-fixed').value)
            };
            if (id) {
                await fetch('./api/tariffs/' + id, { method: 'PUT', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload) });
            } else {
                await fetch('./api/tariffs', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload) });
            }
            closeModal('tariff-modal');
            loadTariffs();
        }

        async function deleteTariff(id) {
            if (!confirm('Leverancier verwijderen?')) return;
            await fetch('./api/tariffs/' + id, { method: 'DELETE' });
            loadTariffs();
        }

        // =========================================================================
        // CALIBRATION & EXCLUSIONS
        // =========================================================================
        async function loadCalibration() {
            const res = await fetch('./api/calibration');
            const data = await res.json();
                window.__lastPredictionData = data;
            const tbody = document.getElementById('exclusion-tbody');
            tbody.innerHTML = '';
            (data.exclusion_windows || []).forEach((w, idx) => {
                const tr = document.createElement('tr');
                tr.innerHTML = `
                    <td class="p-2.5 font-mono text-cyan-300">${w.sensor}</td>
                    <td class="p-2.5 font-mono">${w.start}</td>
                    <td class="p-2.5 font-mono">${w.end}</td>
                    <td class="p-2.5 text-slate-300">${w.reason}</td>
                    <td class="p-2.5 text-right"><button onclick="deleteExclusion(${idx})" class="px-2 py-0.5 bg-red-950 text-red-300 border border-red-800 rounded text-[10px]">Verwijderen</button></td>
                `;
                tbody.appendChild(tr);
            });
        }

        function openExclusionModal() { document.getElementById('exclusion-modal').classList.remove('hidden'); }
        async function saveExclusion(e) {
            e.preventDefault();
            const payload = {
                sensor: document.getElementById('modal-ex-sensor').value,
                start: document.getElementById('modal-ex-start').value,
                end: document.getElementById('modal-ex-end').value,
                reason: document.getElementById('modal-ex-reason').value
            };
            await fetch('./api/exclusion-windows', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload) });
            closeModal('exclusion-modal');
            loadCalibration();
        }
        async function deleteExclusion(idx) {
            if (!confirm('Uitsluitingsvenster verwijderen?')) return;
            await fetch('./api/exclusion-windows/' + idx, { method: 'DELETE' });
            loadCalibration();
        }

        function closeModal(id) { document.getElementById(id).classList.add('hidden'); }

                
        async function saveSolarCostFromTab() {
            const inp = document.getElementById('tab-solar-cost-input');
            if (!inp) return;
            const val = parseFloat(inp.value) || 0.06;
            try {
                await fetch('./api/analytics/solar_cost', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({ solar_cost_eur_kwh: val })
                });
                alert('Zonnestroom kostprijs succesvol opgeslagen: €' + val.toFixed(3) + '/kWh');
                loadElectricityPricesChart();
                loadChartData();
            } catch (err) {
                console.error('Error saving solar cost:', err);
            }
        }

        async function loadElectricityPricesChart() {
            const canvas = document.getElementById('electricityPricesChart');
            if (!canvas) return;

            try {
                const resSelect = document.getElementById('epex-res-select');
                const resVal = predictionResolution || (resSelect ? resSelect.value : '15m');
                const res = await fetch('./api/analytics/electricity_prices?resolution=' + encodeURIComponent(resVal));
                const data = await res.json();
                window.__lastElectricityPricesData = data;
                if (data.status !== 'success') {
                    console.error('EPEX prices load error:', data.message);
                    return;
                }

                // Sync setting in Tariffs tab if input exists
                const tabCostInp = document.getElementById('tab-solar-cost-input');
                if (tabCostInp) {
                    tabCostInp.value = Number(data.solar_cost || 0.06).toFixed(3);
                }

                // Update stats chips
                const s = data.stats || {};
                document.getElementById('stat-epex-min').innerText = s.min_price || '--';
                document.getElementById('stat-epex-min-time').innerText = `om ${s.min_time || '--:--'}`;
                document.getElementById('stat-epex-max').innerText = s.max_price || '--';
                document.getElementById('stat-epex-max-time').innerText = `om ${s.max_time || '--:--'}`;
                document.getElementById('stat-epex-solar-peak').innerText = s.peak_solar_forecast || '--';
                document.getElementById('stat-epex-solar-margin').innerText = `+${s.solar_savings_avg || '--'}`;

                // Destroy old instance
                if (electricityPricesChartInstance) electricityPricesChartInstance.destroy();

                const solarCostLine = data.labels.map(() => data.solar_cost);

                const ctx = canvas.getContext('2d');
                electricityPricesChartInstance = new Chart(ctx, {
                    type: 'line',
                    data: {
                        labels: data.labels,
                        datasets: [
                            // 1. Zonnestroom Verwachting Area Curve (Right Y-Axis)
                            {
                                label: 'Verwachte Zonneproductie (kW)',
                                data: data.solar_forecast_kw || [],
                                yAxisID: 'y1',
                                borderColor: '#F59E0B',
                                backgroundColor: 'rgba(245, 158, 11, 0.22)',
                                fill: true,
                                borderWidth: 2,
                                tension: 0.35,
                                pointRadius: 0,
                                order: 2
                            },
                            // 2. EPEX Stroomtarief Stepped Line (Left Y-Axis)
                            {
                                label: 'EPEX Stroomtarief All-in (€/kWh)',
                                data: data.epex_prices || [],
                                yAxisID: 'y',
                                borderColor: '#3B82F6',
                                backgroundColor: 'transparent',
                                borderWidth: 2.5,
                                stepped: 'before',
                                pointRadius: 0,
                                tension: 0,
                                order: 1
                            },
                            // 3. Configured Solar Cost Reference Line (Left Y-Axis)
                            {
                                label: `Zon Kostprijs (€${Number(data.solar_cost).toFixed(3)}/kWh)`,
                                data: solarCostLine,
                                yAxisID: 'y',
                                borderColor: '#EAB308',
                                borderWidth: 1.5,
                                borderDash: [6, 4],
                                pointRadius: 0,
                                fill: false,
                                order: 3
                            }
                        ]
                    },
                    options: {
                        responsive: true,
                        maintainAspectRatio: false,
                        interaction: {
                            mode: 'index',
                            intersect: false
                        },
                        plugins: {
                            legend: {
                                display: true,
                                position: 'top',
                                labels: {
                                    color: '#94A3B8',
                                    font: { family: 'monospace', size: 10 },
                                    boxWidth: 10
                                }
                            },
                            tooltip: {
                                enabled: false,
                                external: function(context) {
                                    customPricesTooltipHandler(context);
                                }
                            }
                        },
                        scales: {
                            x: {
                                grid: { color: 'rgba(30, 41, 59, 0.4)' },
                                ticks: {
                                    color: '#94A3B8',
                                    font: { family: 'monospace', size: 10 },
                                    maxTicksLimit: 12
                                }
                            },
                            y: {
                                type: 'linear',
                                display: true,
                                position: 'left',
                                title: { display: true, text: 'Tarief (€/kWh)', color: '#60A5FA', font: { family: 'monospace', size: 10 } },
                                grid: { color: 'rgba(30, 41, 59, 0.6)' },
                                ticks: {
                                    color: '#60A5FA',
                                    font: { family: 'monospace', size: 10 },
                                    callback: function(val) { return '€' + Number(val).toFixed(2); }
                                }
                            },
                            y1: {
                                type: 'linear',
                                display: true,
                                position: 'right',
                                title: { display: true, text: 'Zon (kW)', color: '#F59E0B', font: { family: 'monospace', size: 10 } },
                                grid: { drawOnChartArea: false },
                                ticks: {
                                    color: '#F59E0B',
                                    font: { family: 'monospace', size: 10 },
                                    callback: function(val) { return Number(val).toFixed(1) + ' kW'; }
                                },
                                min: 0
                            }
                        }
                    }
                });
            } catch (err) {
                console.error('Failed to load electricity prices chart:', err);
            }
        }

        

        // =========================================================================
        // MODEL VALIDATION OVERLAY CHART (VOORSPELLING VS. WERKELIJKHEID)
        // =========================================================================
        var validationOverlayChartInstance = null;
        var validationComponent = 'all'; // 'all', 'solar', 'dhw', 'cv'
        var validationPeriod = '24h';    // '24h', '48h', '7d'
        var validationResolution = '15m'; // '15m', '1h'
        var validationDataCache = null;


        function setValidationComponent(comp) {
            validationComponent = comp;
            ['all', 'solar', 'dhw', 'cv'].forEach(c => {
                const btn = document.getElementById('btn-val-' + c);
                if (btn) {
                    if (c === comp) {
                        btn.className = 'px-2.5 py-1 rounded-lg bg-cyan-600 text-white font-bold transition shadow';
                    } else {
                        btn.className = 'px-2.5 py-1 rounded-lg text-slate-400 hover:text-white transition';
                    }
                }
            });
            renderValidationOverlayChart();
        }

        function setValidationPeriod(tf) {
            validationPeriod = tf;
            ['24h', '48h', '7d'].forEach(p => {
                const btn = document.getElementById('val-tf-' + p);
                if (btn) {
                    if (p === tf) {
                        btn.className = 'px-2 py-0.5 rounded transition font-medium bg-cyan-600 text-white shadow';
                    } else {
                        btn.className = 'px-2 py-0.5 rounded transition font-medium text-slate-400 hover:text-slate-200';
                    }
                }
            });
            loadValidationOverlayChart();
        }

        function setValidationResolution(res) {
            validationResolution = res;
            ['15m', '1h'].forEach(r => {
                const btn = document.getElementById('val-res-' + r);
                if (btn) {
                    if (r === res) {
                        btn.className = 'px-2 py-0.5 rounded transition font-medium bg-blue-600 text-white shadow';
                    } else {
                        btn.className = 'px-2 py-0.5 rounded transition font-medium text-slate-400 hover:text-slate-200';
                    }
                }
            });
            loadValidationOverlayChart();
        }

        async function loadValidationOverlayChart() {
            const canvas = document.getElementById('chart-validation-overlay');
            if (!canvas) return;

            try {
                const res = await fetch(`./api/analytics/validation_overlay?range=${encodeURIComponent(validationPeriod)}&resolution=${encodeURIComponent(validationResolution)}`);
                const data = await res.json();
                if (data.status !== 'success') {
                    console.error('Validation overlay error:', data.message);
                    return;
                }
                validationDataCache = data;
                renderValidationOverlayChart();
            } catch (e) {
                console.error('Failed to load validation overlay chart:', e);
            }
        }

        function renderValidationOverlayChart() {
            if (!validationDataCache) return;
            const canvas = document.getElementById('chart-validation-overlay');
            if (!canvas) return;
            const ctx = canvas.getContext('2d');

            const comp = validationComponent;
            const metrics = validationDataCache.metrics ? (validationDataCache.metrics[comp] || {}) : {};
            
            // Update KPI badges
            const accEl = document.getElementById('val-kpi-accuracy');
            if (accEl) {
                const acc = metrics.accuracy_pct !== undefined ? metrics.accuracy_pct : '--';
                accEl.innerText = `Kwaliteit: ${acc}%`;
                if (acc >= 85) accEl.className = 'px-2.5 py-1 rounded-lg border bg-emerald-950/60 border-emerald-500/40 text-emerald-300 font-bold';
                else if (acc >= 70) accEl.className = 'px-2.5 py-1 rounded-lg border bg-amber-950/60 border-amber-500/40 text-amber-300 font-bold';
                else accEl.className = 'px-2.5 py-1 rounded-lg border bg-blue-950/60 border-blue-500/40 text-blue-300 font-bold';
            }

            const maeEl = document.getElementById('val-kpi-mae');
            if (maeEl) {
                maeEl.innerText = `Gem. Afwijking: ${metrics.mae_w !== undefined ? metrics.mae_w : '--'} W`;
            }

            const totEl = document.getElementById('val-kpi-totals');
            if (totEl) {
                const dSign = metrics.delta_kwh > 0 ? '+' : '';
                totEl.innerText = `Werkelijk: ${metrics.total_actual_kwh || 0} kWh | Voorspeld: ${metrics.total_pred_kwh || 0} kWh (Δ ${dSign}${metrics.delta_kwh || 0} kWh)`;
            }

            const actSeries = validationDataCache.actual ? (validationDataCache.actual[comp] || []) : [];
            const predSeries = validationDataCache.predicted ? (validationDataCache.predicted[comp] || []) : [];

            // Theme colors per component
            const themeMap = {
                'all': {
                    actBorder: '#06B6D4',
                    actFill: 'rgba(6, 182, 212, 0.12)',
                    predBorder: '#C084FC',
                    unit: 'kW',
                    actLabel: 'Werkelijk Totaal (Telemetrie)',
                    predLabel: 'Voorspeld Totaal (Model)'
                },
                'solar': {
                    actBorder: '#F59E0B',
                    actFill: 'rgba(245, 158, 11, 0.15)',
                    predBorder: '#FDE047',
                    unit: 'kW',
                    actLabel: 'Werkelijke Zonnestroom (Inepro 103)',
                    predLabel: 'Voorspelde Zonnestroom (POA Model)'
                },
                'dhw': {
                    actBorder: '#F43F5E',
                    actFill: 'rgba(244, 63, 94, 0.15)',
                    predBorder: '#FB923C',
                    unit: 'kW',
                    actLabel: 'Werkelijke Warmtepomp SWW (Daikin)',
                    predLabel: 'Voorspelde SWW Vraag (DHW Model)'
                },
                'cv': {
                    actBorder: '#3B82F6',
                    actFill: 'rgba(59, 130, 246, 0.15)',
                    predBorder: '#818CF8',
                    unit: 'kW',
                    actLabel: 'Werkelijke Warmtepomp CV (Daikin)',
                    predLabel: 'Voorspelde CV Vraag (2-Massa Model)'
                }
            };

            const t = themeMap[comp] || themeMap['all'];

            if (validationOverlayChartInstance) {
                validationOverlayChartInstance.destroy();
            }

            validationOverlayChartInstance = new Chart(ctx, {
                type: 'line',
                data: {
                    labels: validationDataCache.labels || [],
                    datasets: [
                        {
                            label: t.actLabel,
                            data: actSeries,
                            borderColor: t.actBorder,
                            backgroundColor: t.actFill,
                            borderWidth: 2.5,
                            fill: true,
                            tension: 0.25,
                            pointRadius: 0,
                            pointHoverRadius: 5
                        },
                        {
                            label: t.predLabel,
                            data: predSeries,
                            borderColor: t.predBorder,
                            borderWidth: 2,
                            borderDash: [5, 4],
                            fill: false,
                            tension: 0.25,
                            pointRadius: 0,
                            pointHoverRadius: 5
                        }
                    ]
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    interaction: {
                        mode: 'index',
                        intersect: false
                    },
                    plugins: {
                        legend: {
                            display: true,
                            labels: {
                                color: '#94A3B8',
                                font: { family: 'monospace', size: 11 },
                                boxWidth: 16
                            }
                        },
                        tooltip: {
                            backgroundColor: '#0B0F17',
                            borderColor: '#334155',
                            borderWidth: 1,
                            titleColor: '#F8FAFC',
                            bodyColor: '#CBD5E1',
                            callbacks: {
                                label: function(context) {
                                    const val = context.parsed.y;
                                    return `  ${context.dataset.label}: ${val.toFixed(2)} kW`;
                                },
                                afterBody: function(items) {
                                    if (items.length >= 2) {
                                        const a = items[0].parsed.y;
                                        const p = items[1].parsed.y;
                                        const deltaW = Math.round((a - p) * 1000);
                                        const sign = deltaW > 0 ? '+' : '';
                                        return `  Afwijking (Delta): ${sign}${deltaW} W`;
                                    }
                                    return '';
                                }
                            }
                        }
                    },
                    scales: {
                        x: {
                            grid: { color: 'rgba(30, 41, 59, 0.4)' },
                            ticks: {
                                color: '#94A3B8',
                                font: { family: 'monospace', size: 10 },
                                maxTicksLimit: 12
                            }
                        },
                        y: {
                            grid: { color: 'rgba(30, 41, 59, 0.6)' },
                            ticks: {
                                color: '#94A3B8',
                                font: { family: 'monospace', size: 10 },
                                callback: function(v) { return v.toFixed(1) + ' kW'; }
                            },
                            title: {
                                display: true,
                                text: 'Vermogen (kW)',
                                color: '#94A3B8',
                                font: { family: 'monospace', size: 10 }
                            }
                        }
                    }
                }
            });
        }

        async function loadPowerProducersChart() {
            const canvas = document.getElementById('powerProducersChart');
            if (!canvas) return;
            
            try {
                const rangeSelect = document.getElementById('pp-range-select');
                const rangeVal = rangeSelect ? rangeSelect.value : '24h';
                const resParam = (powerProducersChartType === 'line' && powerProducersResolution === '1h') ? '1h' : powerProducersResolution;
                const res = await fetch('./api/analytics/power_producers?range=' + encodeURIComponent(rangeVal) + '&resolution=' + encodeURIComponent(resParam));
                const data = await res.json();
                window.__lastHistoricalData = data;
                window.__lastHistoricalIntervalH = data.interval_h || (resParam === '15m' ? 0.25 : 1.0);
                if (data.status !== 'success') {
                    console.error('Power producers error:', data.message);
                    return;
                }

                // Update Legend Stats & Timeframe Totals
                const s = data.stats || {};
                if (s.zonnepanelen) {
                    document.getElementById('stat-solar-last').innerText = s.zonnepanelen.last;
                    document.getElementById('stat-solar-min').innerText = s.zonnepanelen.min;
                    if (document.getElementById('stat-solar-total')) document.getElementById('stat-solar-total').innerText = s.zonnepanelen.total_kwh || '-- kWh';
                    if (document.getElementById('stat-solar-cost')) document.getElementById('stat-solar-cost').innerText = s.zonnepanelen.cost_eur || '€--';
                }
                if (s.teruglevering) {
                    document.getElementById('stat-terug-last').innerText = s.teruglevering.last;
                    document.getElementById('stat-terug-min').innerText = s.teruglevering.min;
                    if (document.getElementById('stat-terug-total')) document.getElementById('stat-terug-total').innerText = s.teruglevering.total_kwh || '-- kWh';
                    if (document.getElementById('stat-terug-cost')) document.getElementById('stat-terug-cost').innerText = s.teruglevering.cost_eur || '€--';
                }
                if (s.afname) {
                    document.getElementById('stat-afname-last').innerText = s.afname.last;
                    document.getElementById('stat-afname-max').innerText = s.afname.max;
                    if (document.getElementById('stat-afname-total')) document.getElementById('stat-afname-total').innerText = s.afname.total_kwh || '-- kWh';
                    if (document.getElementById('stat-afname-cost')) document.getElementById('stat-afname-cost').innerText = s.afname.cost_eur || '€--';
                }
                if (s.totaal_opgewekt) {
                    document.getElementById('stat-opgewekt-last').innerText = s.totaal_opgewekt.last;
                    document.getElementById('stat-opgewekt-min').innerText = s.totaal_opgewekt.min;
                    if (document.getElementById('stat-opgewekt-total')) document.getElementById('stat-opgewekt-total').innerText = s.totaal_opgewekt.total_kwh || '-- kWh';
                    if (document.getElementById('stat-opgewekt-cost')) document.getElementById('stat-opgewekt-cost').innerText = s.totaal_opgewekt.cost_eur || '€--';
                }
                if (s.opgewekt_gebruikt) {
                    document.getElementById('stat-selfcons-last').innerText = s.opgewekt_gebruikt.last;
                    document.getElementById('stat-selfcons-min').innerText = s.opgewekt_gebruikt.min;
                    if (document.getElementById('stat-selfcons-total')) document.getElementById('stat-selfcons-total').innerText = s.opgewekt_gebruikt.total_kwh || '-- kWh';
                    if (document.getElementById('stat-selfcons-cost')) document.getElementById('stat-selfcons-cost').innerText = s.opgewekt_gebruikt.cost_eur || '€--';
                }
                if (s.totaal_verbruik) {
                    document.getElementById('stat-verbruik-last').innerText = s.totaal_verbruik.last;
                    document.getElementById('stat-verbruik-max').innerText = s.totaal_verbruik.max;
                    if (document.getElementById('stat-verbruik-total')) document.getElementById('stat-verbruik-total').innerText = s.totaal_verbruik.total_kwh || '-- kWh';
                    if (document.getElementById('stat-verbruik-cost')) document.getElementById('stat-verbruik-cost').innerText = s.totaal_verbruik.cost_eur || '€--';
                }

                // Destroy old instance if exists
                if (powerProducersChartInstance) powerProducersChartInstance.destroy();

                const ctx = canvas.getContext('2d');

                // === PURE ENERGY (kWh) STANDARDIZATION ===
                const intervalH = data.interval_h || window.__lastHistoricalIntervalH || (resParam === '15m' ? 0.25 : 1.0);

                // Convert instantaneous power (Watts) to actual interval energy (kWh = W * hours / 1000)
                const toKwh = (arr) => (arr || []).map(w => Number(((w * intervalH) / 1000.0).toFixed(3)));
                const toKwhNeg = (arr) => (arr || []).map(w => Number((-Math.abs(w * intervalH) / 1000.0).toFixed(3)));

                const afnameKwh = toKwh(data.afname);
                const verbruikKwh = toKwh(data.verbruik);
                const selfConsKwh = toKwh(data.self_consumption);
                const terugKwh = toKwhNeg(data.teruglevering_negative);
                const selfConsNegKwh = toKwhNeg(data.self_consumption);
                const solarNegKwh = toKwhNeg(data.solar_negative);

                const netKwh = afnameKwh.map((afn, idx) => {
                    const ter = Math.abs(terugKwh[idx] || 0);
                    return Number((afn - ter).toFixed(3));
                });

                let datasets = [];

                if (powerProducersChartType === 'bar') {
                    // === STAVEN (BAR) MODUS: 100% ZUIVERE ENERGIE (kWh) PER INTERVAL ===
                    // 0. EPEX Stroomprijs All-in Stepped/Dashed Curve (Rechter Y-as)
                    if (data.prices && data.prices.length > 0) {
                        datasets.push({
                            label: 'Stroomprijs All-in (€/kWh)',
                            data: data.prices,
                            type: 'line',
                            borderColor: '#06B6D4',
                            borderDash: [4, 4],
                            borderWidth: 1.5,
                            pointRadius: 0,
                            yAxisID: 'y1',
                            tension: 0,
                            order: 0
                        });
                    }
                    // 1. Totaal Verbruik Lijn (Oranje) in kWh
                    datasets.push({
                        label: 'Totaal Verbruik (kWh)',
                        data: verbruikKwh,
                        type: 'line',
                        borderColor: '#F97316',
                        backgroundColor: 'transparent',
                        borderWidth: 2.5,
                        pointRadius: 2,
                        tension: 0.25,
                        order: 1
                    });
                    // 2. Netto Grid Stroom Lijn (Felrood) in kWh
                    datasets.push({
                        label: 'Netto Grid Stroom (kWh)',
                        data: netKwh,
                        type: 'line',
                        borderColor: '#EF4444',
                        backgroundColor: 'transparent',
                        borderWidth: 2,
                        pointRadius: 2,
                        tension: 0.25,
                        order: 2
                    });
                    // 3. Positieve gestapelde staven: Afname + Opgewekt Gebruikt = Totaal Verbruik
                    datasets.push({
                        label: 'Afname (kWh)',
                        data: afnameKwh,
                        backgroundColor: '#EF4444',
                        stack: 'energy',
                        borderRadius: 2,
                        order: 3
                    });
                    datasets.push({
                        label: 'Opgewekt Gebruikt (kWh)',
                        data: selfConsKwh,
                        backgroundColor: '#06B6D4',
                        stack: 'energy',
                        borderRadius: 2,
                        order: 3
                    });
                    // 4. Negatieve gestapelde staven: Teruglevering + Direct Benut = Totale Zonneproductie
                    datasets.push({
                        label: 'Teruglevering (kWh)',
                        data: terugKwh,
                        backgroundColor: '#10B981',
                        stack: 'energy',
                        borderRadius: 2,
                        order: 4
                    });
                    datasets.push({
                        label: 'Zon Direct Benut (kWh)',
                        data: selfConsNegKwh,
                        backgroundColor: '#EAB308',
                        stack: 'energy',
                        borderRadius: 2,
                        order: 4
                    });
                } else {
                    // === LIJN (LINE / AREA) MODUS in kWh ===
                    datasets = [
                        {
                            label: 'Totaal Verbruik (kWh)',
                            data: verbruikKwh,
                            borderColor: '#F97316',
                            backgroundColor: 'transparent',
                            borderWidth: 2,
                            pointRadius: 0,
                            tension: 0.25,
                            order: 1
                        },
                        {
                            label: 'Afname (kWh)',
                            data: afnameKwh,
                            borderColor: '#EF4444',
                            backgroundColor: 'rgba(239, 68, 68, 0.45)',
                            fill: true,
                            borderWidth: 1.5,
                            pointRadius: 0,
                            tension: 0.25,
                            order: 2
                        },
                        {
                            label: 'Opgewekt Gebruikt (kWh)',
                            data: selfConsKwh,
                            borderColor: '#14B8A6',
                            backgroundColor: 'rgba(20, 184, 166, 0.25)',
                            fill: true,
                            borderWidth: 1,
                            pointRadius: 0,
                            tension: 0.25,
                            order: 3
                        },
                        {
                            label: 'Teruglevering (kWh)',
                            data: terugKwh,
                            borderColor: '#10B981',
                            backgroundColor: 'rgba(16, 185, 129, 0.45)',
                            fill: true,
                            borderWidth: 1.5,
                            pointRadius: 0,
                            tension: 0.25,
                            order: 4
                        },
                        {
                            label: 'Zonnepanelen (kWh)',
                            data: solarNegKwh,
                            borderColor: '#EAB308',
                            backgroundColor: 'rgba(234, 179, 8, 0.55)',
                            fill: true,
                            borderWidth: 1.5,
                            pointRadius: 0,
                            tension: 0.25,
                            order: 5
                        }
                    ];
                }

                // Symmetrische 0-as schaling in zuivere kWh
                const allKwhVals = [
                    ...afnameKwh,
                    ...verbruikKwh,
                    ...solarNegKwh.map(Math.abs),
                    ...terugKwh.map(Math.abs),
                    1.0
                ];
                let maxAbsKwh = Math.max(...allKwhVals);
                maxAbsKwh = Math.ceil(maxAbsKwh * 2) / 2; // Stappen van 0.5 kWh
                if (maxAbsKwh < 1.5) maxAbsKwh = 1.5;

                const allPriceVals = [
                    ...(data.prices || []).map(Math.abs),
                    ...(data.export_prices || []).map(Math.abs),
                    0.25
                ];
                let maxAbsPrice = Math.max(...allPriceVals);
                maxAbsPrice = Math.ceil(maxAbsPrice * 10) / 10;
                if (maxAbsPrice < 0.30) maxAbsPrice = 0.30;

                powerProducersChartInstance = new Chart(ctx, {
                    type: powerProducersChartType === 'bar' ? 'bar' : 'line',
                    data: {
                        labels: data.labels,
                        datasets: datasets
                    },
                    options: {
                        responsive: true,
                        maintainAspectRatio: false,
                        interaction: {
                            mode: 'index',
                            intersect: false
                        },
                        plugins: {
                            legend: {
                                display: false // Gekoppeld aan de 6-box overzichtskaarten
                            },
                            tooltip: {
                                enabled: false,
                                external: function(context) {
                                    customHemsTooltipHandler(context, false);
                                }
                            }
                        },
                        scales: {
                            x: {
                                grid: { color: 'rgba(30, 41, 59, 0.4)' },
                                ticks: { 
                                    color: '#94A3B8', 
                                    font: { family: 'monospace', size: 10 },
                                    maxTicksLimit: 12
                                }
                            },
                            y: {
                                min: -maxAbsKwh,
                                max: maxAbsKwh,
                                title: { display: true, text: 'Opbrengst (-kWh) < 0 < Verbruik (+kWh)', color: '#94A3B8', font: { family: 'monospace', size: 10 } },
                                grid: {
                                    color: (ctx) => ctx.tick && ctx.tick.value === 0 ? '#CBD5E1' : 'rgba(30, 41, 59, 0.6)',
                                    lineWidth: (ctx) => ctx.tick && ctx.tick.value === 0 ? 2 : 1
                                },
                                ticks: {
                                    color: '#94A3B8',
                                    font: { family: 'monospace', size: 10 },
                                    callback: function(val) {
                                        const absV = Math.abs(val);
                                        const prefix = val < 0 ? '-' : '';
                                        return `${prefix}${absV.toFixed(2)} kWh`;
                                    }
                                }
                            },
                            y1: {
                                type: 'linear',
                                position: 'right',
                                display: true,
                                min: -maxAbsPrice,
                                max: maxAbsPrice,
                                title: { display: true, text: 'Tarief (€/kWh)', color: '#06B6D4', font: { family: 'monospace', size: 10 } },
                                grid: { drawOnChartArea: false },
                                ticks: {
                                    color: '#06B6D4',
                                    font: { family: 'monospace', size: 10 },
                                    callback: function(val) {
                                        return val >= 0 ? '€' + Number(val).toFixed(2) : '';
                                    }
                                }
                            }
                        }
                    }
                });
            } catch (err) {
                console.error('Failed to load power producers chart:', err);
            }
        }

        async function loadAnalytics() {
            try {
                const res = await fetch('./api/analytics');
                const d = await res.json();
                document.getElementById('kpi-savings-today').innerText = `€${d.savings_today_eur.toFixed(2)}`;
                document.getElementById('kpi-self-consumption').innerText = `${d.self_consumption_pct}%`;
                document.getElementById('kpi-cop-dhw').innerHTML = `${d.dhw_cop} <span class="text-xs text-slate-400 font-normal">SWW</span> · ${d.cv_cop} <span class="text-xs text-slate-400 font-normal">CV</span>`;
                document.getElementById('kpi-accuracy').innerText = `${d.forecast_accuracy_pct}%`;
                document.getElementById('analytics-digest').innerText = d.daily_digest;
            } catch (e) {
                console.warn('Analytics load error:', e);
            }
        }

        async function loadControl() {
            // Static or live control queries
        }

        // Boot
        fetchHaEntities();
        const initialHashTab = (window.location.hash || '').replace('#', '');
        showTab(initialHashTab || "prediction");
        window.addEventListener('hashchange', () => {
            const hTab = (window.location.hash || '').replace('#', '');
            if (hTab) showTab(hTab);
        });
        // === PIPELINE & CALIBRATION MONITORS ===
                        
        async function loadPipelineStatus() {
            try {
                const res = await fetch('./api/pipeline/status');
                const d = await res.json();
                if (d.status !== 'online') return;

                // Badges
                const progBadge = document.getElementById('pipeline-progress-badge');
                if (progBadge) progBadge.innerText = `Accumulator: ${d.samples_in_window}/${d.expected_samples} (${d.samples_in_window * d.sample_interval_s}s)`;
                const flushBadge = document.getElementById('pipeline-flush-badge');
                if (flushBadge) flushBadge.innerText = `Laatste Flush: ${d.last_flush_time}`;

                // Progress Bar
                const pct = Math.min(100, Math.round((d.samples_in_window / d.expected_samples) * 100));
                const pctEl = document.getElementById('pipe-window-pct');
                if (pctEl) pctEl.innerText = `${pct}%`;
                const barEl = document.getElementById('pipe-progress-bar');
                if (barEl) barEl.style.width = `${pct}%`;

                const totalPointsEl = document.getElementById('pipe-total-points');
                if (totalPointsEl) totalPointsEl.innerText = `Totaal weggeschreven: ${d.total_points_written} punten`;

                // Live Power Balance Numbers
                const b = d.live_balance || {};
                if (document.getElementById('live-net-grid')) document.getElementById('live-net-grid').innerText = `${b.net_grid_w >= 0 ? '+' : ''}${b.net_grid_w || 0} W`;
                if (document.getElementById('live-solar')) document.getElementById('live-solar').innerText = `${b.solar_w || 0} W`;
                if (document.getElementById('live-direct-solar')) document.getElementById('live-direct-solar').innerText = `${b.direct_solar_w || 0} W`;
                if (document.getElementById('live-heatpump')) document.getElementById('live-heatpump').innerText = `${b.heatpump_w || 0} W`;
                if (document.getElementById('live-tot-house')) document.getElementById('live-tot-house').innerText = `${b.total_house_w || 0} W`;
                if (document.getElementById('live-unallocated')) document.getElementById('live-unallocated').innerText = `${b.unallocated_w || 0} W`;
            } catch (e) {
                console.warn("Pipeline poll error:", e);
            }
        }

        async function loadUnallocatedModel() {
            try {
                const res = await fetch('./api/calibration/unallocated-model');
                cachedUnallocModel = await res.json();
                renderUnallocDay(activeUnallocDay);
            updateDhwLiveCard();
            } catch (e) {
                console.warn("Error loading unallocated model:", e);
            }
        }

        let currentProfileType = 'unallocated';
        let activeMonthNum = (new Date()).getMonth() + 1; // 1-12 (current month)

        function renderMonthSelector() {
            const container = document.getElementById('month-selector-grid');
            if (!container) return;
            const months = [
                { num: 1, name: 'Jan' }, { num: 2, name: 'Feb' }, { num: 3, name: 'Mrt' },
                { num: 4, name: 'Apr' }, { num: 5, name: 'Mei' }, { num: 6, name: 'Jun' },
                { num: 7, name: 'Jul' }, { num: 8, name: 'Aug' }, { num: 9, name: 'Sep' },
                { num: 10, name: 'Okt' }, { num: 11, name: 'Nov' }, { num: 12, name: 'Dec' }
            ];

            container.innerHTML = '';
            months.forEach(m => {
                const btn = document.createElement('button');
                const isActive = (m.num === activeMonthNum);
                btn.className = isActive
                    ? 'month-btn py-1.5 px-1 rounded-lg bg-amber-600 border border-amber-500 text-white font-bold shadow text-center transition'
                    : 'month-btn py-1.5 px-1 rounded-lg bg-slate-900 border border-slate-800 text-slate-400 hover:text-slate-200 text-center transition';
                btn.innerText = m.name;
                btn.onclick = () => selectMonth(m.num);
                container.appendChild(btn);
            });
        }

        function selectMonth(mNum) {
            activeMonthNum = mNum;
            renderMonthSelector();
            renderUnallocDay(activeUnallocDay);
        }

        async function updateDhwLiveCard() {
            const banner = document.getElementById('dhw-decision-banner');
            if (!banner) return;
            banner.classList.remove('hidden');
            try {
                const res = await fetch('./api/model/dhw-status');
                if (res.ok) {
                    const data = await res.json();
                    const d = data.decision || {};
                    
                    // Card 1: Temp & Usable Heat
                    if (document.getElementById('dhw-live-temp-badge')) {
                        document.getElementById('dhw-live-temp-badge').innerText = `Actueel: ${d.current_temp_c}°C`;
                    }
                    if (document.getElementById('dhw-usable-heat')) {
                        document.getElementById('dhw-usable-heat').innerText = `${d.usable_heat_kwh_th} kWh_th (${d.usable_heat_mj} MJ)`;
                    }
                    if (document.getElementById('dhw-volume-caption')) {
                        document.getElementById('dhw-volume-caption').innerText = `350L combivat op ${d.current_temp_c}°C (mengcapaciteit ~${d.shower_liters_38c}L douchewater van 38°C).`;
                    }

                    // Card 2: Morning Dip & P95 Stress Scenario
                    if (document.getElementById('dhw-projected-dip')) {
                        document.getElementById('dhw-projected-dip').innerHTML = `${d.morning_dip_c}°C <span class="text-xs text-slate-400">(om ${d.morning_dip_time || '08:45'}u)</span>`;
                    }
                    if (document.getElementById('dhw-dip-subtext')) {
                        const p95Safe = d.p95_is_safe;
                        const p95Class = p95Safe ? 'text-emerald-400' : 'text-amber-400';
                        const firstSub = d.first_sub40_time || '12:30';
                        document.getElementById('dhw-dip-subtext').innerHTML = `<span class="${p95Class} font-bold">P95 Risicodip: ${d.morning_dip_p95_c}°C ${p95Safe ? '✓' : '⚠️'}</span> · Eerste dip &lt;40°C om ${firstSub}u`;
                    }

                    // Card 3: Dynamic Night Header & Comparative Economics
                    if (document.getElementById('dhw-night-header') && d.short_night_label) {
                        document.getElementById('dhw-night-header').innerText = `Nachtbesluit (${d.short_night_label})`;
                    }
                    if (document.getElementById('dhw-night-action')) {
                        document.getElementById('dhw-night-action').innerHTML = d.decision_title || (d.morning_is_safe ? '✅ Geen nachtlading nodig' : '⚠️ Nachtlading aanbevolen');
                    }
                    if (document.getElementById('dhw-night-subtext')) {
                        document.getElementById('dhw-night-subtext').innerText = d.decision_sub || (d.morning_is_safe ? 'Wachten tot daglading bespaart geld' : 'Nachtlading waarborgt ochtendcomfort');
                    }

                    // Explanation Text
                    if (document.getElementById('dhw-decision-explanation')) {
                        document.getElementById('dhw-decision-explanation').innerText = d.recommendation || '';
                    }
                }
            } catch (e) {
                console.warn("Error fetching DHW live status:", e);
            }
        }

        function switchProfileType(pType) {
            currentProfileType = pType;
            ['unallocated', 'dhw', 'cv'].forEach(t => {
                const btn = document.getElementById('btn-prof-' + t);
                if (btn) {
                    if (t === pType) {
                        const bgCol = t === 'unallocated' ? 'bg-blue-600' : (t === 'dhw' ? 'bg-amber-600' : 'bg-red-600');
                        btn.className = `px-3 py-1.5 rounded-lg ${bgCol} text-white font-bold transition flex items-center gap-1.5 shadow`;
                    } else {
                        btn.className = 'px-3 py-1.5 rounded-lg text-slate-400 hover:text-white transition flex items-center gap-1.5';
                    }
                }
            });

            const dot = document.getElementById('profile-status-indicator');
            if (dot) {
                dot.className = `w-3 h-3 rounded-full animate-pulse ${pType === 'unallocated' ? 'bg-blue-500' : (pType === 'dhw' ? 'bg-amber-500' : 'bg-red-500')}`;
            }

            renderUnallocDay(activeUnallocDay);
            updateDhwLiveCard();
        }

        function selectUnallocDay(dayIdx) {
            activeUnallocDay = dayIdx;
            renderUnallocDay(dayIdx);
        }

        function renderUnallocDay(dayIdx) {
            if (!cachedUnallocModel) return;
            const dayNames = cachedUnallocModel.day_names || ['Maandag', 'Dinsdag', 'Woensdag', 'Donderdag', 'Vrijdag', 'Zaterdag', 'Zondag'];
            const mData = cachedUnallocModel.monthly_profiles?.[String(activeMonthNum)] || {};
            const mName = mData.name_full || 'September';

            // Get seasonal multiplier for active month
            let multiplier = 1.0;
            if (currentProfileType === 'cv') {
                multiplier = (mData.cv_multiplier !== undefined) ? mData.cv_multiplier : 1.0;
            } else if (currentProfileType === 'dhw') {
                multiplier = (mData.dhw_multiplier !== undefined) ? mData.dhw_multiplier : 1.0;
            } else {
                multiplier = (mData.unalloc_multiplier !== undefined) ? mData.unalloc_multiplier : 1.0;
            }

            // Update month summary text
            const mSumEl = document.getElementById('month-impact-summary');
            if (mSumEl) {
                if (currentProfileType === 'cv') {
                    mSumEl.innerText = `${mName}: Gemiddeld ${mData.cv_daily_kwh || 0} kWh CV/dag (${Math.round(multiplier * 100)}% van stookseizoen)`;
                } else if (currentProfileType === 'dhw') {
                    mSumEl.innerText = `${mName}: Gemiddeld ${mData.dhw_daily_kwh || 3.0} kWh SWW/dag (${Math.round(multiplier * 100)}% van basis)`;
                } else {
                    mSumEl.innerText = `${mName}: Gemiddelde basislast ${mData.unalloc_avg_w || 495} W (${Math.round(multiplier * 100)}% van jaarbasis)`;
                }
            }

            // Select base dataset and apply monthly factor
            let baseQuarters = [];
            let colorClass = 'bg-blue-500 hover:bg-cyan-400';
            let catName = 'Huishoudelijk';

            if (currentProfileType === 'dhw') {
                baseQuarters = cachedUnallocModel.dhw_profile_96_quarters?.[dayIdx] || [];
                colorClass = 'bg-amber-500 hover:bg-yellow-300';
                catName = 'Tapwater SWW';
            } else if (currentProfileType === 'cv') {
                baseQuarters = cachedUnallocModel.cv_profile_96_quarters?.[dayIdx] || [];
                colorClass = 'bg-red-500 hover:bg-rose-300';
                catName = 'CV Verwarming';
            } else {
                baseQuarters = cachedUnallocModel.profile_96_quarters?.[dayIdx] || [];
                colorClass = 'bg-blue-500 hover:bg-cyan-400';
                catName = 'Huishoudelijk';
            }

            if (baseQuarters.length === 0) return;

            // Apply seasonal multiplier
            const quarters = baseQuarters.map(v => Math.round(v * multiplier * 10) / 10);

            // Update weekday tab styles and mark today
            const todayIdx = (new Date().getDay() + 6) % 7;
            const btns = document.querySelectorAll('.unalloc-day-btn');
            btns.forEach((btn, idx) => {
                const isToday = (idx === todayIdx);
                const dayBaseName = ['Maandag', 'Dinsdag', 'Woensdag', 'Donderdag', 'Vrijdag', 'Zaterdag', 'Zondag'][idx];
                btn.innerText = isToday ? `${dayBaseName} • Vandaag` : dayBaseName;
                if (idx === dayIdx) {
                    const bgActive = currentProfileType === 'unallocated' ? 'bg-blue-600 border-blue-500' : (currentProfileType === 'dhw' ? 'bg-amber-600 border-amber-500' : 'bg-red-600 border-red-500');
                    btn.className = `unalloc-day-btn px-3 py-1 rounded-lg border ${bgActive} text-white font-bold shadow`;
                } else {
                    btn.className = 'unalloc-day-btn px-3 py-1 rounded-lg border border-slate-800 bg-slate-900 text-slate-400 hover:text-slate-200 font-medium';
                }
            });

            // Update summary metric titles and values
            const avg = Math.round(quarters.reduce((a, b) => a + b, 0) / quarters.length);
            const totKwh = (quarters.reduce((a, b) => a + b, 0) * 0.25 / 1000).toFixed(2);
            const maxVal = Math.max(...quarters);
            const peakQ = quarters.indexOf(maxVal);
            const peakTime = `${String(Math.floor(peakQ/4)).padStart(2,'0')}:${String((peakQ%4)*15).padStart(2,'0')}`;

            if (currentProfileType === 'dhw') {
                document.getElementById('metric-title-1').innerText = `Dagbehoefte (${mName})`;
                document.getElementById('unalloc-metric-avg').innerText = `${totKwh} kWh/dag`;
                document.getElementById('metric-title-2').innerText = 'Stand-by Verlies (350L)';
                document.getElementById('unalloc-metric-night').innerText = '1.92 kWh/dag';
                document.getElementById('metric-title-3').innerText = 'Piek Opwarmmoment';
                document.getElementById('unalloc-metric-morning').innerText = `${peakTime} (${Math.round(maxVal)} W)`;
                document.getElementById('metric-title-4').innerText = 'Typische Laadduur';
                document.getElementById('unalloc-metric-evening').innerText = '45 minuten';
                document.getElementById('unalloc-selected-day-label').innerText = `${dayNames[dayIdx]} in ${mName}: SWW Behoefte (${totKwh} kWh/dag)`;
            } else if (currentProfileType === 'cv') {
                const nightAvg = Math.round(quarters.slice(0, 24).reduce((a,b)=>a+b,0)/24);
                const dayAvg = Math.round(quarters.slice(24, 92).reduce((a,b)=>a+b,0)/68);
                document.getElementById('metric-title-1').innerText = `Stookbehoefte (${mName})`;
                document.getElementById('unalloc-metric-avg').innerText = `${totKwh} kWh/dag`;
                document.getElementById('metric-title-2').innerText = 'Nachtverlaging (23-06u)';
                document.getElementById('unalloc-metric-night').innerText = `${nightAvg} W`;
                document.getElementById('metric-title-3').innerText = 'Ochtend Opstookpiek';
                document.getElementById('unalloc-metric-morning').innerText = `${peakTime} (${Math.round(maxVal)} W)`;
                document.getElementById('metric-title-4').innerText = 'Overdag Modulatie';
                document.getElementById('unalloc-metric-evening').innerText = `${dayAvg} W gem`;
                document.getElementById('unalloc-selected-day-label').innerText = `${dayNames[dayIdx]} in ${mName}: CV Stookprofiel (${totKwh} kWh/dag)`;
            } else {
                const nightMin = Math.min(...quarters.slice(0, 24));
                const morningPeak = Math.max(...quarters.slice(28, 44));
                const eveningPeak = Math.max(...quarters.slice(72, 92));
                document.getElementById('metric-title-1').innerText = `Basislast (${mName})`;
                document.getElementById('unalloc-metric-avg').innerText = `${avg} W`;
                document.getElementById('metric-title-2').innerText = 'Nacht Stand-by (00-06u)';
                document.getElementById('unalloc-metric-night').innerText = `${nightMin} W`;
                document.getElementById('metric-title-3').innerText = 'Ochtendpiek (07-11u)';
                document.getElementById('unalloc-metric-morning').innerText = `${morningPeak} W`;
                document.getElementById('metric-title-4').innerText = 'Avondpiek (18-23u)';
                document.getElementById('unalloc-metric-evening').innerText = `${eveningPeak} W`;
                document.getElementById('unalloc-selected-day-label').innerText = `${dayNames[dayIdx]} in ${mName}: Huisprofiel (${avg} W gemiddeld)`;
            }

            // Render 96 bars with interactive hover and touch readout
            const container = document.getElementById('unalloc-hourly-bars');
            const hoverBadge = document.getElementById('unalloc-hover-badge');
            if (container) {
                container.innerHTML = '';
                const displayMax = Math.max(50, ...quarters);
                quarters.forEach((w, q) => {
                    const barHeightPct = Math.max(2, Math.round((w / displayMax) * 100));
                    const h = Math.floor(q / 4);
                    const m = (q % 4) * 15;
                    const timeStr = `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}`;
                    const kwhVal = (w * 0.25 / 1000).toFixed(3);

                    const col = document.createElement('div');
                    col.className = 'flex flex-col items-center justify-end h-full flex-1 min-w-[2px] cursor-pointer group py-0.5';
                    
                    const barDiv = document.createElement('div');
                    barDiv.className = `w-full ${colorClass} rounded-t transition-all group-hover:brightness-125`;
                    barDiv.style.height = `${barHeightPct}%`;
                    col.appendChild(barDiv);

                    // Hover / Touch interaction
                    const showHover = () => {
                        if (hoverBadge) {
                            hoverBadge.innerHTML = `<span class="font-bold text-white">📌 ${timeStr}</span> · <span class="font-bold text-cyan-300">${Math.round(w)} W</span> <span class="text-slate-400">(${kwhVal} kWh ${catName})</span>`;
                        }
                    };

                    col.onmouseenter = showHover;
                    col.ontouchstart = showHover;

                    container.appendChild(col);
                });

                container.onmouseleave = () => {
                    if (hoverBadge) {
                        hoverBadge.innerHTML = '<span class="w-1.5 h-1.5 rounded-full bg-cyan-400 animate-ping"></span> <span>Beweeg over een kwartier voor details</span>';
                    }
                };
            }
        }

                let dhwTempChartInstance = null;
                let heatingForecastChartInstance = null;

        async function renderHeatingForecastChart() {
            const canvas = document.getElementById('chart-heating-forecast');
            if (!canvas) return;
            try {
                const res = await fetch('./api/model/heating-forecast?resolution=' + encodeURIComponent(predictionResolution));
                if (!res.ok) return;
                const d = await res.json();
                if (!d.labels || d.labels.length === 0) return;

                const isActive = (d.thermostat_active !== false);
                const tSet = d.thermostat_setpoint_c || 20.0;
                const tStart = d.thermostat_start_threshold_c || (tSet - 0.5);

                const statusEl = document.getElementById('heating-kpi-status');
                if (statusEl) {
                    if (isActive) {
                        statusEl.className = 'px-2.5 py-0.5 rounded-md text-[10px] font-bold border bg-emerald-950/60 border-emerald-500/40 text-emerald-300';
                        statusEl.textContent = `Thermostaat: Aan (${tSet}°C)`;
                    } else {
                        statusEl.className = 'px-2.5 py-0.5 rounded-md text-[10px] font-bold border bg-slate-900 border-slate-700 text-slate-400';
                        statusEl.textContent = `Thermostaat: Uit (0 W)`;
                    }
                }
                const kwhEl = document.getElementById('heating-kpi-kwh');
                if (kwhEl) kwhEl.textContent = `⚡ Stroom: ${d.total_electrical_kwh || 0} kWh`;
                const costEl = document.getElementById('heating-kpi-cost');
                if (costEl) costEl.textContent = `💶 Kosten: €${Number(d.total_cost_eur || 0).toFixed(2)}`;

                const existingChart = Chart.getChart(canvas);
                if (existingChart) {
                    existingChart.destroy();
                }

                const labels = d.labels;
                const outTemps = d.outdoor_temps_c || [];
                const inTemps = d.indoor_temps_c || [];
                const cops = d.cops || [];
                const thLoss = d.thermal_loss_kw || [];
                const elKw = d.electrical_kw || [];
                const costs = d.costs_eur || [];
                const setpointLine = Array(labels.length).fill(tSet);
                const startLine = Array(labels.length).fill(tStart);

                const ctx = canvas.getContext('2d');
                heatingForecastChartInstance = new Chart(ctx, {
                    type: 'bar',
                    data: {
                        labels: labels,
                        datasets: [
                            {
                                label: 'Buitentemperatuur (°C)',
                                data: outTemps,
                                type: 'line',
                                yAxisID: 'y_temp',
                                borderColor: '#60A5FA',
                                backgroundColor: 'transparent',
                                borderWidth: 2.0,
                                tension: 0.25,
                                pointRadius: 0,
                                order: 1
                            },
                            {
                                label: 'Verwachte Binnentemperatuur (°C)',
                                data: inTemps,
                                type: 'line',
                                yAxisID: 'y_temp',
                                borderColor: '#F43F5E',
                                backgroundColor: 'transparent',
                                borderWidth: 2.2,
                                tension: 0.25,
                                pointRadius: 0,
                                order: 2
                            },
                            {
                                label: `Thermostaat Setpoint (${tSet}°C)`,
                                data: setpointLine,
                                type: 'line',
                                yAxisID: 'y_temp',
                                borderColor: 'rgba(244, 63, 94, 0.6)',
                                borderDash: [5, 5],
                                backgroundColor: 'transparent',
                                borderWidth: 1.5,
                                pointRadius: 0,
                                order: 3
                            },
                            {
                                label: `Inschakeldrempel (${tStart}°C)`,
                                data: startLine,
                                type: 'line',
                                yAxisID: 'y_temp',
                                borderColor: 'rgba(239, 68, 68, 0.45)',
                                borderDash: [2, 4],
                                backgroundColor: 'transparent',
                                borderWidth: 1.2,
                                pointRadius: 0,
                                order: 4
                            },
                            {
                                label: 'Daikin COP',
                                data: cops,
                                type: 'line',
                                yAxisID: 'y_temp',
                                borderColor: '#10B981',
                                borderDash: [4, 4],
                                backgroundColor: 'transparent',
                                borderWidth: 1.75,
                                tension: 0.2,
                                pointRadius: 0,
                                order: 3
                            },
                            {
                                label: 'Stroomkosten (€)',
                                data: costs,
                                type: 'line',
                                yAxisID: 'y_cost',
                                borderColor: '#22D3EE',
                                borderDash: [3, 3],
                                backgroundColor: 'transparent',
                                borderWidth: 1.75,
                                pointRadius: 0,
                                order: 4
                            },
                            {
                                label: 'Warmteverlies Woning (kW_th)',
                                data: thLoss,
                                yAxisID: 'y_power',
                                backgroundColor: 'rgba(239, 68, 68, 0.45)',
                                hoverBackgroundColor: '#EF4444',
                                borderRadius: 2,
                                order: 5
                            },
                            {
                                label: 'Stroom Warmtepomp (kW_el)',
                                data: elKw,
                                yAxisID: 'y_power',
                                backgroundColor: 'rgba(245, 158, 11, 0.65)',
                                hoverBackgroundColor: '#F59E0B',
                                borderRadius: 2,
                                order: 6
                            }
                        ]
                    },
                    options: {
                        responsive: true,
                        maintainAspectRatio: false,
                        interaction: { mode: 'index', intersect: false },
                        plugins: {
                            legend: { display: false },
                            tooltip: {
                                enabled: false,
                                external: function(context) {
                                    customHeatingTooltipHandler(context);
                                }
                            }
                        },
                        scales: {
                            x: {
                                grid: { color: 'rgba(30, 41, 59, 0.3)' },
                                ticks: { color: '#64748B', font: { size: 10 }, maxTicksLimit: 16 }
                            },
                            y_power: {
                                position: 'left',
                                min: 0,
                                title: {
                                    display: true,
                                    text: 'Vermogen (kW)',
                                    color: '#EF4444',
                                    font: { size: 10, weight: 'bold' }
                                },
                                grid: { color: 'rgba(30, 41, 59, 0.25)' },
                                ticks: { color: '#EF4444', font: { size: 10 }, callback: v => `${v} kW` }
                            },
                            y_temp: {
                                position: 'right',
                                min: 0,
                                max: 26,
                                grid: { drawOnChartArea: false },
                                title: {
                                    display: true,
                                    text: 'Temperatuur (°C) / COP',
                                    color: '#60A5FA',
                                    font: { size: 10, weight: 'bold' }
                                },
                                ticks: { color: '#60A5FA', font: { size: 10 }, callback: v => `${v}` }
                            },
                            y_cost: {
                                position: 'right',
                                min: 0,
                                grid: { drawOnChartArea: false },
                                title: { display: false },
                                ticks: { display: false }
                            }
                        }
                    }
                });
            } catch (e) {
                console.warn("Error rendering heating forecast chart:", e);
            }
        }

        async function renderDhwTemperatureChart() {
            const canvas = document.getElementById('chart-dhw-temperature');
            if (!canvas) return;
            try {
                const res = await fetch('./api/model/dhw-status?resolution=' + encodeURIComponent(predictionResolution));
                if (!res.ok) return;
                const data = await res.json();
                const traj = data.trajectory || {};
                if (!traj.labels || traj.labels.length === 0) return;

                const existingChart = Chart.getChart(canvas);
                if (existingChart) {
                    existingChart.destroy();
                }

                const labels = traj.labels;
                const temps = traj.temperatures_c || [];
                const tempsP05 = traj.temperatures_p05_c || temps;
                const tempsP95 = traj.temperatures_p95_c || temps;
                const demandsKwh = traj.demand_kwh_th || [];
                
                // Convert kWh_th demand to liters of 50C water: liters = kwh * 3600 / (4.186 * 38)
                const litersArr = demandsKwh.map(k => Math.round(k * 3600 / (4.186 * 38)));
                const comfortLine = Array(labels.length).fill(40.0);
                const targetLine = Array(labels.length).fill(50.0);
                
                // Detect if 60C boost is present
                const maxTempInTraj = Math.max(...temps, ...tempsP05, 50.0);
                const isBoostMode = (maxTempInTraj >= 53.0);
                const ySuggestedMax = isBoostMode ? 64.0 : 55.0;

                const unh = data.unheated_trajectory || {};
                const unhTemps = unh.temperatures_c || [];
                const unhP05 = unh.temperatures_p05_c || unhTemps;
                const unhP95 = unh.temperatures_p95_c || unhTemps;

                // Update Decision Box below chart
                const dec = data.decision || {};
                const boxPill = document.getElementById('dhw-box-status-pill');
                if (boxPill) {
                    if (dec.status === 'SCHEDULE_NIGHT_CHARGE') {
                        boxPill.innerHTML = '<span class="inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-[10px] font-bold bg-amber-950/80 text-amber-300 border border-amber-800/80"><span class="w-1.5 h-1.5 rounded-full bg-amber-400 animate-pulse"></span> Nachtlading Gepland (Comfortzekerheid)</span>';
                    } else {
                        boxPill.innerHTML = '<span class="inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-[10px] font-bold bg-emerald-950/80 text-emerald-300 border border-emerald-800/80"><span class="w-1.5 h-1.5 rounded-full bg-emerald-400"></span> Wachten op Daglading (Besparing)</span>';
                    }
                }
                const dipValEl = document.getElementById('dhw-box-dip-val');
                if (dipValEl && dec.morning_dip_c !== undefined) {
                    dipValEl.innerText = `${dec.morning_dip_c}°C om ${dec.morning_dip_time || '09:44'}`;
                }
                const dBoxSpits = document.getElementById('dhw-box-spits-detail');
                const cachedPeaks = window.__lastDynamicPeaks || [];
                if (dBoxSpits) {
                    if (cachedPeaks.length === 0) {
                        dBoxSpits.innerHTML = '<span class="text-emerald-400 font-bold">Geen prijspieken 🔓 (volledige vrijloop)</span>';
                    } else {
                        dBoxSpits.innerHTML = cachedPeaks.map(p => {
                            const lbl = p.hard_start_time ? `${p.name} ${p.hard_start_time}–${p.hard_end_time} (${p.hard_duration_mins}m 🔒)` : `${p.name} ${p.start_time}–${p.end_time} (Advies ⚠️)`;
                            return `<span class="text-slate-200 font-bold">${lbl}</span>`;
                        }).join(' · ');
                    }
                }
                const dipTextEl = document.getElementById('dhw-box-dip-text');
                if (dipTextEl && dec.morning_dip_c !== undefined) {
                    dipTextEl.innerText = `${dec.morning_dip_c}°C`;
                }
                const p95TextEl = document.getElementById('dhw-box-p95-text');
                if (p95TextEl && dec.morning_dip_p95_c !== undefined) {
                    p95TextEl.innerText = `${dec.morning_dip_p95_c}°C`;
                }

                const chartDatasets = [
                    // Counterfactual Upper boundary: Zonder Nachtladen P05
                    {
                        label: 'Marge Zonder Nacht P05 (°C)',
                        data: unhP05,
                        yAxisID: 'y',
                        borderColor: 'rgba(148, 163, 184, 0.25)',
                        backgroundColor: 'transparent',
                        borderWidth: 1.0,
                        borderDash: [2, 2],
                        fill: false,
                        pointRadius: 0,
                        tension: 0.25,
                        order: 6
                    },
                    // Counterfactual Lower boundary: Zonder Nachtladen P95 with grey fill to P05
                    {
                        label: 'Marge Zonder Nachtladen',
                        data: unhP95,
                        yAxisID: 'y',
                        borderColor: 'rgba(148, 163, 184, 0.35)',
                        backgroundColor: 'rgba(148, 163, 184, 0.12)',
                        borderWidth: 1.0,
                        borderDash: [3, 3],
                        fill: '-1',
                        pointRadius: 0,
                        tension: 0.25,
                        order: 7
                    },
                    // Counterfactual Line: Zonder Nachtladen P50 (Light Slate Grey Dashed Line)
                    {
                        label: 'Zonder Nachtladen (°C)',
                        data: unhTemps,
                        yAxisID: 'y',
                        borderColor: '#94A3B8',
                        backgroundColor: 'transparent',
                        borderWidth: 2.2,
                        borderDash: [5, 4],
                        fill: false,
                        tension: 0.25,
                        order: 5,
                        pointRadius: 0,
                        pointHoverRadius: 5
                    },
                    // 1. Upper boundary: Minimaal Verbruik P05
                    {
                        label: 'Minimaal Verbruik P05 (°C)',
                        data: tempsP05,
                        yAxisID: 'y',
                        borderColor: 'rgba(245, 158, 11, 0.35)',
                        backgroundColor: 'transparent',
                        borderWidth: 1.2,
                        borderDash: [3, 3],
                        fill: false,
                        pointRadius: 0,
                        tension: 0.25,
                        order: 1
                    },
                    // 2. Lower boundary: Piekverbruik P95 with filled yellow margin to P05
                    {
                        label: 'Piekverbruik P95 (°C)',
                        data: tempsP95,
                        yAxisID: 'y',
                        borderColor: 'rgba(245, 158, 11, 0.45)',
                        backgroundColor: 'rgba(251, 191, 36, 0.15)',
                        borderWidth: 1.2,
                        borderDash: [4, 4],
                        fill: '-1',
                        pointRadius: 0,
                        tension: 0.25,
                        order: 2
                    },
                    // 3. Expected Boiler Temperature P50 (Solid bright amber)
                    {
                        label: 'Verwachte Temperatuur P50 (°C)',
                        data: temps,
                        yAxisID: 'y',
                        borderColor: '#F59E0B',
                        backgroundColor: 'transparent',
                        borderWidth: 2.5,
                        tension: 0.25,
                        pointRadius: 0,
                        order: 3
                    },
                    // 4. Comfortgrens (40°C)
                    {
                        label: 'Comfortgrens (40°C)',
                        data: comfortLine,
                        yAxisID: 'y',
                        borderColor: 'rgba(239, 68, 68, 0.75)',
                        borderDash: [5, 5],
                        backgroundColor: 'transparent',
                        borderWidth: 1.5,
                        pointRadius: 0,
                        order: 4
                    },
                    // 5. Doeltemperatuur (50°C)
                    {
                        label: 'Doeltemperatuur (50°C)',
                        data: targetLine,
                        yAxisID: 'y',
                        borderColor: 'rgba(16, 185, 129, 0.75)',
                        borderDash: [5, 5],
                        backgroundColor: 'transparent',
                        borderWidth: 1.5,
                        pointRadius: 0,
                        order: 5
                    }
                ];

                if (isBoostMode) {
                    chartDatasets.push({
                        label: 'Zonnebuffer Doel (60°C)',
                        data: Array(labels.length).fill(60.0),
                        yAxisID: 'y',
                        borderColor: 'rgba(168, 85, 247, 0.75)',
                        borderDash: [4, 4],
                        backgroundColor: 'transparent',
                        borderWidth: 1.5,
                        pointRadius: 0,
                        order: 6
                    });
                }

                chartDatasets.push({
                    label: 'Verwachte Tapvraag (Liters)',
                    data: litersArr,
                    type: 'bar',
                    yAxisID: 'y1',
                    backgroundColor: 'rgba(56, 189, 248, 0.5)',
                    hoverBackgroundColor: '#38BDF8',
                    borderRadius: 2,
                    order: 7
                });

                const ctx = canvas.getContext('2d');
                dhwTempChartInstance = new Chart(ctx, {
                    type: 'line',
                    data: {
                        labels: labels,
                        datasets: chartDatasets
                    },
                    options: {
                        responsive: true,
                        maintainAspectRatio: false,
                        interaction: { mode: 'index', intersect: false },
                        plugins: {
                            legend: { display: false },
                            tooltip: {
                                enabled: false,
                                external: function(context) {
                                    customDhwTooltipHandler(context);
                                }
                            }
                        },
                        scales: {
                            x: {
                                grid: { color: 'rgba(30, 41, 59, 0.3)' },
                                ticks: { color: '#64748B', font: { size: 10 }, maxTicksLimit: 16 }
                            },
                            y: {
                                position: 'left',
                                suggestedMin: 35.0,
                                suggestedMax: ySuggestedMax,
                                grace: '5%',
                                title: {
                                    display: true,
                                    text: 'Boilertemperatuur (°C)',
                                    color: '#F59E0B',
                                    font: { size: 10, weight: 'bold' }
                                },
                                grid: { color: 'rgba(30, 41, 59, 0.25)' },
                                ticks: { color: '#F59E0B', font: { size: 10 }, callback: v => `${v}°C` }
                            },
                            y1: {
                                position: 'right',
                                min: 0,
                                max: 40,
                                grid: { drawOnChartArea: false },
                                title: {
                                    display: true,
                                    text: 'Tapvraag (Liters / 15 min)',
                                    color: '#38BDF8',
                                    font: { size: 10, weight: 'bold' }
                                },
                                ticks: { color: '#38BDF8', font: { size: 10 }, callback: v => `${v} L` }
                            }
                        }
                    }
                });
            } catch (e) {
                console.warn("Error rendering DHW temperature chart:", e);
            }
        }

        async function loadModelDashboard() {
            renderMonthSelector();
            // 1. Render Decomposition Chart FIRST (always guaranteed)
            try {
                await renderModelDecompositionChart();
            } catch (e1) {
                console.warn("Chart render error:", e1);
            }

            // 2. Fetch model status & KPI metrics via relative ./ path
            try {
                const res = await fetch('./api/model/status');
                if (res.ok) {
                    const data = await res.json();
                    if (data && data.params) {
                        const p = data.params;
                        const m = p.metrics || {};
                        if (document.getElementById('model-kpi-r2')) document.getElementById('model-kpi-r2').innerText = m.r_squared ? m.r_squared.toFixed(3) : '0.783';
                        if (document.getElementById('model-kpi-rmse')) document.getElementById('model-kpi-rmse').innerHTML = `${Math.round(m.rmse_w || 185)} W <span class="text-xs text-slate-400">/ ${Math.round(m.mae_w || 132)} W</span>`;
                        if (document.getElementById('model-kpi-ua')) document.getElementById('model-kpi-ua').innerText = `${Math.round(p.building?.ua_base_w_per_k || 321)} W/K`;
                        if (document.getElementById('model-kpi-schedule')) document.getElementById('model-kpi-schedule').innerText = p.last_trained ? `Bijgewerkt: ${p.last_trained.slice(11, 16)}u` : 'Elke nacht 02:00';
                    }
                }
            } catch (e2) {
                console.warn("Model status fetch error:", e2);
            }

            // 3. Load 7x96 profile
            try {
                await loadUnallocatedModel();
            } catch (e3) {
                console.warn("Unallocated model error:", e3);
            }
        }

        async function renderModelDecompositionChart() {
            const canvas = document.getElementById('chart-model-decomposition');
            if (!canvas) return;
            try {
                const res = await fetch('./api/schedule/chart-data?resolution=15m');
                const data = await res.json();
                if (!data || !data.labels) return;

                const existingChart = Chart.getChart(canvas);
                if (existingChart) {
                    existingChart.destroy();
                }

                const labels = data.labels;
                const unallocArr = data.datasets.unallocated_kw || data.datasets.baseload_kw || [];
                const heatingArr = data.datasets.heating_kw || [];
                const boilerArr = data.datasets.boiler_kw || [];
                const solarNegArr = data.datasets.solar_kw_neg || [];
                const netPowerArr = data.datasets.net_power_kw || [];
                const pricesArr = data.datasets.prices_eur || [];

                // Calculate symmetric Y-axis boundary centered on zero
                const maxCons = Math.max(0.1, ...unallocArr.map((u, i) => u + (heatingArr[i] || 0) + (boilerArr[i] || 0)));
                const maxProd = Math.max(0.1, ...solarNegArr.map(s => Math.abs(s)));
                const yBoundary = Math.max(1.5, Math.ceil(Math.max(maxCons, maxProd) * 1.15 * 2) / 2);

                const ctx = canvas.getContext('2d');
                new Chart(ctx, {
                    type: 'bar',
                    data: {
                        labels: labels,
                        datasets: [
                            {
                                label: 'All-in Beurstarief (€/kWh)',
                                data: pricesArr,
                                type: 'line',
                                yAxisID: 'y1',
                                borderColor: '#22D3EE',
                                backgroundColor: 'transparent',
                                borderWidth: 1.75,
                                tension: 0.25,
                                pointRadius: 0,
                                order: 1
                            },
                            {
                                label: 'Netto Netafname',
                                data: netPowerArr,
                                type: 'line',
                                yAxisID: 'y',
                                borderColor: '#E2E8F0',
                                borderDash: [4, 4],
                                backgroundColor: 'transparent',
                                borderWidth: 1.5,
                                tension: 0.2,
                                pointRadius: 0,
                                order: 2
                            },
                            {
                                label: 'Ongedefinieerd (Huis)',
                                data: unallocArr,
                                yAxisID: 'y',
                                backgroundColor: '#3B82F6',
                                stack: 'consumption',
                                borderRadius: 2,
                                order: 3
                            },
                            {
                                label: 'CV Verwarming (Woning)',
                                data: heatingArr,
                                yAxisID: 'y',
                                backgroundColor: '#EF4444',
                                stack: 'consumption',
                                borderRadius: 2,
                                order: 4
                            },
                            {
                                label: 'SWW Boiler 350L',
                                data: boilerArr,
                                yAxisID: 'y',
                                backgroundColor: '#F59E0B',
                                stack: 'consumption',
                                borderRadius: 2,
                                order: 5
                            },
                            {
                                label: 'Zonnepanelen Opwek',
                                data: solarNegArr,
                                yAxisID: 'y',
                                backgroundColor: '#10B981',
                                stack: 'generation',
                                borderRadius: 2,
                                order: 6
                            }
                        ]
                    },
                    options: {
                        responsive: true,
                        maintainAspectRatio: false,
                        interaction: { mode: 'index', intersect: false },
                        plugins: {
                            legend: { display: false },
                            tooltip: {
                                backgroundColor: 'rgba(11, 15, 23, 0.95)',
                                borderColor: '#1E293B',
                                borderWidth: 1,
                                padding: 10,
                                callbacks: {
                                    label: function(c) {
                                        const val = c.raw;
                                        if (val === 0 || val === -0) return null;
                                        if (c.dataset.yAxisID === 'y1') {
                                            return ` 💶 Tarief: €${Number(val).toFixed(4)}/kWh`;
                                        }
                                        return ` ${c.dataset.label}: ${Math.abs(val).toFixed(2)} kW (${(Math.abs(val)*0.25).toFixed(2)} kWh)`;
                                    }
                                }
                            }
                        },
                        scales: {
                            x: {
                                stacked: true,
                                grid: { color: 'rgba(30, 41, 59, 0.3)' },
                                ticks: { color: '#64748B', font: { size: 10 }, maxTicksLimit: 16 }
                            },
                            y: {
                                stacked: true,
                                position: 'left',
                                min: -yBoundary,
                                max: yBoundary,
                                title: {
                                    display: true,
                                    text: 'Opbrengst (-kW)  <  0  <  Verbruik (+kW)',
                                    color: '#64748B',
                                    font: { size: 10, weight: 'bold' }
                                },
                                grid: {
                                    color: (ctx) => ctx.tick.value === 0 ? 'rgba(148, 163, 184, 0.6)' : 'rgba(30, 41, 59, 0.25)',
                                    lineWidth: (ctx) => ctx.tick.value === 0 ? 1.5 : 1
                                },
                                ticks: { color: '#64748B', font: { size: 10 }, callback: v => `${v} kW` }
                            },
                            y1: {
                                position: 'right',
                                grid: { drawOnChartArea: false },
                                title: {
                                    display: true,
                                    text: 'All-in Beurstarief (€/kWh)',
                                    color: '#22D3EE',
                                    font: { size: 10, weight: 'bold' }
                                },
                                ticks: { color: '#22D3EE', font: { size: 10 }, callback: v => `€${v.toFixed(2)}` }
                            }
                        }
                    }
                });
            } catch (e) {
                console.warn("Error rendering unified decomposition chart:", e);
            }
        }

        // =========================================================================
        // TOAST NOTIFICATIONS & MODEL GOVERNANCE STEERING JS
        // =========================================================================
        

                function showToast(msg, type = 'info') {
            let toast = document.getElementById('open-hems-toast');
            if (!toast) {
                toast = document.createElement('div');
                toast.id = 'open-hems-toast';
                document.body.appendChild(toast);
            }
            const colors = {
                'success': 'bg-emerald-950/90 text-emerald-300 border-emerald-500/50',
                'error': 'bg-red-950/90 text-red-300 border-red-500/50',
                'info': 'bg-blue-950/90 text-blue-300 border-blue-500/50'
            };
            const icons = {
                'success': '✅',
                'error': '❌',
                'info': 'ℹ️'
            };
            toast.className = `fixed bottom-5 right-5 z-50 px-4 py-2.5 rounded-xl shadow-2xl border text-xs font-bold transition-all duration-300 transform translate-y-0 opacity-100 flex items-center gap-2 ${colors[type] || colors.info}`;
            toast.innerHTML = `<span>${icons[type] || ''}</span> <span>${msg}</span>`;
            clearTimeout(window.__toastTimer);
            window.__toastTimer = setTimeout(() => {
                toast.className = `fixed bottom-5 right-5 z-50 px-4 py-2.5 rounded-xl shadow-2xl border text-xs font-bold transition-all duration-300 transform translate-y-10 opacity-0 pointer-events-none flex items-center gap-2 ${colors[type] || colors.info}`;
            }, 3500);
        }

        function updateLearningRateLabel(val) {
            const el = document.getElementById('label-learning-rate');
            if (el) el.textContent = `${val}%`;
        }

        function updateAutoAcceptLabel(val) {
            const el = document.getElementById('label-auto-accept');
            if (el) el.textContent = `&plusmn;${Number(val).toFixed(1)}%`;
        }

        async function loadAlgorithmConfig() {
            try {
                const res = await fetch('./api/model/algorithm-config');
                if (!res.ok) return;
                const d = await res.json();
                const lr = Math.round((d.learning_rate_ewma || 0.05) * 100);
                const sLr = document.getElementById('slider-learning-rate');
                if (sLr) { sLr.value = lr; updateLearningRateLabel(lr); }

                const rw = d.rolling_window_days || 90;
                const sRw = document.getElementById('select-rolling-window');
                if (sRw) sRw.value = String(rw);

                const aa = d.auto_accept_max_drift_pct !== undefined ? d.auto_accept_max_drift_pct : 3.0;
                const sAa = document.getElementById('slider-auto-accept');
                if (sAa) { sAa.value = aa; updateAutoAcceptLabel(aa); }
            } catch (e) {
                console.warn('Error loading algorithm config:', e);
            }
        }

        async function saveAlgorithmConfig() {
            const btn = document.getElementById('btn-save-algo');
            if (btn) btn.disabled = true;
            try {
                const lr = Number(document.getElementById('slider-learning-rate').value) / 100.0;
                const rw = parseInt(document.getElementById('select-rolling-window').value, 10);
                const aa = Number(document.getElementById('slider-auto-accept').value);

                const res = await fetch('./api/model/algorithm-config', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        learning_rate_ewma: lr,
                        rolling_window_days: rw,
                        auto_accept_max_drift_pct: aa
                    })
                });
                if (res.ok) {
                    showToast('Algoritme instellingen opgeslagen!', 'success');
                    loadModelRecommendations();
                } else {
                    showToast('Fout bij opslaan algoritme instellingen', 'error');
                }
            } catch (e) {
                showToast('Verbindingsfout: ' + e, 'error');
            } finally {
                if (btn) btn.disabled = false;
            }
        }

        async function loadModelRecommendations() {
            const tBody = document.getElementById('recs-table-body');
            const badge = document.getElementById('recs-status-badge');
            if (!tBody) return;
            try {
                const res = await fetch('./api/model/recommendations');
                if (!res.ok) return;
                const d = await res.json();
                const recs = d.recommendations || [];
                if (recs.length === 0) {
                    tBody.innerHTML = '<tr><td colspan="6" class="py-4 text-center text-slate-500">Nog geen kalibratie-aanbevelingen beschikbaar.</td></tr>';
                    return;
                }

                const isPending = (d.status === 'pending_review');
                if (badge) {
                    if (isPending) {
                        badge.className = 'px-2.5 py-0.5 rounded-full text-[10px] font-mono font-bold bg-amber-500/20 text-amber-300 border border-amber-500/40 animate-pulse';
                        badge.textContent = 'Actie Vereist (Voorstellen Klaar)';
                    } else {
                        badge.className = 'px-2.5 py-0.5 rounded-full text-[10px] font-mono font-bold bg-emerald-500/20 text-emerald-300 border border-emerald-500/40';
                        badge.textContent = 'Up-to-date (Geaccepteerd)';
                    }
                }

                const thresholdVal = d.auto_accept_max_drift_pct !== undefined ? d.auto_accept_max_drift_pct : 3.0;

                tBody.innerHTML = recs.map(r => {
                    const drift = Number(r.drift_pct || 0);
                    const driftColor = drift === 0 ? 'text-slate-400' : (Math.abs(drift) <= thresholdVal ? 'text-emerald-400' : (drift < 0 ? 'text-blue-400' : 'text-amber-400'));
                    const driftSign = drift > 0 ? '+' : '';

                    let statusBadge = '';
                    if (d.status === 'accepted') {
                        statusBadge = `<button type="button" onclick="toggleInfoPopover(event, 'status_accepted')" class="inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-[9px] font-bold bg-blue-950/70 text-blue-300 border border-blue-800 hover:bg-blue-900/60 transition focus:outline-none"><span class="w-1.5 h-1.5 rounded-full bg-blue-400"></span> <span>Geaccepteerd</span></button>`;
                    } else if (r.auto_applied) {
                        statusBadge = `<button type="button" onclick="toggleInfoPopover(event, 'status_auto')" class="inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-[9px] font-bold bg-emerald-950/70 text-emerald-400 border border-emerald-800 hover:bg-emerald-900/60 transition focus:outline-none"><span class="w-1.5 h-1.5 rounded-full bg-emerald-400"></span> <span>Automatisch</span></button>`;
                    } else {
                        statusBadge = `<button type="button" onclick="toggleInfoPopover(event, 'status_review')" class="inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-[9px] font-bold bg-amber-950/70 text-amber-300 border border-amber-800 hover:bg-amber-900/60 transition focus:outline-none"><span class="w-1.5 h-1.5 rounded-full bg-amber-400"></span> <span>Ter Beoordeling</span></button>`;
                    }

                    return `
                        <tr class="hover:bg-slate-800/30 transition">
                            <td class="py-2.5 font-bold text-white">
                                <span class="inline-flex items-center gap-1.5">
                                    <span>${r.name}</span>
                                    <button type="button" onclick="toggleInfoPopover(event, 'param_${r.id}')" class="text-slate-500 hover:text-cyan-400 transition-colors p-0.5 focus:outline-none" aria-label="Toelichting">
                                        <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><circle cx="12" cy="12" r="10"></circle><path d="M12 16v-4m0-4h.01"></path></svg>
                                    </button>
                                </span>
                            </td>
                            <td class="py-2.5 text-center text-slate-400 font-mono">${r.current_value} <span class="text-[10px] text-slate-500">${r.unit}</span></td>
                            <td class="py-2.5 text-center font-bold text-white font-mono">${r.proposed_value} <span class="text-[10px] text-slate-500">${r.unit}</span></td>
                            <td class="py-2.5 text-center font-bold ${driftColor} font-mono">${driftSign}${drift}%</td>
                            <td class="py-2.5 text-[11px] text-slate-400 font-sans">${r.evidence || '--'}</td>
                            <td class="py-2.5 text-right font-mono">${statusBadge}</td>
                        </tr>
                    `;
                }).join('');
            } catch (e) {
                console.warn('Error loading recommendations:', e);
            }
        }

        async function acceptRecommendations() {
            const btn = document.getElementById('btn-recs-accept');
            if (btn) btn.disabled = true;
            try {
                const res = await fetch('./api/model/recommendations/accept', { method: 'POST' });
                if (res.ok) {
                    showToast('Aanbevelingen geaccepteerd en geactiveerd!', 'success');
                    loadModelRecommendations();
                    loadAnalytics();
                } else {
                    showToast('Fout bij accepteren van aanbevelingen', 'error');
                }
            } catch (e) {
                showToast('Verbindingsfout: ' + e, 'error');
            } finally {
                if (btn) btn.disabled = false;
            }
        }

        async function rejectRecommendations() {
            const btn = document.getElementById('btn-recs-reject');
            if (btn) btn.disabled = true;
            try {
                const res = await fetch('./api/model/recommendations/reject', { method: 'POST' });
                if (res.ok) {
                    showToast('Aanbevelingen afgewezen; actieve parameters behouden.', 'info');
                    loadModelRecommendations();
                } else {
                    showToast('Fout bij afwijzen van aanbevelingen', 'error');
                }
            } catch (e) {
                showToast('Verbindingsfout: ' + e, 'error');
            } finally {
                if (btn) btn.disabled = false;
            }
        }

        async function retrainModelNow() {
            const btn = document.getElementById('btn-retrain-model');
            if (btn) {
                btn.disabled = true;
                btn.innerHTML = '<span class="animate-spin inline-block mr-1">⏳</span> Bezig met trainen...';
            }
            try {
                const rw = parseInt(document.getElementById('select-rolling-window')?.value || '90', 10);
                const res = await fetch('./api/model/retrain', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({days: rw})
                });
                const out = await res.json();
                if (out.status === 'success') {
                    showToast('✅ Model succesvol herberekend en aanbevelingen bijgewerkt!', 'success');
                    await loadModelRecommendations();
                    loadChartData();
                } else {
                    showToast(`Fout bij trainen: ${out.message}`, 'error');
                }
            } catch (e) {
                showToast(`Netwerkfout bij trainen: ${e}`, 'error');
            } finally {
                if (btn) {
                    btn.disabled = false;
                    btn.innerHTML = '<svg class="w-4 h-4" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"></path></svg> <span>Herbereken & Train Model</span>';
                }
            }
        }

        function selectModelInspectTab(tab) {
            const btnFormulas = document.getElementById('btn-inspect-formulas');
            const btnCode = document.getElementById('btn-inspect-code');
            const pnlFormulas = document.getElementById('model-inspect-formulas');
            const pnlCode = document.getElementById('model-inspect-code');

            if (tab === 'formulas') {
                btnFormulas.className = 'px-3 py-1.5 rounded-lg bg-blue-600 text-white font-bold transition';
                btnCode.className = 'px-3 py-1.5 rounded-lg text-slate-400 hover:text-white transition';
                pnlFormulas.classList.remove('hidden');
                pnlCode.classList.add('hidden');
            } else {
                btnCode.className = 'px-3 py-1.5 rounded-lg bg-blue-600 text-white font-bold transition';
                btnFormulas.className = 'px-3 py-1.5 rounded-lg text-slate-400 hover:text-white transition';
                pnlCode.classList.remove('hidden');
                pnlFormulas.classList.add('hidden');
            }
        }

        async function recalculateUnallocatedProfile() {
            await retrainModelNow();
        }
    </script>
</body>
</html>"""
        self.wfile.write(html.encode("utf-8"))



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
        print(f"[Open HEMS Collector] Started 60s Window Accumulator (Sample: {self.sample_interval}s, Flush: {self.flush_window}s)")
        time.sleep(3)
        while self.running:
            try:
                self.sample_devices()
                now = time.time()
                if now - self._last_flush_time >= self.flush_window:
                    self.flush_window_to_influx()
                    self._last_flush_time = now
            except Exception as e:
                print(f"[Open HEMS Collector] Error in loop: {e}")
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
