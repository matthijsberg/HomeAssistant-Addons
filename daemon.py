#!/usr/bin/env python3
"""
Open HEMS Background Daemon & Full Ingress Management Console
============================================================
Version: 0.2.1-dev.1
Provides:
  - RESTful CRUD API for Devices, Tariffs, Calibration Offsets, and Exclusion Windows
  - Responsive Single-Page Application (SPA) with full navigation
  - Background Periodic Dispatch and Optimization Trigger
"""

import sys
import os
import re
import argparse
import json
import urllib.parse
from http.server import HTTPServer, BaseHTTPRequestHandler
from datetime import datetime
from pathlib import Path

# Setup paths
PROJECT_ROOT = Path("/config/projects/energy-scheduler")
CONFIG_FILE = Path("/config/heatpump_config.json")
PARAMS_FILE = Path("/config/heatpump_model_parameters.json")
CACHE_FILE = Path("/config/data/energy_feed_cache.json")

sys.path.insert(0, str(PROJECT_ROOT))
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


def ensure_default_devices(cfg: dict):
    """Ensures standard canonical devices exist in the config if not explicitly declared."""
    if "devices" not in cfg or not cfg["devices"]:
        cfg["devices"] = [
            {
                "id": "main_grid_meter",
                "name": "Hoofdmeter (P1 DSMR)",
                "type": "grid_meter",
                "adapter": "p1_dsmr",
                "capabilities": ["read_power", "read_energy"],
                "parameters": {"max_amps_per_phase": 25.0, "phases": 3}
            },
            {
                "id": "rooftop_solar",
                "name": "Zonnepanelen (SolarEdge)",
                "type": "solar_inverter",
                "adapter": "sunspec_modbus",
                "capabilities": ["read_power", "read_energy", "curtail_production"],
                "parameters": {"peak_power_kw": 5.5, "tilt_deg": 40.0, "azimuth_deg": 225.0}
            },
            {
                "id": "daikin_heat_pump",
                "name": "Daikin Altherma 3 H HT (18 kW)",
                "type": "heat_pump",
                "adapter": "smart_grid_relay",
                "capabilities": ["set_mode", "read_power"],
                "parameters": {
                    "compressor_power_kw": 3.0,
                    "min_run_time_minutes": 20,
                    "isolate_space_heating_during_dhw": True
                }
            },
            {
                "id": "dhw_tank",
                "name": "Warm Tapwatervat (OEG 350L SWW)",
                "type": "dhw_boiler",
                "adapter": "temperature_sensor",
                "capabilities": ["read_temperature", "read_energy"],
                "parameters": {
                    "volume_liters": 350,
                    "target_temp_c": 50.0,
                    "boost_temp_c": 60.0,
                    "emergency_reheat_c": 38.0,
                    "deadband_reheat_c": 46.0
                }
            },
            {
                "id": "deye_home_battery",
                "name": "Deye Hybride Thuisaccu (10 kW / 10 kWh)",
                "type": "home_battery",
                "adapter": "deye_modbus_tcp",
                "capabilities": ["read_power", "read_soc", "set_power_limit", "set_mode"],
                "parameters": {
                    "capacity_kwh": 10.0,
                    "max_charge_power_w": 5000,
                    "max_discharge_power_w": 5000,
                    "min_soc_pct": 10.0,
                    "max_soc_pct": 95.0
                }
            }
        ]
        save_json(CONFIG_FILE, cfg)


