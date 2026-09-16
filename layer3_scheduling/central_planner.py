"""
Layer 3: Central HEMS Dispatch Planner
======================================
The single source of truth dispatch optimization engine.
Produces an authoritative, unalterable CanonicalDispatchPlan from a CleanTelemetryFrame.

Strictly preserves all operational and physical directives:
- 350L DHW vat (0.407 kWh/K)
- Fysieke dag-prioriteit: overdag (10:00–16:00) altijd eerst dagrun/zonnebuffer (50°C of 60°C) vóór nachtrun
- Nachtdal tie-breaker naar 03:30u bij gelijke prijzen
- Dynamische spitsblokkade met P75/P85, Delta P >= €0.030, micro-piek filter (<30m) en harde wintercap van 150m (2,5u)
- Gestandaardiseerde 6-status taxonomie (forced_off, advised_off, normal, advised_on, forced_on, max_on)
"""

import math
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Any, Optional, Tuple
from models.canonical import (
    StandardizedState,
    STATE_METADATA,
    DispatchPlanSlot,
    DHWPlanSummary,
    CanonicalDispatchPlan,
    calc_percentile,
    detect_dynamic_price_peaks
)
from layer1_data_collection.sanitizer import CleanTelemetryFrame
from layer3_scheduling.plan_store import get_plan_store, PlanStore
from layer3_scheduling.tariff_provider import TariffProvider
from layer3_scheduling.dhw_specs import DhwTankSpec


