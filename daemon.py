#!/usr/bin/env python3
"""
Open HEMS Framework & Management Console
========================================
Version: 0.11.0
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
import argparse
import json
import urllib.parse
import urllib.request
import urllib.error
import ssl
import socket
import base64
import time
from http.server import HTTPServer, BaseHTTPRequestHandler
from datetime import datetime
from pathlib import Path

CONFIG_FILE = Path("/config/heatpump_config.json")
PARAMS_FILE = Path("/config/heatpump_model_parameters.json")
CACHE_FILE = Path("/config/data/energy_feed_cache.json")
HA_API_CONFIG = Path("/config/.ha_api_config.json")
SECRETS_FILE = Path("/config/open_hems_secrets.json")


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
    cfg = load_json(HA_API_CONFIG)
    token = cfg.get("HASS_TOKEN") or os.environ.get("HASS_TOKEN")
    ha_url = cfg.get("HASS_URL") or os.environ.get("HASS_URL") or "https://hass.b3rg.nl:8123"

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
                "database": cfg.get("influxdb", {}).get("database", "hermes"),
                "read_database": "openhems",
                "username": cfg.get("influxdb", {}).get("username", "hermes"),
                "password": cfg.get("influxdb", {}).get("password", ""),
                "retention_policy": cfg.get("influxdb", {}).get("retention_policy", "autogen"),
                "enabled": True,
                "is_default": True
            }
        ]
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
        # ANALYTICS: Pure openhems Power Producers Telemetry (NO HASSIO FALLBACK)
        if path.startswith("/api/analytics/power_producers"):
            try:
                sec = load_secrets()
                cfg = load_json(CONFIG_FILE)
                active_conn = cfg.get("influxdb_connections", [{}])[0]
                
                db_name = active_conn.get("database", "openhems")
                db_user = active_conn.get("username", "openhems")
                pwd = sec.get("influxdb", {}).get(active_conn.get("id"), "") or sec.get("influxdb", {}).get("local_ha_influxdb", "")
                
                # Query 100% strictly from openhems canonical database
                # Group by 1m or 5m buckets (industry standard aggregation)
                q = """
                SELECT mean("power_w") as afname_w FROM "energy_telemetry" WHERE "device_id" = 'main_grid_meter' AND "flow" = 'IMPORT' AND time > now() - 24h GROUP BY time(1m) fill(linear);
                SELECT mean("power_w") as teruglevering_w FROM "energy_telemetry" WHERE "device_id" = 'main_grid_meter' AND "flow" = 'EXPORT' AND time > now() - 24h GROUP BY time(1m) fill(linear);
                SELECT mean("power_w") as solar_w FROM "energy_telemetry" WHERE "device_id" = 'rooftop_solar' AND "flow" = 'GENERATION' AND time > now() - 24h GROUP BY time(1m) fill(linear);
                """

                url = "http://a0d7b954-influxdb:8086/query?" + urllib.parse.urlencode({
                    "u": db_user,
                    "p": pwd,
                    "db": db_name,
                    "q": q
                })
                
                with urllib.request.urlopen(url, timeout=6) as r:
                    data = json.loads(r.read().decode())
                
                afname_pts = data["results"][0].get("series", [{}])[0].get("values", [])
                terug_pts = data["results"][1].get("series", [{}])[0].get("values", [])
                solar_pts = data["results"][2].get("series", [{}])[0].get("values", [])
                
                labels = []
                series_solar_neg = []
                series_terug_neg = []
                series_afname_pos = []
                series_verbruik_pos = []
                series_selfcons_pos = []
                
                # Compute points
                for i in range(len(afname_pts)):
                    ts_str = afname_pts[i][0]
                    # Parse timestamp to local HH:MM
                    try:
                        dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00")).astimezone()
                        time_label = dt.strftime("%H:%M")
                    except Exception:
                        time_label = ts_str[11:16]
                    
                    labels.append(time_label)
                    
                    # Values
                    afname = max(0.0, float(afname_pts[i][1] or 0.0))
                    terug = max(0.0, float(terug_pts[i][1] or 0.0)) if i < len(terug_pts) else 0.0
                    solar = abs(float(solar_pts[i][1] or 0.0)) if i < len(solar_pts) else 0.0
                    
                    # Positive stack
                    verbruik = afname + max(0.0, solar - terug)
                    self_cons = min(solar, verbruik)
                    
                    series_afname_pos.append(round(afname))
                    series_verbruik_pos.append(round(verbruik))
                    series_selfcons_pos.append(round(self_cons))
                    
                    # Negative stack (for dual-polarity display like Grafana)
                    series_solar_neg.append(-round(solar))
                    series_terug_neg.append(-round(terug))
                
                def fmt_w(val):
                    abs_v = abs(val)
                    sign = "-" if val < 0 else ""
                    if abs_v >= 1000:
                        return f"{sign}{abs_v / 1000.0:.2f} kW"
                    return f"{sign}{int(abs_v)} W"

                stats = {
                    "zonnepanelen": {
                        "last": fmt_w(series_solar_neg[-1] if series_solar_neg else 0),
                        "min": fmt_w(min(series_solar_neg) if series_solar_neg else 0),
                        "max": fmt_w(max(series_solar_neg) if series_solar_neg else 0)
                    },
                    "teruglevering": {
                        "last": fmt_w(series_terug_neg[-1] if series_terug_neg else 0),
                        "min": fmt_w(min(series_terug_neg) if series_terug_neg else 0),
                        "max": fmt_w(max(series_terug_neg) if series_terug_neg else 0)
                    },
                    "afname": {
                        "last": fmt_w(series_afname_pos[-1] if series_afname_pos else 0),
                        "min": fmt_w(min(series_afname_pos) if series_afname_pos else 0),
                        "max": fmt_w(max(series_afname_pos) if series_afname_pos else 0)
                    },
                    "totaal_opgewekt": {
                        "last": fmt_w(series_solar_neg[-1] if series_solar_neg else 0),
                        "min": fmt_w(min(series_solar_neg) if series_solar_neg else 0),
                        "max": fmt_w(max(series_solar_neg) if series_solar_neg else 0)
                    },
                    "opgewekt_gebruikt": {
                        "last": fmt_w(-series_selfcons_pos[-1] if series_selfcons_pos else 0),
                        "min": fmt_w(-max(series_selfcons_pos) if series_selfcons_pos else 0),
                        "max": fmt_w(0)
                    },
                    "totaal_verbruik": {
                        "last": fmt_w(series_verbruik_pos[-1] if series_verbruik_pos else 0),
                        "min": fmt_w(min(series_verbruik_pos) if series_verbruik_pos else 0),
                        "max": fmt_w(max(series_verbruik_pos) if series_verbruik_pos else 0)
                    }
                }

                res = {
                    "status": "success",
                    "labels": labels,
                    "afname": series_afname_pos,
                    "verbruik": series_verbruik_pos,
                    "self_consumption": series_selfcons_pos,
                    "solar_negative": series_solar_neg,
                    "teruglevering_negative": series_terug_neg,
                    "stats": stats
                }
                self._send_json(res)
                return
            except Exception as e:
                self._send_json({"status": "error", "message": f"Fout bij ophalen InfluxDB telemetrie: {str(e)}"}, 500)
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

        if path == "/api/status":
            cfg = load_json(CONFIG_FILE)
            params = load_json(PARAMS_FILE)
            ensure_framework_defaults(cfg)
            self._send_json({
                "system": "Open HEMS Framework",
                "version": "0.11.0",
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

            self._send_json({
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

        # API: 24h Stacked Chart Data
        if path == "/api/schedule/chart-data":
            cache = load_json(CACHE_FILE)
            cfg = load_json(CONFIG_FILE)
            ensure_framework_defaults(cfg)

            hours = [f"{h:02d}:00" for h in range(24)]
            prices = []
            solar = []
            baseload = [0.3] * 24
            boiler = [0.0] * 24
            battery_charge = [0.0] * 24
            advices = [""] * 24

            hourly_p = cache.get("market_prices", {}).get("hourly", {})
            solar_map = cache.get("weather_and_solar", {}).get("solar_kw", {})

            min_price = 999.0
            min_price_hour = 13
            max_price = -999.0
            max_price_hour = 18
            max_solar = 0.0
            max_solar_hour = 13

            for h in range(24):
                p = float(hourly_p.get(str(h), 0.25))
                prices.append(round(p, 4))
                if p < min_price and 8 <= h <= 20:
                    min_price = p
                    min_price_hour = h
                if p > max_price:
                    max_price = p
                    max_price_hour = h

                s_kw = float(solar_map.get(str(h), 0.0))
                solar.append(round(s_kw, 2))
                if s_kw > max_solar:
                    max_solar = s_kw
                    max_solar_hour = h

            # Evaluate Policy 2: ThermalBufferPolicy (DHW Boiler)
            dhw_pol = next((p for p in cfg.get("policies", []) if p["type"] == "thermal_buffer"), None)
            dhw_hour = min_price_hour
            if dhw_pol:
                params = dhw_pol.get("parameters", {})
                if params.get("evening_peak_lockout") and 17 <= dhw_hour <= 20:
                    dhw_hour = 14
                if params.get("morning_peak_lockout") and 7 <= dhw_hour <= 8:
                    dhw_hour = 13
            boiler[dhw_hour] = 3.0

            # Evaluate Policy 3: BatteryArbitragePolicy (Accu & Deadband)
            bat_pol = next((p for p in cfg.get("policies", []) if p["type"] == "battery_arbitrage"), None)
            delta_price = max_price - min_price
            deadband_threshold = 0.115
            if bat_pol:
                deadband_threshold = bat_pol.get("parameters", {}).get("min_price_spread_eur_kwh", 0.115)

            battery_status_msg = ""
            if delta_price >= deadband_threshold:
                battery_charge[min_price_hour] = 2.0
                battery_status_msg = f"🔋 Accu-Arbitrage Actief: Laden om {min_price_hour}:00 (€{min_price:.2f}), Ontladen om {max_price_hour}:00 (€{max_price:.2f}) [Delta €{delta_price:.3f} > €{deadband_threshold:.3f}]"
            else:
                battery_status_msg = f"⏸️ Accu Rust (Deadband): Delta €{delta_price:.3f}/kWh is te klein (< €{deadband_threshold:.3f}/kWh). Geen net-arbitrage."
                if max_solar > 1.5:
                    battery_charge[max_solar_hour] = round(min(2.0, max_solar - 0.5), 2)
                    battery_status_msg += f" Wel zonne-buffer om {max_solar_hour}:00."

            advices[dhw_hour] = f"♨️ Boiler 350L Boost naar 60°C op laagste stroomtarief (€{min_price:.2f}/kWh)"
            if max_solar > 1.5:
                advices[max_solar_hour] = f"☀️ Zonnepiek ({max_solar:.1f} kW) — Gratis stroom van eigen dak!"
            advices[max_price_hour] = f"⛔ Prijspiek (€{max_price:.2f}/kWh) — Zware verbruikers blokkeren!"

            self._send_json({
                "labels": hours,
                "datasets": {
                    "baseload_kw": baseload,
                    "boiler_kw": boiler,
                    "battery_charge_kw": battery_charge,
                    "solar_kw": solar,
                    "prices_eur": prices
                },
                "advices": advices,
                "cheapest_hour": min_price_hour,
                "cheapest_price_eur": min_price,
                "peak_solar_hour": max_solar_hour,
                "peak_solar_kw": max_solar,
                "max_price_hour": max_price_hour,
                "max_price_eur": max_price,
                "battery_status_msg": battery_status_msg
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

        # UPDATE: Specific Device
        m_dev = re.match(r"^/api/devices/([^/]+)$", path)
        if m_dev:
            dev_id = m_dev.group(1)
            cfg = load_json(CONFIG_FILE)
            ensure_framework_defaults(cfg)
            for d in cfg["devices"]:
                if d["id"] == dev_id:
                    for k in ["name", "type", "source_type", "adapter", "capabilities", "ha_power_entity", "ha_energy_entity", "ha_temp_entity", "ha_control_entity", "mqtt_broker_id", "mqtt_power_topic", "mqtt_power_json_key", "mqtt_control_topic", "parameters"]:
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
                <!-- LAAG 4: ANALYSE & RAPPORTAGE (BOVENAAN) -->
                <div class="px-3 pt-2 pb-1 text-[10px] font-bold text-cyan-400 uppercase tracking-wider flex items-center justify-between">
                    <span>Laag 4: Analyse & Rapport</span>
                    <span class="px-1.5 py-0.2 bg-cyan-950 text-cyan-300 text-[9px] rounded border border-cyan-800">KPI</span>
                </div>
                <a href="#analytics" onclick="showTab('analytics')" id="nav-analytics" class="nav-link active flex items-center gap-3 px-3.5 py-2.5 rounded-xl text-xs font-medium text-slate-400 hover:text-white hover:bg-slate-800/40 transition-colors">
                    <svg class="w-4 h-4 text-cyan-400" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z"></path></svg>
                    <span>Analyse & Besparing</span>
                </a>

                <!-- LAAG 3: OPTIMALISATIE & BELEID (INCLUSIEF PEAK SHAVING) -->
                <div class="px-3 pt-3 pb-1 text-[10px] font-bold text-purple-400 uppercase tracking-wider">
                    <span>Laag 3: Optimalisatie & Beleid</span>
                </div>
                <a href="#dashboard" onclick="showTab('dashboard')" id="nav-dashboard" class="nav-link flex items-center gap-3 px-3.5 py-2.5 rounded-xl text-xs font-medium text-slate-400 hover:text-white hover:bg-slate-800/40 transition-colors">
                    <svg class="w-4 h-4 text-blue-400" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path d="M13 10V3L4 14h7v7l9-11h-7z"></path></svg>
                    <span>24h Planning & Grafiek</span>
                </a>
                <a href="#policies" onclick="showTab('policies')" id="nav-policies" class="nav-link flex items-center gap-3 px-3.5 py-2.5 rounded-xl text-xs font-medium text-slate-400 hover:text-white hover:bg-slate-800/40 transition-colors">
                    <svg class="w-4 h-4 text-purple-400" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path d="M12 6V4m0 2a2 2 0 100 4m0-4a2 2 0 110 4m-6 8a2 2 0 100-4m0 4a2 2 0 110-4m0 4v2m0-6V4m6 6v10m6-2a2 2 0 100-4m0 4a2 2 0 110-4m0 4v2m0-6V4"></path></svg>
                    <span>Beleid & Policies</span>
                    <span class="ml-auto text-[10px] px-1.5 py-0.5 bg-purple-900/40 text-purple-300 font-medium rounded border border-purple-800" id="badge-pol-count">3</span>
                </a>
                <a href="#tariffs" onclick="showTab('tariffs')" id="nav-tariffs" class="nav-link flex items-center gap-3 px-3.5 py-2.5 rounded-xl text-xs font-medium text-slate-400 hover:text-white hover:bg-slate-800/40 transition-colors">
                    <svg class="w-4 h-4 text-emerald-400" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path d="M12 8c-1.657 0-3 .895-3 2s1.343 2 3 2 3 .895 3 2-1.343 2-3 2m0-8c1.11 0 2.08.402 2.599 1M12 8V7m0 1v8m0 0v1m0-1c-1.11 0-2.08-.402-2.599-1M21 12a9 9 0 11-18 0 9 9 0 0118 0z"></path></svg>
                    <span>Energieleveranciers (Tarieven)</span>
                </a>

                <!-- LAAG 2: ZELFLEREND & KALIBRATIE -->
                <div class="px-3 pt-3 pb-1 text-[10px] font-bold text-amber-400 uppercase tracking-wider">
                    <span>Laag 2: Zelflerend & Fysica</span>
                </div>
                <a href="#calibration" onclick="showTab('calibration')" id="nav-calibration" class="nav-link flex items-center gap-3 px-3.5 py-2.5 rounded-xl text-xs font-medium text-slate-400 hover:text-white hover:bg-slate-800/40 transition-colors">
                    <svg class="w-4 h-4 text-amber-400" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path d="M3 6l3 18h12l3-18H3zm6 3v10m6-10v10M9 6V4a2 2 0 012-2h2a2 2 0 012 2v2"></path></svg>
                    <span>Kalibratie & Offsets</span>
                </a>

                <!-- LAAG 1: DATA & VERBINDINGEN (ONDERAAN) -->
                <div class="px-3 pt-3 pb-1 text-[10px] font-bold text-slate-500 uppercase tracking-wider">
                    <span>Laag 1: Data & Verbindingen</span>
                </div>
                <a href="#devices" onclick="showTab('devices')" id="nav-devices" class="nav-link flex items-center gap-3 px-3.5 py-2.5 rounded-xl text-xs font-medium text-slate-400 hover:text-white hover:bg-slate-800/40 transition-colors">
                    <svg class="w-4 h-4 text-slate-400" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path d="M9 3v2m6-2v2M9 19v2m6-2v2M5 9H3m2 6H3m18-6h-2m2 6h-2M7 19h10a2 2 0 002-2V7a2 2 0 00-2-2H7a2 2 0 00-2 2v10a2 2 0 002 2zM9 9h6v6H9V9z"></path></svg>
                    <span>Apparaten (Hardware Links)</span>
                    <span class="ml-auto text-[10px] px-1.5 py-0.5 bg-blue-900/40 text-blue-300 font-medium rounded border border-blue-800" id="badge-dev-count">0</span>
                </a>
                <a href="#infrastructure" onclick="showTab('infrastructure')" id="nav-infrastructure" class="nav-link flex items-center gap-3 px-3.5 py-2.5 rounded-xl text-xs font-medium text-slate-400 hover:text-white hover:bg-slate-800/40 transition-colors">
                    <svg class="w-4 h-4 text-slate-400" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path d="M4 7v10c0 2.21 3.582 4 8 4s8-1.79 8-4V7M4 7c0 2.21 3.582 4 8 4s8-1.79 8-4M4 7c0-2.21 3.582-4 8-4s8 1.79 8 4m0 5c0 2.21-3.582 4-8 4s-8-1.79-8-4"></path></svg>
                    <span>Verbindingen & Opslag (DB & MQTT)</span>
                    <span class="ml-auto text-[10px] px-1.5 py-0.5 bg-slate-800 text-slate-300 font-medium rounded border border-slate-700" id="badge-infra-conns">2</span>
                </a>
                <a href="#providers" onclick="showTab('providers')" id="nav-providers" class="nav-link flex items-center gap-3 px-3.5 py-2.5 rounded-xl text-xs font-medium text-slate-400 hover:text-white hover:bg-slate-800/40 transition-colors">
                    <svg class="w-4 h-4 text-slate-400" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path d="M21 12a9 9 0 01-9 9m9-9a9 9 0 00-9-9m9 9H3m9 9a9 9 0 01-9-9m9 9c1.657 0 3-4.03 3-9s-1.343-9-3-9m0 18c-1.657 0-3-4.03-3-9s1.343-9 3-9m-9 9a9 9 0 019-9"></path></svg>
                    <span>Open APIs (EPEX / Meteo)</span>
                </a></nav>
        </div>

        <div class="p-4 border-t border-[#1E293B] bg-[#0A0D14]/80 text-[10px] text-slate-500 flex justify-between">
            <span>Versie: <strong class="text-slate-400">v0.11.0</strong></span>
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
            <div id="view-analytics" class="tab-content active space-y-6">
                <div class="bg-gradient-to-r from-cyan-950/80 via-[#0e1422] to-blue-950/80 border border-cyan-500/30 rounded-2xl p-4 sm:p-5 shadow-xl flex flex-col sm:flex-row sm:items-center justify-between gap-4">
                    <div class="flex items-center gap-3 sm:gap-4">
                        <div class="w-10 h-10 sm:w-12 sm:h-12 rounded-xl bg-cyan-500/20 border border-cyan-500/30 flex items-center justify-center text-cyan-400 text-xl sm:text-2xl shadow-[0_0_15px_rgba(6,182,212,0.2)] flex-shrink-0">
                            📊
                        </div>
                        <div>
                            <div class="flex items-center gap-2">
                                <span class="text-xs uppercase font-bold text-cyan-400 tracking-wider">Laag 5 Analyse & Rapportage</span>
                                <span class="px-2 py-0.5 rounded text-[10px] font-mono font-bold bg-cyan-500/20 text-cyan-300 border border-cyan-500/40">AUDIT & KPI</span>
                            </div>
                            <div class="text-xs sm:text-sm font-bold text-white mt-1">
                                Realtime KPI monitoring, besparingsberekeningen en prognose-auditing.
                            </div>
                        </div>
                    </div>
                </div>

                <!-- 4 KPI CARDS -->
                <div class="grid grid-cols-2 md:grid-cols-4 gap-4">
                    <div class="bg-[#0e1422] border border-[#1E293B] rounded-2xl p-4 shadow">
                        <span class="text-[10px] uppercase font-bold text-slate-400 block">Besparing Vandaag</span>
                        <div class="text-xl font-bold text-emerald-400 mt-1" id="kpi-savings-today">€0.85</div>
                        <span class="text-[10px] text-slate-500">t.o.v. standaard verbruik</span>
                    </div>
                    <div class="bg-[#0e1422] border border-[#1E293B] rounded-2xl p-4 shadow">
                        <span class="text-[10px] uppercase font-bold text-slate-400 block">Zelfconsumptie</span>
                        <div class="text-xl font-bold text-amber-400 mt-1" id="kpi-self-consumption">78.4%</div>
                        <span class="text-[10px] text-slate-500">Zon direct lokaal benut</span>
                    </div>
                    <div class="bg-[#0e1422] border border-[#1E293B] rounded-2xl p-4 shadow">
                        <span class="text-[10px] uppercase font-bold text-slate-400 block">Warmtepomp COP</span>
                        <div class="text-xl font-bold text-cyan-400 mt-1" id="kpi-cop-dhw">2.04 <span class="text-xs text-slate-400 font-normal">SWW</span> · 4.80 <span class="text-xs text-slate-400 font-normal">CV</span></div>
                        <span class="text-[10px] text-slate-500">Gemeten seizoensrendement</span>
                    </div>
                    <div class="bg-[#0e1422] border border-[#1E293B] rounded-2xl p-4 shadow">
                        <span class="text-[10px] uppercase font-bold text-slate-400 block">Prognose Validatie</span>
                        <div class="text-xl font-bold text-purple-400 mt-1" id="kpi-accuracy">92.6%</div>
                        <span class="text-[10px] text-slate-500">MAE: 0.18 kW</span>
                    </div>
                </div>

                <!-- GRAFANA-STYLE POWER PRODUCERS CHART (DUAL POLARITY) -->
                <div class="bg-[#0e1422] border border-[#1E293B] rounded-2xl p-5 shadow-2xl space-y-4">
                    <div class="flex flex-col sm:flex-row sm:items-center justify-between gap-3 border-b border-slate-800/80 pb-3">
                        <div class="flex items-center gap-2.5">
                            <span class="w-3 h-3 rounded-full bg-emerald-500 animate-pulse"></span>
                            <h3 class="text-sm font-bold text-white tracking-wide">Power Producers & Netstromen</h3>
                            <span class="text-[10px] text-slate-500 font-mono px-2 py-0.5 rounded bg-slate-900 border border-slate-800">InfluxDB Realtime (24h)</span>
                        </div>
                        <div class="flex items-center gap-2 text-xs">
                            <button onclick="loadPowerProducersChart()" class="px-2.5 py-1 bg-slate-800 hover:bg-slate-700 text-slate-300 text-xs rounded-lg font-medium border border-slate-700 transition">
                                🔄 Live Verversen
                            </button>
                        </div>
                    </div>

                    <!-- Dual Polarity Chart Canvas -->
                    <div class="relative w-full h-72 sm:h-80">
                        <canvas id="powerProducersChart"></canvas>
                    </div>

                    <!-- Exact Grafana-Style Legend & Metrics Table -->
                    <div class="pt-2 border-t border-slate-800/80">
                        <div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-2.5 text-xs font-mono">
                            <!-- Zonnepanelen -->
                            <div class="bg-[#0B0F17] p-2.5 rounded-xl border border-slate-800/90 flex items-center justify-between">
                                <div class="flex items-center gap-2">
                                    <span class="w-3 h-1.5 rounded-sm bg-yellow-500"></span>
                                    <span class="text-slate-300">Zonnepanelen</span>
                                </div>
                                <div class="text-[11px] space-x-2 text-right">
                                    <span class="text-slate-400">Last: <strong class="text-yellow-400" id="stat-solar-last">--</strong></span>
                                    <span class="text-slate-500">Min: <span class="text-yellow-500/80" id="stat-solar-min">--</span></span>
                                </div>
                            </div>
                            <!-- Teruglevering -->
                            <div class="bg-[#0B0F17] p-2.5 rounded-xl border border-slate-800/90 flex items-center justify-between">
                                <div class="flex items-center gap-2">
                                    <span class="w-3 h-1.5 rounded-sm bg-emerald-500"></span>
                                    <span class="text-slate-300">Teruglevering</span>
                                </div>
                                <div class="text-[11px] space-x-2 text-right">
                                    <span class="text-slate-400">Last: <strong class="text-emerald-400" id="stat-terug-last">--</strong></span>
                                    <span class="text-slate-500">Min: <span class="text-emerald-500/80" id="stat-terug-min">--</span></span>
                                </div>
                            </div>
                            <!-- Afname -->
                            <div class="bg-[#0B0F17] p-2.5 rounded-xl border border-slate-800/90 flex items-center justify-between">
                                <div class="flex items-center gap-2">
                                    <span class="w-3 h-1.5 rounded-sm bg-red-500"></span>
                                    <span class="text-slate-300">Afname</span>
                                </div>
                                <div class="text-[11px] space-x-2 text-right">
                                    <span class="text-slate-400">Last: <strong class="text-red-400" id="stat-afname-last">--</strong></span>
                                    <span class="text-slate-500">Max: <span class="text-red-500/80" id="stat-afname-max">--</span></span>
                                </div>
                            </div>
                            <!-- Totaal opgewekt -->
                            <div class="bg-[#0B0F17] p-2.5 rounded-xl border border-slate-800/90 flex items-center justify-between">
                                <div class="flex items-center gap-2">
                                    <span class="w-3 h-1.5 rounded-sm bg-lime-500"></span>
                                    <span class="text-slate-300">Totaal opgewekt</span>
                                </div>
                                <div class="text-[11px] space-x-2 text-right">
                                    <span class="text-slate-400">Last: <strong class="text-lime-400" id="stat-opgewekt-last">--</strong></span>
                                    <span class="text-slate-500">Min: <span class="text-lime-500/80" id="stat-opgewekt-min">--</span></span>
                                </div>
                            </div>
                            <!-- Opgewekt Gebruikt -->
                            <div class="bg-[#0B0F17] p-2.5 rounded-xl border border-slate-800/90 flex items-center justify-between">
                                <div class="flex items-center gap-2">
                                    <span class="w-3 h-1.5 rounded-sm bg-teal-400"></span>
                                    <span class="text-slate-300">Opgewekt Gebruikt</span>
                                </div>
                                <div class="text-[11px] space-x-2 text-right">
                                    <span class="text-slate-400">Last: <strong class="text-teal-400" id="stat-selfcons-last">--</strong></span>
                                    <span class="text-slate-500">Min: <span class="text-teal-500/80" id="stat-selfcons-min">--</span></span>
                                </div>
                            </div>
                            <!-- Totaal Verbruik -->
                            <div class="bg-[#0B0F17] p-2.5 rounded-xl border border-slate-800/90 flex items-center justify-between">
                                <div class="flex items-center gap-2">
                                    <span class="w-3 h-1.5 rounded-sm bg-orange-500"></span>
                                    <span class="text-slate-300">Totaal Verbruik</span>
                                </div>
                                <div class="text-[11px] space-x-2 text-right">
                                    <span class="text-slate-400">Last: <strong class="text-orange-400" id="stat-verbruik-last">--</strong></span>
                                    <span class="text-slate-500">Max: <span class="text-orange-500/80" id="stat-verbruik-max">--</span></span>
                                </div>
                            </div>
                        </div>
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

            <div id="view-infrastructure" class="tab-content space-y-6">
                <!-- Status & Telemetry Header Banner -->
                <div class="bg-gradient-to-r from-emerald-950/80 via-[#0e1422] to-blue-950/80 border border-emerald-500/30 rounded-2xl p-4 sm:p-5 shadow-xl flex flex-col sm:flex-row sm:items-center justify-between gap-4">
                    <div class="flex items-start sm:items-center gap-3 sm:gap-4">
                        <div class="w-10 h-10 sm:w-12 sm:h-12 rounded-xl bg-emerald-500/20 border border-emerald-500/30 flex items-center justify-center text-emerald-400 text-xl sm:text-2xl shadow-[0_0_15px_rgba(16,185,129,0.2)] flex-shrink-0">
                            🔌
                        </div>
                        <div class="min-w-0">
                            <div class="flex items-center gap-2 flex-wrap">
                                <span class="text-xs uppercase font-bold text-emerald-400 tracking-wider">Laag 1 Dataverzameling & Connectiviteit</span>
                                <span class="px-2 py-0.5 rounded text-[10px] font-mono font-bold bg-emerald-500/20 text-emerald-300 border border-emerald-500/40">STANDALONE & MULTI-INSTANCE</span>
                            </div>
                            <div class="text-xs sm:text-sm font-bold text-white mt-1 break-words" id="infra-summary-text">
                                InfluxDB tijdreeksopslag & MQTT streaming gereed voor realtime datastromen.
                            </div>
                        </div>
                    </div>
                    <button onclick="writeTestTelemetryPoint()" class="px-3.5 py-2 bg-emerald-600 hover:bg-emerald-500 text-white text-xs font-semibold rounded-xl shadow-lg transition-all flex items-center justify-center gap-2 flex-shrink-0 w-full sm:w-auto">
                        <span>⚡ Schrijf Test Telemetrie</span>
                    </button>
                </div>

                <!-- SECTION 1: INFLUXDB CONNECTIONS CRUD -->
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

                <!-- SECTION 2: MQTT BROKERS CRUD -->
                <div class="space-y-4">
                    <div class="flex justify-between items-center">
                        <div>
                            <h2 class="text-base font-bold text-white flex items-center gap-2">
                                <span>MQTT Message Brokers</span>
                                <span class="text-xs font-normal text-slate-400">(Streaming data-inname en events)</span>
                            </h2>
                            <p class="text-xs text-slate-400">Verbind met lokale Home Assistant Mosquitto brokers, externe cloud brokers of omvormer gateways.</p>
                        </div>
                        <button onclick="openMqttModal()" class="px-3 py-1.5 bg-amber-600 hover:bg-amber-500 text-white text-xs font-semibold rounded-xl shadow transition-all">
                            + MQTT Broker Toevoegen
                        </button>
                    </div>
                    <div id="mqtt-conns-container" class="grid grid-cols-1 md:grid-cols-2 gap-5">
                        <!-- Loaded dynamically -->
                    </div>
                </div>

                <!-- SECTION 3: LIVE TELEMETRY STREAM STATUS CARD -->
                <div class="bg-[#0e1422] border border-[#1E293B] rounded-2xl p-6 shadow-xl space-y-4">
                    <div class="flex justify-between items-center">
                        <div>
                            <h3 class="text-sm font-bold text-white">Live Data-Inname Telemetrie Monitor</h3>
                            <p class="text-xs text-slate-400">Reële metingen geregistreerd in de InfluxDB tijdreeksdatabase.</p>
                        </div>
                        <span class="text-[11px] font-mono text-emerald-400" id="last-write-status">Gereed voor datastromen</span>
                    </div>

                    <div class="grid grid-cols-1 md:grid-cols-3 gap-4 font-mono text-xs">
                        <div class="bg-[#0B0F17] border border-slate-800 p-4 rounded-xl">
                            <span class="text-slate-500 block text-[10px] uppercase">Open HEMS Metingen</span>
                            <span class="text-xl font-bold text-emerald-400 mt-1 block" id="stat-openhems-count">Actief</span>
                            <span class="text-[10px] text-cyan-400">Database: openhems (Canonical HEMS Store)</span>
                        </div>
                        <div class="bg-[#0B0F17] border border-slate-800 p-4 rounded-xl">
                            <span class="text-slate-500 block text-[10px] uppercase">Tumble Window Buffer</span>
                            <span class="text-xl font-bold text-purple-400 mt-1 block">60s Gemiddelde</span>
                            <span class="text-[10px] text-purple-300">Anti-Spike Filter Actief</span>
                        </div>
                        <div class="bg-[#0B0F17] border border-slate-800 p-4 rounded-xl">
                            <span class="text-slate-500 block text-[10px] uppercase">Integriteit & Protocol</span>
                            <span class="text-xl font-bold text-emerald-400 mt-1 block">Line Protocol</span>
                            <span class="text-[10px] text-slate-400">Nanoseconde precisie</span>
                        </div>
                    </div>
                </div>
            </div>

            <!-- TAB 1: 24H STACKED BAR GRAPH -->
            <div id="view-dashboard" class="tab-content space-y-6">
                <div id="recommendation-banner" class="bg-gradient-to-r from-emerald-950/80 via-[#0e1422] to-amber-950/80 border border-emerald-500/40 rounded-2xl p-4 shadow-xl flex items-center justify-between">
                    <div class="flex items-center gap-3">
                        <div class="w-10 h-10 rounded-xl bg-emerald-500/20 border border-emerald-500/30 flex items-center justify-center text-emerald-400 text-xl shadow-[0_0_15px_rgba(16,185,129,0.3)]">💡</div>
                        <div>
                            <span class="text-[10px] uppercase font-bold text-emerald-400 tracking-wider">Dynamisch Verbruiksadvies</span>
                            <div class="text-sm font-bold text-white mt-0.5" id="banner-text">Bezig met laden...</div>
                        </div>
                    </div>
                    <span class="px-2.5 py-1 rounded-lg text-xs font-mono font-bold bg-emerald-500/20 text-emerald-300 border border-emerald-500/40" id="banner-tag">OPTIMAL DISPATCH</span>
                </div>

                <div class="bg-[#0e1422] border border-[#1E293B] rounded-xl p-3.5 flex items-center gap-3 text-xs text-slate-300">
                    <span class="text-base">🔋</span>
                    <span id="battery-status-banner" class="font-mono text-emerald-400">Accu-beleid wordt geëvalueerd...</span>
                </div>

                <div class="bg-[#0e1422] border border-[#1E293B] rounded-2xl p-6 shadow-xl">
                    <div class="flex justify-between items-center mb-6">
                        <div>
                            <h3 class="text-sm font-bold text-white">24-Uurs Vermogens- & Productieverwachting</h3>
                            <p class="text-xs text-slate-400">Gestapeld verbruik (kW) t.o.v. zonne-opwek en dynamische stroomprijs</p>
                        </div>
                        <div class="flex items-center gap-4 text-xs">
                            <div class="flex items-center gap-1.5"><span class="w-3 h-3 rounded bg-blue-500"></span> <span>Sluipverbruik</span></div>
                            <div class="flex items-center gap-1.5"><span class="w-3 h-3 rounded bg-pink-500"></span> <span>Warmtepomp / SWW</span></div>
                            <div class="flex items-center gap-1.5"><span class="w-3 h-3 rounded bg-emerald-500"></span> <span>Accu Laden</span></div>
                            <div class="flex items-center gap-1.5"><span class="w-3 h-1 bg-amber-400"></span> <span>Zon (kW)</span></div>
                            <div class="flex items-center gap-1.5"><span class="w-3 h-1 bg-cyan-400"></span> <span>Prijs (€/kWh)</span></div>
                        </div>
                    </div>
                    <div class="h-96">
                        <canvas id="hemsChart"></canvas>
                    </div>
                </div>
            </div>

            <!-- TAB 2: POLICIES CRUD & MULTI-DEVICE ORCHESTRATION -->
            <div id="view-policies" class="tab-content space-y-6">
                <!-- System-wide Multi-Device Constraints (Peak Shaving & Interlocks) -->
                <div class="bg-gradient-to-r from-purple-950/60 via-[#0e1422] to-indigo-950/60 border border-purple-500/30 rounded-2xl p-5 shadow-xl">
                    <div class="flex justify-between items-start mb-4">
                        <div class="flex items-center gap-3">
                            <div class="w-10 h-10 rounded-xl bg-purple-500/20 border border-purple-500/30 flex items-center justify-center text-purple-400 text-xl">
                                ⚖️
                            </div>
                            <div>
                                <h3 class="text-sm font-bold text-white">Systeembrede Limieten & Peak Shaving (Multi-Device)</h3>
                                <p class="text-xs text-slate-400">Beperkingen die over meerdere apparaten tegelijk gelden.</p>
                            </div>
                        </div>
                        <span class="px-2 py-0.5 rounded text-[10px] font-mono font-bold bg-purple-500/20 text-purple-300 border border-purple-500/40">BELEIDSREGELS</span>
                    </div>
                    
                    <div class="grid grid-cols-1 md:grid-cols-3 gap-4 font-mono text-xs">
                        <div class="bg-[#0B0F17] p-3 rounded-xl border border-slate-800">
                            <div class="text-[10px] text-slate-400 uppercase">⚡ Max Netafname (Peak Shaving)</div>
                            <div class="text-base font-bold text-white mt-1">17.250 W <span class="text-xs text-slate-500">(3x25A)</span></div>
                            <div class="text-[10px] text-emerald-400 mt-1">✓ Smoor laadpaal/accu bij pieken</div>
                        </div>
                        <div class="bg-[#0B0F17] p-3 rounded-xl border border-slate-800">
                            <div class="text-[10px] text-slate-400 uppercase">🔀 Hydraulische Uitsluiting</div>
                            <div class="text-base font-bold text-white mt-1">CV Uit bij SWW Boost</div>
                            <div class="text-[10px] text-emerald-400 mt-1">✓ Voorkomt 9 kW Backup Heater</div>
                        </div>
                        <div class="bg-[#0B0F17] p-3 rounded-xl border border-slate-800">
                            <div class="text-[10px] text-slate-400 uppercase">☀️ Zonne-Overschot Volgorde</div>
                            <div class="text-base font-bold text-white mt-1">1. SWW → 2. Accu → 3. Net</div>
                            <div class="text-[10px] text-purple-400 mt-1">✓ Maximale eigen consumptie</div>
                        </div>
                    </div>
                </div>

                <div class="flex justify-between items-center pt-2">
                    <div>
                        <h2 class="text-base font-bold text-white">Beleidsregels per Archetype</h2>
                        <p class="text-xs text-slate-400">Koppel apparaten aan shiftable, thermische of batterij-arbitrage policies.</p>
                    </div>
                    <button onclick="openPolicyModal()" class="px-3 py-1.5 bg-purple-600 hover:bg-purple-500 text-white text-xs font-semibold rounded-xl shadow transition-all">
                        + Nieuwe Policy Aanmaken
                    </button>
                </div>
                <div id="policies-container" class="grid grid-cols-1 md:grid-cols-3 gap-5"></div>
            </div>

            <!-- TAB 3: APPARATEN CRUD -->
            <div id="view-devices" class="tab-content space-y-4">
                <div class="flex justify-between items-center">
                    <div>
                        <h2 class="text-base font-bold text-white">Apparaten & Hardware (Physical Resources)</h2>
                        <p class="text-xs text-slate-400">Koppel Home Assistant entiteiten en technische limieten (vermogen, capaciteit).</p>
                    </div>
                    <button onclick="openDeviceModal()" class="px-3 py-1.5 bg-blue-600 hover:bg-blue-500 text-white text-xs font-semibold rounded-xl shadow transition-all">
                        + Apparaat Toevoegen
                    </button>
                </div>
                <div id="devices-container" class="grid grid-cols-1 md:grid-cols-3 gap-5"></div>
            </div>

            <!-- TAB 4: TARIFFS & SUPPLIERS CRUD -->
            <div id="view-tariffs" class="tab-content space-y-4">
                <div class="flex justify-between items-center">
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

            <!-- TAB 5: OPEN APIS & FEEDS -->
            <div id="view-providers" class="tab-content space-y-4">
                <div class="flex justify-between items-center">
                    <div>
                        <h2 class="text-base font-bold text-white">Open API Providers & Omgevingsfeeds</h2>
                        <p class="text-xs text-slate-400">Publieke en lokale databronnen voor beursprijzen, zonnestraling en weerscondities.</p>
                    </div>
                    <button onclick="loadProviders()" class="px-3 py-1.5 bg-slate-800 hover:bg-slate-700 text-slate-200 border border-slate-700 text-xs rounded-lg font-medium">
                        🔄 Verversen
                    </button>
                </div>
                <div id="providers-container" class="grid grid-cols-1 md:grid-cols-2 gap-5"></div>
            </div>

            <!-- TAB 6: CALIBRATION & EXCLUSION WINDOWS -->
            <div id="view-calibration" class="tab-content space-y-6">
                <div>
                    <h2 class="text-base font-bold text-white">Zelflerende Feedback & Sensor-Downtime</h2>
                    <p class="text-xs text-slate-400">Beheer data-uitsluitingsmaskers en empirische gebouw-/dakparameters.</p>
                </div>
                <div class="bg-[#0e1422] border border-[#1E293B] rounded-2xl p-6">
                    <div class="flex justify-between items-center mb-4">
                        <h3 class="text-sm font-bold text-white">Data Uitsluitingsmaskers (Sensor Downtime)</h3>
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

        </div>
    </main>

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

                <!-- HA Adapter Fields -->
                <div id="dev-source-ha-fields" class="space-y-3">
                    <div>
                        <label class="block mb-1 text-slate-400">Gekoppelde Vermogenssensor in Home Assistant (W of kW)</label>
                        <select id="modal-dev-ha-power" class="w-full bg-[#0B0F17] border border-slate-800 rounded-lg p-2 text-white font-mono text-[11px]">
                            <option value="">-- Selecteer Home Assistant Entiteit --</option>
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
        let chartInstance = null;
        let haEntitiesCache = [];
        let currentPolicyParams = {};
        let activeTabId = 'analytics';
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
                'analytics': ['Analyse & Rapportage (Laag 5)', 'Kostenbesparingen, COP seizoensrendementen en prognose-auditing.'], 'control': ['Veiligheid & Aansturing (Laag 4)', 'Hardware guardrails, compressor dwell-time status en Smart Grid relais.'], 'infrastructure': ['Verbindingen & Opslag (Laag 1)', 'Beheer InfluxDB en MQTT instanties voor tijdreeksopslag en streaming connectiviteit.'],
                'dashboard': ['24h Verwachting & Gestapeld Verbruik', 'Gestapelde uurgrafiek: basislast, warmtepomp, accu en zonne-advies'],
                'policies': ['Beleidsregels & Orchestratie (Policy Engine)', 'Definieer overkoepelend beleid op basis van kosten, zonne-opwek en comfortguardrails.'],
                'devices': ['Apparaten & Hardware (Physical Resources)', 'Koppel Home Assistant entiteiten en technische limieten.'],
                'tariffs': ['Energieleveranciers & Tariefstructuren', 'Beheer contracten (Powerpeers, Tibber, vast/dynamisch) en opslagen.'],
                'providers': ['Standaard Open API Providers', 'Breed toepasbare publieke databronnen die het framework out-of-the-box ontsluit.'],
                'calibration': ['Zelflerende Feedback & Sensor-Downtime', 'Beheer data-uitsluitingsmaskers en empirische gebouw-/dakparameters.']
            };
            const t = titles[tabId] || ['Open HEMS', ''];
            document.getElementById('header-title').innerText = t[0];
            document.getElementById('header-sub').innerText = t[1];

            if (tabId === 'analytics') loadAnalytics();
            if (tabId === 'control') loadControl();
            if (tabId === 'infrastructure') loadInfrastructure();
            if (tabId === 'dashboard') loadChartData();
            if (tabId === 'policies') loadPolicies();
            if (tabId === 'devices') loadDevices();
            if (tabId === 'tariffs') loadTariffs();
            if (tabId === 'calibration') loadCalibration();
        }

        function refreshCurrentTab() {
            showTab(activeTabId);
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
                haEntitiesCache = data.entities || [];
                populateHaDropdowns();
            } catch (e) {
                console.warn('Could not load HA entities:', e);
            }
        }

        function populateHaDropdowns() {
            const powerSelect = document.getElementById('modal-dev-ha-power');
            const controlSelect = document.getElementById('modal-dev-ha-control');

            powerSelect.innerHTML = '<option value="">-- Selecteer Home Assistant Sensor --</option>';
            controlSelect.innerHTML = '<option value="">-- Geen / Niet bestuurbaar --</option>';

            haEntitiesCache.forEach(e => {
                const opt = document.createElement('option');
                opt.value = e.entity_id;
                opt.innerText = `${e.friendly_name} (${e.entity_id})`;

                if (e.domain === 'sensor' && (e.entity_id.includes('power') || e.entity_id.includes('watt') || e.entity_id.includes('temp'))) {
                    powerSelect.appendChild(opt.cloneNode(true));
                }
                if (e.domain === 'switch' || e.domain === 'climate' || e.domain === 'input_boolean') {
                    controlSelect.appendChild(opt.cloneNode(true));
                }
            });
        }

        async function loadChartData() {
            try {
                const res = await fetch('./api/schedule/chart-data');
                const data = await res.json();

                const adv = data.advices[data.cheapest_hour] || `Beste stroomtarief om ${data.cheapest_hour}:00 (€${data.cheapest_price_eur.toFixed(4)}/kWh)`;
                document.getElementById('banner-text').innerText = adv;
                document.getElementById('battery-status-banner').innerText = data.battery_status_msg;

                const ctx = document.getElementById('hemsChart').getContext('2d');
                if (chartInstance) chartInstance.destroy();

                chartInstance = new Chart(ctx, {
                    type: 'bar',
                    data: {
                        labels: data.labels,
                        datasets: [
                            {
                                label: 'Basislast (kW)',
                                data: data.datasets.baseload_kw,
                                backgroundColor: '#3B82F6',
                                stack: 'consumption',
                                borderRadius: 4
                            },
                            {
                                label: 'Warmtepomp / SWW (kW)',
                                data: data.datasets.boiler_kw,
                                backgroundColor: '#EC4899',
                                stack: 'consumption',
                                borderRadius: 4
                            },
                            {
                                label: 'Accu Laden (kW)',
                                data: data.datasets.battery_charge_kw,
                                backgroundColor: '#10B981',
                                stack: 'consumption',
                                borderRadius: 4
                            },
                            {
                                label: 'Zon Productie (kW)',
                                data: data.datasets.solar_kw,
                                type: 'line',
                                borderColor: '#F59E0B',
                                borderWidth: 3,
                                pointBackgroundColor: '#F59E0B',
                                pointRadius: 3,
                                tension: 0.35,
                                yAxisID: 'y'
                            },
                            {
                                label: 'Stroomprijs (€/kWh)',
                                data: data.datasets.prices_eur,
                                type: 'line',
                                borderColor: '#06B6D4',
                                borderDash: [5, 5],
                                borderWidth: 2,
                                pointRadius: 0,
                                yAxisID: 'y1'
                            }
                        ]
                    },
                    options: {
                        responsive: true,
                        maintainAspectRatio: false,
                        interaction: { mode: 'index', intersect: false },
                        scales: {
                            x: {
                                grid: { color: '#1E293B' },
                                ticks: { color: '#94A3B8', font: { family: 'monospace' } }
                            },
                            y: {
                                stacked: true,
                                title: { display: true, text: 'Vermogen / Energie (kW)', color: '#94A3B8' },
                                grid: { color: '#1E293B' },
                                ticks: { color: '#94A3B8' }
                            },
                            y1: {
                                position: 'right',
                                title: { display: true, text: 'Prijs (€/kWh)', color: '#06B6D4' },
                                grid: { drawOnChartArea: false },
                                ticks: { color: '#06B6D4' }
                            }
                        }
                    }
                });
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
        async function loadDevices() {
            const [devRes, polRes] = await Promise.all([fetch('./api/devices'), fetch('./api/policies')]);
            const devData = await devRes.json();
            const polData = await polRes.json();

            const policies = polData.policies || [];
            const container = document.getElementById('devices-container');
            container.innerHTML = '';
            document.getElementById('badge-dev-count').innerText = (devData.devices || []).length;

            (devData.devices || []).forEach(dev => {
                const boundPolicies = policies.filter(p => (p.target_devices || []).includes(dev.id));
                const policyBadge = boundPolicies.length > 0
                    ? boundPolicies.map(p => `<span class="px-1.5 py-0.5 rounded text-[10px] bg-purple-900/40 text-purple-300 border border-purple-800 font-medium">${p.name}</span>`).join(' ')
                    : '<span class="text-slate-500 italic">Geen beleid gekoppeld (stand-by)</span>';

                const card = document.createElement('div');
                card.className = 'bg-[#0e1422] border border-[#1E293B] rounded-2xl p-5 flex flex-col justify-between shadow-lg';
                card.innerHTML = `
                    <div>
                        <div class="flex justify-between items-start mb-2">
                            <h4 class="font-bold text-white text-sm">${dev.name}</h4>
                            <span class="px-2 py-0.5 rounded text-[10px] font-semibold bg-blue-900/40 text-blue-300 border border-blue-800">${dev.type}</span>
                        </div>
                        <div class="text-[11px] text-slate-400 mb-2 flex items-center gap-1.5 flex-wrap">
                            <span>Beleid:</span> ${policyBadge}
                        </div>
                        <div class="text-[11px] text-slate-300 bg-[#0B0F17] p-2.5 rounded-lg border border-slate-800 mb-3 font-mono space-y-1">
                            ${dev.source_type === 'mqtt' 
                                ? `<div>Bron: <span class="text-amber-400 font-bold">⚡ MQTT Topic</span></div>
                                   <div class="truncate text-slate-400 text-[10px]">${dev.mqtt_power_topic || 'geen topic'}</div>
                                   ${dev.mqtt_control_topic ? `<div class="truncate text-slate-400 text-[10px]">Cmd: ${dev.mqtt_control_topic}</div>` : ''}`
                                : `<div>Bron: <span class="text-cyan-400 font-bold">🏠 Home Assistant</span></div>
                                   <div class="truncate text-slate-400 text-[10px]">${dev.ha_power_entity || 'geen sensor'}</div>
                                   <div class="truncate text-slate-400 text-[10px]">${dev.ha_control_entity || 'geen switch'}</div>`
                            }
                        </div>
                    </div>
                    <div class="flex justify-end gap-2 pt-3 border-t border-[#1E293B]">
                        <button onclick='openDeviceModal(${JSON.stringify(dev)})' class="px-2.5 py-1 bg-slate-800 hover:bg-slate-700 text-slate-200 text-xs rounded-lg">Bewerken</button>
                        <button onclick="deleteDevice('${dev.id}')" class="px-2.5 py-1 bg-red-950/60 hover:bg-red-900 text-red-300 border border-red-800 text-xs rounded-lg">Verwijderen</button>
                    </div>
                `;
                container.appendChild(card);
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

        function openDeviceModal(dev = null) {
            populateHaDropdowns();
            
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
                document.getElementById('modal-dev-ha-power').value = dev.ha_power_entity || '';
                document.getElementById('modal-dev-ha-control').value = dev.ha_control_entity || '';
                document.getElementById('modal-dev-mqtt-broker').value = dev.mqtt_broker_id || '';
                document.getElementById('modal-dev-mqtt-power-topic').value = dev.mqtt_power_topic || '';
                document.getElementById('modal-dev-mqtt-json-key').value = dev.mqtt_power_json_key || '';
                document.getElementById('modal-dev-mqtt-control-topic').value = dev.mqtt_control_topic || '';
            } else {
                document.getElementById('modal-dev-title').innerText = 'Nieuw Apparaat Toevoegen';
                document.getElementById('modal-dev-id').value = '';
                document.getElementById('modal-dev-name').value = '';
                document.getElementById('modal-dev-source-type').value = 'homeassistant';
                document.getElementById('modal-dev-mqtt-power-topic').value = '';
                document.getElementById('modal-dev-mqtt-json-key').value = '';
                document.getElementById('modal-dev-mqtt-control-topic').value = '';
            }
            document.getElementById('modal-dev-min-runtime').value = (dev && dev.parameters) ? (dev.parameters.min_runtime_minutes || '') : '';
            document.getElementById('modal-dev-max-power').value = (dev && dev.parameters) ? (dev.parameters.max_power_w || '') : '';
            document.getElementById('modal-dev-emergency-threshold').value = (dev && dev.parameters) ? (dev.parameters.emergency_threshold || '') : '';
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
                ha_control_entity: document.getElementById('modal-dev-ha-control').value,
                mqtt_broker_id: document.getElementById('modal-dev-mqtt-broker').value,
                mqtt_power_topic: document.getElementById('modal-dev-mqtt-power-topic').value,
                mqtt_power_json_key: document.getElementById('modal-dev-mqtt-json-key').value,
                mqtt_control_topic: document.getElementById('modal-dev-mqtt-control-topic').value,
                parameters: {
                    min_runtime_minutes: parseInt(document.getElementById('modal-dev-min-runtime').value) || 0,
                    max_power_w: parseFloat(document.getElementById('modal-dev-max-power').value) || 0,
                    emergency_threshold: parseFloat(document.getElementById('modal-dev-emergency-threshold').value) || 0
                }
            };
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

                let powerProducersChartInstance = null;

        async function loadPowerProducersChart() {
            const canvas = document.getElementById('powerProducersChart');
            if (!canvas) return;
            
            try {
                const res = await fetch('./api/analytics/power_producers');
                const data = await res.json();
                if (data.status !== 'success') {
                    console.error('Power producers error:', data.message);
                    return;
                }

                // Update Legend Stats
                const s = data.stats || {};
                if (s.zonnepanelen) {
                    document.getElementById('stat-solar-last').innerText = s.zonnepanelen.last;
                    document.getElementById('stat-solar-min').innerText = s.zonnepanelen.min;
                }
                if (s.teruglevering) {
                    document.getElementById('stat-terug-last').innerText = s.teruglevering.last;
                    document.getElementById('stat-terug-min').innerText = s.teruglevering.min;
                }
                if (s.afname) {
                    document.getElementById('stat-afname-last').innerText = s.afname.last;
                    document.getElementById('stat-afname-max').innerText = s.afname.max;
                }
                if (s.totaal_opgewekt) {
                    document.getElementById('stat-opgewekt-last').innerText = s.totaal_opgewekt.last;
                    document.getElementById('stat-opgewekt-min').innerText = s.totaal_opgewekt.min;
                }
                if (s.opgewekt_gebruikt) {
                    document.getElementById('stat-selfcons-last').innerText = s.opgewekt_gebruikt.last;
                    document.getElementById('stat-selfcons-min').innerText = s.opgewekt_gebruikt.min;
                }
                if (s.totaal_verbruik) {
                    document.getElementById('stat-verbruik-last').innerText = s.totaal_verbruik.last;
                    document.getElementById('stat-verbruik-max').innerText = s.totaal_verbruik.max;
                }

                // Destroy old instance if exists
                if (powerProducersChartInstance) powerProducersChartInstance.destroy();

                const ctx = canvas.getContext('2d');
                powerProducersChartInstance = new Chart(ctx, {
                    type: 'line',
                    data: {
                        labels: data.labels,
                        datasets: [
                            // 1. Totaal Verbruik (Orange line on top)
                            {
                                label: 'Totaal Verbruik',
                                data: data.verbruik,
                                borderColor: '#F97316',
                                backgroundColor: 'transparent',
                                borderWidth: 2,
                                pointRadius: 0,
                                tension: 0.25,
                                order: 1
                            },
                            // 2. Afname (Crimson/Red fill on positive axis)
                            {
                                label: 'Afname',
                                data: data.afname,
                                borderColor: '#EF4444',
                                backgroundColor: 'rgba(239, 68, 68, 0.45)',
                                fill: true,
                                borderWidth: 1.5,
                                pointRadius: 0,
                                tension: 0.25,
                                order: 2
                            },
                            // 3. Opgewekt Gebruikt (Teal/Green fill)
                            {
                                label: 'Opgewekt Gebruikt',
                                data: data.self_consumption,
                                borderColor: '#14B8A6',
                                backgroundColor: 'rgba(20, 184, 166, 0.25)',
                                fill: true,
                                borderWidth: 1,
                                pointRadius: 0,
                                tension: 0.25,
                                order: 3
                            },
                            // 4. Teruglevering (Green area below zero)
                            {
                                label: 'Teruglevering',
                                data: data.teruglevering_negative,
                                borderColor: '#10B981',
                                backgroundColor: 'rgba(16, 185, 129, 0.45)',
                                fill: true,
                                borderWidth: 1.5,
                                pointRadius: 0,
                                tension: 0.25,
                                order: 4
                            },
                            // 5. Zonnepanelen (Yellow area below zero)
                            {
                                label: 'Zonnepanelen',
                                data: data.solar_negative,
                                borderColor: '#EAB308',
                                backgroundColor: 'rgba(234, 179, 8, 0.55)',
                                fill: true,
                                borderWidth: 1.5,
                                pointRadius: 0,
                                tension: 0.25,
                                order: 5
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
                                display: false // Using custom Grafana table legend
                            },
                            tooltip: {
                                backgroundColor: 'rgba(11, 15, 23, 0.95)',
                                borderColor: '#1E293B',
                                borderWidth: 1,
                                titleFont: { family: 'monospace', size: 12 },
                                bodyFont: { family: 'monospace', size: 11 },
                                callbacks: {
                                    label: function(context) {
                                        const val = context.raw || 0;
                                        const absV = Math.abs(val);
                                        const str = absV >= 1000 ? `${(absV / 1000).toFixed(2)} kW` : `${absV} W`;
                                        return `${context.dataset.label}: ${val < 0 ? '-' : ''}${str}`;
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
                                    callback: function(val) {
                                        const absV = Math.abs(val);
                                        const prefix = val < 0 ? '-' : '';
                                        return absV >= 1000 ? `${prefix}${absV / 1000} kW` : `${val} W`;
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
        showTab("analytics");
    </script>
</body>
</html>"""
        self.wfile.write(html.encode("utf-8"))


def run_server(port=8099):
    server = HTTPServer(("0.0.0.0", port), HemsApiHandler)
    print(f"Open HEMS Framework Console running on port {port}...")
    server.serve_forever()


def main():
    parser = argparse.ArgumentParser(description="Open HEMS Daemon")
    parser.add_argument("--config", default=str(CONFIG_FILE))
    parser.add_argument("--interval", type=int, default=15)
    parser.add_argument("--port", type=int, default=8099)
    args = parser.parse_args()

    cfg = load_json(Path(args.config) if args.config else CONFIG_FILE)
    ensure_framework_defaults(cfg)
    run_server(args.port)


if __name__ == "__main__":
    main()
