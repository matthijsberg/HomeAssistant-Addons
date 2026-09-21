#!/usr/bin/env python3
"""
Open HEMS Framework & Management Console
========================================
Version: 0.103.56
Generic Energy Management Platform:
  - Multi-Vector Telemetry & Optimization Daemon
  - Domain Router Dispatch to api/routes_*.py
  - Background Layer 1 Sampling & InfluxDB Sync
"""

import argparse
import json
import os
import socket
import ssl
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer, ThreadingHTTPServer
from pathlib import Path
from zoneinfo import ZoneInfo

# Priority path anchoring
ROOT_DIR = Path(__file__).resolve().parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from api import routes_analytics, routes_model, routes_schedule, routes_system
from models.canonical import normalize_power_reading
from site_adapters.daikin_p1p2 import DaikinP1P2StateClassifier
from layer3_scheduling.decision_audit import DecisionAuditLogger
from api.context import (
    GLOBAL_COLLECTOR, GLOBAL_DHW_MODEL, GLOBAL_MODEL,
    ensure_active_canonical_plan, INDEX_HTML_PATH, WEB_DIR
)
from api.secrets_store import (
    CONFIG_FILE, PARAMS_FILE, SECRETS_FILE,
    load_json, save_json, load_secrets, save_secret, get_secret, ensure_framework_defaults
)
from integrations.homeassistant.client import (
    get_ha_client_config, get_ha_states_map, fetch_ha_entities,
    call_ha_service, call_ha_service_detailed, make_daikin_ha_actuator
)
from api.infra_diagnostics import (
    write_hems_annotation, log_technical_error,
    test_influxdb_connection, test_mqtt_connection
)
from api.energy_feed import (
    AMS_TZ, DUTCH_DAYS_SHORT, format_slot_label,
    calculate_poa_solar_kw, get_anchored_weather_forecast,
    fetch_recent_telemetry_history, get_epex_tariffs_cached
)
from layer3_scheduling.plan_decision_evaluator import (
    evaluate_and_apply_dhw_run_merger,
    evaluate_and_log_night_boiler_decision,
    evaluate_and_log_planner_decisions
)
from layer3_scheduling.plan_store import PlanStore
from layer4_control.room_thermostat_buffer import RoomThermostatBufferController

GLOBAL_ROOM_BUFFER_CTRL = RoomThermostatBufferController(default_baseline_c=20.5)

