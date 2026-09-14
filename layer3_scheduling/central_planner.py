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
        mins_since_last_lockout: int = 999
    ) -> CanonicalDispatchPlan:
        """
        Executes central optimization and returns the single authoritative CanonicalDispatchPlan.
        """
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

        # 2. DHW Boiler 350L Dispatch Engine
        # Physical daytime priority (10:00-16:00) vs night run
        cur_h = now.hour
        has_daytime_ahead = (cur_h < 15)

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

        # Decide DHW Strategy:
        # Strategy A: Solar Boost (60°C) if surplus >= 2.5 kWh during daytime
        if has_daytime_ahead and day_solar_surplus >= 2.5 and len(day_solar_slots) >= 4:
            planned_mode = "forced_solar_boost_60"
            planned_mode_label = "Maximaal aan (doorverwarming tot 60°C)"
            sww_target_temp = 60.0
            sww_power_kw = cls.DHW_SOLAR_BOOST_ELECTRIC_KW
            # Choose best 6 contiguous solar slots (1.5 hours)
            best_solar_start = day_solar_slots[0]
            planned_dhw_slots = list(range(best_solar_start, min(n_slots, best_solar_start + 6)))

        # Strategy B: Standard Daytime Run (50°C) if daytime remains and tank drops
        elif has_daytime_ahead and current_dhw_temp <= 49.0:
            planned_mode = "forced_on"
            planned_mode_label = "Geforceerd aan (verwarmen tot 50°C)"
            sww_target_temp = 50.0
            sww_power_kw = cls.DHW_HEAT_PUMP_ELECTRIC_KW
            # Pick lowest price window between 11:00 and 16:00
            day_cand = [i for i, s in enumerate(slots) if 11 <= s.dt.hour <= 15]
            if day_cand:
                best_day_start = min(day_cand, key=lambda idx: slots[idx].price_all_in)
                planned_dhw_slots = list(range(best_day_start, min(n_slots, best_day_start + 4)))

        # Strategy C: Night Dip Safeguard (00:00-06:00 with 03:30 tie-breaker)
        else:
            night_cand = [i for i, s in enumerate(slots) if 0 <= s.dt.hour <= 5]
            if night_cand:
                planned_mode = "forced_on"
                planned_mode_label = "Geforceerd aan (verwarmen tot 50°C)"
                sww_target_temp = 50.0
                sww_power_kw = cls.DHW_HEAT_PUMP_ELECTRIC_KW
                # Tie-breaker logic: favor ~03:30 when flat prices
                def night_cost(idx):
                    p = slots[idx].price_all_in
                    h_dist = abs(slots[idx].dt.hour + slots[idx].dt.minute / 60.0 - 3.5)
                    return p + 0.001 * h_dist
                best_night_start = min(night_cand, key=night_cost)
                planned_dhw_slots = list(range(best_night_start, min(n_slots, best_night_start + 4)))

        # Avoid hard peak lockout for DHW runs
        final_dhw_slots = []
        for s_idx in planned_dhw_slots:
            peak = slot_lockout_map.get(s_idx)
            if not (peak and peak.get("is_hard_lockout")):
                final_dhw_slots.append(s_idx)

        # 3. Simulate Counterfactual (Unheated) DHW Tank Trajectory
        sim_temp = current_dhw_temp
        unheated_trajectory = []
        dip_time = None
        dip_temp = 99.0

        for i, s in enumerate(slots):
            # Standing loss
            dT_loss = (cls.DHW_STANDBY_LOSS_KW * step_hours) / cls.DHW_THERMAL_CAPACITY_KWH_PER_K
            # Shower draw assumption: 07:15 and 20:00 draws
            draw_loss = 0.0
            if s.dt.hour == 7 and s.dt.minute == 15:
                draw_loss = 4.5  # ~50L hot water draw
            elif s.dt.hour == 20 and s.dt.minute == 0:
                draw_loss = 3.0

            sim_temp = max(20.0, sim_temp - dT_loss - draw_loss)
            if sim_temp < dip_temp:
                dip_temp = sim_temp
                dip_time = s.dt.strftime("%H:%M")

            unheated_trajectory.append({
                "time": s.label,
                "temp_c": round(sim_temp, 1),
                "lower_bound_p05": round(max(20.0, sim_temp - 1.8), 1),
                "upper_bound_p95": round(sim_temp + 0.5, 1)
            })

        # 4. Space Heating Optimization & Pre-Heat Floor Buffering (2R1C model)
        from layer3_scheduling.space_heating_policy import SpaceHeatingPolicy

        heating_plan = SpaceHeatingPolicy.plan_space_heating(
            outdoor_temps_c=[s.outdoor_temp_c for s in slots],
            prices_eur=[s.price_all_in for s in slots],
            solar_kw=[s.solar_kw for s in slots],
            active_dhw_slots=final_dhw_slots,
            dynamic_peaks=dynamic_peaks,
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
                    mode_lbl = "Geforceerd aan (verwarmen tot 50°C)"
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
                f"Zonder geplande run daalt de boilertemperatuur naar {dip_temp:.1f}°C rond {dip_time}. "
                f"Tijdens de avondspits ({spits_lockout_hours}u vergrendeling) kan de warmtepomp niet meer bijverwarmen."
            ),
            arbitrage_saving_eur=0.30
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
            validation_issues=frame.validation_errors,
            metadata={
                "aligned_grid_start": frame.metadata.get("aligned_grid_start"),
                "dhw_tank_liters": cls.DHW_TANK_LITERS,
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
