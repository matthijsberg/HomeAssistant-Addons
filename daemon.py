#!/usr/bin/env python3
"""
Open HEMS Framework & Management Console
========================================
Version: 0.3.1
Generic Energy Management Platform:
  - Clean slate framework with pluggable providers (EPEX Spot, Open-Meteo)
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
import ssl
from http.server import HTTPServer, BaseHTTPRequestHandler
from datetime import datetime
from pathlib import Path

CONFIG_FILE = Path("/config/heatpump_config.json")
PARAMS_FILE = Path("/config/heatpump_model_parameters.json")
CACHE_FILE = Path("/config/data/energy_feed_cache.json")
HA_API_CONFIG = Path("/config/.ha_api_config.json")

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
                if domain in ["sensor", "switch", "climate", "binary_sensor", "input_boolean"]:
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


def ensure_framework_defaults(cfg: dict):
    """Initializes the generic framework defaults if config is fresh."""
    dirty = False

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

        # API: Status
        if path == "/api/status":
            cfg = load_json(CONFIG_FILE)
            params = load_json(PARAMS_FILE)
            ensure_framework_defaults(cfg)
            self._send_json({
                "system": "Open HEMS Framework",
                "version": "0.3.1",
                "timestamp": datetime.now().isoformat(),
                "status": "online",
                "site_name": cfg.get("site", {}).get("name", "Woning Culemborg"),
                "total_devices": len(cfg.get("devices", [])),
                "total_policies": len(cfg.get("policies", [])),
                "total_tariffs": len(cfg.get("tariffs_list", [])),
                "dhw_optimal_run": cfg.get("last_optimal_run", "13:00"),
                "dhw_temperature": 52.8,
                "heatpump_power_w": 33.0,
                "smart_grid_mode": "SG2",
                "last_calibration": params.get("calibration_timestamp", "Recent")
            })
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

        # API: 24h Stacked Chart Data with Decoupled Policy Evaluation
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
            battery_discharge = [0.0] * 24
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
                # Check morning/evening peak lockouts
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
                battery_discharge[max_price_hour] = -2.0
                battery_status_msg = f"🔋 Accu-Arbitrage Actief: Laden om {min_price_hour}:00 (€{min_price:.2f}), Ontladen om {max_price_hour}:00 (€{max_price:.2f}) [Delta €{delta_price:.3f} > €{deadband_threshold:.3f}]"
            else:
                battery_status_msg = f"⏸️ Accu Rust (Deadband): Delta €{delta_price:.3f}/kWh is te klein (< €{deadband_threshold:.3f}/kWh). Geen net-arbitrage."
                # Solar buffering only if surplus exists
                if max_solar > 1.5:
                    battery_charge[max_solar_hour] = round(min(2.0, max_solar - 0.5), 2)
                    battery_status_msg += f" Wel zonne-buffer om {max_solar_hour}:00."

            # Set Advice Callout Banners
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
    # POST ROUTER (Create)
    # =========================================================================
    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path.rstrip("/")
        body = self._read_json_body()

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
                "adapter": body.get("adapter", "custom"),
                "capabilities": body.get("capabilities", ["read_power"]),
                "ha_power_entity": body.get("ha_power_entity", ""),
                "ha_energy_entity": body.get("ha_energy_entity", ""),
                "ha_temp_entity": body.get("ha_temp_entity", ""),
                "ha_control_entity": body.get("ha_control_entity", ""),
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
                    for k in ["name", "type", "adapter", "capabilities", "ha_power_entity", "ha_energy_entity", "ha_temp_entity", "ha_control_entity", "parameters"]:
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
    # HTML SINGLE PAGE APPLICATION (Framework UI + Policies & Stacked Graph)
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
<body class="h-full text-slate-200 antialiased flex overflow-hidden bg-[#080B11] font-sans select-none">

    <!-- LEFT SIDEBAR NAVIGATION -->
    <aside class="w-64 flex-shrink-0 bg-[#0B0F17] border-r border-[#1E293B] flex flex-col justify-between z-20">
        <div>
            <!-- Header Brand -->
            <div class="h-20 px-6 flex items-center justify-between border-b border-[#1E293B]">
                <div class="flex items-center gap-3">
                    <div class="w-10 h-10 rounded-xl bg-gradient-to-tr from-amber-500/20 to-amber-400/10 border border-amber-500/30 flex items-center justify-center text-amber-400 shadow-[0_0_15px_rgba(245,158,11,0.2)]">
                        <svg class="w-5 h-5 fill-current" viewBox="0 0 24 24"><path d="M13 2L3 14h9l-1 8 10-12h-9l1-8z"></path></svg>
                    </div>
                    <div>
                        <div class="flex items-center gap-2">
                            <span class="font-bold tracking-tight text-white text-base">Open HEMS</span>
                            <span class="px-1.5 py-0.5 text-[9px] font-semibold bg-blue-500/10 text-blue-400 rounded border border-blue-500/20">FRAMEWORK</span>
                        </div>
                        <p class="text-[11px] text-slate-400">Decoupled Policy Engine</p>
                    </div>
                </div>
            </div>

            <!-- Nav Links -->
            <nav class="p-3 space-y-1">
                <div class="px-3 pt-3 pb-1 text-[10px] font-bold text-slate-500 uppercase tracking-wider">Visualisatie & Status</div>
                <a href="#dashboard" onclick="showTab('dashboard')" id="nav-dashboard" class="nav-link active flex items-center gap-3 px-3.5 py-2.5 rounded-xl text-xs font-medium text-slate-400 hover:text-white hover:bg-slate-800/40 transition-colors">
                    <svg class="w-4 h-4 text-blue-400" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z"></path></svg>
                    <span>24h Grafiek & Advies</span>
                </a>

                <div class="px-3 pt-4 pb-1 text-[10px] font-bold text-slate-500 uppercase tracking-wider">Orchestratie & Regels</div>
                <a href="#policies" onclick="showTab('policies')" id="nav-policies" class="nav-link flex items-center gap-3 px-3.5 py-2.5 rounded-xl text-xs font-medium text-slate-400 hover:text-white hover:bg-slate-800/40 transition-colors">
                    <svg class="w-4 h-4 text-purple-400" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path d="M12 6V4m0 2a2 2 0 100 4m0-4a2 2 0 110 4m-6 8a2 2 0 100-4m0 4a2 2 0 110-4m0 4v2m0-6V4m6 6v10m6-2a2 2 0 100-4m0 4a2 2 0 110-4m0 4v2m0-6V4"></path></svg>
                    <span>Beleid & Policies</span>
                    <span class="ml-auto text-[10px] px-1.5 py-0.5 bg-purple-900/40 text-purple-300 font-medium rounded border border-purple-800" id="badge-pol-count">3</span>
                </a>

                <div class="px-3 pt-4 pb-1 text-[10px] font-bold text-slate-500 uppercase tracking-wider">Entiteiten & Bronnen</div>
                <a href="#devices" onclick="showTab('devices')" id="nav-devices" class="nav-link flex items-center gap-3 px-3.5 py-2.5 rounded-xl text-xs font-medium text-slate-400 hover:text-white hover:bg-slate-800/40 transition-colors">
                    <svg class="w-4 h-4 text-cyan-400" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path d="M9 3v2m6-2v2M9 19v2m6-2v2M5 9H3m2 6H3m18-6h-2m2 6h-2M7 19h10a2 2 0 002-2V7a2 2 0 00-2-2H7a2 2 0 00-2 2v10a2 2 0 002 2zM9 9h6v6H9V9z"></path></svg>
                    <span>Apparaten (Hardware Links)</span>
                    <span class="ml-auto text-[10px] px-1.5 py-0.5 bg-blue-900/40 text-blue-300 font-medium rounded border border-blue-800" id="badge-dev-count">0</span>
                </a>
                <a href="#tariffs" onclick="showTab('tariffs')" id="nav-tariffs" class="nav-link flex items-center gap-3 px-3.5 py-2.5 rounded-xl text-xs font-medium text-slate-400 hover:text-white hover:bg-slate-800/40 transition-colors">
                    <svg class="w-4 h-4 text-emerald-400" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path d="M12 8c-1.657 0-3 .895-3 2s1.343 2 3 2 3 .895 3 2-1.343 2-3 2m0-8c1.11 0 2.08.402 2.599 1M12 8V7m0 1v8m0 0v1m0-1c-1.11 0-2.08-.402-2.599-1M21 12a9 9 0 11-18 0 9 9 0 0118 0z"></path></svg>
                    <span>Energieleveranciers (Tarieven)</span>
                </a>

                <div class="px-3 pt-4 pb-1 text-[10px] font-bold text-slate-500 uppercase tracking-wider">APIs & Zelflerend</div>
                <a href="#providers" onclick="showTab('providers')" id="nav-providers" class="nav-link flex items-center gap-3 px-3.5 py-2.5 rounded-xl text-xs font-medium text-slate-400 hover:text-white hover:bg-slate-800/40 transition-colors">
                    <svg class="w-4 h-4 text-amber-400" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path d="M21 12a9 9 0 01-9 9m9-9a9 9 0 00-9-9m9 9H3m9 9a9 9 0 01-9-9m9 9c1.657 0 3-4.03 3-9s-1.343-9-3-9m0 18c-1.657 0-3-4.03-3-9s1.343-9 3-9m-9 9a9 9 0 019-9"></path></svg>
                    <span>Open APIs (EPEX / Meteo)</span>
                </a>
                <a href="#calibration" onclick="showTab('calibration')" id="nav-calibration" class="nav-link flex items-center gap-3 px-3.5 py-2.5 rounded-xl text-xs font-medium text-slate-400 hover:text-white hover:bg-slate-800/40 transition-colors">
                    <svg class="w-4 h-4 text-slate-400" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path d="M3 6l3 18h12l3-18H3zm6 3v10m6-10v10M9 6V4a2 2 0 012-2h2a2 2 0 012 2v2"></path></svg>
                    <span>Kalibratie & Offsets</span>
                </a>
            </nav>
        </div>

        <div class="p-4 border-t border-[#1E293B] bg-[#0A0D14]/80 text-[10px] text-slate-500 flex justify-between">
            <span>Versie: <strong class="text-slate-400">v0.3.1</strong></span>
            <span>Policy Decoupled</span>
        </div>
    </aside>

    <!-- MAIN VIEW -->
    <main class="flex-1 flex flex-col min-w-0 overflow-y-auto bg-[#080B11]">
        <header class="h-20 border-b border-[#1E293B] bg-[#0B0F17]/90 backdrop-blur px-8 flex items-center justify-between sticky top-0 z-30">
            <div>
                <h1 class="text-lg font-bold text-white tracking-tight" id="header-title">24h Verwachting & Gestapeld Verbruik</h1>
                <p class="text-xs text-slate-400 mt-0.5" id="header-sub">Gestapelde uurgrafiek: basislast, warmtepomp, accu en zonne-advies</p>
            </div>
            <div class="flex items-center gap-3">
                <button onclick="loadChartData()" class="px-3 py-1.5 bg-blue-600 hover:bg-blue-500 text-white text-xs font-semibold rounded-xl shadow transition-all flex items-center gap-1.5">
                    <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"></path></svg>
                    <span>Herberekenen</span>
                </button>
            </div>
        </header>

        <div class="p-8 space-y-6">

            <!-- TAB 1: 24H STACKED BAR GRAPH & RECOMMENDATION BALLOONS -->
            <div id="view-dashboard" class="tab-content active space-y-6">
                <!-- Recommendation Balloon Banner -->
                <div id="recommendation-banner" class="bg-gradient-to-r from-emerald-950/80 via-[#0e1422] to-amber-950/80 border border-emerald-500/40 rounded-2xl p-4 shadow-xl flex items-center justify-between">
                    <div class="flex items-center gap-3">
                        <div class="w-10 h-10 rounded-xl bg-emerald-500/20 border border-emerald-500/30 flex items-center justify-center text-emerald-400 text-xl shadow-[0_0_15px_rgba(16,185,129,0.3)]">
                            💡
                        </div>
                        <div>
                            <span class="text-[10px] uppercase font-bold text-emerald-400 tracking-wider">Dynamisch Verbruiksadvies</span>
                            <div class="text-sm font-bold text-white mt-0.5" id="banner-text">
                                Goedkoopste stroom verwacht om 13:00 (€0.18/kWh) — Warmtepomp buffert automatisch naar 60°C!
                            </div>
                        </div>
                    </div>
                    <span class="px-2.5 py-1 rounded-lg text-xs font-mono font-bold bg-emerald-500/20 text-emerald-300 border border-emerald-500/40" id="banner-tag">
                        OPTIMAL DISPATCH
                    </span>
                </div>

                <!-- Battery Economic Status Banner -->
                <div class="bg-[#0e1422] border border-[#1E293B] rounded-xl p-3.5 flex items-center gap-3 text-xs text-slate-300">
                    <span class="text-base">🔋</span>
                    <span id="battery-status-banner" class="font-mono text-emerald-400">Accu-beleid wordt geëvalueerd...</span>
                </div>

                <!-- The Stacked Bar Chart Card -->
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

            <!-- TAB 2: POLICIES CRUD (The Core Orchestration Rules) -->
            <div id="view-policies" class="tab-content space-y-4">
                <div class="flex justify-between items-center">
                    <div>
                        <h2 class="text-base font-bold text-white">Beleidsregels & Orchestratie (Policy Engine)</h2>
                        <p class="text-xs text-slate-400">Definieer overkoepelend beleid op basis van kosten, zonne-opwek en comfortguardrails.</p>
                    </div>
                    <button onclick="openPolicyModal()" class="px-3 py-1.5 bg-purple-600 hover:bg-purple-500 text-white text-xs font-semibold rounded-xl shadow transition-all">
                        + Nieuwe Policy Aanmaken
                    </button>
                </div>
                <div id="policies-container" class="grid grid-cols-1 md:grid-cols-3 gap-5">
                    <!-- Loaded dynamically -->
                </div>
            </div>

            <!-- TAB 3: APPARATEN CRUD (Pure Hardware Links) -->
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
                <div id="devices-container" class="grid grid-cols-1 md:grid-cols-3 gap-5">
                    <!-- Loaded dynamically -->
                </div>
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
                <div id="tariffs-container" class="grid grid-cols-1 md:grid-cols-2 gap-5">
                    <!-- Loaded dynamically -->
                </div>
            </div>

            <!-- TAB 5: OPEN APIS -->
            <div id="view-providers" class="tab-content space-y-4">
                <div>
                    <h2 class="text-base font-bold text-white">Standaard Open API Providers</h2>
                    <p class="text-xs text-slate-400">Breed toepasbare publieke databronnen die het framework out-of-the-box ontsluit.</p>
                </div>
                <div class="grid grid-cols-1 md:grid-cols-2 gap-5">
                    <div class="bg-[#0e1422] border border-[#1E293B] rounded-2xl p-5">
                        <div class="flex justify-between items-center mb-2">
                            <h3 class="font-bold text-white text-sm">EPEX Spot / EnergyZero API</h3>
                            <span class="px-2 py-0.5 rounded text-[10px] font-semibold bg-emerald-950 text-emerald-300 border border-emerald-800">ACTIEF</span>
                        </div>
                        <p class="text-xs text-slate-400 mb-2">Publieke Europese day-ahead beursprijzen per uur en kwartier.</p>
                        <code class="text-[11px] text-cyan-300 block bg-[#0B0F17] p-2 rounded">https://api.energyzero.net/v1/energyprices</code>
                    </div>
                    <div class="bg-[#0e1422] border border-[#1E293B] rounded-2xl p-5">
                        <div class="flex justify-between items-center mb-2">
                            <h3 class="font-bold text-white text-sm">Open-Meteo Solar & Weather</h3>
                            <span class="px-2 py-0.5 rounded text-[10px] font-semibold bg-emerald-950 text-emerald-300 border border-emerald-800">ACTIEF</span>
                        </div>
                        <p class="text-xs text-slate-400 mb-2">48-uurs globale instraling (W/m²), temperatuur en windvoorspelling.</p>
                        <code class="text-[11px] text-cyan-300 block bg-[#0B0F17] p-2 rounded">https://api.open-meteo.com/v1/forecast</code>
                    </div>
                </div>
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

    <!-- MODAL: ADD / EDIT POLICY -->
    <div id="policy-modal" class="fixed inset-0 bg-black/70 flex items-center justify-center hidden z-50">
        <div class="bg-[#0e1422] border border-[#1E293B] rounded-2xl p-6 w-full max-w-lg text-xs text-slate-300 max-h-[90vh] overflow-y-auto">
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

                <!-- Device Selector for Policy Binding -->
                <div>
                    <label class="block mb-1 text-slate-400">Gekoppelde HEMS Apparaten (Selecteer één of meer)</label>
                    <div id="modal-pol-devices-list" class="bg-[#0B0F17] border border-slate-800 rounded-lg p-2.5 max-h-36 overflow-y-auto space-y-1.5 font-sans text-xs">
                        <span class="text-slate-500 italic">Apparaten laden...</span>
                    </div>
                </div>

                <!-- Dynamic Parameters Container -->
                <div id="pol-params-container" class="space-y-3 pt-2 border-t border-slate-800">
                    <!-- Fields injected based on type -->
                </div>

                <div class="flex justify-end gap-2 pt-3 border-t border-slate-800">
                    <button type="button" onclick="closeModal('policy-modal')" class="px-3 py-1.5 bg-slate-800 text-slate-400 rounded-lg">Annuleren</button>
                    <button type="submit" class="px-3 py-1.5 bg-purple-600 hover:bg-purple-500 text-white font-semibold rounded-lg">Opslaan</button>
                </div>
            </form>
        </div>
    </div>

    <!-- MODAL: ADD / EDIT DEVICE (Hardware & HA Selectors Only) -->
    <div id="device-modal" class="fixed inset-0 bg-black/70 flex items-center justify-center hidden z-50">
        <div class="bg-[#0e1422] border border-[#1E293B] rounded-2xl p-6 w-full max-w-lg text-xs text-slate-300">
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

                <!-- HA Entity Selector Dropdowns -->
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

                <div class="flex justify-end gap-2 pt-3 border-t border-slate-800">
                    <button type="button" onclick="closeModal('device-modal')" class="px-3 py-1.5 bg-slate-800 text-slate-400 rounded-lg">Annuleren</button>
                    <button type="submit" class="px-3 py-1.5 bg-blue-600 text-white font-semibold rounded-lg">Opslaan</button>
                </div>
            </form>
        </div>
    </div>

    <!-- MODAL: ADD / EDIT TARIFF -->
    <div id="tariff-modal" class="fixed inset-0 bg-black/70 flex items-center justify-center hidden z-50">
        <div class="bg-[#0e1422] border border-[#1E293B] rounded-2xl p-6 w-full max-w-md text-xs text-slate-300">
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
    <div id="exclusion-modal" class="fixed inset-0 bg-black/70 flex items-center justify-center hidden z-50">
        <div class="bg-[#0e1422] border border-[#1E293B] rounded-2xl p-6 w-full max-w-md text-xs text-slate-300">
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

        function showTab(tabId) {
            document.querySelectorAll('.tab-content').forEach(el => el.classList.remove('active'));
            document.querySelectorAll('.nav-link').forEach(el => el.classList.remove('active'));
            const target = document.getElementById('view-' + tabId);
            if (target) target.classList.add('active');
            const link = document.getElementById('nav-' + tabId);
            if (link) link.classList.add('active');

            const titles = {
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

            if (tabId === 'dashboard') loadChartData();
            if (tabId === 'policies') loadPolicies();
            if (tabId === 'devices') loadDevices();
            if (tabId === 'tariffs') loadTariffs();
            if (tabId === 'calibration') loadCalibration();
        }

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

                // Update recommendation banner
                const adv = data.advices[data.cheapest_hour] || `Beste stroomtarief om ${data.cheapest_hour}:00 (€${data.cheapest_price_eur.toFixed(4)}/kWh)`;
                document.getElementById('banner-text').innerText = adv;
                document.getElementById('battery-status-banner').innerText = data.battery_status_msg;

                // Render Chart.js Stacked Bar & Curves
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
                        },
                        plugins: {
                            tooltip: {
                                callbacks: {
                                    afterBody: function(items) {
                                        const idx = items[0].dataIndex;
                                        if (data.advices[idx]) {
                                            return '\\n💡 ' + data.advices[idx];
                                        }
                                        return '';
                                    }
                                }
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

                // Render friendly device badges
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
                        <p class="text-[11px] text-slate-400 mb-1">Sensor: <code class="text-cyan-300">${dev.ha_power_entity || 'Geen'}</code></p>
                        <p class="text-[11px] text-slate-400 mb-3">Relais/Switch: <code class="text-cyan-300">${dev.ha_control_entity || 'Geen'}</code></p>
                    </div>
                    <div class="flex justify-end gap-2 pt-3 border-t border-[#1E293B]">
                        <button onclick='openDeviceModal(${JSON.stringify(dev)})' class="px-2.5 py-1 bg-slate-800 hover:bg-slate-700 text-slate-200 text-xs rounded-lg">Bewerken</button>
                        <button onclick="deleteDevice('${dev.id}')" class="px-2.5 py-1 bg-red-950/60 hover:bg-red-900 text-red-300 border border-red-800 text-xs rounded-lg">Verwijderen</button>
                    </div>
                `;
                container.appendChild(card);
            });
        }

        function openDeviceModal(dev = null) {
            populateHaDropdowns();
            if (dev) {
                document.getElementById('modal-dev-title').innerText = 'Apparaat Bewerken';
                document.getElementById('modal-dev-id').value = dev.id;
                document.getElementById('modal-dev-name').value = dev.name;
                document.getElementById('modal-dev-type').value = dev.type;
                document.getElementById('modal-dev-ha-power').value = dev.ha_power_entity || '';
                document.getElementById('modal-dev-ha-control').value = dev.ha_control_entity || '';
            } else {
                document.getElementById('modal-dev-title').innerText = 'Nieuw Apparaat Toevoegen';
                document.getElementById('modal-dev-id').value = '';
                document.getElementById('modal-dev-name').value = '';
            }
            document.getElementById('device-modal').classList.remove('hidden');
        }

        async function saveDevice(e) {
            e.preventDefault();
            const id = document.getElementById('modal-dev-id').value;
            const payload = {
                name: document.getElementById('modal-dev-name').value,
                type: document.getElementById('modal-dev-type').value,
                ha_power_entity: document.getElementById('modal-dev-ha-power').value,
                ha_control_entity: document.getElementById('modal-dev-ha-control').value
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

        // Boot
        fetchHaEntities();
        loadChartData();
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