class HemsApiHandler(BaseHTTPRequestHandler):
    """Unified Ingress UI and REST API Handler with complete CRUD."""

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
            self._send_json({
                "system": "Open HEMS",
                "version": "0.2.1-dev.1",
                "timestamp": datetime.now().isoformat(),
                "status": "online",
                "site_name": cfg.get("site", {}).get("name", "Woning Culemborg"),
                "total_devices": len(cfg.get("devices", [])),
                "dhw_optimal_run": cfg.get("last_optimal_run", "13:00"),
                "last_calibration": params.get("calibration_timestamp", "Recent")
            })
            return

        # API: Devices (Read All)
        if path == "/api/devices":
            cfg = load_json(CONFIG_FILE)
            ensure_default_devices(cfg)
            self._send_json({"devices": cfg.get("devices", [])})
            return

        # API: Tariffs (Read)
        if path == "/api/tariffs":
            cfg = load_json(CONFIG_FILE)
            t = cfg.get("tariffs", {})
            self._send_json({
                "provider": t.get("provider", "powerpeers_energyzero"),
                "contract_start_date": t.get("contract_start_date", "2026-09-25"),
                "import_markup_eur_kwh": t.get("import_markup_eur_kwh", 0.01210),
                "export_markup_eur_kwh": t.get("export_markup_eur_kwh", 0.01210),
                "electricity_tax_eur_kwh": t.get("electricity_tax_eur_kwh", 0.11085),
                "fixed_monthly_fee_eur": t.get("fixed_monthly_fee_eur", 6.25)
            })
            return

        # API: Calibration & Offsets (Read)
        if path == "/api/calibration":
            params = load_json(PARAMS_FILE)
            cfg = load_json(CONFIG_FILE)
            self._send_json({
                "parameters": params,
                "exclusion_windows": cfg.get("data_exclusion_windows", [])
            })
            return

        # API: Schedule & Waterfall (Read)
        if path == "/api/schedule":
            cache = load_json(CACHE_FILE)
            slots = []
            if "market_prices" in cache and "hourly" in cache["market_prices"]:
                hourly = cache["market_prices"]["hourly"]
                for h_str, p in sorted(hourly.items()):
                    slots.append({
                        "hour": int(h_str),
                        "price_eur": p,
                        "solar_kw": cache.get("weather_and_solar", {}).get("solar_kw", {}).get(str(h_str), 0.0),
                        "sg_mode": "SG4" if int(h_str) == 13 else ("SG1" if int(h_str) in [7, 8, 18, 19] else ("SG3" if int(h_str) in [12, 14, 15] else "SG2"))
                    })
            self._send_json({"slots": slots, "updated_at": cache.get("created_at", datetime.now().isoformat())})
            return

        # Serve Main SPA HTML
        self._serve_spa()

    # =========================================================================
    # POST ROUTER (Create)
    # =========================================================================
    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path.rstrip("/")
        body = self._read_json_body()

        # CREATE: Device
        if path == "/api/devices":
            cfg = load_json(CONFIG_FILE)
            ensure_default_devices(cfg)
            dev_id = body.get("id") or f"device_{int(datetime.now().timestamp())}"
            new_dev = {
                "id": dev_id,
                "name": body.get("name", "Nieuw Apparaat"),
                "type": body.get("type", "generic"),
                "adapter": body.get("adapter", "custom"),
                "capabilities": body.get("capabilities", ["read_power"]),
                "parameters": body.get("parameters", {})
            }
            cfg["devices"].append(new_dev)
            save_json(CONFIG_FILE, cfg)
            self._send_json({"status": "created", "device": new_dev}, 201)
            return

        # CREATE: Data Exclusion Window
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

        # ACTION: Recalculate Schedule
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

        # UPDATE: Specific Device
        m = re.match(r"^/api/devices/([^/]+)$", path)
        if m:
            dev_id = m.group(1)
            cfg = load_json(CONFIG_FILE)
            ensure_default_devices(cfg)
            for d in cfg["devices"]:
                if d["id"] == dev_id:
                    if "name" in body: d["name"] = body["name"]
                    if "type" in body: d["type"] = body["type"]
                    if "adapter" in body: d["adapter"] = body["adapter"]
                    if "capabilities" in body: d["capabilities"] = body["capabilities"]
                    if "parameters" in body: d["parameters"] = body["parameters"]
                    save_json(CONFIG_FILE, cfg)
                    self._send_json({"status": "updated", "device": d})
                    return
            self._send_json({"error": "Device not found"}, 404)
            return

        # UPDATE: Tariffs
        if path == "/api/tariffs":
            cfg = load_json(CONFIG_FILE)
            t = cfg.setdefault("tariffs", {})
            for k in ["import_markup_eur_kwh", "export_markup_eur_kwh", "electricity_tax_eur_kwh", "fixed_monthly_fee_eur", "contract_start_date"]:
                if k in body:
                    t[k] = float(body[k]) if "eur" in k else str(body[k])
            save_json(CONFIG_FILE, cfg)
            self._send_json({"status": "updated", "tariffs": t})
            return

        # UPDATE: Calibration Offsets
        if path == "/api/calibration":
            params = load_json(PARAMS_FILE)
            if "solar_hourly_tilt_profile" in body:
                params.setdefault("solar_hourly_tilt_profile", {}).update(body["solar_hourly_tilt_profile"])
            if "ua_base" in body:
                params["ua_base"] = float(body["ua_base"])
            if "dhw_standby_loss_kwh" in body:
                params["dhw_standby_loss_kwh"] = float(body["dhw_standby_loss_kwh"])
            save_json(PARAMS_FILE, params)
            self._send_json({"status": "updated", "parameters": params})
            return

        self._send_json({"error": "Endpoint not found"}, 404)

    # =========================================================================
    # DELETE ROUTER (Delete)
    # =========================================================================
    def do_DELETE(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path.rstrip("/")

        # DELETE: Device
        m = re.match(r"^/api/devices/([^/]+)$", path)
        if m:
            dev_id = m.group(1)
            cfg = load_json(CONFIG_FILE)
            ensure_default_devices(cfg)
            initial_len = len(cfg["devices"])
            cfg["devices"] = [d for d in cfg["devices"] if d["id"] != dev_id]
            if len(cfg["devices"]) < initial_len:
                save_json(CONFIG_FILE, cfg)
                self._send_json({"status": "deleted", "id": dev_id})
            else:
                self._send_json({"error": "Device not found"}, 404)
            return

        # DELETE: Exclusion Window by index
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
    # HTML SINGLE PAGE APPLICATION (SPA)
    # =========================================================================
    def _serve_spa(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()

        html = """<!DOCTYPE html>
<html lang="nl">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Open HEMS Console</title>
    <style>
        :root {
            --bg-main: #0F172A;
            --bg-card: #1E293B;
            --bg-hover: #334155;
            --border: #334155;
            --text-main: #F8FAFC;
            --text-muted: #94A3B8;
            --primary: #3B82F6;
            --success: #10B981;
            --warning: #F59E0B;
            --danger: #EF4444;
        }
        * { box-sizing: border-box; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;
            background-color: var(--bg-main);
            color: var(--text-main);
            margin: 0;
            padding: 0;
            display: flex;
            height: 100vh;
            overflow: hidden;
        }
        /* Sidebar Navigation */
        .sidebar {
            width: 250px;
            background: #090D16;
            border-right: 1px solid var(--border);
            display: flex;
            flex-direction: column;
            padding: 20px 0;
        }
        .brand {
            padding: 0 20px 20px 20px;
            font-size: 1.25rem;
            font-weight: 700;
            border-bottom: 1px solid var(--border);
            display: flex;
            align-items: center;
            gap: 10px;
        }
        .nav-menu {
            list-style: none;
            padding: 20px 10px;
            margin: 0;
            flex: 1;
        }
        .nav-item {
            padding: 12px 16px;
            border-radius: 8px;
            cursor: pointer;
            display: flex;
            align-items: center;
            gap: 12px;
            font-size: 0.95rem;
            color: var(--text-muted);
            margin-bottom: 6px;
            transition: all 0.15s ease;
        }
        .nav-item:hover, .nav-item.active {
            background: var(--bg-card);
            color: var(--text-main);
            font-weight: 600;
        }
        .nav-item.active {
            border-left: 3px solid var(--primary);
        }
        .version-tag {
            padding: 16px 20px;
            font-size: 0.8rem;
            color: var(--text-muted);
            border-top: 1px solid var(--border);
        }
        /* Main Content Viewport */
        .main {
            flex: 1;
            overflow-y: auto;
            padding: 32px;
        }
        .tab-content { display: none; }
        .tab-content.active { display: block; }
        h1 { margin-top: 0; font-size: 1.75rem; }
        .grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
            gap: 20px;
            margin-bottom: 28px;
        }
        .card {
            background: var(--bg-card);
            border: 1px solid var(--border);
            border-radius: 12px;
            padding: 22px;
        }
        .card h3 { margin: 0 0 8px 0; font-size: 0.9rem; color: var(--text-muted); text-transform: uppercase; letter-spacing: 0.05em; }
        .card .val { font-size: 2rem; font-weight: 700; margin-bottom: 4px; }
        .btn {
            background: var(--primary);
            color: white;
            border: none;
            padding: 10px 18px;
            border-radius: 8px;
            font-weight: 600;
            cursor: pointer;
            display: inline-flex;
            align-items: center;
            gap: 8px;
        }
        .btn:hover { opacity: 0.9; }
        .btn-danger { background: var(--danger); }
        .btn-secondary { background: #475569; }
        .badge {
            padding: 4px 8px;
            border-radius: 6px;
            font-size: 0.75rem;
            font-weight: 600;
            display: inline-block;
        }
        .badge-success { background: #064E3B; color: #10B981; }
        .badge-warning { background: #78350F; color: #F59E0B; }
        .badge-primary { background: #1E3A8A; color: #60A5FA; }

        /* Tables */
        table {
            width: 100%;
            border-collapse: collapse;
            margin-top: 16px;
            background: var(--bg-card);
            border-radius: 12px;
            overflow: hidden;
            border: 1px solid var(--border);
        }
        th, td {
            padding: 14px 18px;
            text-align: left;
            border-bottom: 1px solid var(--border);
            font-size: 0.9rem;
        }
        th { background: #131D2D; color: var(--text-muted); font-weight: 600; }
        tr:last-child td { border-bottom: none; }

        /* Modals */
        .modal {
            display: none;
            position: fixed;
            top: 0; left: 0; right: 0; bottom: 0;
            background: rgba(0,0,0,0.7);
            align-items: center;
            justify-content: center;
            z-index: 100;
        }
        .modal.open { display: flex; }
        .modal-body {
            background: var(--bg-card);
            border: 1px solid var(--border);
            border-radius: 14px;
            width: 100%;
            max-width: 520px;
            padding: 24px;
        }
        .form-group { margin-bottom: 16px; }
        .form-group label { display: block; margin-bottom: 6px; font-size: 0.85rem; color: var(--text-muted); }
        .form-group input, .form-group select {
            width: 100%;
            background: #0F172A;
            border: 1px solid var(--border);
            color: white;
            padding: 10px;
            border-radius: 6px;
            font-size: 0.9rem;
        }
    </style>
</head>
<body>
    <!-- Sidebar Navigation -->
    <div class="sidebar">
        <div class="brand">
            ⚡ <span>Open HEMS</span>
        </div>
        <ul class="nav-menu">
            <li class="nav-item active" onclick="showTab('dashboard')">📊 Overzicht</li>
            <li class="nav-item" onclick="showTab('schedule')">⚡ 24h Planning</li>
            <li class="nav-item" onclick="showTab('devices')">🔌 Apparaten</li>
            <li class="nav-item" onclick="showTab('calibration')">🔬 Kalibratie & Offsets</li>
            <li class="nav-item" onclick="showTab('tariffs')">⚙️ Tarieven & Instellingen</li>
        </ul>
        <div class="version-tag">
            Versie: <strong>v0.2.1-dev.1</strong><br>
            Ingress Native Dashboard
        </div>
    </div>

    <!-- Main Content Area -->
    <div class="main">

        <!-- TAB 1: DASHBOARD -->
        <div id="tab-dashboard" class="tab-content active">
            <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:20px;">
                <h1>Systeemstatus & Vermogensstroom</h1>
                <span class="badge badge-success" id="dash-status">ONLINE · OPTIMALISATIE ACTIEF</span>
            </div>
            <div class="grid">
                <div class="card">
                    <h3>Warm Tapwater (350L SWW)</h3>
                    <div class="val" id="dash-dhw-temp">52.8 °C</div>
                    <p style="color:var(--text-muted); font-size:0.85rem; margin:0;">Geplande zonne-boost: <strong>13:00</strong> (60°C)</p>
                </div>
                <div class="card">
                    <h3>Smart Grid Status</h3>
                    <div class="val" id="dash-sg-mode" style="color:var(--primary);">SG2 (Normaal)</div>
                    <p style="color:var(--text-muted); font-size:0.85rem; margin:0;">Relais S10S/S11S direct in Daikin RAM</p>
                </div>
                <div class="card">
                    <h3>Actuele Dynamische Prijs</h3>
                    <div class="val" id="dash-price">€0.26 / kWh</div>
                    <p style="color:var(--text-muted); font-size:0.85rem; margin:0;">Powerpeers · 0.0121 markup incl. BTW</p>
                </div>
                <div class="card">
                    <h3>Gebouwisolatie (UA)</h3>
                    <div class="val" id="dash-ua">8.95 kW/K</div>
                    <p style="color:var(--text-muted); font-size:0.85rem; margin:0;">Zomerpauze actief (stookvraag: 0 kW)</p>
                </div>
            </div>

            <div class="card">
                <h3>Snelle Acties</h3>
                <div style="display:flex; gap:12px; margin-top:10px;">
                    <button class="btn" onclick="recalculateSchedule()">⚡ Nu Herberekenen</button>
                    <button class="btn btn-secondary" onclick="showTab('schedule')">Bekijk 24h Waterval</button>
                </div>
            </div>
        </div>

        <!-- TAB 2: SCHEDULE -->
        <div id="tab-schedule" class="tab-content">
            <div style="display:flex; justify-content:space-between; align-items:center;">
                <h1>24-Uurs Vermogenswaterval & Planning</h1>
                <button class="btn" onclick="recalculateSchedule()">🔄 Verversen</button>
            </div>
            <table>
                <thead>
                    <tr>
                        <th>Tijdslot</th>
                        <th>Stroomprijs</th>
                        <th>Verwachte Zonne-opwek</th>
                        <th>Smart Grid Modus</th>
                        <th>Toewijzing</th>
                    </tr>
                </thead>
                <tbody id="schedule-tbody">
                    <!-- Loaded via JS -->
                </tbody>
            </table>
        </div>

        <!-- TAB 3: DEVICES (CRUD) -->
        <div id="tab-devices" class="tab-content">
            <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:20px;">
                <h1>Apparatenbeheer (Devices CRUD)</h1>
                <button class="btn" onclick="openDeviceModal()">+ Nieuw Apparaat</button>
            </div>
            <div id="devices-list" class="grid">
                <!-- Device cards loaded via JS -->
            </div>
        </div>

        <!-- TAB 4: CALIBRATION & OFFSETS -->
        <div id="tab-calibration" class="tab-content">
            <h1>Zelflerende Feedback & Correcties</h1>
            <p style="color:var(--text-muted);">Uurlijkse zonnehoek- en schaduwmatrix K(h) en data-kwaliteitsmaskers.</p>

            <div class="grid">
                <div class="card">
                    <h3>Gebouwschil UA_base</h3>
                    <div class="val" id="calib-ua">8.95 kW/K</div>
                    <button class="btn btn-secondary" style="margin-top:10px;" onclick="editUa()">Aanpassen</button>
                </div>
                <div class="card">
                    <h3>350L Vat Stilstandsverlies</h3>
                    <div class="val" id="calib-dhw-loss">1.95 kWh/dag</div>
                    <button class="btn btn-secondary" style="margin-top:10px;" onclick="editDhwLoss()">Aanpassen</button>
                </div>
            </div>

            <h3>Data Uitsluitingsvensters (Sensor Downtime)</h3>
            <button class="btn" style="margin-bottom:14px;" onclick="openExclusionModal()">+ Uitsluitingsvenster Toevoegen</button>
            <table>
                <thead>
                    <tr>
                        <th>Sensor</th>
                        <th>Startdatum</th>
                        <th>Einddatum</th>
                        <th>Reden</th>
                        <th>Actie</th>
                    </tr>
                </thead>
                <tbody id="exclusion-tbody">
                    <!-- Loaded via JS -->
                </tbody>
            </table>
        </div>

        <!-- TAB 5: TARIFFS & SETTINGS -->
        <div id="tab-tariffs" class="tab-content">
            <h1>Tarieven & Veiligheidsinstellingen</h1>
            <div class="card" style="max-width:600px;">
                <form id="tariff-form" onsubmit="saveTariffs(event)">
                    <div class="form-group">
                        <label>Leverancier</label>
                        <input type="text" id="t-provider" value="Powerpeers (EnergyZero API)" disabled>
                    </div>
                    <div class="form-group">
                        <label>Contract Ingangsdatum</label>
                        <input type="date" id="t-start-date" value="2026-09-25">
                    </div>
                    <div class="form-group">
                        <label>Import Opslag (€/kWh incl. BTW)</label>
                        <input type="number" step="0.0001" id="t-import-markup" value="0.0121">
                    </div>
                    <div class="form-group">
                        <label>Export Opslag (€/kWh incl. BTW)</label>
                        <input type="number" step="0.0001" id="t-export-markup" value="0.0121">
                    </div>
                    <div class="form-group">
                        <label>Energiebelasting Elektriciteit (€/kWh incl. BTW)</label>
                        <input type="number" step="0.00001" id="t-tax" value="0.11085">
                    </div>
                    <div class="form-group">
                        <label>Vastrecht (€/maand)</label>
                        <input type="number" step="0.01" id="t-fixed" value="6.25">
                    </div>
                    <button type="submit" class="btn">Opslaan</button>
                </form>
            </div>
        </div>

    </div>

    <!-- MODAL: ADD / EDIT DEVICE -->
    <div id="device-modal" class="modal">
        <div class="modal-body">
            <h2 id="device-modal-title" style="margin-top:0;">Nieuw Apparaat Toevoegen</h2>
            <form id="device-form" onsubmit="saveDevice(event)">
                <input type="hidden" id="dev-id">
                <div class="form-group">
                    <label>Apparaatnaam</label>
                    <input type="text" id="dev-name" required placeholder="bijv. Thuisbatterij">
                </div>
                <div class="form-group">
                    <label>Apparaattype</label>
                    <select id="dev-type">
                        <option value="grid_meter">Netmeter (P1)</option>
                        <option value="solar_inverter">Zonnepanelen (Omvormer)</option>
                        <option value="heat_pump">Warmtepomp</option>
                        <option value="dhw_boiler">Warm Tapwater (SWW)</option>
                        <option value="home_battery">Thuisbatterij</option>
                        <option value="ev_charger">EV Laadpaal</option>
                    </select>
                </div>
                <div class="form-group">
                    <label>Protocol / Adapter</label>
                    <select id="dev-adapter">
                        <option value="p1_dsmr">P1 / DSMR</option>
                        <option value="sunspec_modbus">Modbus / SunSpec</option>
                        <option value="smart_grid_relay">Smart Grid Relais (S10S/S11S)</option>
                        <option value="temperature_sensor">Temperatuursensor</option>
                        <option value="deye_modbus_tcp">Deye Modbus TCP</option>
                        <option value="mqtt">MQTT</option>
                    </select>
                </div>
                <div style="display:flex; justify-content:flex-end; gap:10px; margin-top:20px;">
                    <button type="button" class="btn btn-secondary" onclick="closeModal('device-modal')">Annuleren</button>
                    <button type="submit" class="btn">Opslaan</button>
                </div>
            </form>
        </div>
    </div>

    <!-- MODAL: EXCLUSION WINDOW -->
    <div id="exclusion-modal" class="modal">
        <div class="modal-body">
            <h2 style="margin-top:0;">Uitsluitingsvenster Toevoegen</h2>
            <form onsubmit="saveExclusionWindow(event)">
                <div class="form-group">
                    <label>Sensor ID</label>
                    <input type="text" id="ex-sensor" value="sensor.warmtepomp_power" required>
                </div>
                <div class="form-group">
                    <label>Startdatum</label>
                    <input type="date" id="ex-start" required>
                </div>
                <div class="form-group">
                    <label>Einddatum</label>
                    <input type="date" id="ex-end" required>
                </div>
                <div class="form-group">
                    <label>Reden</label>
                    <input type="text" id="ex-reason" value="Modbus meter ontkoppeld" required>
                </div>
                <div style="display:flex; justify-content:flex-end; gap:10px; margin-top:20px;">
                    <button type="button" class="btn btn-secondary" onclick="closeModal('exclusion-modal')">Annuleren</button>
                    <button type="submit" class="btn">Toevoegen</button>
                </div>
            </form>
        </div>
    </div>

    <script>
        // Navigation Logic
        function showTab(tabName) {
            document.querySelectorAll('.tab-content').forEach(el => el.classList.remove('active'));
            document.querySelectorAll('.nav-item').forEach(el => el.classList.remove('active'));
            const target = document.getElementById('tab-' + tabName);
            if (target) target.classList.add('active');
            event.target.classList.add('active');

            if (tabName === 'devices') loadDevices();
            if (tabName === 'schedule') loadSchedule();
            if (tabName === 'calibration') loadCalibration();
            if (tabName === 'tariffs') loadTariffs();
        }

        // Modals
        function openDeviceModal(dev = null) {
            document.getElementById('device-form').reset();
            if (dev) {
                document.getElementById('device-modal-title').innerText = 'Apparaat Bewerken';
                document.getElementById('dev-id').value = dev.id;
                document.getElementById('dev-name').value = dev.name;
                document.getElementById('dev-type').value = dev.type;
                document.getElementById('dev-adapter').value = dev.adapter;
            } else {
                document.getElementById('device-modal-title').innerText = 'Nieuw Apparaat Toevoegen';
                document.getElementById('dev-id').value = '';
            }
            document.getElementById('device-modal').classList.add('open');
        }

        function openExclusionModal() {
            document.getElementById('exclusion-modal').classList.add('open');
        }

        function closeModal(id) {
            document.getElementById(id).classList.remove('open');
        }

        // API Calls: Devices CRUD
        async function loadDevices() {
            const res = await fetch('./api/devices');
            const data = await res.json();
            const container = document.getElementById('devices-list');
            container.innerHTML = '';
            (data.devices || []).forEach(d => {
                const card = document.createElement('div');
                card.className = 'card';
                card.innerHTML = `
                    <div style="display:flex; justify-content:space-between; align-items:start;">
                        <h2 style="margin:0; font-size:1.15rem;">${d.name}</h2>
                        <span class="badge badge-primary">${d.type}</span>
                    </div>
                    <p style="color:var(--text-muted); font-size:0.85rem; margin:8px 0 16px 0;">Adapter: <code>${d.adapter}</code></p>
                    <div style="margin-bottom:16px;">
                        ${(d.capabilities || []).map(c => `<span class="badge" style="background:#334155; margin-right:4px;">${c}</span>`).join('')}
                    </div>
                    <div style="display:flex; gap:8px; justify-content:flex-end;">
                        <button class="btn btn-secondary" style="padding:6px 12px; font-size:0.8rem;" onclick='openDeviceModal(${JSON.stringify(d)})'>Bewerken</button>
                        <button class="btn btn-danger" style="padding:6px 12px; font-size:0.8rem;" onclick="deleteDevice('${d.id}')">Verwijderen</button>
                    </div>
                `;
                container.appendChild(card);
            });
        }

        async function saveDevice(e) {
            e.preventDefault();
            const id = document.getElementById('dev-id').value;
            const payload = {
                name: document.getElementById('dev-name').value,
                type: document.getElementById('dev-type').value,
                adapter: document.getElementById('dev-adapter').value
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

        // API Calls: Schedule
        async function loadSchedule() {
            const res = await fetch('./api/schedule');
            const data = await res.json();
            const tbody = document.getElementById('schedule-tbody');
            tbody.innerHTML = '';
            (data.slots || []).forEach(s => {
                const tr = document.createElement('tr');
                tr.innerHTML = `
                    <td><strong>${String(s.hour).padStart(2, '0')}:00</strong></td>
                    <td>€${s.price_eur.toFixed(4)} / kWh</td>
                    <td>${s.solar_kw > 0 ? (s.solar_kw.toFixed(1) + ' kW') : '-'}</td>
                    <td><span class="badge ${s.sg_mode === 'SG4' ? 'badge-warning' : (s.sg_mode === 'SG1' ? 'badge-danger' : (s.sg_mode === 'SG3' ? 'badge-success' : 'badge-primary'))}">${s.sg_mode}</span></td>
                    <td>${s.sg_mode === 'SG4' ? '🔥 60°C Boiler Boost' : (s.sg_mode === 'SG1' ? '⛔ Spitsblokkade' : (s.sg_mode === 'SG3' ? '☀️ Zon-Buffering' : 'Weersafhankelijk'))}</td>
                `;
                tbody.appendChild(tr);
            });
        }

        async function recalculateSchedule() {
            const btn = event.target;
            btn.innerText = 'Bezig met herberekenen...';
            btn.disabled = true;
            try {
                const res = await fetch('./api/schedule/recalculate', { method: 'POST' });
                const d = await res.json();
                alert('Herberekening voltooid!');
                loadSchedule();
            } finally {
                btn.innerText = '⚡ Nu Herberekenen';
                btn.disabled = false;
            }
        }

        // API Calls: Calibration
        async function loadCalibration() {
            const res = await fetch('./api/calibration');
            const data = await res.json();
            if (data.parameters) {
                document.getElementById('calib-ua').innerText = (data.parameters.ua_base || 8.95) + ' kW/K';
                document.getElementById('calib-dhw-loss').innerText = (data.parameters.dhw_standby_loss_kwh || 1.95) + ' kWh/dag';
            }
            const tbody = document.getElementById('exclusion-tbody');
            tbody.innerHTML = '';
            (data.exclusion_windows || []).forEach((w, idx) => {
                const tr = document.createElement('tr');
                tr.innerHTML = `
                    <td><code>${w.sensor}</code></td>
                    <td>${w.start}</td>
                    <td>${w.end}</td>
                    <td>${w.reason}</td>
                    <td><button class="btn btn-danger" style="padding:4px 8px; font-size:0.75rem;" onclick="deleteExclusion(${idx})">Verwijderen</button></td>
                `;
                tbody.appendChild(tr);
            });
        }

        async function saveExclusionWindow(e) {
            e.preventDefault();
            const payload = {
                sensor: document.getElementById('ex-sensor').value,
                start: document.getElementById('ex-start').value,
                end: document.getElementById('ex-end').value,
                reason: document.getElementById('ex-reason').value
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

        // API Calls: Tariffs
        async function loadTariffs() {
            const res = await fetch('./api/tariffs');
            const t = await res.json();
            document.getElementById('t-start-date').value = t.contract_start_date || '2026-09-25';
            document.getElementById('t-import-markup').value = t.import_markup_eur_kwh || 0.0121;
            document.getElementById('t-export-markup').value = t.export_markup_eur_kwh || 0.0121;
            document.getElementById('t-tax').value = t.electricity_tax_eur_kwh || 0.11085;
            document.getElementById('t-fixed').value = t.fixed_monthly_fee_eur || 6.25;
        }

        async function saveTariffs(e) {
            e.preventDefault();
            const payload = {
                contract_start_date: document.getElementById('t-start-date').value,
                import_markup_eur_kwh: parseFloat(document.getElementById('t-import-markup').value),
                export_markup_eur_kwh: parseFloat(document.getElementById('t-export-markup').value),
                electricity_tax_eur_kwh: parseFloat(document.getElementById('t-tax').value),
                fixed_monthly_fee_eur: parseFloat(document.getElementById('t-fixed').value)
            };
            await fetch('./api/tariffs', { method: 'PUT', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload) });
            alert('Tarieven succesvol bijgewerkt!');
        }

        // Initial Load
        loadDevices();
    </script>
</body>
</html>"""
        self.wfile.write(html.encode("utf-8"))


def run_server(port=8099):
    server = HTTPServer(("0.0.0.0", port), HemsApiHandler)
    print(f"Open HEMS Management Console & REST API running on port {port}...")
    server.serve_forever()


def main():
    parser = argparse.ArgumentParser(description="Open HEMS Daemon")
    parser.add_argument("--config", default=str(CONFIG_FILE))
    parser.add_argument("--interval", type=int, default=15)
    parser.add_argument("--port", type=int, default=8099)
    args = parser.parse_args()

    cfg = load_json(Path(args.config) if args.config else CONFIG_FILE)
    ensure_default_devices(cfg)
    run_server(args.port)


if __name__ == "__main__":
    main()
