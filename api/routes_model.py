# Open HEMS: Physical Modeling & Calibration Router
import urllib
import json
import math
import ssl
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from pathlib import Path
from typing import Dict, Any, List, Optional

from api.i18n import localize_dhw_decision
from api.context import (
    load_json, save_json, load_secrets, get_ha_client_config,
    get_ha_states_map, calculate_poa_solar_kw, format_slot_label,
    fetch_recent_telemetry_history, ensure_active_canonical_plan,
    GLOBAL_MODEL, GLOBAL_DHW_MODEL, GLOBAL_COLLECTOR, GLOBAL_CENTRAL_CACHE,
    evaluate_and_apply_dhw_run_merger
)
from layer3_scheduling.space_heating_policy import SpaceHeatingPolicy

def handle_get(handler, path: str, qp: dict) -> bool:
    if path.startswith("/api/model/heating-forecast"):
        qp = urllib.parse.parse_qs(urllib.parse.urlparse(handler.path).query)
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

        # Extract lockout and heating ranges for visual overlay (offset by history_count)
        from models.canonical import extract_plan_spitsblok_ranges, extract_plan_heating_ranges
        forced_off_ranges = extract_plan_spitsblok_ranges(plan.slots, history_count=len(hist_pts), is_15m=is_15m)
        heating_ranges = extract_plan_heating_ranges(plan.slots, history_count=len(hist_pts), is_15m=is_15m, domain="space_heating")

        handler._send_json({
            "resolution": res_mode,
            "labels": hist_labels + labels,
            "outdoor_temps_c": hist_outdoor + out_temps,
            "indoor_temps_c": hist_indoor + in_temps,
            "unheated_temps_c": hist_indoor + (h_summary.unheated_room_temps_c if (h_summary and h_summary.unheated_room_temps_c) else in_temps),
            "indoor_temps_p05_c": hist_indoor + (h_summary.room_temps_p05_c if (h_summary and h_summary.room_temps_p05_c) else in_temps),
            "indoor_temps_p95_c": hist_indoor + (h_summary.room_temps_p95_c if (h_summary and h_summary.room_temps_p95_c) else in_temps),
            "unheated_temps_p05_c": hist_indoor + (h_summary.unheated_temps_p05_c if (h_summary and h_summary.unheated_temps_p05_c) else in_temps),
            "unheated_temps_p95_c": hist_indoor + (h_summary.unheated_temps_p95_c if (h_summary and h_summary.unheated_temps_p95_c) else in_temps),
            "floor_temps_c": hist_floor + floor_temps,
            "cops": hist_cops + cops,
            "thermal_loss_kw": hist_th_loss + th_loss_kw,
            "electrical_kw": hist_el_kw + el_power_kw,
            "costs_eur": hist_costs + costs_eur,
            "history_count": len(hist_pts),
            "forced_off_ranges": forced_off_ranges,
            "heating_ranges": heating_ranges,
            "total_thermal_kwh": tot_th,
            "total_electrical_kwh": tot_el,
            "total_cost_eur": tot_cost,
            "thermostat_setpoint_c": t_setpoint,
            "thermostat_start_threshold_c": t_start_threshold,
            "thermostat_active": t_active,
            "thermostat_status_label": t_status
        })
        return True

    if path.startswith("/api/model/dhw-status"):
        qp = urllib.parse.parse_qs(urllib.parse.urlparse(handler.path).query)
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
            # Decision details are derived directly from the authoritative CentralPlanner plan
            lang = qp.get("lang", ["nl"])[0]
            decision = getattr(plan.dhw_summary, "decision_details", None) if (plan and plan.dhw_summary) else None
            if decision:
                decision = localize_dhw_decision(decision, lang=lang)
            if not decision:
                decision = {
                    "status": "STANDBY",
                    "planned_mode": planned_mode,
                    "box_title": "Buffer Efficiëntie: Wel of Niet Bufferen (50°C vs. 60°C)?",
                    "badge_html": '<span class="inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-[10px] font-bold bg-slate-900 text-slate-300 border border-slate-700"><span class="w-1.5 h-1.5 rounded-full bg-slate-400"></span> Afwachten (Vat op temperatuur)</span>',
                    "comfort_card_title": "1️⃣ Basislading 50°C Nodig voor Avondspits?",
                    "comfort_text": f"Het vat is nu {t_live:.1f}°C. Standby bewaakt.",
                    "finance_card_title": "2️⃣ Afweging: Doorbuffereen naar 60°C (24h Dekking)?",
                    "finance_text": "Evaluatie loopt via CentralPlanner.",
                    "bullet_1": "Basislading 50°C: Standby",
                    "bullet_2": "Bufferen naar 60°C: Standby",
                    "morning_dip_c": 45.0,
                    "morning_dip_time": "09:30",
                    "dynamic_peaks": cached_dyn_peaks,
                    "savings_eur": 0.0
                }

            # Evaluate Opportunistic Run Merger
            try:
                plan_for_merger = ensure_active_canonical_plan()
                merge_outcome = evaluate_and_apply_dhw_run_merger(plan_for_merger, t_live)
                if merge_outcome:
                    decision["opportunistic_merge"] = {
                        "should_merge": merge_outcome.should_merge,
                        "reason": merge_outcome.reason,
                        "promoted_mode": merge_outcome.promoted_mode,
                        "target_temp_c": merge_outcome.target_temp_c,
                        "original_slot_time": merge_outcome.original_slot_time,
                        "savings_estimate_eur": merge_outcome.savings_estimate_eur
                    }
                    if merge_outcome.should_merge:
                        decision["badge_html"] = '<span class="inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-[10px] font-bold bg-purple-950/80 text-purple-300 border border-purple-800/80"><span class="w-1.5 h-1.5 rounded-full bg-purple-400 animate-pulse"></span> ⚡ Opportunistische Zonnebuffer Actief (tot 60°C)</span>'
            except Exception as e_mrg:
                print(f"Warning in evaluate_and_apply_dhw_run_merger: {e_mrg}")

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

            from models.canonical import extract_plan_spitsblok_ranges, extract_plan_heating_ranges
            forced_off_ranges = extract_plan_spitsblok_ranges(plan.slots, history_count=len(hist_pts), is_15m=is_15m)
            heating_ranges = extract_plan_heating_ranges(plan.slots, history_count=len(hist_pts), is_15m=is_15m, domain="dhw")

            handler._send_json({
                "status": "online",
                "resolution": res_mode,
                "decision": decision,
                "trajectory": traj,
                "unheated_trajectory": unheated_traj,
                "forced_off_ranges": forced_off_ranges,
                "heating_ranges": heating_ranges,
                "history_count": len(hist_pts)
            })
        else:
            handler._send_json({"status": "error", "message": "DHW model niet geladen"}, 500)
        return True

    if path == "/api/model/algorithm-config":
        params = load_json(PARAMS_FILE) if PARAMS_FILE.exists() else {}
        handler._send_json({
            "learning_rate_ewma": float(params.get("learning_rate_ewma", 0.05)),
            "rolling_window_days": int(params.get("rolling_window_days", 90)),
            "auto_accept_max_drift_pct": float(params.get("auto_accept_max_drift_pct", 3.0)),
            "wind_exclusion_limit_ms": float(params.get("wind_exclusion_limit_ms", 8.0)),
            "solar_exclusion_limit_w_m2": float(params.get("solar_exclusion_limit_w_m2", 500.0))
        })
        return True

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
            handler._send_json(recs_dict)
        else:
            handler._send_json({"status": "empty", "recommendations": []})
        return True

    if path == "/api/model/status":
        if not GLOBAL_MODEL:
            handler._send_json({"status": "error", "message": "Model niet geladen"}, 500)
            return True
        handler._send_json({
            "status": "online",
            "params": GLOBAL_MODEL.params,
            "profile_metadata": {
                "resolution": GLOBAL_MODEL.profile.get("resolution", "15m"),
                "last_updated": GLOBAL_MODEL.profile.get("last_updated"),
                "dow_count": len(GLOBAL_MODEL.profile.get("profile_96_quarters", []))
            }
        })
        return True

    if path == "/api/model/retrain":
        if not GLOBAL_MODEL:
            handler._send_json({"status": "error", "message": "Model niet geladen"}, 500)
            return True
        days = 120
        try:
            res = GLOBAL_MODEL.retrain_from_openhems(days_history=days)
            handler._send_json(res)
        except Exception as e:
            handler._send_json({"status": "error", "message": str(e)}, 500)
        return True

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
        handler._send_json(res_dict)
        return True

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
        handler._send_json(prof_data)
        return True


    return False

