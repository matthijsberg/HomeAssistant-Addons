# Open HEMS: Physical Modeling & Calibration Router
import json
import math
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
    GLOBAL_MODEL, GLOBAL_DHW_MODEL, GLOBAL_COLLECTOR,
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

        handler._send_json({
            "resolution": res_mode,
            "labels": hist_labels + labels,
            "outdoor_temps_c": hist_outdoor + out_temps,
            "indoor_temps_c": hist_indoor + in_temps,
            "floor_temps_c": hist_floor + floor_temps,
            "cops": hist_cops + cops,
            "thermal_loss_kw": hist_th_loss + th_loss_kw,
            "electrical_kw": hist_el_kw + el_power_kw,
            "costs_eur": hist_costs + costs_eur,
            "history_count": len(hist_pts),
            "total_thermal_kwh": tot_th,
            "total_electrical_kwh": tot_el,
            "total_cost_eur": tot_cost,
            "thermostat_setpoint_c": t_setpoint,
            "thermostat_start_threshold_c": t_start_threshold,
            "thermostat_active": t_active,
            "thermostat_status_label": t_status
        })
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
            spits_temps = []
            for s_i, u_t in enumerate(raw_unh):
                s_dt = base_sim_dt + timedelta(minutes=15 * s_i)
                if 18 <= s_dt.hour <= 22 and s_dt.date() == now_ams.date():
                    spits_temps.append(u_t)
            unh_spits_temp = min(spits_temps) if spits_temps else max(38.0, round(t_live - 4.5, 1))

            # Physics & Tariffs (350L vat = 0.407 kWh_th / K)
            c_tank = 0.407
            cop_50 = 2.85
            cop_60 = 2.15

            cur_price = GLOBAL_CENTRAL_CACHE.get("current_price_eur", 0.24)
            solar_kw_now = GLOBAL_CENTRAL_CACHE.get("current_solar_kw", 0.0)
            is_solar_surplus = (solar_kw_now >= 1.2)

            solar_cost_kwh = float(load_json(CONFIG_FILE).get("solar_cost_eur_kwh", 0.06))
            effective_price_now = solar_cost_kwh if is_solar_surplus else cur_price

            evening_peak_price = 0.35
            for p_entry in cached_dyn_peaks:
                if p_entry.get("max_price"):
                    evening_peak_price = max(evening_peak_price, p_entry["max_price"])

            # Electricity needed to buffer to 50C and 60C
            delta_t_50 = max(0.0, 50.0 - t_live)
            kwh_e_50 = round((delta_t_50 * c_tank) / cop_50, 2)

            delta_t_60 = max(0.0, 60.0 - t_live)
            kwh_e_60 = round((delta_t_60 * c_tank) / cop_60, 2)

            cost_now_50 = round(kwh_e_50 * effective_price_now, 2)
            cost_now_60 = round(kwh_e_60 * effective_price_now, 2)
            cost_later_run = round(max(1.3, kwh_e_60 if kwh_e_60 > 0 else 1.5) * evening_peak_price, 2)
            savings_60 = round(max(0.0, cost_later_run - cost_now_60), 2)

            if is_daytime:
                box_title = "Buffer Efficiëntie: Wel of Niet Bufferen (50°C vs. 60°C)?"
                comfort_card_title = "1️⃣ Basislading 50°C Nodig voor Avondspits?"
                finance_card_title = "2️⃣ Afweging: Doorbuffereen naar 60°C (24h Dekking)?"

                # Stap 1: Moeten we nu überhaupt verwarmen naar 50°C voor de avondspits?
                is_50_needed = (t_live < 48.0 or unh_spits_temp < 43.0)
                if not is_50_needed:
                    comfort_text = (
                        f"Het vat is nu <strong>{t_live:.1f}°C</strong> en al op basistemperatuur (doel: 50°C). "
                        f"Zonder enige verwarming (<span class='text-slate-400 font-mono'>grijze lijn</span>) blijft het vat tijdens de avondspits (18:45–22:15) ruim op comforttemperatuur "
                        f"(~{unh_spits_temp:.1f}°C). Een basislading naar 50°C is vóór de spits dus <strong>niet nodig</strong>."
                    )
                    bullet_1 = f"Basislading 50°C: Niet nodig (vat op peil, daalt naar ~{unh_spits_temp:.1f}°C in spits)"
                else:
                    comfort_text = (
                        f"Het vat is nu <strong>{t_live:.1f}°C</strong>. Zonder bijwarmen (<span class='text-slate-400 font-mono'>grijze lijn</span>) daalt het vat tijdens de avondspits "
                        f"naar <strong>{unh_spits_temp:.1f}°C</strong> (richting de 40°C comfortdrempel). "
                        f"Een basislading naar 50°C is vóór de avondspits <strong>noodzakelijk</strong> om koude douches te voorkomen."
                    )
                    bullet_1 = f"Basislading 50°C: Noodzakelijk vóór 18:45 (spitsdip {unh_spits_temp:.1f}°C dreigt)"

                # Stap 2: Wel of niet doorwarmen naar 60°C?
                finance_text = (
                    f"Doorwarmen naar 60°C vraagt ~{kwh_e_60} kWh stroom. "
                    f"Met 60°C dekken we niet alleen de avondspits, maar overbruggen we ook de complete nacht én ochtendspits (een <strong>volledige dag vooruit</strong> zonder tussentijdse runs!). "
                    f"Ondanks het lichte extra stilstandsverlies (~0,5 kWh over 20u) is nu laden met zon/dalstroom "
                    + (f"(~€{cost_now_60:.2f} met zonne-overschot) " if is_solar_surplus else f"(~€{cost_now_60:.2f} tegen actueel tarief) ")
                    + f"veel voordeliger dan later bijwarmen tijdens de avondspits of ochtend (~€{cost_later_run:.2f})."
                )
                bullet_2 = f"Bufferen naar 60°C: ~€{savings_60:.2f} voordeel + 24h rust voor warmtepomp"

                if planned_mode in ["forced_solar_boost_60", "max_on"]:
                    badge_html = '<span class="inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-[10px] font-bold bg-purple-950/80 text-purple-300 border border-purple-800/80"><span class="w-1.5 h-1.5 rounded-full bg-purple-400 animate-pulse"></span> Zonnebuffer Geadviseerd (tot 60°C)</span>'
                elif planned_mode in ["forced_standard_50", "forced_on", "advised_on"]:
                    badge_html = '<span class="inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-[10px] font-bold bg-emerald-950/80 text-emerald-300 border border-emerald-800/80"><span class="w-1.5 h-1.5 rounded-full bg-emerald-400"></span> Comfortlading Geadviseerd (tot 50°C)</span>'
                else:
                    badge_html = '<span class="inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-[10px] font-bold bg-slate-900 text-slate-300 border border-slate-700"><span class="w-1.5 h-1.5 rounded-full bg-slate-400"></span> Afwachten (Vat op temperatuur)</span>'
            else:
                box_title = "Buffer Efficiëntie: Nachtlading vs. Afwachten tot Middagzon?"
                comfort_card_title = "1️⃣ Basislading 50°C Nodig voor Ochtendspits?"
                finance_card_title = "2️⃣ Afweging: Nu Laden vs. Wachten op Morgenmiddag?"

                heated_morning_dip = traj.get("morning_dip_temp_c", 45.4) if traj else 45.4
                # Stap 1 Nacht: Is 50C nodig voor ochtendcomfort?
                if not comfort_guaranteed:
                    comfort_text = (
                        f"Zonder nachtlading (<span class='text-slate-400 font-mono'>grijze lijn</span>) daalt het vat door nachtelijke stilstand en ochtenddouches naar "
                        f"<strong class='text-amber-300'>{unh_morning_dip}°C</strong> (bij piekverbruik zelfs <strong class='text-red-400'>{unh_morning_p95}°C</strong>) vóór 10:00 uur. "
                        f"Comfortrisico: een lading naar 50°C vannacht is <strong>noodzakelijk voor ochtendcomfort</strong>. "
                        f"Met de geplande nachtlading (<span class='text-amber-400 font-mono'>gele lijn</span>) blijft het vat tijdens de ochtendspits comfortabel op minimaal <strong>{heated_morning_dip}°C</strong>."
                    )
                    bullet_1 = f"Basislading 50°C: Noodzakelijk (zonder lading dip naar {unh_morning_dip}°C; met lading {heated_morning_dip}°C)"
                else:
                    comfort_text = (
                        f"Het vat daalt vannacht zonder lading (<span class='text-slate-400 font-mono'>grijze lijn</span>) naar {unh_morning_dip}°C. "
                        f"Ochtendcomfort blijft ruim boven 40°C gewaarborgd. Een nachtlading is voor comfort <strong>niet strikt verplicht</strong>."
                    )
                    bullet_1 = f"Basislading 50°C: Niet verplicht (ochtenddip blijft {unh_morning_dip}°C)"

                finance_text = (
                    f"Nachtstroom kost vannacht ~€0,26/kWh (~€0,38 per run). Morgenmiddag rond 12:00–14:00 is stroom goedkoper met zonne-energie (~€0,15 per run). "
                    + (f"Comfortzekerheid vóór 10:00u weegt zwaarder dan wachten op zon (garandeert {heated_morning_dip}°C)." if not comfort_guaranteed else "Wachten tot middagzon bespaart ~€0,23.")
                )
                bullet_2 = f"Nachtlading gepland voor gegarandeerd ochtendcomfort ({heated_morning_dip}°C)" if not comfort_guaranteed else "Afwachten tot middagzon bespaart ~€0,23"

                if planned_mode in ["forced_night_50", "forced_on"]:
                    badge_html = '<span class="inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-[10px] font-bold bg-amber-950/80 text-amber-300 border border-amber-800/80"><span class="w-1.5 h-1.5 rounded-full bg-amber-400 animate-pulse"></span> Nachtlading Gepland (Comfortzekerheid)</span>'
                else:
                    badge_html = '<span class="inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-[10px] font-bold bg-emerald-950/80 text-emerald-300 border border-emerald-800/80"><span class="w-1.5 h-1.5 rounded-full bg-emerald-400"></span> Wachten op Middagzon (Besparing)</span>'

            # Evaluate Opportunistic Run Merger
            merge_outcome = None
            try:
                plan_for_merger = ensure_active_canonical_plan()
                merge_outcome = evaluate_and_apply_dhw_run_merger(plan_for_merger, t_live)
                if merge_outcome and merge_outcome.should_merge:
                    badge_html = '<span class="inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-[10px] font-bold bg-purple-950/80 text-purple-300 border border-purple-800/80"><span class="w-1.5 h-1.5 rounded-full bg-purple-400 animate-pulse"></span> ⚡ Opportunistische Zonnebuffer Actief (tot 60°C)</span>'
            except Exception as e_mrg:
                print(f"Warning in evaluate_and_apply_dhw_run_merger: {e_mrg}")

            decision = {
                "status": "SCHEDULE_NIGHT_CHARGE" if (planned_mode in ["forced_night_50", "forced_on"] and not comfort_guaranteed) else "SKIP_NIGHT_CHARGE",
                "planned_mode": planned_mode,
                "box_title": box_title,
                "badge_html": badge_html,
                "comfort_card_title": comfort_card_title,
                "comfort_text": comfort_text,
                "finance_card_title": finance_card_title,
                "finance_text": finance_text,
                "bullet_1": bullet_1,
                "bullet_2": bullet_2,
                "morning_dip_c": unh_morning_dip,
                "morning_dip_time": unh_morning_dip_time,
                "morning_dip_p95_c": unh_morning_p95,
                "dynamic_peaks": cached_dyn_peaks,
                "opportunistic_merge": {
                    "should_merge": merge_outcome.should_merge,
                    "reason": merge_outcome.reason,
                    "promoted_mode": merge_outcome.promoted_mode,
                    "target_temp_c": merge_outcome.target_temp_c,
                    "original_slot_time": merge_outcome.original_slot_time,
                    "savings_estimate_eur": merge_outcome.savings_estimate_eur
                } if merge_outcome else None
            }

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

            forced_off_ranges = []
            in_block = False
            start_b_idx = 0
            h_cnt = len(hist_pts)
            n_eval = len(plan.slots) if is_15m else min(24, len(plan.slots) // 4)
            for h_idx in range(n_eval):
                if is_15m:
                    is_forced = (plan.slots[h_idx].mode_code == "forced_off")
                else:
                    is_forced = any(plan.slots[h_idx * 4 + k].mode_code == "forced_off" for k in range(4) if (h_idx * 4 + k) < len(plan.slots))
                if is_forced and not in_block:
                    in_block = True
                    start_b_idx = h_idx
                elif not is_forced and in_block:
                    in_block = False
                    forced_off_ranges.append({
                        "start_idx": h_cnt + start_b_idx,
                        "end_idx": h_cnt + h_idx - 1,
                        "start_label": plan.slots[start_b_idx * 4 if not is_15m else start_b_idx].time_label,
                        "end_label": plan.slots[(h_idx - 1) * 4 if not is_15m else (h_idx - 1)].time_label
                    })
            if in_block:
                forced_off_ranges.append({
                    "start_idx": h_cnt + start_b_idx,
                    "end_idx": h_cnt + n_eval - 1,
                    "start_label": plan.slots[start_b_idx * 4 if not is_15m else start_b_idx].time_label,
                    "end_label": plan.slots[-1].time_label
                })

            handler._send_json({
                "status": "online",
                "resolution": res_mode,
                "decision": decision,
                "trajectory": traj,
                "unheated_trajectory": unheated_traj,
                "forced_off_ranges": forced_off_ranges,
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
