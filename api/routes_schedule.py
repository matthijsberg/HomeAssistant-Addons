# Open HEMS: 24h Dispatch Schedule & Optimization Router
import urllib
import json
import math
import os
import re
import ssl
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from pathlib import Path
from typing import Dict, Any, List, Optional

from api.context import (
    AMS_TZ, SECRETS_FILE, PARAMS_FILE, CONFIG_FILE,
    load_json, save_json, load_secrets, get_ha_client_config,
    get_ha_states_map, calculate_poa_solar_kw, format_slot_label,
    fetch_recent_telemetry_history, ensure_active_canonical_plan,
    ensure_framework_defaults, DUTCH_DAYS_SHORT, GLOBAL_CENTRAL_CACHE,
    evaluate_and_apply_dhw_run_merger, get_epex_tariffs_cached,
    GLOBAL_MODEL, GLOBAL_DHW_MODEL, GLOBAL_COLLECTOR
)
from models.canonical import StandardizedState, STATE_METADATA, detect_dynamic_price_peaks

def handle_get(handler, path: str, qp: dict) -> bool:
    if path == "/api/control/status":
        handler._send_json({
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
        return True

    # API: Status
    if path == "/api/policies":
        cfg = load_json(CONFIG_FILE)
        ensure_framework_defaults(cfg)
        handler._send_json({"policies": cfg.get("policies", [])})
        return True

    # API: Devices (Read All)
    if path == "/api/schedule/chart-data":
        cfg = load_json(CONFIG_FILE)
        ensure_framework_defaults(cfg)

        parsed_url = urllib.parse.urlparse(handler.path)
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
            from layer1_data_collection.geo_location import get_geo_coordinates
            geo_lat, geo_lon = get_geo_coordinates(cfg)
            from layer1_data_collection.forecast_solar import ForecastSolarProvider
            fs_prov = ForecastSolarProvider(lat=geo_lat, lon=geo_lon, tilt=s_tilt, azimuth_deg_south=45.0, kwp=s_kwp, inverter_max_kw=s_inv, calibration_factor=s_cal)
            fs_slots = fs_prov.get_calibrated_quarter_slots(base_dt, horizon_slots=total_slots, step_mins=step_mins)
            for sl in fs_slots:
                k_s = sl["dt"].strftime("%Y-%m-%d %H:%M" if is_15m else "%Y-%m-%d %H:00")
                solar_map[k_s] = sl["solar_kw"]
        except Exception as e_fs:
            print(f"Warning fetching Forecast.Solar in chart-data: {e_fs}")

        has_solar_chart = any(v > 0.05 for v in solar_map.values())
        try:
            from layer1_data_collection.geo_location import get_geo_coordinates
            geo_lat, geo_lon = get_geo_coordinates(cfg)
            url_m = f"https://api.open-meteo.com/v1/forecast?latitude={geo_lat}&longitude={geo_lon}&hourly=temperature_2m,shortwave_radiation,wind_speed_10m,relative_humidity_2m&timezone=Europe%2FAmsterdam&forecast_days=2"
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
            daylight_slots = [it for it in timeline_items if 10 <= it["dt"].hour <= 16]

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

        handler._send_json({
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
        return True

    return False

def handle_post(handler, path: str, body: dict) -> bool:
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
        handler._send_json({"status": "created", "policy": new_pol}, 201)
        return True

    # CREATE: Device
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
        handler._send_json({"status": "created", "window": new_win}, 201)
        return True

    if path == "/api/schedule/recalculate":
        try:
            fresh_plan = ensure_active_canonical_plan(force_refresh=True)
            handler._send_json({
                "status": "success",
                "message": "Dispatch schema succesvol herberekend via centrale PlanStore",
                "generated_at": fresh_plan.generated_at,
                "slots_count": len(fresh_plan.slots),
                "planned_mode": fresh_plan.dhw_summary.planned_mode if fresh_plan.dhw_summary else "normal"
            })
        except Exception as e:
            handler._send_json({"status": "failed", "error": str(e)}, 500)
        return True


    return False

def handle_put(handler, path: str, body: dict) -> bool:
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
                handler._send_json({"status": "updated", "policy": p})
                return True
        handler._send_json({"error": "Policy not found"}, 404)
        return True

    return False