def handle_post(handler, path: str, body: dict) -> bool:
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
            handler._send_json({"status": "success", "message": "Algoritme instellingen opgeslagen", "params": params})
        except Exception as e:
            handler._send_json({"status": "error", "message": str(e)}, 500)
        return True

    if path == "/api/model/recommendations/accept":
        try:
            recs_file = Path("/config/model_recommendations.json")
            if not recs_file.exists():
                handler._send_json({"status": "error", "message": "Geen aanbevelingen gevonden"}, 404)
                return True
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

            handler._send_json({"status": "success", "message": "Aanbevelingen geaccepteerd en modelparameters geactiveerd!"})
        except Exception as e:
            handler._send_json({"status": "error", "message": str(e)}, 500)
        return True

    if path == "/api/model/recommendations/reject":
        try:
            recs_file = Path("/config/model_recommendations.json")
            if recs_file.exists():
                recs_data = load_json(recs_file)
                recs_data["status"] = "rejected"
                recs_data["rejected_at"] = datetime.now(AMS_TZ).isoformat()
                save_json(recs_file, recs_data)
            handler._send_json({"status": "success", "message": "Aanbevelingen afgewezen; actieve parameters blijven ongewijzigd."})
        except Exception as e:
            handler._send_json({"status": "error", "message": str(e)}, 500)
        return True

    if path == "/api/model/retrain":
        if not GLOBAL_MODEL:
            handler._send_json({"status": "error", "message": "Model niet geladen"}, 500)
            return True
        try:
            days = int(body.get("days", 120)) if body else 120
            res = GLOBAL_MODEL.retrain_from_openhems(days_history=days)
            handler._send_json(res)
        except Exception as e:
            handler._send_json({"status": "error", "message": str(e)}, 500)
        return True


    # INFRASTRUCTURE: Test Home Assistant Core

    return False
