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
    get_state_metadata,
    DispatchPlanSlot,
    DHWPlanSummary,
    CanonicalDispatchPlan,
)
from layer3_scheduling.peak_detection import (
    calc_percentile,
    detect_dynamic_price_peaks
)
from layer1_data_collection.sanitizer import CleanTelemetryFrame
from layer3_scheduling.plan_store import get_plan_store, PlanStore
from layer3_scheduling.tariff_provider import TariffProvider
from layer3_scheduling.dhw_specs import DhwTankSpec
from models.physics import calculate_dhw_cop


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
        # Physical daytime priority vs emergency night run
        cur_h = now.hour
        is_night_time = (cur_h >= 21 or cur_h < 6)

        planned_dhw_slots = []
        planned_mode = "normal"
        planned_mode_label = "Normaal (Standby)"
        sww_target_temp = 50.0
        sww_power_kw = cls.DHW_HEAT_PUMP_ELECTRIC_KW
        daytime_arbitrage_audit = None
        arbiter_res = None

        # Priority 0: DHW Circuit Disabled in Home Assistant (e.g. Vacation / Holiday / Off)
        if not getattr(frame, "is_dhw_enabled", True):
            planned_dhw_slots = []
            planned_mode = "off"
            planned_mode_label = "DHW Uitgeschakeld (Vakantie / Standby)"
            sww_target_temp = 50.0
            sww_power_kw = 0.0
            daytime_arbitrage_audit = None
            arbiter_res = None
        # Priority 1: Morning comfort risk (<40°C) during night/evening -> Nachtverwarming (20:00 - 06:00)
        # Comfortzekerheid vóór ochtendspits weegt zwaarder dan wachten op zon.
        elif (morning_comfort_risk or current_dhw_temp <= 41.0) and is_night_time:
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
                    cop_slot = calculate_dhw_cop(target_temp_c=50.0, outdoor_temp_c=t_out)
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

        # Priority 2: Dynamic Daytime Economic Arbitration (DhwDaytimeArbiter)
        # Evaluates 24-hour all-in electricity cost comparing 50°C vs 60°C across the rolling horizon
        else:
            from layer3_scheduling.dhw_daytime_arbiter import DhwDaytimeArbiter

            # Query previous plan from PlanStore for anti-cycling & run continuity
            prev_path = None
            prev_target = None
            is_running = False
            try:
                prev_plan = get_plan_store().get_plan()
                if prev_plan and prev_plan.slots:
                    gen_dt = getattr(prev_plan, "generated_at", None)
                    if isinstance(gen_dt, str):
                        gen_dt = datetime.fromisoformat(gen_dt)
                    if gen_dt is None or abs((now - gen_dt).total_seconds()) <= 2700:
                        prev_audit = getattr(prev_plan, "metadata", {}).get("daytime_arbitrage_audit", {}) if getattr(prev_plan, "metadata", None) else {}
                        prev_path = prev_audit.get("selected_path_id")
                        prev_target = prev_audit.get("target_temp_c")
                        matching = []
                        for s in prev_plan.slots:
                            s_dt = getattr(s, "dt", None)
                            if s_dt is None and getattr(s, "dt_iso", ""):
                                try:
                                    s_dt = datetime.fromisoformat(s.dt_iso)
                                except Exception:
                                    pass
                            if s_dt and s_dt <= now < (s_dt + timedelta(minutes=step_mins)):
                                matching.append(s)
                        is_running = any(s.dhw_kw > 0.0 for s in matching) if matching else bool(prev_plan.slots[0].dhw_kw > 0.0)
            except Exception:
                pass

            arbiter_res = DhwDaytimeArbiter.evaluate_daytime_arbitrage(
                slots=slots,
                current_dhw_temp=current_dhw_temp,
                dynamic_peaks=dynamic_peaks,
                dhw_model=dhw_model,
                now_dt=now,
                step_hours=step_hours,
                tariff_provider=tp,
                tank_spec=spec,
                previous_selected_path=prev_path,
                previous_target_temp_c=prev_target,
                is_dhw_running=is_running,
            )
            planned_mode = arbiter_res.planned_mode
            planned_mode_label = arbiter_res.planned_mode_label
            sww_target_temp = arbiter_res.target_temp_c
            sww_power_kw = arbiter_res.power_kw
            planned_dhw_slots = arbiter_res.planned_slots
            daytime_arbitrage_audit = arbiter_res.to_audit_dict()

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
            wind_speeds_ms=frame.wind_speeds,
            is_heating_enabled=frame.is_space_heating_enabled,
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

            meta = get_state_metadata(state)
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
                    mode_code=state,
                    mode_label=mode_lbl,
                    color_hex=meta["color_hex"],
                    tailwind_class=meta["tailwind_text"],
                    description=desc
                )
            )

        # 5. Assemble DHW Plan Summary
        # Extract contiguous run blocks (e.g. Run 1: 03:15-04:45, Run 2: 14:00-15:15)
        run_blocks = []
        if final_dhw_slots:
            current_block = [final_dhw_slots[0]]
            for s_idx in final_dhw_slots[1:]:
                if s_idx == current_block[-1] + 1:
                    current_block.append(s_idx)
                else:
                    run_blocks.append(current_block)
                    current_block = [s_idx]
            run_blocks.append(current_block)

        if run_blocks:
            first_block = run_blocks[0]
            run_start = slots[first_block[0]].dt.strftime("%H:%M")
            run_end = (slots[first_block[-1]].dt + timedelta(minutes=step_mins)).strftime("%H:%M")
            if len(run_blocks) > 1:
                second_block = run_blocks[1]
                s2_start = slots[second_block[0]].dt.strftime("%H:%M")
                s2_end = (slots[second_block[-1]].dt + timedelta(minutes=step_mins)).strftime("%H:%M")
                runs_window_str = f"{run_start}–{run_end} & {s2_start}–{s2_end}"
            else:
                runs_window_str = f"{run_start}–{run_end}"
        else:
            run_start = "N.v.t."
            run_end = "N.v.t."
            runs_window_str = "N.v.t."

        run_dur_min = len(final_dhw_slots) * step_mins
        total_kwh_stroom = round(len(final_dhw_slots) * step_hours * sww_power_kw, 1)

        summary_mode = (
            StandardizedState.MAX_ON if planned_mode == "forced_solar_boost_60"
            else (StandardizedState.FORCED_ON if final_dhw_slots else StandardizedState.NORMAL)
        )
        summary_meta = get_state_metadata(summary_mode)

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
        # Calculate actual cost of scheduled DHW slots if planned, otherwise estimate based on current rate
        if final_dhw_slots:
            cost_actual_run = round(sum(slots[s_idx].price_all_in * step_hours * sww_power_kw for s_idx in final_dhw_slots), 2)
        else:
            cost_actual_run = 0.0
        cost_now_run = cost_actual_run if final_dhw_slots else round(kwh_e_run * eff_price, 2)

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

        # Today's evening peak dip: strictly evaluate slots belonging to TODAY'S evening peak (do not look at tomorrow evening!)
        eve_peak_slots = []
        for p in dynamic_peaks:
            if "avond" in p.get("name", "").lower() or "spits" in p.get("name", "").lower():
                s_idx = p.get("start_idx", 0)
                e_idx = p.get("end_idx", 0)
                if s_idx < 32:  # Only today's peak
                    for k in range(s_idx, min(e_idx, len(sim_temps))):
                        eve_peak_slots.append(sim_temps[k])
                break

        if eve_peak_slots:
            unh_spits = round(min(eve_peak_slots), 1)
        else:
            today_eve = [
                sim_temps[k] for k in range(min(28, len(sim_temps)))
                if 17 <= (now.hour + k * step_mins // 60) % 24 <= 22
            ]
            unh_spits = round(min(today_eve), 1) if today_eve else round(current_dhw_temp - 4.5, 1)

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

        if is_solar_surplus:
            sol_pct = int(round(sol_share * 100))
            blend_str = f"~€{cost_now_run:.2f} ({sol_pct}% zon @ €{export_now:.3f} + {100-sol_pct}% net @ €{p_now:.3f})"
        else:
            blend_str = f"~€{cost_now_run:.2f} tegen actueel tarief (€{p_now:.3f}/kWh)"

        box_title = "Buffer Efficiëntie: Optimale Boilertemperatuur & Horizon-Dekking"
        comfort_card_title = "Dispatch-Redenering: Waarom & Tot Welke Temperatuur?"
        finance_card_title = ""
        finance_text = ""

        if arbiter_res:
            sel_path = arbiter_res.selected_path
            calc_savings = round(arbiter_res.savings_eur, 2)
            is_night_run = bool(sel_path.night_run_required and sel_path.night_slots and not sel_path.day_slots)

            if arbiter_res.situation == "SITUATION_1_EVENING_COMFORT_RISK":
                spits_qualifier = "(comfortrisico)" if unh_spits < 40.0 else "(comfortabel gewaarborgd)"
                vat_verloop = (
                    f"Het vat is nu <strong>{current_dhw_temp:.1f}°C</strong>. "
                    f"Zonder bijwarmen (<span class='text-slate-400 font-mono'>grijze lijn</span>) daalt het vat tijdens de avondspits naar <strong>{unh_spits:.1f}°C</strong> {spits_qualifier}."
                )
                bullet_1 = f"Vatverloop: Zonder lading daalt vat naar {unh_spits:.1f}°C in spits {spits_qualifier}"
            else:
                vat_verloop = (
                    f"Het vat is nu <strong>{current_dhw_temp:.1f}°C</strong>. "
                    f"Zonder bijwarmen (<span class='text-slate-400 font-mono'>grijze lijn</span>) blijft het vat tijdens zowel de avondspits ({unh_spits:.1f}°C) als morgenochtend ({morning_dip_c:.1f}°C) ruim boven de 40°C comfortgrens."
                )
                bullet_1 = f"Vatverloop: Spits {unh_spits:.1f}°C, ochtenddip {morning_dip_c:.1f}°C (comfort gegarandeerd)"

            comfort_text = (
                f"{vat_verloop}<br><br>"
                f"<strong>Besluit &amp; Doeltemperatuur:</strong> {arbiter_res.explanation}"
            )

            # Bullet 2 & Badge afleiden van het geselecteerde pad
            if sel_path.path_id == "PAD_A2_DAY_60":
                bullet_2 = f"Geplande actie: Doortrekken naar {sww_target_temp:.1f}°C om {run_start}–{run_end} (bespaart ~€{calc_savings:.2f} t.o.v. nachtlading)"
                badge_html = f'<span class="inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-[10px] font-bold bg-amber-950/80 text-amber-300 border border-amber-800/80"><span class="w-1.5 h-1.5 rounded-full bg-amber-400 animate-pulse"></span> Buffer Lading ({sww_target_temp:.1f}°C)</span>'
            elif sel_path.path_id == "PAD_B2_BUFFER_60":
                bullet_2 = f"Geplande actie: Preventieve buffer naar {sww_target_temp:.1f}°C om {run_start}–{run_end} (bespaart ~€{calc_savings:.2f} t.o.v. nacht)"
                badge_html = f'<span class="inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-[10px] font-bold bg-amber-950/80 text-amber-300 border border-amber-800/80"><span class="w-1.5 h-1.5 rounded-full bg-amber-400 animate-pulse"></span> Buffer Lading ({sww_target_temp:.1f}°C)</span>'
            elif sel_path.path_id == "PAD_A1_DAY_50":
                if sww_target_temp >= (spec.boost_setpoint_c - 0.1):
                    bullet_2 = f"Geplande actie: Lading naar {sww_target_temp:.1f}°C om {run_start}–{run_end} (volledige horizon-dekking)"
                    badge_html = f'<span class="inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-[10px] font-bold bg-emerald-950/80 text-emerald-300 border border-emerald-800/80"><span class="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse"></span> Volledige Lading ({sww_target_temp:.1f}°C)</span>'
                else:
                    savings_str = f" (bespaart ~€{calc_savings:.2f} t.o.v. 60°C)" if calc_savings > 0 else ""
                    bullet_2 = f"Geplande actie: Lading naar {sww_target_temp:.1f}°C om {run_start}–{run_end}{savings_str}"
                    badge_html = f'<span class="inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-[10px] font-bold bg-emerald-950/80 text-emerald-300 border border-emerald-800/80"><span class="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse"></span> Optimale Lading ({sww_target_temp:.1f}°C)</span>'
            else:
                # Standby / Geen dagrun vandaag
                if sel_path.night_run_required and sel_path.night_slots:
                    action_label = "2 ladingen" if len(run_blocks) > 1 else "Nachtlading"
                    bullet_2 = f"Geplande actie: {action_label} naar {sww_target_temp:.1f}°C om {runs_window_str} (~€{cost_now_run:.2f} op daltarief)"
                    badge_title = "Nacht- &amp; Zonnebuffer Gepland" if len(run_blocks) > 1 else "Nachtlading Gepland"
                    badge_html = f'<span class="inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-[10px] font-bold bg-amber-950/80 text-amber-300 border border-amber-800/80"><span class="w-1.5 h-1.5 rounded-full bg-amber-400 animate-pulse"></span> {badge_title} ({runs_window_str} tot {sww_target_temp:.1f}°C)</span>'
                else:
                    bullet_2 = "Geplande actie: Standby (0 kWh verbruik, wachten op volgend venster)"
                    badge_html = '<span class="inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-[10px] font-bold bg-slate-900 text-slate-300 border border-slate-700"><span class="w-1.5 h-1.5 rounded-full bg-slate-400"></span> Afwachten (Vat dekt horizon)</span>'

            # Financiële padvergelijking opbouwen op basis van de geëvalueerde paden van de arbiter
            if len(arbiter_res.evaluated_paths) > 1:
                finance_card_title = "Financiële Padvergelijking (24-uurs Horizon)"
                path_rows = []
                for p in arbiter_res.evaluated_paths:
                    is_chosen = (p.path_id == sel_path.path_id)
                    marker = "✓ " if is_chosen else "• "
                    cost_disp = f"€{p.total_24h_cost_eur:.2f}" if p.total_24h_cost_eur < 900 else "Niet rendabel"
                    path_rows.append(f"{marker}<strong>{p.name}</strong>: 24u-kosten {cost_disp} ({p.day_window_label})")
                if calc_savings > 0:
                    savings_summary = f"<br><br><strong>Besparing:</strong> Gekozen pad levert ~€{calc_savings:.2f} financieel voordeel op."
                else:
                    savings_summary = ""
                finance_text = "<br>".join(path_rows) + savings_summary

        else:
            is_night_run = False
            if final_dhw_slots:
                is_night_run = any(slots[s_idx].dt.hour < 7 or slots[s_idx].dt.hour >= 21 for s_idx in final_dhw_slots)
                if is_night_run:
                    avoid_clause = " Overdag forceren naar 60°C is niet nodig en vermeden: dit bespaart stroom door de veel hogere COP en 55% minder stilstandsverlies." if sww_target_temp < (spec.boost_setpoint_c - 0.1) else ""
                    comfort_text = (
                        f"Het vat is nu <strong>{current_dhw_temp:.1f}°C</strong>. Zonder bijwarmen (<span class='text-slate-400 font-mono'>grijze lijn</span>) daalt het vat vanavond door de avondspits naar <strong>{unh_spits:.1f}°C</strong> (comfortabel gewaarborgd), maar koelt vannacht met ochtenddouches door naar <strong class='text-amber-300'>{morning_dip_c:.1f}°C</strong> (onder de 40°C comfortgrens).<br><br>"
                        f"<strong>Besluit &amp; Doeltemperatuur:</strong> Om het ochtendcomfort te garanderen is een nachtlading naar <strong>{sww_target_temp:.1f}°C</strong> gepland om <strong>{run_start}–{run_end}</strong> in het goedkoopste nachtdal (~€{cost_now_run:.2f} tegen daltarief, COP ~3.30)."
                        f"{avoid_clause}"
                    )
                    bullet_1 = f"Vatverloop: Spitsdip {unh_spits:.1f}°C (veilig), ochtenddip zonder lading {morning_dip_c:.1f}°C (comfortrisico)"
                    bullet_2 = f"Geplande actie: Nachtlading naar {sww_target_temp:.1f}°C om {run_start}–{run_end} (~€{cost_now_run:.2f} op daltarief)"
                    badge_html = f'<span class="inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-[10px] font-bold bg-amber-950/80 text-amber-300 border border-amber-800/80"><span class="w-1.5 h-1.5 rounded-full bg-amber-400 animate-pulse"></span> Nachtlading Gepland ({run_start}–{run_end} tot {sww_target_temp:.1f}°C)</span>'
                else:
                    if sww_target_temp >= (spec.boost_setpoint_c - 0.1):
                        avoid_sentence = f"Volledige lading naar {spec.boost_setpoint_c:.0f}°C noodzakelijk om horizon te overbruggen."
                        bullet_save = ""
                    else:
                        avoid_sentence = f"Doortrekken naar 60°C is vermeden wegens lagere COP (2.05) en extra stilstandsverlies (besparing: ~€{calc_savings:.2f})."
                        bullet_save = f" (bespaart ~€{calc_savings:.2f} t.o.v. 60°C)"
                    comfort_text = (
                        f"Het vat is nu <strong>{current_dhw_temp:.1f}°C</strong>. Zonder bijwarmen (<span class='text-slate-400 font-mono'>grijze lijn</span>) daalt het vat tijdens de avondspits naar <strong>{unh_spits:.1f}°C</strong> (richting de comfortdrempel).<br><br>"
                        f"<strong>Besluit &amp; Doeltemperatuur:</strong> Een gerichte lading naar <strong>{sww_target_temp:.1f}°C</strong> is gepland om <strong>{run_start}–{run_end}</strong> ({blend_str}). "
                        f"Deze berekende doeltemperatuur dekt de warmtevraag en stilstand ruim af tot het volgende laadvenster zonder nachtrun. "
                        f"{avoid_sentence}"
                    )
                    bullet_1 = f"Vatverloop: Zonder lading daalt vat naar {unh_spits:.1f}°C in spits"
                    bullet_2 = f"Geplande actie: Lading naar {sww_target_temp:.1f}°C om {run_start}–{run_end}{bullet_save}"
                    badge_html = f'<span class="inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-[10px] font-bold bg-emerald-950/80 text-emerald-300 border border-emerald-800/80"><span class="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse"></span> Optimale Lading ({sww_target_temp:.1f}°C)</span>'
            else:
                comfort_text = (
                    f"Het vat is nu <strong>{current_dhw_temp:.1f}°C</strong>. Zonder bijwarmen (<span class='text-slate-400 font-mono'>grijze lijn</span>) blijft het vat tijdens zowel de avondspits ({unh_spits:.1f}°C) als morgenochtend ({morning_dip_c:.1f}°C) ruim boven de 40°C comfortgrens.<br><br>"
                    f"<strong>Besluit &amp; Doeltemperatuur:</strong> Standby behouden (geen lading). Het vat dekt de volledige horizon tot het volgende goedkope laadvenster op eigen buffer."
                )
                bullet_1 = f"Vatverloop: Spits {unh_spits:.1f}°C, ochtenddip {morning_dip_c:.1f}°C (comfort gegarandeerd)"
                bullet_2 = f"Geplande actie: Standby (0 kWh verbruik, wachten op volgend venster)"
                badge_html = '<span class="inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-[10px] font-bold bg-slate-900 text-slate-300 border border-slate-700"><span class="w-1.5 h-1.5 rounded-full bg-slate-400"></span> Afwachten (Vat dekt horizon)</span>'

        decision_type = "night_run" if (final_dhw_slots and is_night_run) else ("day_run" if final_dhw_slots else "standby")
        template_params = {
            "decision_type": decision_type,
            "current_temp": f"{current_dhw_temp:.1f}",
            "evening_dip": f"{unh_spits:.1f}",
            "morning_dip": f"{morning_dip_c:.1f}",
            "target_temp": f"{sww_target_temp:.1f}",
            "temp": f"{sww_target_temp:.1f}",
            "start": run_start,
            "end": run_end,
            "cost": f"{cost_now_run:.2f}",
            "blend": blend_str,
            "savings": f"{calc_savings:.2f}"
        }

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
            "cost_later_eur": cost_later,
            "template_params": template_params
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
                "solar_surplus_day_kwh": round(sum(max(0.0, s.solar_kw - s.unallocated_kw) * step_hours for s in slots), 2),
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