class CentralPlanner:
    """Central deterministic dispatch planner for all HEMS energy vectors."""

    # Physical constants (User memory & thermodynamic calibration)
    DHW_TANK_LITERS = 350.0
    DHW_THERMAL_CAPACITY_KWH_PER_K = 0.407  # 350L * 4.184 kJ/(kg*K) / 3600
    DHW_STANDBY_LOSS_KW = 0.055             # ~1.3 kWh/24h standing loss
    DHW_HEAT_PUMP_ELECTRIC_KW = 1.8         # Standard 1.8 kW electric heating power
    DHW_SOLAR_BOOST_ELECTRIC_KW = 2.4       # 2.4 kW boost power to 60°C

    @classmethod
    def plan(
        cls,
        frame: CleanTelemetryFrame,
        current_dhw_temp: float = 48.0,
        model_parameters: Optional[Dict[str, Any]] = None,
        tariffs: Optional[TariffProvider] = None,
        store: Optional[PlanStore] = None,
        past_continuous_lockout_mins: int = 0,
        mins_since_last_lockout: int = 999,
        dhw_spec: Optional[DhwTankSpec] = None
    ) -> CanonicalDispatchPlan:
        """
        Executes central optimization and returns the single authoritative CanonicalDispatchPlan.
        """
        spec = dhw_spec or DhwTankSpec()
        tp = tariffs or TariffProvider()
        now = frame.timestamp
        step_mins = frame.resolution_minutes
        slots = frame.slots
        n_slots = len(slots)
        step_hours = step_mins / 60.0

        if past_continuous_lockout_mins == 0 and frame.metadata:
            past_continuous_lockout_mins = frame.metadata.get("past_continuous_lockout_mins", 0)
        if mins_since_last_lockout == 999 and frame.metadata:
            mins_since_last_lockout = frame.metadata.get("mins_since_last_lockout", 999)

        # Timeline items for canonical peak detector
        timeline_items = []
        for s in slots:
            timeline_items.append({
                "idx": s.slot_idx,
                "dt": s.dt,
                "label": s.label,
                "price": s.price_all_in,
                "solar": s.solar_kw,
                "unalloc": s.unallocated_kw
            })

        # 1. Dynamic Spitsblokkades (Central Peak Detector with Winter Cap & Past Awareness)
        dynamic_peaks, slot_lockout_map = detect_dynamic_price_peaks(
            timeline_items=timeline_items,
            step_mins=step_mins,
            max_lockout_mins=150,  # Hard 2.5 hour winter comfort cap
            past_continuous_lockout_mins=past_continuous_lockout_mins,
            mins_since_last_lockout=mins_since_last_lockout
        )

        # 2. Simulate Counterfactual (Unheated) DHW Tank Trajectory using Calibrated DhwThermalModel
        from layer2_calibration.dhw_thermal_model import DhwThermalModel
        dhw_model = DhwThermalModel()
        unheated_sim = dhw_model.simulate_trajectory(
            t_start_c=current_dhw_temp,
            start_dt=now,
            hours_ahead=24,
            heat_pump_schedule_slots=[]
        )
        sim_temps = unheated_sim.get("temperatures_c", [])
        sim_labels = unheated_sim.get("labels", [])
        sim_p05 = unheated_sim.get("temps_p05", [])
        sim_p95 = unheated_sim.get("temps_p95", [])

        unheated_trajectory = []
        for i in range(min(n_slots, len(sim_temps))):
            t_val = sim_temps[i]
            unheated_trajectory.append({
                "time": sim_labels[i] if i < len(sim_labels) else slots[i].label,
                "temp_c": round(t_val, 1),
                "lower_bound_p05": round(sim_p05[i] if i < len(sim_p05) else max(20.0, t_val - 1.8), 1),
                "upper_bound_p95": round(sim_p95[i] if i < len(sim_p95) else t_val + 0.5, 1)
            })

        # Morning comfort check: check tank temperature in morning slots (06:00 - 09:45 tomorrow)
        morning_slots_sim = [
            (idx, lbl, t) for idx, (lbl, t) in enumerate(zip(sim_labels, sim_temps))
            if ("06:00" <= lbl <= "09:45" and idx >= 16)
        ]
        if morning_slots_sim:
            min_morn_slot = min(morning_slots_sim, key=lambda x: x[2])
            morning_dip_c = round(min_morn_slot[2], 1)
            morning_dip_time = min_morn_slot[1]
        else:
            morning_dip_c = round(min(sim_temps[:36]), 1) if sim_temps else current_dhw_temp
            morning_dip_time = "08:30"

        morning_comfort_risk = (morning_dip_c < 40.0)

        # 3. DHW Boiler 350L Dispatch Engine
        # Physical daytime priority (10:00-16:00) vs night run
        cur_h = now.hour
        is_night_time = (cur_h >= 21 or cur_h < 6)
        has_daytime_ahead = (not is_night_time)

        day_solar_surplus = 0.0
        day_solar_slots = []
        for i, s in enumerate(slots):
            if 10 <= s.dt.hour <= 16:
                surplus = max(0.0, s.solar_kw - s.unallocated_kw)
                day_solar_surplus += surplus * step_hours
                if surplus >= 0.8:
                    day_solar_slots.append(i)

        planned_dhw_slots = []
        planned_mode = "normal"
        planned_mode_label = "Normaal (Standby)"
        sww_target_temp = 50.0
        sww_power_kw = cls.DHW_HEAT_PUMP_ELECTRIC_KW
        daytime_arbitrage_audit = None

        # Priority 1: Morning comfort risk (<40°C) during night/evening -> Nachtverwarming (20:00 - 06:00)
        # Comfortzekerheid vóór 10:00u weegt zwaarder dan wachten op zon.
        if (morning_comfort_risk or current_dhw_temp <= 41.0) and is_night_time:
            # 1. Thermal heat requirement to reach target setpoint
            # Factor in estimated cooldown until night run (~1.5K)
            est_tank_temp = max(35.0, current_dhw_temp - 1.5)
            delta_t = max(1.0, spec.target_setpoint_c - est_tank_temp)
            th_need_kwh = delta_t * spec.thermal_capacity_kwh_per_k

            # Thermal capacity -> kWh_th per slot
            th_per_slot = spec.thermal_output_kw * step_hours
            n_req_slots = max(2, min(8, math.ceil(th_need_kwh / th_per_slot)))

            # 2. Find morning peak start slot or first slot with hour >= 6
            morn_start_idx = n_slots
            for idx, s in enumerate(slots):
                if idx > 0 and ((s.dt.hour >= 6 and (s.dt.date() > now.date() or now.hour < 6)) or (slot_lockout_map.get(idx, {}).get("is_hard_lockout") and s.dt.hour < 11)):
                    morn_start_idx = idx
                    break

            candidate_windows = []
            for start_idx in range(n_slots - n_req_slots + 1):
                end_idx = start_idx + n_req_slots
                if end_idx > morn_start_idx:
                    continue

                window_slots = slots[start_idx:end_idx]
                s_start = window_slots[0]
                # Start must be after evening peak / >= 20:00, or early morning < 06:00
                is_night_window = (s_start.dt.hour >= 20 or s_start.dt.hour < 6)
                if not is_night_window:
                    continue

                # Must not intersect any hard peak lockout
                has_lockout = any(slot_lockout_map.get(k, {}).get("is_hard_lockout") for k in range(start_idx, end_idx))
                if has_lockout:
                    continue

                # Calculate window cost:
                # a. COP per quarter based on predicted outdoor temperature: COP = 2.55 + 0.075 * T_outdoor
                total_window_cost_eur = 0.0
                total_el_kwh = 0.0
                cops = []
                for k_idx, s_k in enumerate(window_slots):
                    t_out = s_k.outdoor_temp_c
                    cop_slot = max(1.8, min(4.5, 2.55 + 0.075 * t_out))
                    cops.append(cop_slot)
                    el_slot_kwh = (th_need_kwh / n_req_slots) / cop_slot
                    total_el_kwh += el_slot_kwh

                    p_in = s_k.price_all_in
                    p_exp = tp.calculate_export_value_from_import(p_in)
                    surplus_kw = max(0.0, s_k.solar_kw - s_k.unallocated_kw)
                    surplus_kwh = surplus_kw * step_hours
                    self_kwh = min(el_slot_kwh, surplus_kwh)
                    grid_kwh = max(0.0, el_slot_kwh - self_kwh)

                    total_window_cost_eur += (grid_kwh * p_in) + (self_kwh * p_exp)

                # b. Standing loss from end of run until morning peak start
                mean_cop = sum(cops) / len(cops) if cops else 2.8
                hours_until_morn = max(0.0, (morn_start_idx - end_idx) * step_hours)
                extra_th_loss_kwh = hours_until_morn * spec.standby_loss_50_kw
                extra_el_loss_kwh = extra_th_loss_kwh / mean_cop
                mean_price = sum(s_k.price_all_in for s_k in window_slots) / len(window_slots)
                total_window_cost_eur += extra_el_loss_kwh * mean_price

                candidate_windows.append((total_window_cost_eur, start_idx, n_req_slots, total_el_kwh, mean_cop))

            if candidate_windows:
                candidate_windows.sort(key=lambda x: x[0])
                best_cost, best_start, best_len, best_el, best_cop = candidate_windows[0]
                planned_mode = "forced_night_50"
                planned_mode_label = f"Geforceerd aan (Nachtlading tot {spec.target_setpoint_c:.0f}°C)"
                sww_target_temp = spec.target_setpoint_c
                sww_power_kw = spec.heat_pump_electric_kw
                planned_dhw_slots = list(range(best_start, best_start + best_len))
            else:
                planned_mode = "normal"
                planned_mode_label = "Normaal (Standby — Geen nachtvenster)"
                planned_dhw_slots = []

        # Priority 2: Daytime Economic Arbitration (DhwDaytimeArbiter)
        # Evaluates 24-hour all-in electricity cost comparing 50°C vs 60°C and simulated night run
        elif has_daytime_ahead:
            from layer3_scheduling.dhw_daytime_arbiter import DhwDaytimeArbiter
            arbiter_res = DhwDaytimeArbiter.evaluate_daytime_arbitrage(
                slots=slots,
                current_dhw_temp=current_dhw_temp,
                dynamic_peaks=dynamic_peaks,
                dhw_model=dhw_model,
                now_dt=now,
                step_hours=step_hours,
                tariff_provider=tp,
                tank_spec=spec
            )
            planned_mode = arbiter_res.planned_mode
            planned_mode_label = arbiter_res.planned_mode_label
            sww_target_temp = arbiter_res.target_temp_c
            sww_power_kw = arbiter_res.power_kw
            planned_dhw_slots = arbiter_res.planned_slots
            daytime_arbitrage_audit = arbiter_res.to_audit_dict()
        else:
            planned_mode = "normal"
            planned_mode_label = "Normaal (Standby — Wachten op middag/zon)"
            planned_dhw_slots = []

        # Avoid hard peak lockout for DHW runs
        final_dhw_slots = []
        for s_idx in planned_dhw_slots:
            peak = slot_lockout_map.get(s_idx)
            if not (peak and peak.get("is_hard_lockout")):
                final_dhw_slots.append(s_idx)

        # 4. Space Heating Optimization & Pre-Heat Floor Buffering (2R1C model)
        from layer3_scheduling.space_heating_policy import SpaceHeatingPolicy

        heating_plan = SpaceHeatingPolicy.plan_space_heating(
            outdoor_temps_c=[s.outdoor_temp_c for s in slots],
            prices_eur=[s.price_all_in for s in slots],
            solar_kw=[s.solar_kw for s in slots],
            active_dhw_slots=final_dhw_slots,
            dynamic_peaks=dynamic_peaks,
            current_room_temp_c=frame.current_room_temp,
            current_floor_temp_c=frame.current_floor_temp,
            target_room_temp_c=frame.target_room_temp,
            step_hours=step_hours
        )

        # 5. Assemble Canonical Dispatch Slots (with strict 6-state taxonomy)
        min_timeline_price = min([s.price_all_in for s in slots] or [0.20])
        dispatch_slots: List[DispatchPlanSlot] = []

        for i, s in enumerate(slots):
            p_val = s.price_all_in
            sol_val = s.solar_kw
            unalloc_val = s.unallocated_kw
            peak_info = slot_lockout_map.get(i)

            # Space heating allocation from 2R1C floor buffer model
            h_slot = heating_plan.slots[i] if i < len(heating_plan.slots) else None
            heating_kw = h_slot.heating_kw_el if h_slot else 0.0

            # DHW allocation
            dhw_kw = sww_power_kw if i in final_dhw_slots else 0.0
            if dhw_kw > 0.0:
                # Daikin physical constraint: CV pauses during active DHW run
                heating_kw = 0.0

            # State Taxonomy Assignment (6 states)
            if peak_info and peak_info.get("is_hard_lockout"):
                # 1. Geforceerd uit (blok)
                state = StandardizedState.FORCED_OFF
                mode_lbl = f"Geforceerd uit (blok) — {peak_info['name']}"
                desc = f"Geforceerd uit ({s.label}): Prijspiek max €{peak_info['max_price']:.3f}/kWh. Compressor SG4/CV vergrendeld tegen piektarieven."
                heating_kw = 0.0
                dhw_kw = 0.0
            elif peak_info and not peak_info.get("is_hard_lockout") and dhw_kw == 0.0:
                # 2. Geadviseerd uit
                state = StandardizedState.ADVISED_OFF
                mode_lbl = f"Geadviseerd uit — {peak_info['name']}"
                desc = f"Geadviseerd uit ({s.label}): Verhoogd tarief (€{p_val:.3f}/kWh). CV op minimale modulatievloer (950W)."
                if heating_kw > 0.0:
                    heating_kw = 0.95  # clamp to bottom modulation floor
            elif dhw_kw > 0.0:
                if planned_mode == "forced_solar_boost_60":
                    # 6. Maximaal aan (60°C)
                    state = StandardizedState.MAX_ON
                    mode_lbl = "Maximaal aan (doorverwarming tot 60°C)"
                    desc = f"Maximaal aan ({s.label}): Zonnebuffer doorverwarming naar 60°C · Vermogen {dhw_kw} kW elektrisch."
                else:
                    # 5. Geforceerd aan (50°C)
                    state = StandardizedState.FORCED_ON
                    mode_lbl = "Geforceerd aan (Nachtlading tot 50°C)" if planned_mode == "forced_night_50" else "Geforceerd aan (verwarmen tot 50°C)"
                    desc = f"Geforceerd aan ({s.label}): Verwarmen naar setpoint 50°C · Vermogen {dhw_kw} kW elektrisch."
            elif h_slot and h_slot.is_preheat_active:
                # 4. Geadviseerd aan (Vloerbuffer Pre-Heat SG3)
                state = StandardizedState.ADVISED_ON
                mode_lbl = "Geadviseerd aan (Vloerbuffer Pre-Heat SG3)"
                desc = f"Geadviseerd aan ({s.label}): Voordelig daltarief (€{p_val:.3f}/kWh). Betondekvloer wordt preventief voorverwarmd ({heating_kw} kW) om spitsblokkades comfortabel te overbruggen."
            elif (sol_val >= 1.5 or p_val <= min_timeline_price + 0.030) and (10 <= s.dt.hour <= 16):
                # 4. Geadviseerd aan (Zonne-overschot)
                state = StandardizedState.ADVISED_ON
                mode_lbl = "Geadviseerd aan (Zonne-overschot)"
                desc = f"Geadviseerd aan ({s.label}): Voordelig venster (€{p_val:.3f}/kWh). Warmtepomp mag hoger doorverwarmen voor zonnebuffer."
            else:
                # 3. Normaal
                state = StandardizedState.NORMAL
                mode_lbl = "Normaal (Standby)"
                desc = f"Normaal ({s.label}): Vrijloopvenster (€{p_val:.3f}/kWh). Warmtepomp en boiler in normale werking."

            meta = STATE_METADATA[state]
            net_import = round((unalloc_val + heating_kw + dhw_kw) - sol_val, 3)

            dispatch_slots.append(
                DispatchPlanSlot(
                    slot_idx=i,
                    time_label=s.label,
                    dt_iso=s.dt.isoformat(),
                    price_eur=s.price_all_in,
                    solar_kw=sol_val,
                    unallocated_kw=unalloc_val,
                    heating_kw=round(heating_kw, 2),
                    dhw_kw=round(dhw_kw, 2),
                    net_import_kw=net_import,
                    mode_code=meta["code"],
                    mode_label=mode_lbl,
                    color_hex=meta["color_hex"],
                    tailwind_class=meta["tailwind_text"],
                    description=desc
                )
            )

        # 5. Assemble DHW Plan Summary
        run_start = slots[final_dhw_slots[0]].dt.strftime("%H:%M") if final_dhw_slots else "N.v.t."
        run_end = (slots[final_dhw_slots[-1]].dt + timedelta(minutes=step_mins)).strftime("%H:%M") if final_dhw_slots else "N.v.t."
        run_dur_min = len(final_dhw_slots) * step_mins
        total_kwh_stroom = round(len(final_dhw_slots) * step_hours * sww_power_kw, 1)

        summary_meta = STATE_METADATA[
            StandardizedState.MAX_ON if planned_mode == "forced_solar_boost_60"
            else (StandardizedState.FORCED_ON if final_dhw_slots else StandardizedState.NORMAL)
        ]

        # Calculate dynamic spitslockout hours
        total_lockout_mins = sum(p.get("hard_duration_mins", 0) for p in dynamic_peaks if p.get("is_hard_lockout"))
        spits_lockout_hours = round(total_lockout_mins / 60.0, 1)

        # Build centralized decision details contract (Single Source of Truth)
        cur_h = now.hour
        is_daytime = (6 <= cur_h < 20)
        s0 = slots[0] if slots else None
        p_now = round(s0.price_all_in, 4) if s0 else 0.235
        sol_now = round(s0.solar_kw, 2) if s0 else 0.0
        unalloc_now = round(s0.unallocated_kw, 2) if s0 else 0.35
        surplus_now = max(0.0, sol_now - unalloc_now)
        is_solar_surplus = (surplus_now >= 0.8)

        spot_now = tp.calculate_spot_from_import(p_now)
        export_now = tp.calculate_export_value_from_import(p_now)

        run_pwr = sww_power_kw or 2.4
        sol_used = min(run_pwr, surplus_now)
        sol_share = (sol_used / run_pwr) if run_pwr > 0 else 0.0
        eff_price = round((sol_share * export_now) + ((1.0 - sol_share) * p_now), 4)

        kwh_e_run = total_kwh_stroom if total_kwh_stroom > 0 else 2.4
        cost_now_run = round(kwh_e_run * eff_price, 2)

        night_slots = [
            s for s in slots
            if (21 <= s.dt.hour <= 23 or 0 <= s.dt.hour <= 6)
            and not slot_lockout_map.get(s.slot_idx, {}).get("is_hard_lockout")
        ]
        later_price = min((s.price_all_in for s in night_slots), default=0.26)
        cost_later = round(kwh_e_run * later_price, 2)
        calc_savings = round(max(0.0, cost_later - cost_now_run), 2)
        if daytime_arbitrage_audit and daytime_arbitrage_audit.get("savings_eur"):
            calc_savings = round(daytime_arbitrage_audit["savings_eur"], 2)

        unh_spits = round(min([t["temp_c"] for t in unheated_trajectory if "18:" in t["time"] or "19:" in t["time"] or "20:" in t["time"] or "21:" in t["time"]], default=current_dhw_temp - 4.5), 1)

        # Time headroom until evening peak lockout
        mins_until_peak = 999.0
        peak_start_lbl = "18:45"
        for p in dynamic_peaks:
            if p.get("is_hard_lockout") and ("avond" in p.get("name", "").lower() or "spits" in p.get("name", "").lower()):
                p_s = p.get("hard_start_time") or p.get("start_time")
                if p_s:
                    peak_start_lbl = p_s
                s_idx = p.get("start_idx", 999)
                mins_until_peak = max(0.0, s_idx * step_mins)
                break

        th_need_60 = max(2.5, spec.boost_setpoint_c - current_dhw_temp) * spec.thermal_capacity_kwh_per_k
        n_slots_60 = max(3, min(8, math.ceil(th_need_60 / (spec.thermal_output_kw * step_hours))))
        est_duration_60_mins = n_slots_60 * step_mins
        buffer_60_feasible = (mins_until_peak >= est_duration_60_mins)

        if is_daytime:
            box_title = "Buffer Efficiëntie: Wel of Niet Bufferen (50°C vs. 60°C)?"
            comfort_card_title = "1️⃣ Basislading 50°C Nodig voor Avondspits?"

            is_50_needed = (current_dhw_temp < 48.0 or unh_spits < 43.0)
            if not is_50_needed:
                comfort_text = (
                    f"Het vat is nu <strong>{current_dhw_temp:.1f}°C</strong> en al op basistemperatuur (doel: 50°C). "
                    f"Zonder bijwarmen (<span class='text-slate-400 font-mono'>grijze lijn</span>) blijft het vat tijdens de avondspits ({peak_start_lbl}) ruim op comforttemperatuur "
                    f"(~{unh_spits:.1f}°C). Een basislading naar 50°C is vóór de spits <strong>niet nodig</strong>."
                )
                bullet_1 = f"Basislading 50°C: Niet nodig (vat op peil, daalt naar ~{unh_spits:.1f}°C in spits)"
            else:
                comfort_text = (
                    f"Het vat is nu <strong>{current_dhw_temp:.1f}°C</strong>. Zonder bijwarmen (<span class='text-slate-400 font-mono'>grijze lijn</span>) daalt het vat tijdens de avondspits "
                    f"naar <strong>{unh_spits:.1f}°C</strong> (richting de 40°C comfortdrempel). "
                    f"Een basislading naar 50°C is vóór de avondspits <strong>noodzakelijk</strong> om koude douches te voorkomen."
                )
                bullet_1 = f"Basislading 50°C: Noodzakelijk vóór {peak_start_lbl} (spitsdip {unh_spits:.1f}°C dreigt)"

            if is_solar_surplus:
                sol_pct = int(round(sol_share * 100))
                blend_str = f"~€{cost_now_run:.2f} ({sol_pct}% zon @ €{export_now:.3f} + {100-sol_pct}% net @ €{p_now:.3f})"
            else:
                blend_str = f"~€{cost_now_run:.2f} tegen actueel tarief (€{p_now:.3f}/kWh)"

            if not buffer_60_feasible and mins_until_peak < 999:
                finance_card_title = "2️⃣ Afweging: Doorbuffereen naar 60°C Niet Meer Haalbaar"
                finance_text = (
                    f"Doorwarmen naar 60°C vraagt circa <strong>{est_duration_60_mins} minuten</strong> ononderbroken stooktijd. "
                    f"Er resteren nog slechts <strong>{int(mins_until_peak)} minuten</strong> tot de avondspitsblokkade ({peak_start_lbl}). "
                    f"Een 60°C bufferrun kan daardoor vóór de spits niet meer worden afgerond zonder in het dure piektarief te lopen. "
                    f"Daarom is doorbufferen vergrendeld en is uitsluitend een kortere 50°C comfortlading (of afwachten) toegestaan."
                )
                bullet_2 = f"Bufferen naar 60°C: Niet meer haalbaar vóór {peak_start_lbl} (nog {int(mins_until_peak)}m, {est_duration_60_mins}m vereist)"
            else:
                finance_card_title = "2️⃣ Afweging: Doorbuffereen naar 60°C (24h Dekking)?"
                finance_text = (
                    f"Doorwarmen naar 60°C vraagt ~{kwh_e_run:.2f} kWh stroom. "
                    f"Met 60°C dekken we niet alleen de avondspits, maar overbruggen we ook de complete nacht én ochtendspits (een <strong>volledige dag vooruit</strong> zonder tussentijdse runs!). "
                    f"Ondanks het lichte extra stilstandsverlies (~0,5 kWh over 20u) is nu laden met zon/dalstroom "
                    f"({blend_str}) veel voordeliger dan later bijwarmen tijdens het nachtdal (~€{cost_later:.2f} tegen €{later_price:.3f}/kWh)."
                )
                bullet_2 = f"Bufferen naar 60°C: ~€{calc_savings:.2f} voordeel + 24h rust voor warmtepomp"

            if planned_mode in ["forced_solar_boost_60", "max_on"]:
                badge_html = f'<span class="inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-[10px] font-bold bg-purple-950/80 text-purple-300 border border-purple-800/80"><span class="w-1.5 h-1.5 rounded-full bg-purple-400 animate-pulse"></span> Zonnebuffer ({sww_target_temp:.1f}°C)</span>'
            elif planned_mode in ["forced_standard_50", "forced_on", "advised_on"]:
                badge_html = f'<span class="inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-[10px] font-bold bg-emerald-950/80 text-emerald-300 border border-emerald-800/80"><span class="w-1.5 h-1.5 rounded-full bg-emerald-400"></span> Optimale Lading ({sww_target_temp:.1f}°C)</span>'
            else:
                badge_html = '<span class="inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-[10px] font-bold bg-slate-900 text-slate-300 border border-slate-700"><span class="w-1.5 h-1.5 rounded-full bg-slate-400"></span> Afwachten (Vat dekt horizon)</span>'
        else:
            box_title = "Buffer Efficiëntie: Nachtlading vs. Afwachten tot Middagzon?"
            comfort_card_title = "1️⃣ Basislading 50°C Nodig voor Ochtendspits?"
            finance_card_title = "2️⃣ Afweging: Nu Laden vs. Wachten op Morgenmiddag?"

            if morning_comfort_risk:
                comfort_text = (
                    f"Zonder nachtlading (<span class='text-slate-400 font-mono'>grijze lijn</span>) daalt het vat door nachtelijke stilstand en ochtenddouches naar "
                    f"<strong class='text-amber-300'>{morning_dip_c:.1f}°C</strong> vóór 10:00 uur morgenochtend. "
                    f"Comfortrisico: een lading naar 50°C vannacht is <strong>noodzakelijk voor ochtendcomfort</strong>."
                )
                bullet_1 = f"Basislading 50°C: Noodzakelijk (zonder lading ochtenddip {morning_dip_c:.1f}°C)"
            else:
                comfort_text = (
                    f"Het vat daalt vannacht zonder lading (<span class='text-slate-400 font-mono'>grijze lijn</span>) naar {morning_dip_c:.1f}°C. "
                    f"Ochtendcomfort blijft ruim boven 40°C gewaarborgd. Een nachtlading is voor comfort <strong>niet strikt verplicht</strong>."
                )
                bullet_1 = f"Basislading 50°C: Niet verplicht (ochtenddip blijft {morning_dip_c:.1f}°C)"

            finance_text = (
                f"Nachtstroom kost vannacht ~€{later_price:.3f}/kWh (~€{cost_later:.2f} per run). Morgenmiddag rond 12:00–14:00 is stroom goedkoper met zonne-energie (~€{cost_now_run:.2f} per run). "
                + ("Comfortzekerheid vóór 10:00u weegt zwaarder dan wachten op zon." if morning_comfort_risk else f"Wachten tot middagzon bespaart ~€{calc_savings:.2f}.")
            )
            bullet_2 = f"Advies: {'Nachtlading uitvoeren' if morning_comfort_risk else 'Wachten op middagzon'}"
            badge_html = '<span class="inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-[10px] font-bold bg-amber-950/80 text-amber-300 border border-amber-800/80"><span class="w-1.5 h-1.5 rounded-full bg-amber-400 animate-pulse"></span> Nachtlading Gepland</span>' if morning_comfort_risk else '<span class="inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-[10px] font-bold bg-slate-900 text-slate-300 border border-slate-700"><span class="w-1.5 h-1.5 rounded-full bg-slate-400"></span> Wachten op Middagzon</span>'

        decision_details = {
            "status": "SCHEDULE_NIGHT_CHARGE" if (not is_daytime and morning_comfort_risk) else ("DAYTIME_BUFFER_60" if planned_mode in ["forced_solar_boost_60", "max_on"] else "STANDBY"),
            "planned_mode": summary_meta["code"],
            "box_title": box_title,
            "badge_html": badge_html,
            "comfort_card_title": comfort_card_title,
            "comfort_text": comfort_text,
            "finance_card_title": finance_card_title,
            "finance_text": finance_text,
            "bullet_1": bullet_1,
            "bullet_2": bullet_2,
            "morning_dip_c": morning_dip_c,
            "morning_dip_time": morning_dip_time,
            "dynamic_peaks": dynamic_peaks,
            "savings_eur": calc_savings,
            "cost_now_eur": cost_now_run,
            "cost_later_eur": cost_later
        }

        dhw_summary = DHWPlanSummary(
            planned_mode=summary_meta["code"],
            planned_mode_label=planned_mode_label,
            color_hex=summary_meta["color_hex"],
            tailwind_class=summary_meta["tailwind_text"],
            target_temp_c=sww_target_temp,
            run_start=run_start,
            run_end=run_end,
            run_duration_min=run_dur_min,
            power_kw=sww_power_kw,
            total_stroom_kwh=total_kwh_stroom,
            spits_lockout_hours=spits_lockout_hours,
            dynamic_peaks=dynamic_peaks,
            unheated_trajectory=unheated_trajectory,
            counterfactual_reason=(
                f"Zonder geplande run daalt de boilertemperatuur naar {morning_dip_c:.1f}°C rond {morning_dip_time}. "
                f"Tijdens de ochtendspits ({spits_lockout_hours}u vergrendeling) kan de warmtepomp niet meer bijverwarmen."
            ),
            arbitrage_saving_eur=calc_savings,
            decision_details=decision_details
        )

        plan = CanonicalDispatchPlan(
            generated_at=now.isoformat(),
            horizon_hours=(n_slots * step_mins) / 60.0,
            resolution_mins=step_mins,
            is_fresh=frame.is_fresh,
            freshness_age_seconds=frame.freshness_age_seconds,
            slots=dispatch_slots,
            dhw_summary=dhw_summary,
            dynamic_peaks=dynamic_peaks,
            heating_summary=heating_plan,
            validation_issues=frame.validation_errors,
            metadata={
                "aligned_grid_start": frame.metadata.get("aligned_grid_start"),
                "dhw_tank_liters": cls.DHW_TANK_LITERS,
                "daytime_arbitrage_audit": daytime_arbitrage_audit,
                "solar_surplus_day_kwh": round(day_solar_surplus, 2),
                "is_heating_season": heating_plan.is_heating_season,
                "season_status_label": heating_plan.season_status_label,
                "total_heating_kwh_el": heating_plan.total_heating_kwh_el,
                "total_heating_kwh_th": heating_plan.total_heating_kwh_th,
                "heating_average_cop": heating_plan.average_cop,
                "preheat_hours": heating_plan.preheat_hours,
                "lockout_hours": heating_plan.lockout_hours,
                "min_projected_room_temp_c": heating_plan.min_projected_room_temp_c,
                "max_projected_room_temp_c": heating_plan.max_projected_room_temp_c
            }
        )

        # Automatically publish to the injected or default singleton store
        target_store = store or get_plan_store()
        target_store.publish_plan(plan)

        return plan
