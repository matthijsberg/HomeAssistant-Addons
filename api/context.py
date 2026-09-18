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

ROOT_DIR = str(Path(__file__).resolve().parent.parent)
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
    from models.canonical import StandardizedState, get_state_metadata, CanonicalDispatchPlan
    GLOBAL_MODEL = HybridForecastingModel()
    GLOBAL_DHW_MODEL = DhwThermalModel()
except Exception as _e_model:
    print(f"[WARN] Failed to initialize Forecasting Models: {_e_model}")
    GLOBAL_MODEL = None
    GLOBAL_DHW_MODEL = None

GLOBAL_COLLECTOR = None

from http.server import HTTPServer, BaseHTTPRequestHandler
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from pathlib import Path

# Site-specific adapters (decoupled from core engine)
sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, "/addons/open-hems")
sys.path.insert(0, "/opt/open-hems")
from site_adapters.daikin_p1p2 import DaikinP1P2StateClassifier, HeatPumpDisaggregation
from models.canonical import normalize_power_reading, StandardizedState, get_state_metadata
from layer3_scheduling.peak_detection import detect_dynamic_price_peaks, calc_percentile

from api.secrets_store import (
    CONFIG_FILE, PARAMS_FILE, SECRETS_FILE, HA_API_CONFIG,
    load_json, save_json, load_secrets, save_secret, get_secret, ensure_framework_defaults
)
from integrations.homeassistant.client import (
    get_ha_client_config, fetch_ha_entities, get_ha_states_map,
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
from layer3_scheduling.plan_decision_evaluator import evaluate_and_log_planner_decisions
WEB_DIR = Path(__file__).resolve().parent.parent / "web"
INDEX_HTML_PATH = WEB_DIR / "index.html"


GLOBAL_CENTRAL_CACHE = {
    "timestamp": 0,
    "15m": None,
    "1h": None
}

from layer1_data_collection.geo_location import get_geo_coordinates

_LAST_CANONICAL_PLAN_TIME = None
_PLAN_LOCK = threading.Lock()

def ensure_active_canonical_plan(force_refresh=False, horizon_hours=48.0):
    """
    Ensures an authoritative, synchronized CanonicalDispatchPlan is cached in PlanStore.
    Re-plans every 60 seconds or when explicitly forced. Thread-safe with double-checked locking.
    """
    global _LAST_CANONICAL_PLAN_TIME
    now_ams = datetime.now(ZoneInfo("Europe/Amsterdam"))
    store = get_plan_store()
    current_plan = store.get_plan()

    if not force_refresh and current_plan is not None and _LAST_CANONICAL_PLAN_TIME is not None:
        if (now_ams - _LAST_CANONICAL_PLAN_TIME).total_seconds() < 60:
            return current_plan

    with _PLAN_LOCK:
        current_plan = store.get_plan()
        if not force_refresh and current_plan is not None and _LAST_CANONICAL_PLAN_TIME is not None:
            if (now_ams - _LAST_CANONICAL_PLAN_TIME).total_seconds() < 60:
                return current_plan

    # 1. Fetch EPEX prices from dedicated Day-Ahead cache
    raw_prices, _, _ = get_epex_tariffs_cached(is_15m=True)

    # 2. Fetch Solar & Weather Forecast for Culemborg (48-hour rolling horizon)
    horizon_slots = int(horizon_hours * 4)
    cfg = load_json(CONFIG_FILE)
    s_cfg = cfg.get("solar", {})
    s_kwp = float(s_cfg.get("kwp", 5.76))
    s_inv = float(s_cfg.get("inverter_max_w", 5500)) / 1000.0
    s_tilt = float(s_cfg.get("tilt_degrees", 34))
    s_az = float(s_cfg.get("azimuth_degrees", 225))
    s_cal = float(s_cfg.get("calibration_factor", 1.18))
    use_fs = s_cfg.get("forecast_provider", "forecast_solar") == "forecast_solar"

    geo_lat, geo_lon = get_geo_coordinates(cfg)
    raw_solar = []
    if use_fs:
        try:
            from layer1_data_collection.forecast_solar import ForecastSolarProvider
            fs_prov = ForecastSolarProvider(
                lat=geo_lat, lon=geo_lon, tilt=s_tilt,
                azimuth_deg_south=45.0,
                kwp=s_kwp, inverter_max_kw=s_inv,
                calibration_factor=s_cal
            )
            raw_solar = fs_prov.get_calibrated_quarter_slots(now_ams, horizon_slots=horizon_slots, step_mins=15)
        except Exception as e_fs:
            print(f"Warning fetching Forecast.Solar: {e_fs}")

    has_solar_plan = any(s.get("solar_kw", 0.0) > 0.05 for s in raw_solar)
    if not has_solar_plan:
        raw_solar = []

    raw_weather = []
    try:
        url_m = f"https://api.open-meteo.com/v1/forecast?latitude={geo_lat}&longitude={geo_lon}&hourly=temperature_2m,shortwave_radiation,wind_speed_10m&timezone=Europe%2FAmsterdam&forecast_days=3"
        req_m = urllib.request.Request(url_m, headers={"User-Agent": "OpenHEMS/1.0"})
        with urllib.request.urlopen(req_m, timeout=5) as r_m:
            m_data = json.loads(r_m.read().decode())
            m_times = m_data.get("hourly", {}).get("time", [])
            m_rads = m_data.get("hourly", {}).get("shortwave_radiation", [])
            m_temps = m_data.get("hourly", {}).get("temperature_2m", [])
            m_winds = m_data.get("hourly", {}).get("wind_speed_10m", [])
            s_eff = float(s_cfg.get("efficiency_factor", 0.88))

            existing_solar_times = {s["dt"].strftime("%Y-%m-%d %H:00") for s in raw_solar}
            for idx_m, (t, rad, tmp) in enumerate(zip(m_times, m_rads, m_temps)):
                k_t = t.replace('T', ' ')[:13] + ':00'
                dt_h = datetime.strptime(k_t, "%Y-%m-%d %H:%M").replace(tzinfo=ZoneInfo("Europe/Amsterdam"))
                # If Forecast.Solar has no entries for this hour, fill with Open-Meteo POA solar model
                if not has_solar_plan or dt_h.strftime("%Y-%m-%d %H:00") not in existing_solar_times:
                    poa_kw = calculate_poa_solar_kw(dt_h, float(rad), kwp=s_kwp, tilt_deg=s_tilt, azimuth_deg=s_az, inverter_limit_kw=s_inv, eff=s_eff)
                    raw_solar.append({"dt": dt_h, "solar_kw": poa_kw})
                w_kmh = float(m_winds[idx_m]) if (idx_m < len(m_winds) and m_winds[idx_m] is not None) else 0.0
                raw_weather.append({"dt": dt_h, "temperature": float(tmp), "wind_speed_ms": round(w_kmh / 3.6, 2)})
    except Exception as e_w:
        print(f"Warning fetching Open-Meteo in ensure_active_canonical_plan: {e_w}")

    # 3. Read current tank and room temperature
    cur_dhw = 48.0
    cur_room = 20.0
    cur_floor = None
    cur_target_room = 20.0
    last_hw_time = None
    states_map = get_ha_states_map()
    try:
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

        # Read live floor temperature from underfloor heating sensors (sensors 1..7)
        cur_floor = None
        floor_vals = []
        for i in range(1, 8):
            st_f = states_map.get(f"sensor.warmtepomp_vloerverwarming_sensor_{i}", {}).get("state")
            if st_f and st_f not in ["unavailable", "unknown"]:
                try:
                    floor_vals.append(float(st_f))
                except ValueError:
                    pass
        if floor_vals:
            raw_floor = sum(floor_vals) / len(floor_vals)
            # Physical equilibrium check: when heat pump is idle/standby, stagnant pipes in the utility cupboard
            # read lower than the actual living room concrete thermal mass, which is in equilibrium with room air.
            is_hp_heating = states_map.get("climate.woonkamer_climate_daikin", {}).get("attributes", {}).get("hvac_action") == "heating"
            if not is_hp_heating:
                cur_floor = round(max(raw_floor, cur_room - 0.2), 1)
            else:
                cur_floor = round(raw_floor, 1)
        else:
            cur_floor = cur_room
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

    # 3b. Local Microclimate Observation Nudging (Wittboy & Rooftop Inverter)
    live_t_out = None
    live_solar_kw = None
    live_wind_ms = None
    try:
        from layer1_data_collection.nowcasting import ObservationNowcaster

        # Live Outdoor Temp from Wittboy
        for ent in ["sensor.wittboy_gw2000a_weather_station_gw2000a_outdoor_temperature", "sensor.temperatuur_buiten"]:
            st = states_map.get(ent, {}).get("state")
            if st and st not in ["unavailable", "unknown"]:
                try:
                    live_t_out = float(st)
                    break
                except ValueError:
                    pass

        # Live Rooftop Solar Power
        live_solar_kw = None
        for ent in ["sensor.zonnepanelen_power_avg_5_minutes", "sensor.inepro_metering_pro_380_active_power"]:
            st = states_map.get(ent, {}).get("state")
            if st and st not in ["unavailable", "unknown"]:
                try:
                    v = float(st)
                    live_solar_kw = round(abs(v) / 1000.0, 3)
                    break
                except ValueError:
                    pass

        # Live Wind Speed from Wittboy
        live_wind_ms = None
        st_w = states_map.get("sensor.wittboy_gw2000a_weather_station_gw2000a_wind_speed", {}).get("state")
        if st_w and st_w not in ["unavailable", "unknown"]:
            try:
                live_wind_ms = round(float(st_w) / 3.6, 2)
            except ValueError:
                pass
    except Exception as e_nowcast:
        print(f"[Open HEMS Nowcasting] Error reading live weather telemetry: {e_nowcast}")

    # 4. Check if Space Heating (CV) is enabled via integration adapter
    from integrations.daikin_altherma.reader import DaikinReader
    is_cv_enabled = DaikinReader.is_space_heating_circuit_enabled(states_map)
    is_dhw_enabled = DaikinReader.is_dhw_circuit_enabled(states_map)

    # 5. Extract unallocated profile
    grid_96 = []
    if GLOBAL_MODEL and GLOBAL_MODEL.profile:
        grid_96 = GLOBAL_MODEL.profile.get("profile_96_quarters", [])

    # 6. Sanitize telemetry and apply Local Microclimate Observation Nudging
    frame = TelemetrySanitizer.sanitize(
        now=now_ams,
        raw_prices=raw_prices,
        raw_solar=raw_solar,
        raw_weather=raw_weather,
        raw_unallocated_matrix=grid_96,
        current_dhw_temp=cur_dhw,
        current_room_temp=cur_room,
        current_floor_temp=cur_floor,
        target_room_temp=cur_target_room,
        last_hardware_reading_time=last_hw_time,
        is_space_heating_enabled=is_cv_enabled,
        is_dhw_enabled=is_dhw_enabled,
        live_outdoor_temp_c=live_t_out,
        live_solar_kw=live_solar_kw,
        live_wind_speed_ms=live_wind_ms,
        horizon_slots=horizon_slots,
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

