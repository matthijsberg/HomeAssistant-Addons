# Open HEMS: Self-Learning Calibration & Model Governance Router
import urllib
import json
import ssl
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from pathlib import Path
from typing import Dict, Any, List, Optional

from api.context import (
    ensure_active_canonical_plan, GLOBAL_MODEL, GLOBAL_DHW_MODEL
)
from api.secrets_store import (
    CONFIG_FILE, PARAMS_FILE, load_json, save_json, load_secrets
)
from layer2_calibration.parameter_history import ParameterHistoryManager

def handle_get(handler, path: str, qp: dict) -> bool:
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
                            r["proposed_value"] = act
                            r["drift_pct"] = 0.0
                    elif pid == "heating_modulation":
                        act = p_active.get("heat_pump", {}).get("modulation_curve", "2840 - 92·T")
                        r["current_value"] = act
                        r["proposed_value"] = act
                        r["drift_pct"] = 0.0
                    elif pid == "night_baseload":
                        act = p_active.get("unallocated", {}).get("night_baseload_floor_w")
                        if act is not None:
                            r["current_value"] = act
                            r["proposed_value"] = act
                            r["drift_pct"] = 0.0
                    elif pid == "dhw_standby":
                        act = p_active.get("dhw_tank", {}).get("standby_loss_w_per_k")
                        if act is not None:
                            r["current_value"] = act
                            r["proposed_value"] = act
                            r["drift_pct"] = 0.0
                    elif pid == "pv_yield_ratio":
                        act = p_active.get("solar", {}).get("performance_ratio_pct")
                        if act is not None:
                            r["current_value"] = act
                            r["proposed_value"] = act
                            r["drift_pct"] = 0.0
                    elif pid == "floor_capacity":
                        act = p_active.get("building", {}).get("floor_capacity_kwh_per_k")
                        if act is not None:
                            r["current_value"] = act
                            r["proposed_value"] = act
                            r["drift_pct"] = 0.0
                    elif pid == "c_wind":
                        act = p_active.get("building", {}).get("c_wind_w_per_k_ms")
                        if act is not None:
                            r["current_value"] = act
                            r["proposed_value"] = act
                            r["drift_pct"] = 0.0
                    elif pid == "c_solar":
                        act = p_active.get("building", {}).get("c_solar_passive")
                        if act is not None:
                            r["current_value"] = act
                            r["proposed_value"] = act
                            r["drift_pct"] = 0.0
            handler._send_json(recs_dict)
        else:
            handler._send_json({"status": "empty", "recommendations": []})
        return True

    if path == "/api/model/parameter-history":
        from layer2_calibration.parameter_history import ParameterHistoryManager
        qp = urllib.parse.parse_qs(urllib.parse.urlparse(handler.path).query)
        param_id = qp.get("parameter_id", [qp.get("id", ["building_ua"])])[0]
        timeframe = qp.get("timeframe", ["quarter"])[0]
        data = ParameterHistoryManager.get_parameter_history(param_id, timeframe)
        handler._send_json(data)
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

            for r in recs_data.get("recommendations", []):
                r["auto_applied"] = True
                p_id = r.get("id")
                prop_v = r.get("proposed_value")
                if p_id == "building_ua":
                    val = float(prop_v) if prop_v is not None else float(params.get("building", {}).get("ua_base_w_per_k", 292.5))
                    params.setdefault("building", {})["ua_base_w_per_k"] = val
                    r["current_value"] = val
                    r["proposed_value"] = val
                    r["drift_pct"] = 0.0
                elif p_id == "heating_modulation":
                    curve_str = str(prop_v) if prop_v is not None else "2840 - 92·T"
                    params.setdefault("heat_pump", {})["modulation_curve"] = curve_str
                    r["current_value"] = curve_str
                    r["proposed_value"] = curve_str
                    r["drift_pct"] = 0.0
                elif p_id == "night_baseload":
                    val = float(prop_v) if prop_v is not None else float(params.get("unallocated", {}).get("night_baseload_floor_w", 299.4))
                    params.setdefault("unallocated", {})["night_baseload_floor_w"] = val
                    r["current_value"] = val
                    r["proposed_value"] = val
                    r["drift_pct"] = 0.0
                elif p_id == "dhw_standby":
                    val = float(prop_v) if prop_v is not None else float(params.get("dhw_tank", {}).get("standby_loss_w_per_k", 2.38))
                    params.setdefault("dhw_tank", {})["standby_loss_w_per_k"] = val
                    r["current_value"] = val
                    r["proposed_value"] = val
                    r["drift_pct"] = 0.0
                elif p_id == "pv_yield_ratio":
                    val = float(prop_v) if prop_v is not None else float(params.get("solar", {}).get("performance_ratio_pct", 95.0))
                    params.setdefault("solar", {})["performance_ratio_pct"] = val
                    r["current_value"] = val
                    r["proposed_value"] = val
                    r["drift_pct"] = 0.0
                elif p_id == "floor_capacity":
                    val = float(prop_v) if prop_v is not None else float(params.get("building", {}).get("floor_capacity_kwh_per_k", 14.5))
                    params.setdefault("building", {})["floor_capacity_kwh_per_k"] = val
                    r["current_value"] = val
                    r["proposed_value"] = val
                    r["drift_pct"] = 0.0
                elif p_id == "c_wind":
                    val = float(prop_v) if prop_v is not None else float(params.get("building", {}).get("c_wind_w_per_k_ms", 0.208))
                    params.setdefault("building", {})["c_wind_w_per_k_ms"] = val
                    r["current_value"] = val
                    r["proposed_value"] = val
                    r["drift_pct"] = 0.0
                elif p_id == "c_solar":
                    val = float(prop_v) if prop_v is not None else float(params.get("building", {}).get("c_solar_passive", 0.056))
                    params.setdefault("building", {})["c_solar_passive"] = val
                    r["current_value"] = val
                    r["proposed_value"] = val
                    r["drift_pct"] = 0.0

            recs_data["status"] = "accepted"
            recs_data["accepted_at"] = datetime.now(AMS_TZ).isoformat()
            save_json(recs_file, recs_data)
            save_json(PARAMS_FILE, params)
            if GLOBAL_MODEL:
                GLOBAL_MODEL.params = params

            # Force immediate recalculation of canonical dispatch plan with newly accepted parameters
            ensure_active_canonical_plan(force_refresh=True)

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
