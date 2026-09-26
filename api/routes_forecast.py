# Open HEMS: Operational Forecast Router (DHW & Space Heating)
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
    ensure_active_canonical_plan, GLOBAL_CENTRAL_CACHE,
    GLOBAL_MODEL, GLOBAL_DHW_MODEL, GLOBAL_COLLECTOR
)
from api.secrets_store import (
    CONFIG_FILE, PARAMS_FILE, load_json, save_json, load_secrets
)
from layer3_scheduling.dhw_specs import DhwTankSpec
from integrations.homeassistant.client import (
    get_ha_client_config, get_ha_states_map
)
from api.energy_feed import (
    AMS_TZ, format_slot_label, format_chart_timeline_labels, calculate_poa_solar_kw, fetch_recent_telemetry_history
)
from layer3_scheduling.plan_decision_evaluator import evaluate_and_apply_dhw_run_merger
from layer3_scheduling.space_heating_policy import SpaceHeatingPolicy
from layer3_scheduling.battery_policy import (
    BatteryPolicy, BatterySpec, extract_battery_overlay_ranges, BatterySlotResult
)

def handle_get(handler, path: str, qp: dict) -> bool:
    if path.startswith("/api/model/heating-forecast"):
        qp = urllib.parse.parse_qs(urllib.parse.urlparse(handler.path).query)
        res_mode = qp.get("resolution", ["15m"])[0]
        horizon_mode = qp.get("horizon", ["24h"])[0]
        is_48h = (horizon_mode == "48h")
        is_15m = (res_mode == "15m")
        step_mins = 15 if is_15m else 60
        interval_h = step_mins / 60.0
        n_future_slots = (192 if is_15m else 48) if is_48h else (96 if is_15m else 24)

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
            n_sim = min(len(h_summary.slots), n_future_slots * (4 if not is_15m else 1))
            raw_labels = [plan.slots[i].time_label for i in range(n_sim)] if plan.slots else [f"T+{i}" for i in range(n_sim)]
            raw_out_temps = [round(s.outdoor_temp_c, 2) for s in h_summary.slots[:n_sim]]
            raw_in_temps = [round(s.room_temp_c, 1) for s in h_summary.slots[:n_sim]]
            raw_floor_temps = [round(s.floor_temp_c, 1) for s in h_summary.slots[:n_sim]]
            raw_cops = [round(s.cop, 2) for s in h_summary.slots[:n_sim]]
            raw_th_loss_kw = [round(s.heat_loss_kw, 2) for s in h_summary.slots[:n_sim]]
            raw_el_power_kw = [round(s.heating_kw_el, 2) for s in h_summary.slots[:n_sim]]
            raw_costs_eur = [
                round(s.heating_kw_el * 0.25 * (plan.slots[i].price_eur if i < len(plan.slots) else 0.25), 3)
                for i, s in enumerate(h_summary.slots[:n_sim])
            ]
            raw_unh = (h_summary.unheated_room_temps_c[:n_sim] if (h_summary and h_summary.unheated_room_temps_c) else raw_in_temps)
            raw_in_p05 = (h_summary.room_temps_p05_c[:n_sim] if (h_summary and h_summary.room_temps_p05_c) else raw_in_temps)
            raw_in_p95 = (h_summary.room_temps_p95_c[:n_sim] if (h_summary and h_summary.room_temps_p95_c) else raw_in_temps)
            raw_unh_p05 = (h_summary.unheated_temps_p05_c[:n_sim] if (h_summary and h_summary.unheated_temps_p05_c) else raw_in_temps)
            raw_unh_p95 = (h_summary.unheated_temps_p95_c[:n_sim] if (h_summary and h_summary.unheated_temps_p95_c) else raw_in_temps)

            tot_th = h_summary.total_heating_kwh_th
            tot_el = h_summary.total_heating_kwh_el
            tot_cost = round(sum(raw_costs_eur), 2)
            t_setpoint = h_summary.target_room_temp_c
            t_start_threshold = h_summary.min_comfort_room_c
            t_active = h_summary.is_heating_season
            t_status = h_summary.season_status_label

            if not is_15m:
                # Aggregate to 1-hour slots
                out_temps = []
                in_temps = []
                unheated_temps = []
                in_p05 = []
                in_p95 = []
                unh_p05 = []
                unh_p95 = []
                floor_temps = []
                cops = []
                th_loss_kw = []
                el_power_kw = []
                costs_eur = []
                n_hours = min(n_future_slots, n_sim // 4)
                for h_i in range(n_hours):
                    idx = h_i * 4
                    out_temps.append(round(sum(raw_out_temps[idx:idx+4]) / 4.0, 1))
                    in_temps.append(round(sum(raw_in_temps[idx:idx+4]) / 4.0, 1))
                    unheated_temps.append(round(sum(raw_unh[idx:idx+4]) / 4.0, 1))
                    in_p05.append(round(sum(raw_in_p05[idx:idx+4]) / 4.0, 1))
                    in_p95.append(round(sum(raw_in_p95[idx:idx+4]) / 4.0, 1))
                    unh_p05.append(round(sum(raw_unh_p05[idx:idx+4]) / 4.0, 1))
                    unh_p95.append(round(sum(raw_unh_p95[idx:idx+4]) / 4.0, 1))
                    floor_temps.append(round(sum(raw_floor_temps[idx:idx+4]) / 4.0, 1))
                    cops.append(round(sum(raw_cops[idx:idx+4]) / 4.0, 2))
                    th_loss_kw.append(round(sum(raw_th_loss_kw[idx:idx+4]) / 4.0, 2))
                    el_power_kw.append(round(sum(raw_el_power_kw[idx:idx+4]) / 4.0, 2))
                    costs_eur.append(round(sum(raw_costs_eur[idx:idx+4]), 3))

                fc_dts = [base_dt + timedelta(hours=h_i) for h_i in range(n_hours)]
                all_dts = [hp["dt"] for hp in hist_pts] + fc_dts
                all_labels = format_chart_timeline_labels(all_dts, is_15m=False, now_idx=len(hist_pts))
            else:
                n_q = min(n_future_slots, len(raw_labels))
                out_temps = raw_out_temps[:n_q]
                in_temps = raw_in_temps[:n_q]
                unheated_temps = raw_unh[:n_q]
                in_p05 = raw_in_p05[:n_q]
                in_p95 = raw_in_p95[:n_q]
                unh_p05 = raw_unh_p05[:n_q]
                unh_p95 = raw_unh_p95[:n_q]
                floor_temps = raw_floor_temps[:n_q]
                cops = raw_cops[:n_q]
                th_loss_kw = raw_th_loss_kw[:n_q]
                el_power_kw = raw_el_power_kw[:n_q]
                costs_eur = raw_costs_eur[:n_q]

                fc_dts = [base_dt + timedelta(minutes=15 * q_i) for q_i in range(n_q)]
                all_dts = [hp["dt"] for hp in hist_pts] + fc_dts
                all_labels = format_chart_timeline_labels(all_dts, is_15m=True, now_idx=len(hist_pts))
        else:
            n_sim = 0
            all_labels, out_temps, in_temps, floor_temps, cops, th_loss_kw, el_power_kw, costs_eur = [], [], [], [], [], [], [], []
            unheated_temps, in_p05, in_p95, unh_p05, unh_p95 = [], [], [], [], []
            tot_th, tot_el, tot_cost = 0.0, 0.0, 0.0
            t_setpoint = 20.0
            t_start_threshold = 19.6
            t_active = True
            t_status = "Stookseizoen Actief (Centrale PlanStore)"

        # Extract lockout and heating ranges for visual overlay (offset by history_count)
        from layer3_scheduling.peak_detection import extract_plan_spitsblok_ranges, extract_plan_heating_ranges, extract_plan_soft_advice_ranges
        future_slots_for_overlay = plan.slots[:n_sim] if (plan and plan.slots) else []
        forced_off_ranges = extract_plan_spitsblok_ranges(future_slots_for_overlay, history_count=len(hist_pts), is_15m=is_15m)
        advised_off_ranges = extract_plan_soft_advice_ranges(future_slots_for_overlay, history_count=len(hist_pts), is_15m=is_15m)
        heating_ranges = extract_plan_heating_ranges(future_slots_for_overlay, history_count=len(hist_pts), is_15m=is_15m, domain="space_heating")

        # Build dynamic, human-readable decision explanation for space heating
        run_strs = [f"{hr.get('start_label', '')}–{hr.get('end_label', '')}" for hr in heating_ranges]
        lockout_strs = [f"{fo.get('start_label', '')}–{fo.get('end_label', '')}" for fo in forced_off_ranges]

        has_evening_run = any(any(h in hr.get('start_label', '') for h in ["18:", "19:", "20:", "21:", "22:"]) for hr in heating_ranges)
        has_night_run = any(any(h in hr.get('start_label', '') for h in ["01:", "02:", "03:", "04:", "05:"]) for hr in heating_ranges)

        comfort_reasons = []
        if has_evening_run:
            comfort_reasons.append("Avondherstel: na de avondspits koelt de woning af door wegvallende zon en dalende buitentemperatuur. Zodra de binnentemperatuur naar 19,6 °C daalt, start een rustige stookcyclus (min. 2u runtijd) om de woonkamer stabiel op 20,0 °C te houden.")
        
        comfort_reasons.append("Geen middagstook: om 14:00–16:00 is de buitentemperatuur hoog (~19 °C) en het warmteverlies minimaal. Vloerverwarming heeft een minimale runtijd van 2 uur (~9 kWh thermisch); stoken op een zonnige middag zou leiden tot oververhitting (>21,5 °C), waarbij die buffer over de 5 tussenliggende uren grotendeels weglekt.")
        
        if has_night_run:
            comfort_reasons.append("Nachtvallei Pre-heat: benutting van het laagste nachttarief om de betondekvloer (13,2 kWh/K) thermisch voor te laden, zodat de woning de dure ochtendpiek passief overbrugt.")

        if not heating_ranges:
            comfort_reasons = ["Geen stookruns nodig: binnentemperatuur blijft stabiel boven de comfortgrens (19,6 °C) door milde buitentemperatuur en passieve zonnewinst."]

        heating_explanation = {
            "title": "Ruimteverwarming: Stookstrategie & Vloerbuffer Redenering",
            "status_badge": "Stookseizoen Actief" if t_active else "Standby",
            "comfort_text": " ".join(comfort_reasons),
            "planned_runs_text": ", ".join(run_strs) if run_strs else "Geen actieve runs gepland",
            "buffer_text": "Betonnen dekvloer (13,2 kWh/K) met 3–4u thermische vertraging; buffert energie voor piekoverbrugging.",
            "lockout_text": f"Spitsblokkades: {', '.join(lockout_strs)}" if lockout_strs else "Geen spitsblokkades"
        }

        handler._send_json({
            "resolution": res_mode,
            "labels": all_labels,
            "outdoor_temps_c": hist_outdoor + out_temps,
            "indoor_temps_c": hist_indoor + in_temps,
            "unheated_temps_c": hist_indoor + unheated_temps,
            "indoor_temps_p05_c": hist_indoor + in_p05,
            "indoor_temps_p95_c": hist_indoor + in_p95,
            "unheated_temps_p05_c": hist_indoor + unh_p05,
            "unheated_temps_p95_c": hist_indoor + unh_p95,
            "floor_temps_c": hist_floor + floor_temps,
            "cops": hist_cops + cops,
            "thermal_loss_kw": hist_th_loss + th_loss_kw,
            "electrical_kw": hist_el_kw + el_power_kw,
            "costs_eur": hist_costs + costs_eur,
            "history_count": len(hist_pts),
            "forced_off_ranges": forced_off_ranges,
            "advised_off_ranges": advised_off_ranges,
            "heating_ranges": heating_ranges,
            "total_thermal_kwh": tot_th,
            "total_electrical_kwh": tot_el,
            "total_cost_eur": tot_cost,
            "thermostat_setpoint_c": t_setpoint,
            "thermostat_start_threshold_c": t_start_threshold,
            "thermostat_active": t_active,
            "thermostat_status_label": t_status,
            "decision_explanation": heating_explanation
        })
        return True

    if path.startswith("/api/model/dhw-status"):
        qp = urllib.parse.parse_qs(urllib.parse.urlparse(handler.path).query)
        res_mode = qp.get("resolution", ["15m"])[0]
        horizon_mode = qp.get("horizon", ["24h"])[0]
        is_48h = (horizon_mode == "48h")
        hours_sim = 48 if is_48h else 24
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
        planner_name = "arbiter"
        if plan and plan.dhw_summary and plan.dhw_summary.decision_details:
            planner_name = str(plan.dhw_summary.decision_details.get("planner", "arbiter"))

        if GLOBAL_DHW_MODEL:
            # Safe defaults for plan metadata
            planned_mode = plan.dhw_summary.planned_mode if (plan and plan.dhw_summary) else "normal"
            planned_reason = plan.dhw_summary.planned_mode_label if (plan and plan.dhw_summary) else "Centrale dispatch planning"
            cached_dyn_peaks = plan.dynamic_peaks if plan else []
            plan_details = plan.dhw_summary.decision_details if (plan and plan.dhw_summary and plan.dhw_summary.decision_details) else {}

            # Check if plan already carries authoritative trajectory from optimizer adapter (Dumb View Invariant #1)
            has_optimizer_traj = (
                planner_name == "optimizer"
                and bool(plan_details)
                and "trajectory" in plan_details
            )

            if has_optimizer_traj:
                opt_traj = plan_details["trajectory"]
                opt_unh_traj = plan_details.get("unheated_trajectory", {})
                raw_temps = opt_traj.get("temperatures_c", [])
                raw_p05 = opt_traj.get("temperatures_p05_c", raw_temps)
                raw_p95 = opt_traj.get("temperatures_p95_c", raw_temps)
                raw_dem = opt_traj.get("demand_kwh_th", [])
                if len(raw_dem) < len(raw_temps):
                    raw_dem = list(raw_dem) + [0.0] * (len(raw_temps) - len(raw_dem))

                raw_unh_temps = opt_unh_traj.get("temperatures_c", [])
                raw_unh_p05 = opt_unh_traj.get("temperatures_p05_c", raw_unh_temps)
                raw_unh_p95 = opt_unh_traj.get("temperatures_p95_c", raw_unh_temps)

                # Slice by target_slots (96 for 24h, 192 for 48h)
                target_slots = hours_sim * 4
                raw_temps = raw_temps[:target_slots]
                raw_p05 = raw_p05[:target_slots]
                raw_p95 = raw_p95[:target_slots]
                raw_dem = raw_dem[:target_slots]
                raw_unh_temps = raw_unh_temps[:target_slots]
                raw_unh_p05 = raw_unh_p05[:target_slots]
                raw_unh_p95 = raw_unh_p95[:target_slots]

                start_min = (now_ams.minute // 15) * 15 if is_15m else 0
                base_sim_dt = now_ams.replace(minute=start_min, second=0, microsecond=0)
                raw_dts = [base_sim_dt + timedelta(minutes=15 * i) for i in range(len(raw_temps))]
                raw_lbls = format_chart_timeline_labels(raw_dts, is_15m=True, now_idx=0)
                traj = {
                    "labels": raw_lbls,
                    "temperatures_c": raw_temps,
                    "temperatures_p05_c": raw_p05,
                    "temperatures_p95_c": raw_p95,
                    "demand_kwh_th": raw_dem,
                    "morning_dip_temp_c": plan_details.get("morning_dip_c", 40.0),
                    "morning_dip_time": plan_details.get("morning_dip_time", "09:30"),
                }
                unheated_traj = {
                    "temperatures_c": raw_unh_temps,
                    "temperatures_p05_c": raw_unh_p05,
                    "temperatures_p95_c": raw_unh_p95,
                    "morning_dip_temp_c": plan_details.get("morning_dip_c", 40.0),
                    "morning_dip_time": plan_details.get("morning_dip_time", "09:30"),
                }
            else:
                # Retrieve planned slots & dispatch parameters directly from authoritative CanonicalDispatchPlan
                cached_slots = [i for i, s in enumerate(plan.slots) if s.dhw_kw > 0]
                spec = DhwTankSpec.from_config(load_json(CONFIG_FILE))
                c_power = plan.dhw_summary.power_kw if plan.dhw_summary else spec.heat_pump_electric_kw
                c_target = plan.dhw_summary.target_temp_c if plan.dhw_summary else spec.target_setpoint_c

                start_min = (now_ams.minute // 15) * 15 if is_15m else 0
                base_sim_dt = now_ams.replace(minute=start_min, second=0, microsecond=0)
                traj = GLOBAL_DHW_MODEL.simulate_trajectory(
                    t_live,
                    base_sim_dt,
                    hours_ahead=hours_sim,
                    heat_pump_schedule_slots=cached_slots,
                    target_temp_c=c_target,
                    heat_pump_power_kw=c_power
                )

                # Counterfactual trajectory WITHOUT night recharge (pure passive standby & tap demand)
                unheated_traj = GLOBAL_DHW_MODEL.simulate_trajectory(
                    t_live,
                    base_sim_dt,
                    hours_ahead=hours_sim,
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
                    n_h = min(hours_sim, len(raw_lbls) // 4)
                    h_dts = [base_sim_dt + timedelta(hours=h_i) for h_i in range(n_h)]
                    h_labels = format_chart_timeline_labels(h_dts, is_15m=False, now_idx=0)
                    h_temps, h_p05, h_p95, h_demand = [], [], [], []
                    h_unh_temps, h_unh_p05, h_unh_p95 = [], [], []
                    for h_i in range(n_h):
                        idx = h_i * 4
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
                    q_dts = [base_sim_dt + timedelta(minutes=15 * q_i) for q_i in range(len(raw_lbls))]
                    traj["labels"] = format_chart_timeline_labels(q_dts, is_15m=True, now_idx=0)

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

            from layer3_scheduling.peak_detection import extract_plan_spitsblok_ranges, extract_plan_heating_ranges, extract_plan_soft_advice_ranges
            dhw_plan_slots = plan.slots[:int(hours_sim*4)] if (plan and plan.slots) else []
            forced_off_ranges = extract_plan_spitsblok_ranges(dhw_plan_slots, history_count=len(hist_pts), is_15m=is_15m)
            advised_off_ranges = extract_plan_soft_advice_ranges(dhw_plan_slots, history_count=len(hist_pts), is_15m=is_15m)
            heating_ranges = extract_plan_heating_ranges(dhw_plan_slots, history_count=len(hist_pts), is_15m=is_15m, domain="dhw")

            handler._send_json({
                "status": "online",
                "planner": planner_name,
                "resolution": res_mode,
                "decision": decision,
                "trajectory": traj,
                "unheated_trajectory": unheated_traj,
                "forced_off_ranges": forced_off_ranges,
                "advised_off_ranges": advised_off_ranges,
                "heating_ranges": heating_ranges,
                "history_count": len(hist_pts)
            })
        else:
            handler._send_json({"status": "error", "message": "DHW model niet geladen"}, 500)
        return True

    if path.startswith("/api/model/battery-status"):
        qp = urllib.parse.parse_qs(urllib.parse.urlparse(handler.path).query)
        res_mode = qp.get("resolution", ["15m"])[0]
        horizon_mode = qp.get("horizon", ["24h"])[0]
        is_48h = (horizon_mode == "48h")
        is_15m = (res_mode == "15m")
        hours_sim = 48 if is_48h else 24
        total_slots = hours_sim * (4 if is_15m else 1)
        step_mins = 15 if is_15m else 60

        plan = ensure_active_canonical_plan()
        if not plan or not plan.slots:
            handler._send_json({"status": "error", "message": "Geen actief plan beschikbaar"}, 500)
            return True

        # 4-Layer SOT Invariant: Optimize over full canonical horizon (up to 48h / 192 slots)
        # Slicing for 24h vs 48h is done purely as a Dumb View over this canonical optimization.
        max_canonical_slots = min(len(plan.slots), 192)
        full_slots = plan.slots[:max_canonical_slots]

        unalloc = [s.unallocated_kw for s in full_slots]
        dhw = [s.dhw_kw for s in full_slots]
        heat = [s.heating_kw for s in full_slots]
        solar = [s.solar_kw for s in full_slots]
        prices = [s.price_eur for s in full_slots]
        export_prices = [max(0.0, round((p / 1.21) - 0.11085 - 0.0121 - 0.00605, 4)) for p in prices]
        residual_15m = [round(u + d + h - s, 3) for u, d, h, s in zip(unalloc, dhw, heat, solar)]

        forced_lockouts = {
            i for i, s in enumerate(full_slots)
            if s.mode_code == "forced_off" or getattr(s, "is_lockout", False)
        }

        live_soc = 50.0
        try:
            ha_url, ha_tok = get_ha_client_config()
            if ha_tok and ha_url:
                ctx = ssl.create_default_context()
                ctx.check_hostname = False
                ctx.verify_mode = ssl.CERT_NONE
                req = urllib.request.Request(
                    f"{ha_url}/api/states/sensor.battery_state_of_charge",
                    headers={"Authorization": f"Bearer {ha_tok}", "Content-Type": "application/json"}
                )
                with urllib.request.urlopen(req, timeout=2, context=ctx) as r:
                    st = json.loads(r.read().decode())
                    live_soc = float(st.get("state", 50.0))
        except Exception:
            live_soc = 50.0

        spec = BatterySpec(
            capacity_kwh=15.0,
            usable_capacity_kwh=13.5,
            max_charge_kw=5.0,
            max_discharge_kw=5.0,
            reserve_lookahead_slots=max_canonical_slots,
            valley_lookahead_slots=max_canonical_slots
        )

        summary_15m = BatteryPolicy.optimize(
            residual_demand_kw=residual_15m,
            import_prices=prices,
            export_prices=export_prices,
            spec=spec,
            initial_soc_pct=live_soc,
            step_hours=0.25,
            forced_off_indices=forced_lockouts
        )

        now_ams = datetime.now(AMS_TZ)
        start_min = (now_ams.minute // 15) * 15 if is_15m else 0
        base_dt = now_ams.replace(minute=start_min, second=0, microsecond=0)

        # Prepend 1 hour of history for parity with all other forecast charts
        hist_pts = fetch_recent_telemetry_history(is_15m, base_dt)
        hist_count = len(hist_pts)

        hist_dts = [hp["dt"] for hp in hist_pts]
        future_dts = [base_dt + timedelta(minutes=step_mins * i) for i in range(total_slots)]
        all_dts = hist_dts + future_dts
        labels = format_chart_timeline_labels(all_dts, is_15m=is_15m, now_idx=hist_count)

        if is_15m:
            disp_slots = summary_15m.slots[:total_slots]
        else:
            disp_slots = []
            for h_i in range(total_slots):
                q_slice = summary_15m.slots[h_i * 4:(h_i + 1) * 4]
                if q_slice:
                    avg_p = sum(s.power_kw for s in q_slice) / len(q_slice)
                    if avg_p > 0.05:
                        charge_modes = [s.mode_code for s in q_slice if s.power_kw > 0.01]
                        mode = max(set(charge_modes), key=charge_modes.count) if charge_modes else "CHARGE_SOLAR"
                        label = "Zonneladen" if mode == "CHARGE_SOLAR" else "Netladen (Dal)"
                    elif avg_p < -0.05:
                        dis_modes = [s.mode_code for s in q_slice if s.power_kw < -0.01]
                        mode = max(set(dis_modes), key=dis_modes.count) if dis_modes else "DISCHARGE_PEAK"
                        label = "Spitsontlasting" if mode == "DISCHARGE_PEAK" else "Huisontlasting"
                    elif any(s.mode_code == "HOLD_RESERVE" for s in q_slice):
                        mode = "HOLD_RESERVE"
                        label = "Piekreservering"
                    else:
                        mode = "STANDBY"
                        label = "Standby"
                    end_soc_pct = q_slice[-1].soc_pct
                    end_soc_kwh = q_slice[-1].soc_kwh
                    tot_cost = sum(s.cost_impact_eur for s in q_slice)
                    disp_slots.append(BatterySlotResult(
                        slot_idx=h_i,
                        power_kw=round(avg_p, 3),
                        mode_code=mode,
                        mode_label=label,
                        soc_pct=end_soc_pct,
                        soc_kwh=end_soc_kwh,
                        cost_impact_eur=round(tot_cost, 4)
                    ))

        soc_pct_list = [round(s.soc_pct, 1) for s in disp_slots]
        soc_p05_list = [round(s.soc_p05_pct, 1) for s in disp_slots]
        soc_p95_list = [round(s.soc_p95_pct, 1) for s in disp_slots]
        soc_kwh_list = [round(s.soc_kwh, 2) for s in disp_slots]
        power_kw_list = [round(s.power_kw, 2) for s in disp_slots]
        charge_kw_list = [round(max(0.0, s.power_kw), 2) for s in disp_slots]
        discharge_kw_list = [round(max(0.0, -s.power_kw), 2) for s in disp_slots]
        deficit_disp = [round(s.deficit_kw, 2) for s in disp_slots]
        solar_charge_disp = [round(s.power_kw, 2) if s.mode_code == "CHARGE_SOLAR" and s.power_kw > 0 else 0.0 for s in disp_slots]
        grid_charge_disp = [round(s.power_kw, 2) if s.mode_code == "CHARGE_GRID" and s.power_kw > 0 else 0.0 for s in disp_slots]

        init_soc_pct = summary_15m.initial_soc_pct
        init_soc_kwh = round(init_soc_pct / 100.0 * 15.0, 2)

        if is_15m:
            disp_prices = [round(p, 4) for p in prices[:total_slots]]
            disp_export_prices = [round(p, 4) for p in export_prices[:total_slots]]
        else:
            disp_prices = [round(sum(prices[h*4:(h+1)*4]) / 4.0, 4) for h in range(total_slots)]
            disp_export_prices = [round(sum(export_prices[h*4:(h+1)*4]) / 4.0, 4) for h in range(total_slots)]

        hist_prices = [round(prices[0] if prices else 0.25, 4)] * hist_count
        hist_export_prices = [round(export_prices[0] if export_prices else 0.0, 4)] * hist_count

        all_import_prices = hist_prices + disp_prices
        all_export_prices = hist_export_prices + disp_export_prices

        hist_soc_pct = [init_soc_pct] * hist_count
        hist_soc_kwh = [init_soc_kwh] * hist_count
        hist_power_kw = [0.0] * hist_count
        hist_charge_kw = [0.0] * hist_count
        hist_discharge_kw = [0.0] * hist_count
        hist_deficit_kw = [0.0] * hist_count
        hist_solar_charge = [0.0] * hist_count
        hist_grid_charge = [0.0] * hist_count

        hist_timeline = []
        for i in range(hist_count):
            hist_timeline.append({
                "time": labels[i] if i < len(labels) else f"T-{hist_count-i}",
                "mode": "STANDBY",
                "label": f"Historie ({labels[i]})",
                "color": "#1E293B",
                "css_pattern": "none",
                "power_kw": 0.0,
                "soc_pct": init_soc_pct,
                "description": f"Historisch uur vóór simulatiestart: Batterij gereed · SoC {init_soc_pct:.1f}%"
            })

        ranges = extract_battery_overlay_ranges(summary_15m.slots[:(hours_sim * 4)], history_count=hist_count, is_15m=is_15m)

        mode_meta = {
            "CHARGE_SOLAR": {"color": "#F59E0B", "css": "repeating-linear-gradient(45deg, #F59E0B, #F59E0B 2px, #D97706 2px, #D97706 4px)", "name": "Zonneladen ☀️"},
            "CHARGE_GRID": {"color": "#8B5CF6", "css": "repeating-linear-gradient(45deg, #8B5CF6, #8B5CF6 2px, #7C3AED 2px, #7C3AED 4px)", "name": "Netladen (Dal) 🔌"},
            "DISCHARGE_PEAK": {"color": "#10B981", "css": "repeating-linear-gradient(45deg, #10B981, #10B981 2px, #059669 2px, #059669 4px)", "name": "Spitsontlasting ⚡"},
            "DISCHARGE_BUFFER": {"color": "#10B981", "css": "none", "name": "Huisontlasting (Nul-op-Meter) 🔋"},
            "HOLD_RESERVE": {"color": "#3B82F6", "css": "none", "name": "Piekreservering 🛡️"},
            "STANDBY": {"color": "#1E293B", "css": "none", "name": "Standby ⏸️"}
        }

        timeline_items = []
        for i, s in enumerate(disp_slots):
            meta = mode_meta.get(s.mode_code, mode_meta["STANDBY"])
            t_idx = i + hist_count
            timeline_items.append({
                "time": labels[t_idx] if t_idx < len(labels) else f"T+{i}",
                "mode": s.mode_code,
                "label": meta["name"],
                "color": meta["color"],
                "css_pattern": meta["css"],
                "power_kw": s.power_kw,
                "soc_pct": s.soc_pct,
                "description": f"{meta['name']}: {abs(s.power_kw):.2f} kW · SoC {s.soc_pct:.1f}%"
            })

        explanation_parts = []
        if summary_15m.total_discharged_kwh > 0.5:
            explanation_parts.append(f"Nul-op-de-meter ontlading: De accu levert {summary_15m.total_discharged_kwh:.1f} kWh ter voorkoming van dure netafname (€0,25–€0,45/kWh), doorgaand tot de 10% buffergrens.")
        if summary_15m.total_charged_solar_kwh > 0.5:
            explanation_parts.append(f"Zonne-absorptie: {summary_15m.total_charged_solar_kwh:.1f} kWh gratis PV-overschot opgeslagen.")
        if summary_15m.total_charged_grid_kwh > 0.5:
            explanation_parts.append(f"Daltarief netlading: {summary_15m.total_charged_grid_kwh:.1f} kWh geladen tegen bodemprijzen.")
        else:
            explanation_parts.append("Geen netlading vereist: zon dekt het laadtarief overdag optimaal.")
        if summary_15m.total_deficit_kwh > 0.1:
            explanation_parts.append(f"Residuele netafname: {summary_15m.total_deficit_kwh:.1f} kWh kan niet uit de accu worden gedekt en wordt ingekocht.")

        expl_text = " ".join(explanation_parts)

        handler._send_json({
            "status": "success",
            "resolution": res_mode,
            "horizon": horizon_mode,
            "labels": labels,
            "history_count": hist_count,
            "prices": all_import_prices,
            "export_prices": all_export_prices,
            "trajectory": {
                "soc_pct": hist_soc_pct + soc_pct_list,
                "soc_p05_pct": hist_soc_pct + soc_p05_list,
                "soc_p95_pct": hist_soc_pct + soc_p95_list,
                "soc_kwh": hist_soc_kwh + soc_kwh_list,
                "power_kw": hist_power_kw + power_kw_list,
                "charge_power_kw": hist_charge_kw + charge_kw_list,
                "discharge_power_kw": hist_discharge_kw + discharge_kw_list,
                "deficit_kw": hist_deficit_kw + deficit_disp,
                "solar_charge_kw": hist_solar_charge + solar_charge_disp,
                "grid_charge_kw": hist_grid_charge + grid_charge_disp,
                "import_prices": all_import_prices,
                "export_prices": all_export_prices
            },
            "charge_ranges": ranges["charge"],
            "discharge_ranges": ranges["discharge"],
            "hold_ranges": ranges["hold"],
            "battery_mode_timeline": hist_timeline + timeline_items,
            "kpi_cards": {
                "solar_charged_kwh": summary_15m.total_charged_solar_kwh,
                "grid_charged_kwh": summary_15m.total_charged_grid_kwh,
                "discharged_kwh": summary_15m.total_discharged_kwh,
                "deficit_kwh": summary_15m.total_deficit_kwh,
                "autonomy_pct": summary_15m.autonomy_pct,
                "net_saving_eur": summary_15m.net_financial_saving_eur,
                "initial_soc_pct": summary_15m.initial_soc_pct,
                "final_soc_pct": summary_15m.final_soc_pct,
                "min_projected_soc_pct": summary_15m.min_projected_soc_pct,
                "max_projected_soc_pct": summary_15m.max_projected_soc_pct
            },
            "decision": {
                "box_title": "Thuisbatterij (15 kWh): Dispatch-Redenering & Operating Envelope",
                "explanation": expl_text,
                "autonomy_pct": summary_15m.autonomy_pct,
                "total_deficit_kwh": summary_15m.total_deficit_kwh,
                "total_discharged_kwh": summary_15m.total_discharged_kwh,
                "total_charged_kwh": round(summary_15m.total_charged_solar_kwh + summary_15m.total_charged_grid_kwh, 2),
                "mode_code": disp_slots[0].mode_code if disp_slots else "STANDBY"
            }
        })
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


    return False
