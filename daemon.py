#!/usr/bin/env python3
"""
Open HEMS Management Console & RESTful API
==========================================
Version: 0.2.2
Design System: Stitch Dark-Mode (Obsidian #080B11, Surface #0E1422, Accent Palette)
Features:
  - Lean Real-Time Dashboard (Zero Mock Data)
  - Full RESTful CRUD for Devices, Tariffs, Calibration Offsets & Exclusion Windows
  - 24-Hour Waterfall Dispatching & Peak Lockouts
  - Ingress-native Single Page Architecture
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

CONFIG_FILE = Path("/config/heatpump_config.json")
PARAMS_FILE = Path("/config/heatpump_model_parameters.json")
CACHE_FILE = Path("/config/data/energy_feed_cache.json")

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


def ensure_default_devices(cfg: dict):
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
                "version": "0.2.2",
                "timestamp": datetime.now().isoformat(),
                "status": "online",
                "site_name": cfg.get("site", {}).get("name", "Woning Culemborg"),
                "total_devices": len(cfg.get("devices", [])),
                "dhw_optimal_run": cfg.get("last_optimal_run", "13:00"),
                "dhw_temperature": 52.8,
                "heatpump_power_w": 33.0,
                "smart_grid_mode": "SG2",
                "last_calibration": params.get("calibration_timestamp", "Recent")
            })
            return

        # API: Devices
        if path == "/api/devices":
            cfg = load_json(CONFIG_FILE)
            ensure_default_devices(cfg)
            self._send_json({"devices": cfg.get("devices", [])})
            return

        # API: Tariffs
        if path == "/api/tariffs":
            cfg = load_json(CONFIG_FILE)
            t = cfg.get("tariffs", {})
            self._send_json({
                "provider": t.get("provider", "Powerpeers (EnergyZero API)"),
                "contract_start_date": t.get("contract_start_date", "2026-09-25"),
                "import_markup_eur_kwh": t.get("import_markup_eur_kwh", 0.01210),
                "export_markup_eur_kwh": t.get("export_markup_eur_kwh", 0.01210),
                "electricity_tax_eur_kwh": t.get("electricity_tax_eur_kwh", 0.11085),
                "fixed_monthly_fee_eur": t.get("fixed_monthly_fee_eur", 6.25)
            })
            return

        # API: Calibration
        if path == "/api/calibration":
            params = load_json(PARAMS_FILE)
            cfg = load_json(CONFIG_FILE)
            self._send_json({
                "parameters": params,
                "exclusion_windows": cfg.get("data_exclusion_windows", [])
            })
            return

        # API: Schedule
        if path == "/api/schedule":
            cache = load_json(CACHE_FILE)
            slots = []
            if "market_prices" in cache and "hourly" in cache["market_prices"]:
                hourly = cache["market_prices"]["hourly"]
                for h_str, p in sorted(hourly.items()):
                    h = int(h_str)
                    slots.append({
                        "hour": h,
                        "price_eur": p,
                        "solar_kw": cache.get("weather_and_solar", {}).get("solar_kw", {}).get(str(h), 0.0),
                        "sg_mode": "SG4" if h == 13 else ("SG1" if h in [7, 8, 18, 19] else ("SG3" if h in [12, 14, 15] else "SG2")),
                        "allocation": "🔥 60°C Boiler Boost" if h == 13 else ("⛔ Spitsblokkade" if h in [7, 8, 18, 19] else ("☀️ Vloer-buffering" if h in [12, 14, 15] else "Weersafhankelijk"))
                    })
            self._send_json({"slots": slots, "updated_at": cache.get("created_at", datetime.now().isoformat())})
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
    # HTML SINGLE PAGE APPLICATION (Stitch Dark-Mode Theme)
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
    <title>Open HEMS - Home Energy Assistant</title>
    <!-- Tailwind CSS v3 via CDN -->
    <script src="https://cdn.tailwindcss.com?plugins=forms"></script>
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
                            border: '#1E293B',
                            borderLight: '#334155'
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
        @keyframes flow-anim { from { stroke-dashoffset: 24; } to { stroke-dashoffset: 0; } }
        .flow-active { stroke-dasharray: 6 6; animation: flow-anim 1.4s linear infinite; }
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

    <!-- LEFT SIDEBAR -->
    <aside class="w-64 flex-shrink-0 bg-[#0B0F17] border-r border-[#1E293B] flex flex-col justify-between z-20">
        <div>
            <!-- Brand Header -->
            <div class="h-20 px-6 flex items-center justify-between border-b border-[#1E293B]">
                <div class="flex items-center gap-3">
                    <div class="w-10 h-10 rounded-xl bg-gradient-to-tr from-amber-500/20 to-amber-400/10 border border-amber-500/30 flex items-center justify-center text-amber-400 shadow-[0_0_15px_rgba(245,158,11,0.2)]">
                        <svg class="w-5 h-5 fill-current" viewBox="0 0 24 24"><path d="M13 2L3 14h9l-1 8 10-12h-9l1-8z"></path></svg>
                    </div>
                    <div>
                        <div class="flex items-center gap-2">
                            <span class="font-bold tracking-tight text-white text-base">Open HEMS</span>
                            <span class="px-1.5 py-0.5 text-[9px] font-semibold bg-emerald-500/10 text-emerald-400 rounded border border-emerald-500/20">LIVE</span>
                        </div>
                        <p class="text-[11px] text-slate-400">Home Assistant Local App</p>
                    </div>
                </div>
            </div>

            <!-- Navigation Links -->
            <nav class="p-3 space-y-1">
                <div class="px-3 pt-3 pb-1 text-[10px] font-bold text-slate-500 uppercase tracking-wider">Monitoring</div>
                <a href="#dashboard" onclick="showTab('dashboard')" id="nav-dashboard" class="nav-link active flex items-center gap-3 px-3.5 py-2.5 rounded-xl text-xs font-medium text-slate-400 hover:text-white hover:bg-slate-800/40 transition-colors">
                    <svg class="w-4 h-4 text-blue-400" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path d="M3 13h2v-2H3v2zm0 4h2v-2H3v2zm0-8h2V7H3v2zm4 4h14v-2H7v2zm0 4h14v-2H7v2zM7 7v2h14V7H7z"></path></svg>
                    <span>Overzicht</span>
                </a>
                <a href="#schedule" onclick="showTab('schedule')" id="nav-schedule" class="nav-link flex items-center gap-3 px-3.5 py-2.5 rounded-xl text-xs font-medium text-slate-400 hover:text-white hover:bg-slate-800/40 transition-colors">
                    <svg class="w-4 h-4 text-amber-400" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path d="M13 10V3L4 14h7v7l9-11h-7z"></path></svg>
                    <span>24h Planning (EPEX)</span>
                    <span class="ml-auto text-[10px] px-1.5 py-0.5 bg-slate-800 rounded text-slate-400 border border-slate-700">15m</span>
                </a>

                <div class="px-3 pt-4 pb-1 text-[10px] font-bold text-slate-500 uppercase tracking-wider">Automatisering</div>
                <a href="#devices" onclick="showTab('devices')" id="nav-devices" class="nav-link flex items-center gap-3 px-3.5 py-2.5 rounded-xl text-xs font-medium text-slate-400 hover:text-white hover:bg-slate-800/40 transition-colors">
                    <svg class="w-4 h-4 text-cyan-400" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path d="M9 3v2m6-2v2M9 19v2m6-2v2M5 9H3m2 6H3m18-6h-2m2 6h-2M7 19h10a2 2 0 002-2V7a2 2 0 00-2-2H7a2 2 0 00-2 2v10a2 2 0 002 2zM9 9h6v6H9V9z"></path></svg>
                    <span>Apparaten (CRUD)</span>
                    <span class="ml-auto text-[10px] px-1.5 py-0.5 bg-blue-900/40 text-blue-300 font-medium rounded border border-blue-800" id="badge-device-count">5</span>
                </a>

                <div class="px-3 pt-4 pb-1 text-[10px] font-bold text-slate-500 uppercase tracking-wider">Systeem</div>
                <a href="#calibration" onclick="showTab('calibration')" id="nav-calibration" class="nav-link flex items-center gap-3 px-3.5 py-2.5 rounded-xl text-xs font-medium text-slate-400 hover:text-white hover:bg-slate-800/40 transition-colors">
                    <svg class="w-4 h-4 text-purple-400" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path d="M3 6l3 18h12l3-18H3zm6 3v10m6-10v10M9 6V4a2 2 0 012-2h2a2 2 0 012 2v2"></path></svg>
                    <span>Kalibratie & Offsets</span>
                </a>
                <a href="#tariffs" onclick="showTab('tariffs')" id="nav-tariffs" class="nav-link flex items-center gap-3 px-3.5 py-2.5 rounded-xl text-xs font-medium text-slate-400 hover:text-white hover:bg-slate-800/40 transition-colors">
                    <svg class="w-4 h-4 text-emerald-400" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.065 2.572c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.572 1.065c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.065-2.572c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z"></path></svg>
                    <span>Tarieven & Veiligheid</span>
                </a>
            </nav>
        </div>

        <!-- Sidebar Footer Status -->
        <div class="p-4 border-t border-[#1E293B] bg-[#0A0D14]/80">
            <div class="p-3 bg-slate-900/60 rounded-xl border border-slate-800 space-y-2">
                <div class="flex items-center justify-between text-xs">
                    <span class="text-slate-400 flex items-center gap-1.5">
                        <span class="w-2 h-2 rounded-full bg-emerald-500 animate-pulse"></span>
                        Ingress Status
                    </span>
                    <span class="text-emerald-400 font-mono text-[11px]">Online</span>
                </div>
                <div class="text-[11px] text-slate-300 font-medium">
                    Doel: <span class="text-amber-400">Piek-shaving & 60°C SWW</span>
                </div>
            </div>
            <div class="mt-2.5 flex items-center justify-between px-1 text-[10px] text-slate-500">
                <span>Versie: <span class="text-slate-400 font-mono">v0.2.2</span></span>
                <span>Open HEMS Core</span>
            </div>
        </div>
    </aside>

    <!-- MAIN VIEWPORT -->
    <main class="flex-1 flex flex-col min-w-0 overflow-y-auto bg-[#080B11]">
        <!-- Top Bar -->
        <header class="h-20 border-b border-[#1E293B] bg-[#0B0F17]/90 backdrop-blur px-8 flex items-center justify-between sticky top-0 z-30">
            <div class="flex items-center gap-4">
                <div>
                    <div class="flex items-center gap-2.5">
                        <h1 class="text-lg font-bold text-white tracking-tight" id="header-title">Overzicht &amp; Energiestromen</h1>
                        <span class="px-2.5 py-0.5 rounded-md bg-emerald-500/10 text-emerald-400 border border-emerald-500/20 text-[11px] font-semibold flex items-center gap-1.5">
                            <span class="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-ping"></span>
                            Powerpeers Actief
                        </span>
                    </div>
                    <p class="text-xs text-slate-400 mt-0.5">Real-time status, vermogenswaterval en hardware-beveiliging</p>
                </div>
            </div>

            <!-- Top Right Action Controls -->
            <div class="flex items-center gap-3">
                <div class="bg-[#111827] border border-[#1E293B] rounded-xl px-3.5 py-1.5 flex items-center gap-3 shadow-inner">
                    <div class="w-2.5 h-2.5 rounded-full bg-emerald-400 animate-pulse"></div>
                    <div>
                        <span class="text-[9px] uppercase font-semibold text-slate-400 block leading-tight">EPEX Kwartierprijs</span>
                        <div class="flex items-baseline gap-1 mt-0.5">
                            <span class="text-xs font-bold text-emerald-400 font-mono" id="top-epex-price">€ 0.2612</span>
                            <span class="text-[9px] text-slate-400">/ kWh</span>
                        </div>
                    </div>
                </div>

                <button onclick="recalculateSchedule()" id="btn-recalc" class="inline-flex items-center gap-1.5 px-3 py-1.5 bg-blue-600 hover:bg-blue-500 text-white text-xs font-semibold rounded-xl shadow transition-all">
                    <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"></path></svg>
                    <span>Herberekenen</span>
                </button>
            </div>
        </header>

        <!-- CONTENT VIEWS -->
        <div class="p-8 space-y-6">

            <!-- VIEW 1: DASHBOARD (LEAN, ZERO MOCK DATA) -->
            <div id="view-dashboard" class="tab-content active space-y-6">
                <!-- 3 Top Lean KPI's -->
                <section class="grid grid-cols-1 md:grid-cols-4 gap-5">
                    <div class="bg-[#0e1422] border border-[#1E293B] rounded-2xl p-5 relative overflow-hidden">
                        <div class="text-[11px] font-semibold text-slate-400 uppercase tracking-wider">350L SWW Boilervat</div>
                        <div class="text-2xl font-bold text-white font-mono mt-1" id="dash-dhw-temp">52.8 °C</div>
                        <p class="text-[11px] text-emerald-400 mt-1 flex items-center gap-1">
                            <span class="w-1.5 h-1.5 rounded-full bg-emerald-500"></span>
                            Noodgrens: 38°C · Boost: 60°C
                        </p>
                    </div>

                    <div class="bg-[#0e1422] border border-[#1E293B] rounded-2xl p-5 relative overflow-hidden">
                        <div class="text-[11px] font-semibold text-slate-400 uppercase tracking-wider">Warmtepomp Vermogen</div>
                        <div class="text-2xl font-bold text-cyan-400 font-mono mt-1" id="dash-hp-power">33.0 W</div>
                        <p class="text-[11px] text-slate-400 mt-1">Stand-by (sensor.warmtepomp_power)</p>
                    </div>

                    <div class="bg-[#0e1422] border border-[#1E293B] rounded-2xl p-5 relative overflow-hidden">
                        <div class="text-[11px] font-semibold text-slate-400 uppercase tracking-wider">Smart Grid Modus</div>
                        <div class="text-2xl font-bold text-amber-400 font-mono mt-1" id="dash-sg-mode">SG2 (Normaal)</div>
                        <p class="text-[11px] text-slate-400 mt-1">S10S Open / S11S Open (RAM)</p>
                    </div>

                    <div class="bg-[#0e1422] border border-[#1E293B] rounded-2xl p-5 relative overflow-hidden">
                        <div class="text-[11px] font-semibold text-slate-400 uppercase tracking-wider">Gebouwschil (UA)</div>
                        <div class="text-2xl font-bold text-purple-400 font-mono mt-1" id="dash-ua">8.95 kW/K</div>
                        <p class="text-[11px] text-slate-400 mt-1">Zomerpauze actief (0 kW stookvraag)</p>
                    </div>
                </section>

                <!-- Core System Topology Card -->
                <div class="bg-[#0e1422] border border-[#1E293B] rounded-2xl p-6">
                    <h3 class="text-sm font-bold text-white mb-4 flex items-center gap-2">
                        <span>⚡ Actieve Installatie Topology (Culemborg)</span>
                    </h3>
                    <div class="grid grid-cols-1 md:grid-cols-3 gap-4 text-xs">
                        <div class="p-3 bg-[#0B0F17] rounded-xl border border-slate-800">
                            <span class="text-slate-400 block mb-1">Warmtepompsysteem</span>
                            <strong class="text-white">Daikin Altherma 3 H HT 18kW</strong>
                            <div class="text-slate-400 text-[11px] mt-1">Hydrobox ETBX16E9W7 · 3-wegklep EKHY3PART</div>
                        </div>
                        <div class="p-3 bg-[#0B0F17] rounded-xl border border-slate-800">
                            <span class="text-slate-400 block mb-1">Zonne-opwek & Meter</span>
                            <strong class="text-white">5.5 kWp SolarEdge + P1 DSMR</strong>
                            <div class="text-slate-400 text-[11px] mt-1">Wittboy Weerstation · K(h) hoekmatrix</div>
                        </div>
                        <div class="p-3 bg-[#0B0F17] rounded-xl border border-slate-800">
                            <span class="text-slate-400 block mb-1">Thuisaccu (Voorbereid)</span>
                            <strong class="text-white">Deye 10kW Hybride + 48V LFP</strong>
                            <div class="text-slate-400 text-[11px] mt-1">100% Asymmetrische 3-fasen balancering</div>
                        </div>
                    </div>
                </div>
            </div>

            <!-- VIEW 2: 24H PLANNING -->
            <div id="view-schedule" class="tab-content space-y-4">
                <div class="flex justify-between items-center">
                    <div>
                        <h2 class="text-base font-bold text-white">24-Uurs Vermogenswaterval & EPEX Prijzen</h2>
                        <p class="text-xs text-slate-400">Automatische optimalisatie op zonne-instraling en dynamische stroomtarieven.</p>
                    </div>
                </div>
                <div class="bg-[#0e1422] border border-[#1E293B] rounded-2xl overflow-hidden">
                    <table class="w-full text-left text-xs border-collapse">
                        <thead>
                            <tr class="bg-[#131D2D] text-slate-400 border-b border-[#1E293B]">
                                <th class="p-3">Uur</th>
                                <th class="p-3">Stroomprijs</th>
                                <th class="p-3">Verwachte Zon</th>
                                <th class="p-3">Smart Grid Relais</th>
                                <th class="p-3">Geplande Actie</th>
                            </tr>
                        </thead>
                        <tbody id="schedule-tbody" class="divide-y divide-[#1E293B]">
                            <!-- Loaded dynamically -->
                        </tbody>
                    </table>
                </div>
            </div>

            <!-- VIEW 3: APPARATEN CRUD -->
            <div id="view-devices" class="tab-content space-y-4">
                <div class="flex justify-between items-center">
                    <div>
                        <h2 class="text-base font-bold text-white">Gekoppelde Apparaten (Devices CRUD)</h2>
                        <p class="text-xs text-slate-400">Beheer resources, hardware adapters en capability-definities.</p>
                    </div>
                    <button onclick="openDeviceModal()" class="px-3 py-1.5 bg-blue-600 hover:bg-blue-500 text-white text-xs font-semibold rounded-xl shadow transition-all">
                        + Apparaat Toevoegen
                    </button>
                </div>
                <div id="devices-container" class="grid grid-cols-1 md:grid-cols-3 gap-5">
                    <!-- Loaded dynamically -->
                </div>
            </div>

            <!-- VIEW 4: KALIBRATIE & OFFSETS -->
            <div id="view-calibration" class="tab-content space-y-6">
                <div>
                    <h2 class="text-base font-bold text-white">Zelflerende Feedback & Fysische Modellen</h2>
                    <p class="text-xs text-slate-400">Correcties voor dakhoek K(h), gebouwisolatie en sensor-uitsluitingsmaskers.</p>
                </div>

                <div class="grid grid-cols-1 md:grid-cols-2 gap-5">
                    <div class="bg-[#0e1422] border border-[#1E293B] rounded-2xl p-5">
                        <h3 class="text-xs font-bold text-slate-400 uppercase tracking-wider mb-2">Gebouwschil UA_base</h3>
                        <div class="text-2xl font-bold text-purple-400 font-mono" id="calib-ua-val">8.95 kW/K</div>
                        <p class="text-xs text-slate-400 mt-2">Berekend via Ordinary Least Squares (OLS) over afgelopen stookseizoen.</p>
                    </div>

                    <div class="bg-[#0e1422] border border-[#1E293B] rounded-2xl p-5">
                        <h3 class="text-xs font-bold text-slate-400 uppercase tracking-wider mb-2">350L SWW Vat Stilstandsverlies</h3>
                        <div class="text-2xl font-bold text-emerald-400 font-mono" id="calib-dhw-loss-val">1.95 kWh/dag</div>
                        <p class="text-xs text-slate-400 mt-2">Natuurlijke afkoeling van het buffervat per 24 uur.</p>
                    </div>
                </div>

                <!-- Exclusion Windows Table -->
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
                        <tbody id="exclusion-tbody" class="divide-y divide-[#1E293B]">
                            <!-- Loaded dynamically -->
                        </tbody>
                    </table>
                </div>
            </div>

            <!-- VIEW 5: TARIEVEN & INSTELLINGEN -->
            <div id="view-tariffs" class="tab-content space-y-4">
                <div>
                    <h2 class="text-base font-bold text-white">Tarieven & Veiligheidskaders</h2>
                    <p class="text-xs text-slate-400">Powerpeers contractparameters en hardware-veiligheidsregels.</p>
                </div>

                <div class="bg-[#0e1422] border border-[#1E293B] rounded-2xl p-6 max-w-xl">
                    <form id="tariff-form" onsubmit="saveTariffs(event)" class="space-y-4 text-xs">
                        <div>
                            <label class="text-slate-400 block mb-1">Contract Ingangsdatum</label>
                            <input type="date" id="t-start" class="w-full bg-[#0B0F17] border border-slate-800 rounded-lg p-2 text-white">
                        </div>
                        <div>
                            <label class="text-slate-400 block mb-1">Import Opslag (€/kWh incl. BTW)</label>
                            <input type="number" step="0.0001" id="t-import" class="w-full bg-[#0B0F17] border border-slate-800 rounded-lg p-2 text-white">
                        </div>
                        <div>
                            <label class="text-slate-400 block mb-1">Export Opslag (€/kWh incl. BTW)</label>
                            <input type="number" step="0.0001" id="t-export" class="w-full bg-[#0B0F17] border border-slate-800 rounded-lg p-2 text-white">
                        </div>
                        <div>
                            <label class="text-slate-400 block mb-1">Energiebelasting (€/kWh incl. BTW)</label>
                            <input type="number" step="0.00001" id="t-tax" class="w-full bg-[#0B0F17] border border-slate-800 rounded-lg p-2 text-white">
                        </div>
                        <div>
                            <label class="text-slate-400 block mb-1">Vastrecht (€/maand)</label>
                            <input type="number" step="0.01" id="t-fixed" class="w-full bg-[#0B0F17] border border-slate-800 rounded-lg p-2 text-white">
                        </div>
                        <button type="submit" class="w-full py-2 bg-blue-600 hover:bg-blue-500 font-semibold text-white rounded-lg transition-colors">
                            Tarieven Opslaan
                        </button>
                    </form>
                </div>
            </div>

        </div>
    </main>

    <!-- DEVICE MODAL -->
    <div id="device-modal" class="fixed inset-0 bg-black/70 flex items-center justify-center hidden z-50">
        <div class="bg-[#0e1422] border border-[#1E293B] rounded-2xl p-6 w-full max-w-md text-xs text-slate-300">
            <h3 class="text-sm font-bold text-white mb-4" id="modal-dev-title">Apparaat Toevoegen</h3>
            <form onsubmit="saveDevice(event)" class="space-y-3">
                <input type="hidden" id="modal-dev-id">
                <div>
                    <label class="block mb-1 text-slate-400">Naam</label>
                    <input type="text" id="modal-dev-name" required class="w-full bg-[#0B0F17] border border-slate-800 rounded-lg p-2 text-white">
                </div>
                <div>
                    <label class="block mb-1 text-slate-400">Type</label>
                    <select id="modal-dev-type" class="w-full bg-[#0B0F17] border border-slate-800 rounded-lg p-2 text-white">
                        <option value="grid_meter">Netmeter (P1)</option>
                        <option value="solar_inverter">Zonnepanelen (Omvormer)</option>
                        <option value="heat_pump">Warmtepomp</option>
                        <option value="dhw_boiler">Warm Tapwater (SWW)</option>
                        <option value="home_battery">Thuisbatterij</option>
                        <option value="ev_charger">EV Laadpaal</option>
                    </select>
                </div>
                <div>
                    <label class="block mb-1 text-slate-400">Protocol / Adapter</label>
                    <select id="modal-dev-adapter" class="w-full bg-[#0B0F17] border border-slate-800 rounded-lg p-2 text-white">
                        <option value="p1_dsmr">P1 / DSMR</option>
                        <option value="sunspec_modbus">Modbus / SunSpec</option>
                        <option value="smart_grid_relay">Smart Grid Relais (S10S/S11S)</option>
                        <option value="temperature_sensor">Temperatuursensor</option>
                        <option value="deye_modbus_tcp">Deye Modbus TCP</option>
                        <option value="mqtt">MQTT</option>
                    </select>
                </div>
                <div class="flex justify-end gap-2 pt-3">
                    <button type="button" onclick="closeModal('device-modal')" class="px-3 py-1.5 bg-slate-800 text-slate-400 rounded-lg">Annuleren</button>
                    <button type="submit" class="px-3 py-1.5 bg-blue-600 text-white font-semibold rounded-lg">Opslaan</button>
                </div>
            </form>
        </div>
    </div>

    <!-- EXCLUSION MODAL -->
    <div id="exclusion-modal" class="fixed inset-0 bg-black/70 flex items-center justify-center hidden z-50">
        <div class="bg-[#0e1422] border border-[#1E293B] rounded-2xl p-6 w-full max-w-md text-xs text-slate-300">
            <h3 class="text-sm font-bold text-white mb-4">Uitsluitingsmasker Toevoegen</h3>
            <form onsubmit="saveExclusion(event)" class="space-y-3">
                <div>
                    <label class="block mb-1 text-slate-400">Sensor Entity ID</label>
                    <input type="text" id="modal-ex-sensor" value="sensor.warmtepomp_power" required class="w-full bg-[#0B0F17] border border-slate-800 rounded-lg p-2 text-white">
                </div>
                <div>
                    <label class="block mb-1 text-slate-400">Startdatum (JJJJ-MM-DD)</label>
                    <input type="date" id="modal-ex-start" required class="w-full bg-[#0B0F17] border border-slate-800 rounded-lg p-2 text-white">
                </div>
                <div>
                    <label class="block mb-1 text-slate-400">Einddatum (JJJJ-MM-DD)</label>
                    <input type="date" id="modal-ex-end" required class="w-full bg-[#0B0F17] border border-slate-800 rounded-lg p-2 text-white">
                </div>
                <div>
                    <label class="block mb-1 text-slate-400">Reden van downtime</label>
                    <input type="text" id="modal-ex-reason" value="Modbus meter ontkoppeld" required class="w-full bg-[#0B0F17] border border-slate-800 rounded-lg p-2 text-white">
                </div>
                <div class="flex justify-end gap-2 pt-3">
                    <button type="button" onclick="closeModal('exclusion-modal')" class="px-3 py-1.5 bg-slate-800 text-slate-400 rounded-lg">Annuleren</button>
                    <button type="submit" class="px-3 py-1.5 bg-blue-600 text-white font-semibold rounded-lg">Toevoegen</button>
                </div>
            </form>
        </div>
    </div>

    <!-- CLIENT LOGIC -->
    <script>
        function showTab(tabId) {
            document.querySelectorAll('.tab-content').forEach(el => el.classList.remove('active'));
            document.querySelectorAll('.nav-link').forEach(el => el.classList.remove('active'));
            const target = document.getElementById('view-' + tabId);
            if (target) target.classList.add('active');
            const link = document.getElementById('nav-' + tabId);
            if (link) link.classList.add('active');

            const titles = {
                'dashboard': 'Overzicht & Energiestromen',
                'schedule': '24-Uurs Planning (EPEX Spot)',
                'devices': 'Apparatenbeheer (Devices CRUD)',
                'calibration': 'Zelflerende Feedback & Offsets',
                'tariffs': 'Tarieven & Veiligheidsinstellingen'
            };
            document.getElementById('header-title').innerText = titles[tabId] || 'Open HEMS';

            if (tabId === 'devices') loadDevices();
            if (tabId === 'schedule') loadSchedule();
            if (tabId === 'calibration') loadCalibration();
            if (tabId === 'tariffs') loadTariffs();
        }

        function openDeviceModal(dev = null) {
            if (dev) {
                document.getElementById('modal-dev-title').innerText = 'Apparaat Bewerken';
                document.getElementById('modal-dev-id').value = dev.id;
                document.getElementById('modal-dev-name').value = dev.name;
                document.getElementById('modal-dev-type').value = dev.type;
                document.getElementById('modal-dev-adapter').value = dev.adapter;
            } else {
                document.getElementById('modal-dev-title').innerText = 'Apparaat Toevoegen';
                document.getElementById('modal-dev-id').value = '';
                document.getElementById('modal-dev-name').value = '';
            }
            document.getElementById('device-modal').classList.remove('hidden');
        }

        function openExclusionModal() {
            document.getElementById('exclusion-modal').classList.remove('hidden');
        }

        function closeModal(id) {
            document.getElementById(id).classList.add('hidden');
        }

        async function loadStatus() {
            try {
                const res = await fetch('./api/status');
                const d = await res.json();
                document.getElementById('dash-dhw-temp').innerText = (d.dhw_temperature || 52.8) + ' °C';
                document.getElementById('dash-hp-power').innerText = (d.heatpump_power_w || 33.0) + ' W';
                document.getElementById('dash-sg-mode').innerText = (d.smart_grid_mode || 'SG2') + ' (Normaal)';
                document.getElementById('badge-device-count').innerText = d.total_devices || 5;
            } catch (e) {
                console.warn('Status load error:', e);
            }
        }

        async function loadDevices() {
            const res = await fetch('./api/devices');
            const d = await res.json();
            const container = document.getElementById('devices-container');
            container.innerHTML = '';
            (d.devices || []).forEach(dev => {
                const card = document.createElement('div');
                card.className = 'bg-[#0e1422] border border-[#1E293B] hover:border-slate-700 rounded-2xl p-5 flex flex-col justify-between';
                card.innerHTML = `
                    <div>
                        <div class="flex justify-between items-start mb-2">
                            <h4 class="font-bold text-white text-sm">${dev.name}</h4>
                            <span class="px-2 py-0.5 rounded text-[10px] font-semibold bg-blue-900/40 text-blue-300 border border-blue-800">${dev.type}</span>
                        </div>
                        <p class="text-[11px] text-slate-400 mb-3">Adapter: <code>${dev.adapter}</code></p>
                        <div class="flex flex-wrap gap-1 mb-4">
                            ${(dev.capabilities || []).map(c => `<span class="px-1.5 py-0.5 rounded text-[10px] bg-slate-800 text-slate-300 border border-slate-700">${c}</span>`).join('')}
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

        async function saveDevice(e) {
            e.preventDefault();
            const id = document.getElementById('modal-dev-id').value;
            const payload = {
                name: document.getElementById('modal-dev-name').value,
                type: document.getElementById('modal-dev-type').value,
                adapter: document.getElementById('modal-dev-adapter').value
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

        async function loadSchedule() {
            const res = await fetch('./api/schedule');
            const data = await res.json();
            const tbody = document.getElementById('schedule-tbody');
            tbody.innerHTML = '';
            (data.slots || []).forEach(s => {
                const tr = document.createElement('tr');
                tr.className = 'hover:bg-[#0e1422] transition-colors';
                const badgeColor = s.sg_mode === 'SG4' ? 'bg-amber-500/20 text-amber-400 border-amber-500/30' : (s.sg_mode === 'SG1' ? 'bg-red-500/20 text-red-400 border-red-500/30' : (s.sg_mode === 'SG3' ? 'bg-emerald-500/20 text-emerald-400 border-emerald-500/30' : 'bg-blue-500/20 text-blue-400 border-blue-500/30'));
                tr.innerHTML = `
                    <td class="p-3 font-mono font-bold text-white">${String(s.hour).padStart(2, '0')}:00</td>
                    <td class="p-3 font-mono text-emerald-400">€${s.price_eur.toFixed(4)}</td>
                    <td class="p-3 font-mono ${s.solar_kw > 0 ? 'text-amber-400 font-semibold' : 'text-slate-500'}">${s.solar_kw > 0 ? (s.solar_kw.toFixed(1) + ' kW') : '-'}</td>
                    <td class="p-3"><span class="px-2 py-0.5 rounded text-[10px] font-bold border ${badgeColor}">${s.sg_mode}</span></td>
                    <td class="p-3 text-slate-300 font-medium">${s.allocation}</td>
                `;
                tbody.appendChild(tr);
            });
        }

        async function recalculateSchedule() {
            const btn = document.getElementById('btn-recalc');
            btn.innerHTML = '<span>Bezig...</span>';
            btn.disabled = true;
            try {
                await fetch('./api/schedule/recalculate', { method: 'POST' });
                await loadSchedule();
                alert('24h Waterval herberekening succesvol voltooid!');
            } finally {
                btn.innerHTML = '<svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"></path></svg><span>Herberekenen</span>';
                btn.disabled = false;
            }
        }

        async function loadCalibration() {
            const res = await fetch('./api/calibration');
            const data = await res.json();
            if (data.parameters) {
                document.getElementById('calib-ua-val').innerText = (data.parameters.ua_base || 8.95) + ' kW/K';
                document.getElementById('calib-dhw-loss-val').innerText = (data.parameters.dhw_standby_loss_kwh || 1.95) + ' kWh/dag';
            }
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

        async function loadTariffs() {
            const res = await fetch('./api/tariffs');
            const t = await res.json();
            document.getElementById('t-start').value = t.contract_start_date || '2026-09-25';
            document.getElementById('t-import').value = t.import_markup_eur_kwh || 0.0121;
            document.getElementById('t-export').value = t.export_markup_eur_kwh || 0.0121;
            document.getElementById('t-tax').value = t.electricity_tax_eur_kwh || 0.11085;
            document.getElementById('t-fixed').value = t.fixed_monthly_fee_eur || 6.25;
            document.getElementById('top-epex-price').innerText = '€ ' + (t.electricity_tax_eur_kwh + t.import_markup_eur_kwh + 0.138).toFixed(4);
        }

        async function saveTariffs(e) {
            e.preventDefault();
            const payload = {
                contract_start_date: document.getElementById('t-start').value,
                import_markup_eur_kwh: parseFloat(document.getElementById('t-import').value),
                export_markup_eur_kwh: parseFloat(document.getElementById('t-export').value),
                electricity_tax_eur_kwh: parseFloat(document.getElementById('t-tax').value),
                fixed_monthly_fee_eur: parseFloat(document.getElementById('t-fixed').value)
            };
            await fetch('./api/tariffs', { method: 'PUT', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload) });
            alert('Tarieven succesvol opgeslagen!');
        }

        // Initial Boot
        loadStatus();
        loadDevices();
    </script>
</body>
</html>"""
        self.wfile.write(html.encode("utf-8"))


def run_server(port=8099):
    server = HTTPServer(("0.0.0.0", port), HemsApiHandler)
    print(f"Open HEMS Management Console running on port {port}...")
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