class HemsApiHandler(BaseHTTPRequestHandler):

    def _send_json(self, data, status=200):
        payload = json.dumps(data, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
        self.wfile.write(payload)

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

        # 0. SRE Healthz & Liveness Probe
        if path == "/healthz" or path == "/health":
            now_ams = datetime.now(ZoneInfo("Europe/Amsterdam"))
            plan = ensure_active_canonical_plan()
            self._send_json({
                "status": "healthy",
                "timestamp": now_ams.isoformat(),
                "plan_slots": len(plan.slots) if plan else 0,
                "plan_is_fresh": getattr(plan, "is_fresh", True) if plan else False,
                "active_dhw_slots": sum(1 for s in plan.slots if s.dhw_kw > 0) if plan else 0,
                "dynamic_peaks_count": len(plan.dynamic_peaks) if plan else 0
            })
            return

        # 1. Dispatch to modular domain routers
        if routes_analytics.handle_get(self, path, query_params):
            return
        if routes_model.handle_get(self, path, query_params):
            return
        if routes_schedule.handle_get(self, path, query_params):
            return
        if routes_system.handle_get(self, path, query_params):
            return

        # 2. Static file serving from web/ and locales/
        if path.startswith("/locales/"):
            from api.i18n import LOCALES_DIR
            clean_rel = path.replace("/locales/", "", 1)
            file_path = (LOCALES_DIR / clean_rel).resolve()
            if file_path.is_relative_to(LOCALES_DIR.resolve()) and file_path.is_file():
                self._serve_static_file(file_path)
                return

        if path.startswith("/static/") or path.startswith("/web/") or path.startswith("/js/") or path.endswith(".js") or path.endswith(".css"):
            clean_rel = path.lstrip("/")
            if clean_rel.startswith("web/"):
                clean_rel = clean_rel[4:]
            elif clean_rel.startswith("static/"):
                clean_rel = clean_rel[7:]
            file_path = (WEB_DIR / clean_rel).resolve()
            if file_path.is_relative_to(WEB_DIR.resolve()) and file_path.is_file():
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
            cache_ctrl = "no-cache" if file_path.suffix in [".js", ".css", ".json"] else "public, max-age=3600"
            self.send_header("Cache-Control", cache_ctrl)
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
            except Exception as e_samp:
                print(f"[Open HEMS Collector] Error in sample_devices: {e_samp}", flush=True)

            now = time.time()
            if now - self._last_flush_time >= self.flush_window:
                try:
                    self.flush_window_to_influx()
                except Exception as e_flush:
                    print(f"[Open HEMS Collector] Error in flush_window_to_influx: {e_flush}", flush=True)
                self._last_flush_time = now
                try:
                    self.execute_live_dispatch()
                except Exception as e_disp:
                    print(f"[Open HEMS Collector] Error in execute_live_dispatch: {e_disp}", flush=True)
                try:
                    self.push_ha_sensors()
                except Exception as e_push:
                    print(f"[Open HEMS Collector] Error in push_ha_sensors: {e_push}", flush=True)
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
            val = getattr(cur_slot.mode_code, "value", cur_slot.mode_code)
            req_clean = str(val).strip().lower()
            if req_clean.startswith("standardizedstate."):
                req_clean = req_clean.split(".")[-1]
            mode_to_execute = req_clean

            # In-Flight Run Continuity Guard:
            # If an active run is in flight and tank is below setpoint, protect against premature de-actuation
            in_flight = PlanStore.get_instance().get_in_flight_run()
            if in_flight:
                target_in_flight = float(in_flight.get("target_temp_c", 50.0))
                if t_live < target_in_flight - 0.2:
                    in_flight_mode = in_flight.get("mode", "forced_on")
                    if mode_to_execute in ["normal", "off", "standby"]:
                        mode_to_execute = in_flight_mode
                else:
                    PlanStore.get_instance().clear_in_flight_run()
                    in_flight = None

            # Space Heating: Floor buffer preheat via Room Thermostat (+1.0°C) with rate limiter
            now_utc = datetime.now(timezone.utc)
            room_cl = states_map.get("climate.woonkamer_climate_daikin", {})
            room_attrs = room_cl.get("attributes", {})
            try:
                t_room_live = float(room_attrs.get("current_temperature", 20.5))
            except (ValueError, TypeError):
                t_room_live = 20.5
            try:
                t_room_setpoint = float(room_attrs.get("target_temp_low", room_attrs.get("temperature", 20.5)))
            except (ValueError, TypeError):
                t_room_setpoint = 20.5

            if mode_to_execute == "advised_on":
                elevated_t = GLOBAL_ROOM_BUFFER_CTRL.request_preheat(t_room_live, t_room_setpoint, now=now_utc)
                if elevated_t is not None:
                    call_ha_service("climate", "set_temperature", {
                        "entity_id": "climate.woonkamer_climate_daikin",
                        "target_temp_low": elevated_t,
                        "target_temp_high": elevated_t + 3.0
                    })
                    write_hems_annotation(
                        event_type="space_heating_preheat",
                        title=f"♨️ Vloerbuffer Pre-Heat (+1.0°C) -> {elevated_t}°C",
                        description=f"Kamerthermostaat verhoogd naar {elevated_t}°C (basis {t_room_setpoint}°C) voor betonbuffer vóór piek.",
                        state_code="advised_on",
                        power_kw=1.5,
                        target_temp_c=elevated_t
                    )
                # Keep physical relays in normal (SG2); room thermostat handles heat pump activation cleanly
                mode_to_execute = "normal"
            else:
                restored_t = GLOBAL_ROOM_BUFFER_CTRL.request_release(now=now_utc)
                if restored_t is not None:
                    call_ha_service("climate", "set_temperature", {
                        "entity_id": "climate.woonkamer_climate_daikin",
                        "target_temp_low": restored_t,
                        "target_temp_high": restored_t + 3.0
                    })

            # Watchdog 1: Room Thermostat Failsafe
            wd_room = GLOBAL_ROOM_BUFFER_CTRL.watchdog_check(t_room_setpoint, now=now_utc)
            if wd_room is not None:
                call_ha_service("climate", "set_temperature", {
                    "entity_id": "climate.woonkamer_climate_daikin",
                    "target_temp_low": wd_room,
                    "target_temp_high": wd_room + 3.0
                })

            # Watchdog 2: DHW Thermostat Setpoint Failsafe (ensure standard 50°C baseline)
            dhw_cl_temp = states_map.get("climate.hc_dhw_dhw_setpoint", {}).get("attributes", {}).get("temperature")
            if dhw_cl_temp is not None:
                try:
                    if abs(float(dhw_cl_temp) - 50.0) > 0.2 and not in_flight:
                        call_ha_service("climate", "set_temperature", {
                            "entity_id": "climate.hc_dhw_dhw_setpoint",
                            "temperature": 50.0
                        })
                except (ValueError, TypeError):
                    pass

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
            if in_flight:
                target_t = float(in_flight.get("target_temp_c", 50.0))
            elif getattr(cur_slot, "target_temp_c", None) is not None:
                target_t = float(cur_slot.target_temp_c)
            else:
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
                curr_sel = states_map.get("input_select.warmtepomp_smart_grid_modus", {}).get("state")
                mode_to_option = {
                    "normal": "Automatisch",
                    "forced_off": "Geforceerd uit",
                    "advised_on": "Geadviseerd aan",
                    "forced_on": "Geforceerd aan",
                    "max_on": "Geforceerd aan",
                    "forced_solar_boost_60": "Geforceerd aan",
                    "forced_night_50": "Geforceerd aan",
                    "forced_space_heating": "Geforceerd aan",
                    "advised_off": "Automatisch"
                }
                target_sel = mode_to_option.get(res.effective_mode, "Automatisch")
                curr_cv = (states_map.get("switch.hc_mode_altherma_on", {}).get("state") == "on")

                has_switched = (
                    curr_sel != target_sel or
                    res.command.cv_master_switch_on != curr_cv
                )

                if has_switched:
                    print(f"[Open HEMS Actuator] Hardware geschakeld naar stand '{mode_to_execute}': Modus={target_sel}, CV_Master={res.command.cv_master_switch_on}", flush=True)
                    time.sleep(0.5)
                    fresh_states = get_ha_states_map()
                    act_sel = fresh_states.get("input_select.warmtepomp_smart_grid_modus", {}).get("state")
                    act_cv = (fresh_states.get("switch.hc_mode_altherma_on", {}).get("state") == "on")

                    unconfirmed = []
                    if target_sel != act_sel:
                        unconfirmed.append(f"Smart Grid Modus (doel '{target_sel}', is '{act_sel}')")
                    if res.command.cv_master_switch_on != act_cv:
                        unconfirmed.append(f"CV Master (doel {'AAN' if res.command.cv_master_switch_on else 'UIT'}, is {'AAN' if act_cv else 'UIT'})")

                    if unconfirmed:
                        log_technical_error(
                            domain="hardware",
                            event_type="actuation_unconfirmed",
                            reason="❌ Schakeling Niet Bevestigd door Home Assistant",
                            explanation=f"Open HEMS heeft de status omgezet voor {mode_to_execute}, maar Home Assistant bevestigt de toestand niet: {', '.join(unconfirmed)}.",
                            inputs={
                                "mode_gevraagd": mode_to_execute,
                                "onbevestigd": unconfirmed,
                                "modus_werkelijk": act_sel,
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
                        act_desc = f"Smart Grid modus omgezet: '{target_sel}', CV Master={'AAN' if res.command.cv_master_switch_on else 'UIT'}."
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

    def push_ha_sensors(self):
        """Pushes 24h rolling forecast and dispatch sensors to Home Assistant Core."""
        try:
            plan = ensure_active_canonical_plan()
            if not plan or not plan.slots:
                return
            ha_url, ha_tok = get_ha_client_config()
            if ha_url and ha_tok:
                from integrations.homeassistant.sensor_pusher import HomeAssistantSensorPusher
                pusher = HomeAssistantSensorPusher(ha_url, ha_tok)
                pusher.push_plan_sensors(plan)
        except Exception as e:
            print(f"[Open HEMS SensorPusher] Background push error: {e}", flush=True)

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
    class HemsServer(ThreadingHTTPServer):
        daemon_threads = True
        allow_reuse_address = True

    server = HemsServer(("0.0.0.0", port), HemsApiHandler)
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
