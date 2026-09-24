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
    AMS_TZ, format_slot_label, calculate_poa_solar_kw, fetch_recent_telemetry_history
)
from layer3_scheduling.plan_decision_evaluator import evaluate_and_apply_dhw_run_merger
from layer3_scheduling.space_heating_policy import SpaceHeatingPolicy

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
                labels = []
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
                prev_h_dt = None
                for h_i in range(min(n_future_slots, n_sim // 4)):
                    idx = h_i * 4
                    h_dt = base_dt + timedelta(hours=h_i)
                    labels.append(format_slot_label(h_dt, prev_h_dt, h_i == 0, False))
                    prev_h_dt = h_dt

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
            else:
                labels = raw_labels[:n_future_slots]
                out_temps = raw_out_temps[:n_future_slots]
                in_temps = raw_in_temps[:n_future_slots]
                unheated_temps = raw_unh[:n_future_slots]
                in_p05 = raw_in_p05[:n_future_slots]
                in_p95 = raw_in_p95[:n_future_slots]
                unh_p05 = raw_unh_p05[:n_future_slots]
                unh_p95 = raw_unh_p95[:n_future_slots]
                floor_temps = raw_floor_temps[:n_future_slots]
                cops = raw_cops[:n_future_slots]
                th_loss_kw = raw_th_loss_kw[:n_future_slots]
                el_power_kw = raw_el_power_kw[:n_future_slots]
                costs_eur = raw_costs_eur[:n_future_slots]
        else:
            n_sim = 0
            labels, out_temps, in_temps, floor_temps, cops, th_loss_kw, el_power_kw, costs_eur = [], [], [], [], [], [], [], []
            unheated_temps, in_p05, in_p95, unh_p05, unh_p95 = [], [], [], [], []
            tot_th, tot_el, tot_cost = 0.0, 0.0, 0.0
            t_setpoint = 20.0
            t_start_threshold = 19.6
            t_active = True
            t_status = "Stookseizoen Actief (Centrale PlanStore)"

        # Extract lockout and heating ranges for visual overlay (offset by history_count)
        from layer3_scheduling.peak_detection import extract_plan_spitsblok_ranges, extract_plan_heating_ranges
        future_slots_for_overlay = plan.slots[:n_sim] if (plan and plan.slots) else []
        forced_off_ranges = extract_plan_spitsblok_ranges(future_slots_for_overlay, history_count=len(hist_pts), is_15m=is_15m)
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
            "labels": hist_labels + labels,
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

                base_sim_dt = now_ams
                raw_lbls = [
                    format_slot_label(base_sim_dt + timedelta(minutes=15 * i), None, i == 0, True)
                    for i in range(len(raw_temps))
                ]
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

                base_sim_dt = now_ams
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
                    h_labels, h_temps, h_p05, h_p95, h_demand = [], [], [], [], []
                    h_unh_temps, h_unh_p05, h_unh_p95 = [], [], []
                    prev_h_dt = None
                    for h_i in range(min(hours_sim, len(raw_lbls) // 4)):
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

            from layer3_scheduling.peak_detection import extract_plan_spitsblok_ranges, extract_plan_heating_ranges
            dhw_plan_slots = plan.slots[:int(hours_sim*4)] if (plan and plan.slots) else []
            forced_off_ranges = extract_plan_spitsblok_ranges(dhw_plan_slots, history_count=len(hist_pts), is_15m=is_15m)
            heating_ranges = extract_plan_heating_ranges(dhw_plan_slots, history_count=len(hist_pts), is_15m=is_15m, domain="dhw")

            handler._send_json({
                "status": "online",
                "planner": planner_name,
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


    return False
