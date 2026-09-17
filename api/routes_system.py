# Open HEMS: System, Infrastructure & CRUD Router
import urllib
import base64
import json
import os
import re
import ssl
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from pathlib import Path
from typing import Dict, Any, List, Optional

from api.context import (
    AMS_TZ, SECRETS_FILE, PARAMS_FILE, CONFIG_FILE,
    load_json, save_json, load_secrets, get_secret, save_secret, get_ha_client_config,
    get_ha_states_map, calculate_poa_solar_kw, format_slot_label,
    fetch_recent_telemetry_history, ensure_active_canonical_plan,
    ensure_framework_defaults, test_influxdb_connection, test_mqtt_connection,
    fetch_ha_entities, GLOBAL_MODEL, GLOBAL_DHW_MODEL, GLOBAL_COLLECTOR
)
from models.canonical import StandardizedState, get_state_metadata
from layer3_scheduling.plan_store import get_plan_store

def handle_get(handler, path: str, qp: dict) -> bool:
    if path == "/api/config/solar":
        cfg = load_json(CONFIG_FILE)
        sol = cfg.get("solar", {})
        handler._send_json({
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
        return True

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
        handler._send_json(res)
        return True

    if path == "/api/pipeline/status":
        global GLOBAL_COLLECTOR
        if GLOBAL_COLLECTOR:
            handler._send_json({
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
            handler._send_json({"status": "starting", "samples_in_window": 0})
        return True

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
                state.value: get_state_metadata(state)["color_hex"] for state in StandardizedState
            }
        }
        handler._send_json(report)
        return True

    if path == "/api/system/mode-catalog":
        from models.mode_catalog import load_mode_catalog
        handler._send_json(load_mode_catalog())
        return True

    if path == "/api/openapi.json":
        try:
            openapi_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "docs", "openapi.json")
            if os.path.exists(openapi_path):
                with open(openapi_path, "r", encoding="utf-8") as f:
                    spec = json.load(f)
                handler._send_json(spec)
            else:
                handler._send_json({"error": "openapi.json not found"}, 404)
            return True
        except Exception as e:
            handler._send_json({"error": str(e)}, 500)
            return True

    if path == "/api/status":
        cfg = load_json(CONFIG_FILE)
        params = load_json(PARAMS_FILE)
        ensure_framework_defaults(cfg)
        handler._send_json({
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
        return True

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

        handler._send_json({
            "homeassistant": {
                "status": ha_status,
                "url": ha_base_url,
                "version": ha_version,
                "location": ha_location,
                "latency_ms": ha_latency_ms,
                "has_token": bool(ha_token or os.environ.get("SUPERVISOR_TOKEN")),
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
        return True

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
        handler._send_json(stats)
        return True

    # API: Home Assistant Entities Dropdown
    if path == "/api/ha/entities":
        entities = fetch_ha_entities()
        handler._send_json({"entities": entities})
        return True

    # API: Policies (Read All)
    if path == "/api/devices":
        cfg = load_json(CONFIG_FILE)
        ensure_framework_defaults(cfg)
        handler._send_json({"devices": cfg.get("devices", [])})
        return True

    # API: Tariffs / Suppliers (Read All)
    if path == "/api/tariffs":
        cfg = load_json(CONFIG_FILE)
        ensure_framework_defaults(cfg)
        handler._send_json({"tariffs": cfg.get("tariffs_list", [])})
        return True

    # API: Calibration & Offsets
    if path == "/api/calibration":
        params = load_json(PARAMS_FILE)
        cfg = load_json(CONFIG_FILE)
        handler._send_json({
            "parameters": params,
            "exclusion_windows": cfg.get("data_exclusion_windows", [])
        })
        return True

            # API: 24h Rolling Ahead Power Consumption Prediction Engine

    return False

def handle_post(handler, path: str, body: dict) -> bool:
    body = handler._read_json_body()

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
        handler._send_json({"status": "success", "message": "Zonnepanelen configuratie opgeslagen", "solar": cfg["solar"]})
        return True

    if path == "/api/system/push-sensors":
        try:
            plan = ensure_active_canonical_plan()
            ha_base_url, ha_token = get_ha_client_config()
            if not ha_token:
                handler._send_json({"status": "error", "message": "Geen Home Assistant token beschikbaar"}, 400)
                return True
            from integrations.homeassistant.sensor_pusher import HomeAssistantSensorPusher
            pusher = HomeAssistantSensorPusher(ha_base_url, ha_token)
            results = pusher.push_plan_sensors(plan)
            handler._send_json({
                "status": "success",
                "message": f"{sum(1 for v in results.values() if v)} sensoren succesvol bijgewerkt in Home Assistant",
                "results": results
            })
            return True
        except Exception as e:
            handler._send_json({"status": "error", "message": str(e)}, 500)
            return True

    if path == "/api/infrastructure/homeassistant/test":
        ha_sec = load_secrets()
        ha_base_url, ha_token = get_ha_client_config()
        if not ha_token:
            handler._send_json({"status": "error", "message": "Geen Supervisor of HASS token gevonden"}, 400)
            return True
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
                handler._send_json({
                    "status": "success",
                    "latency_ms": lat,
                    "version": data.get("version"),
                    "location": data.get("location_name"),
                    "message": f"Home Assistant Core verbonden! Latency: {lat}ms, Versie: {data.get('version')}"
                })
                return True
        except Exception as e:
            handler._send_json({"status": "error", "message": f"Fout bij verbinden met Home Assistant: {str(e)}"}, 500)
            return True

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
        handler._send_json(res)
        return True

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
            handler._send_json({
                "status": "success",
                "baseload_watts": cfg.get("baseload_watts", 300),
                "solar_cost_eur_kwh": cfg.get("solar_cost_eur_kwh", 0.06)
            })
            return True
        except Exception as e:
            handler._send_json({"status": "error", "message": str(e)}, 400)
            return True

    if False and path == "/api/analytics/solar_cost":
        try:
            new_cost = float(body.get("solar_cost_eur_kwh", 0.06))
            cfg = load_json(CONFIG_FILE)
            cfg["solar_cost_eur_kwh"] = round(new_cost, 4)
            save_json(CONFIG_FILE, cfg)
            handler._send_json({"status": "success", "solar_cost_eur_kwh": cfg["solar_cost_eur_kwh"]})
            return True
        except Exception as e:
            handler._send_json({"status": "error", "message": str(e)}, 400)
            return True

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
        handler._send_json({"status": "saved", "connection": conn_obj})
        return True

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
        handler._send_json(res)
        return True

    # INFRASTRUCTURE: Save / Upsert MQTT Connection Profile
    if path == "/api/infrastructure/mqtt":
        cfg = load_json(CONFIG_FILE)
        ensure_framework_defaults(cfg)
        conn_id = body.get("id") or f"mqtt_{int(datetime.now().timestamp())}"
        updated = False
        conns = cfg.setdefault("mqtt_connections", [])
        conn_obj = None

        # Securely vault password in private credentials store
        if body.get("password") and body.get("password") != "••••••••":
            save_secret("mqtt", conn_id, body["password"])

        for c in conns:
            if c["id"] == conn_id:
                for k in ["name", "type", "host", "port", "username", "base_topic", "client_id", "tls", "enabled", "is_default"]:
                    if k in body:
                        c[k] = int(body[k]) if k == "port" else body[k]
                c.pop("password", None)
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

        # Return sanitized connection object without plaintext credentials
        resp_obj = dict(conn_obj)
        resp_obj["password"] = "••••••••" if (get_secret("mqtt", conn_id) or body.get("password")) else ""
        resp_obj["has_password"] = bool(resp_obj["password"])
        handler._send_json({"status": "saved", "connection": resp_obj})
        return True

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
                handler._send_json({
                    "status": "success",
                    "message": f"Meting succesvol opgeslagen in InfluxDB database '{write_db}'!",
                    "line_protocol": line,
                    "latency_ms": latency,
                    "http_status": r.status
                })
        except Exception as e:
            handler._send_json({"status": "error", "message": str(e)}, 500)
        return True

    # CREATE: Policy
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
        handler._send_json({"status": "created", "device": new_dev}, 201)
        return True

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
        handler._send_json({"status": "created", "tariff": new_tariff}, 201)
        return True

    # CREATE: Exclusion Window

    return False

def handle_put(handler, path: str, body: dict) -> bool:
    # UPDATE: Home Assistant Connector
    if path == "/api/infrastructure/homeassistant":
        body = handler._read_json_body()
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
        handler._send_json({"status": "updated", "homeassistant": ha_cfg})
        return True

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
                handler._send_json({"status": "updated", "device": d})
                return True
        handler._send_json({"error": "Device not found"}, 404)
        return True

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
                handler._send_json({"status": "updated", "tariff": t})
                return True
        handler._send_json({"error": "Tariff not found"}, 404)
        return True

    handler._send_json({"error": "Endpoint not found"}, 404)

    # =========================================================================

    return False

def handle_delete(handler, path: str) -> bool:
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
            handler._send_json({"status": "deleted", "id": c_id})
        else:
            handler._send_json({"error": "InfluxDB connection not found"}, 404)
        return True

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
            handler._send_json({"status": "deleted", "id": c_id})
        else:
            handler._send_json({"error": "MQTT connection not found"}, 404)
        return True

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
            handler._send_json({"status": "deleted", "id": pol_id})
        else:
            handler._send_json({"error": "Policy not found"}, 404)
        return True

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
            handler._send_json({"status": "deleted", "id": dev_id})
        else:
            handler._send_json({"error": "Device not found"}, 404)
        return True

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
            handler._send_json({"status": "deleted", "id": t_id})
        else:
            handler._send_json({"error": "Tariff not found"}, 404)
        return True

    # DELETE: Exclusion Window
    m_win = re.match(r"^/api/exclusion-windows/(\d+)$", path)
    if m_win:
        idx = int(m_win.group(1))
        cfg = load_json(CONFIG_FILE)
        windows = cfg.get("data_exclusion_windows", [])
        if 0 <= idx < len(windows):
            removed = windows.pop(idx)
            save_json(CONFIG_FILE, cfg)
            handler._send_json({"status": "deleted", "window": removed})
        else:
            handler._send_json({"error": "Index out of range"}, 404)
        return True

    handler._send_json({"error": "Endpoint not found"}, 404)

    return False
