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
import time
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
from layer3_scheduling.dhw_shadow_logger import get_dhw_planner_mode, run_dhw_shadow_comparison
from layer3_scheduling.dhw_optimizer import solve, DhwOptimizerParams
from layer3_scheduling.dhw_plan_adapter import adapt_optimizer_to_dhw_summary
from models.physics import calculate_dhw_cop


class CentralPlanner:
    """Central deterministic dispatch planner for all HEMS energy vectors."""

    # Physical constants (User memory & thermodynamic calibration)
    DHW_TANK_LITERS = 350.0
    DHW_THERMAL_CAPACITY_KWH_PER_K = 0.407  # 350L * 4.184 kJ/(kg*K) / 3600
    DHW_STANDBY_LOSS_KW = 0.055             # ~1.3 kWh/24h standing loss

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
        dhw_spec: Optional[DhwTankSpec] = None,
        dhw_optimizer_params: Optional[DhwOptimizerParams] = None
    ) -> CanonicalDispatchPlan:
        """
        Executes central optimization and returns the single authoritative CanonicalDispatchPlan.
        """
        try:
            from api.secrets_store import CONFIG_FILE, load_json
            cfg = load_json(CONFIG_FILE) if CONFIG_FILE.exists() else {}
        except Exception:
            cfg = {}

        if dhw_spec is None:
            spec = DhwTankSpec.from_config(cfg)
        else:
            spec = dhw_spec

        if dhw_optimizer_params is None:
            dhw_optimizer_params = DhwOptimizerParams.from_config(cfg)

        if model_parameters is None:
            try:
                from api.secrets_store import PARAMS_FILE, load_json
                model_parameters = load_json(PARAMS_FILE) if PARAMS_FILE.exists() else {}
            except Exception:
                model_parameters = {}

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
        sww_power_kw = spec.heat_pump_electric_kw
        daytime_arbitrage_audit = None
        arbiter_res = None
        dhw_summary_override = None
        res_opt = None

        # Priority 0: DHW Circuit Disabled in Home Assistant (e.g. Vacation / Holiday / Off)
        if not getattr(frame, "is_dhw_enabled", True):
            planned_dhw_slots = []
            planned_mode = "off"
            planned_mode_label = "DHW Uitgeschakeld (Vakantie / Standby)"
            sww_target_temp = 50.0
            sww_power_kw = 0.0
            daytime_arbitrage_audit = None
            dhw_summary = DHWPlanSummary(
                planned_mode="off",
                planned_mode_label="DHW Uitgeschakeld",
                color_hex="#64748B",
                tailwind_class="text-slate-500",
                target_temp_c=50.0,
                run_start="Geen run",
                run_end="Geen run",
                run_duration_min=0,
                power_kw=0.0,
                total_stroom_kwh=0.0,
                spits_lockout_hours=round(sum(p.get("hard_duration_mins", 0) for p in dynamic_peaks if p.get("is_hard_lockout")) / 60.0, 1),
                dynamic_peaks=dynamic_peaks,
                unheated_trajectory=[],
                counterfactual_reason="DHW circuit is uitgeschakeld in instellingen.",
                arbitrage_saving_eur=0.0,
                decision_details={"status": "OFF", "planned_mode": "off"}
            )
        else:
            # Check DHW Planner mode: 'optimizer' (default) or 'shadow' (diagnostic)
            planner_mode = get_dhw_planner_mode(model_parameters)

            # Query previous plan from PlanStore for anti-cycling & run continuity
            is_running = False
            try:
                prev_plan = get_plan_store().get_plan()
                if prev_plan and prev_plan.slots:
                    gen_dt = getattr(prev_plan, "generated_at", None)
                    if isinstance(gen_dt, str):
                        gen_dt = datetime.fromisoformat(gen_dt)
                    if gen_dt is None or abs((now - gen_dt).total_seconds()) <= 2700:
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

            run_state0 = ("ON_FREE", 0) if is_running else ("OFF_FREE", 0)

            res_opt = solve(
                slots=slots,
                t0_c=current_dhw_temp,
                run_state0=run_state0,
                dhw_model=dhw_model,
                tariff_provider=tp,
                spec=spec,
                params=dhw_optimizer_params,
                dynamic_peaks=dynamic_peaks,
                model_parameters=model_parameters
            )

            # Evaluate autonomous baseline (WP7)
            from layer3_scheduling.dhw_baseline import simulate_autonomous
            base_res = simulate_autonomous(
                slots=slots,
                t0_c=current_dhw_temp,
                setpoint_c=spec.target_setpoint_c,
                auto_start_delta_c=spec.auto_start_delta_c,
                dhw_model=dhw_model,
                spec=spec,
                params=dhw_optimizer_params,
                model_parameters=model_parameters
            )

            # Guard: optimizer must beat baseline on objective J
            if base_res.j_objective_eur < res_opt.j_objective_eur - 0.005:
                # Baseline is better: Open HEMS adopts baseline plan without forced intervention
                res_opt.planned_slots = [i for i, u in enumerate(base_res.u_plan) if u == 1]
                res_opt.j_objective_eur = base_res.j_objective_eur
                res_opt.total_cost_eur = base_res.total_cost_eur
                res_opt.electricity_cost_eur = base_res.electricity_cost_eur
                res_opt.start_cost_eur = base_res.start_cost_eur
                res_opt.salvage_value_eur = base_res.salvage_value_eur
                res_opt.trajectory["temperatures_c"] = base_res.temperatures_c
                res_opt.trajectory["temperatures_p05_c"] = base_res.temperatures_p05_c
                res_opt.trajectory["temperatures_p95_c"] = base_res.temperatures_p95_c

            dhw_summary = adapt_optimizer_to_dhw_summary(
                opt_result=res_opt,
                slots=slots,
                t0_c=current_dhw_temp,
                run_state0=run_state0,
                dhw_model=dhw_model,
                tariff_provider=tp,
                spec=spec,
                params=dhw_optimizer_params,
                dynamic_peaks=dynamic_peaks,
                model_parameters=model_parameters
            )

            planned_mode = dhw_summary.planned_mode
            planned_mode_label = dhw_summary.planned_mode_label
            sww_target_temp = dhw_summary.target_temp_c
            sww_power_kw = dhw_summary.power_kw
            planned_dhw_slots = res_opt.planned_slots
            daytime_arbitrage_audit = dhw_summary.decision_details or {}

            if planner_mode == "shadow":
                shadow_record = run_dhw_shadow_comparison(
                    slots=slots,
                    current_dhw_temp=current_dhw_temp,
                    dynamic_peaks=dynamic_peaks,
                    arbiter_planned_slots=planned_dhw_slots,
                    dhw_model=dhw_model,
                    tariff_provider=tp,
                    spec=spec,
                    now_dt=now,
                    run_state0=run_state0
                )
                daytime_arbitrage_audit["shadow_comparison"] = shadow_record

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

            # DHW allocation: dynamic electric compressor power per slot from physical tank & outdoor temperature
            if i in final_dhw_slots:
                t_k = res_opt.trajectory["temperatures_c"][i] if (res_opt and "temperatures_c" in res_opt.trajectory and i < len(res_opt.trajectory["temperatures_c"])) else current_dhw_temp
                out_t_k = float(getattr(s, "outdoor_temp_c", getattr(s, "outdoor_temp", 10.0)))
                dhw_kw = spec.get_electric_power_kw(t_k, outdoor_temp_c=out_t_k, params=model_parameters)
            else:
                dhw_kw = 0.0

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
                    desc = f"Maximaal aan ({s.label}): Zonnebuffer doorverwarming naar 60°C · Vermogen {dhw_kw:.2f} kW elektrisch."
                else:
                    # 5. Geforceerd aan (50°C)
                    state = StandardizedState.FORCED_ON
                    mode_lbl = "Geforceerd aan (Nachtlading tot 50°C)" if planned_mode == "forced_night_50" else "Geforceerd aan (verwarmen tot 50°C)"
                    desc = f"Geforceerd aan ({s.label}): Verwarmen naar setpoint 50°C · Vermogen {dhw_kw:.2f} kW elektrisch."
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
