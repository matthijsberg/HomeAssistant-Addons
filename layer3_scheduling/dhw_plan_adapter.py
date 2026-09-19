"""
Open HEMS — DHW Optimizer Canonical Plan Adapter
================================================
Layer 3 Scheduling Adapter:
Bridges DhwOptimizerResult into the authoritative DHWPlanSummary and DispatchPlanSlots,
guaranteeing full conformance to Open HEMS 6-state taxonomy, entity isolation, and dumb views.
"""

from typing import Dict, List, Any, Optional, Tuple
from datetime import datetime, timezone
import math

from models.canonical import (
    DHWPlanSummary,
    DispatchPlanSlot,
    StandardizedState,
    get_state_metadata,
)
from layer3_scheduling.dhw_specs import DhwTankSpec
from layer3_scheduling.dhw_optimizer import (
    DhwOptimizerResult,
    DhwOptimizerParams,
    DhwRun,
)
from layer3_scheduling.dhw_optimizer_explain import explain_dhw_optimization


def adapt_optimizer_to_dhw_summary(
    opt_result: DhwOptimizerResult,
    slots: List[Any],
    t0_c: float,
    run_state0: Any = None,
    dhw_model: Any = None,
    tariff_provider: Any = None,
    spec: Optional[DhwTankSpec] = None,
    params: Optional[DhwOptimizerParams] = None,
    dynamic_peaks: Optional[List[Any]] = None,
    model_parameters: Optional[Dict[str, Any]] = None,
) -> DHWPlanSummary:
    """
    Pure function: Transforms DhwOptimizerResult into the authoritative canonical DHWPlanSummary.

    Requirements:
      - target_temp_c = T_end of the first planned run (e.g. 54.0 or 59.8)
      - power_kw = P_el of that run
      - planned_mode according to 6-state taxonomy:
        max_on if run heats to >= 52.0°C, forced_on otherwise, normal if no run in upcoming 24h
      - run_start/run_end of the first run
      - dynamic_peaks passed through unchanged
      - decision_details.explanation, comfort_text, finance_text and bullets built exclusively
        from the runs and counterfactuals (one sentence per planned run)
      - unheated_trajectory and trajectory derived from the canonical dhw_step
    """
    spec = spec or DhwTankSpec()
    params = params or DhwOptimizerParams()
    N = len(slots)

    # 1. Run counterfactual explanation engine
    explain_data = explain_dhw_optimization(
        slots=slots,
        t0_c=t0_c,
        run_state0=run_state0,
        opt_result=opt_result,
        dhw_model=dhw_model,
        tariff_provider=tariff_provider,
        spec=spec,
        params=params,
        dynamic_peaks=dynamic_peaks
    )

    # 2. Check runs in upcoming 24 hours (first 96 slots)
    runs_24h = [r for r in opt_result.runs if r.start_idx < 96]

    if not runs_24h:
        planned_mode = "normal"
        sww_target_temp = spec.target_setpoint_c
        sww_power_kw = spec.heat_pump_electric_kw
        run_start = "Geen run"
        run_end = "Geen run"
        run_dur_min = 0
    else:
        first_run = runs_24h[0]
        sww_target_temp = round(first_run.t_end_c, 1)
        first_start_idx = first_run.start_idx
        first_out_t = float(getattr(slots[first_start_idx], "outdoor_temp_c", getattr(slots[first_start_idx], "outdoor_temp", 10.0))) if first_start_idx < N else 10.0
        sww_power_kw = spec.get_electric_power_kw(first_run.t_end_c, outdoor_temp_c=first_out_t, params=model_parameters)

        if first_run.t_end_c >= 52.0:
            planned_mode = "max_on"
        else:
            planned_mode = "forced_on"

        run_start = getattr(slots[first_run.start_idx], "label", f"slot {first_run.start_idx}") if first_run.start_idx < N else "nu"
        end_slot_idx = min(N - 1, first_run.end_idx)
        run_end = getattr(slots[end_slot_idx], "label", f"slot {first_run.end_idx}") if end_slot_idx < N else "later"
        run_dur_min = (first_run.end_idx - first_run.start_idx) * 15

    state_enum_map = {
        "normal": StandardizedState.NORMAL,
        "forced_on": StandardizedState.FORCED_ON,
        "max_on": StandardizedState.MAX_ON,
        "forced_off": StandardizedState.FORCED_OFF,
        "advised_off": StandardizedState.ADVISED_OFF,
        "advised_on": StandardizedState.ADVISED_ON,
    }

    state_enum = state_enum_map.get(planned_mode, StandardizedState.NORMAL)
    summary_meta = get_state_metadata(state_enum)
    planned_mode_label = summary_meta.get("label", "Normaal bedrijf")

    # 3. Dynamic peaks & lockout hours
    peaks_list = list(dynamic_peaks or [])
    spits_lockout_hours = round(
        sum(0.25 for p in peaks_list if (isinstance(p, dict) and p.get("is_hard_lockout")) or getattr(p, "is_hard_lockout", False)),
        2
    )

    # 4. Determine status & badge HTML
    is_active_now = (0 in opt_result.planned_slots)
    if is_active_now:
        status_str = "ACTIVE_RUN"
        badge_html = (
            f'<span class="inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-[10px] font-bold bg-amber-950/80 text-amber-300 border border-amber-800/80">'
            f'<span class="w-1.5 h-1.5 rounded-full bg-amber-400 animate-pulse"></span> ⚡ Actief Verwarmen tot {sww_target_temp:.1f}°C</span>'
        )
    elif runs_24h:
        status_str = "SCHEDULED"
        badge_html = (
            f'<span class="inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-[10px] font-bold bg-teal-950/80 text-teal-300 border border-teal-800/80">'
            f'<span class="w-1.5 h-1.5 rounded-full bg-teal-400"></span> Gepland: {run_start}–{run_end} tot {sww_target_temp:.1f}°C</span>'
        )
    else:
        status_str = "STANDBY"
        badge_html = (
            '<span class="inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-[10px] font-bold bg-slate-900 text-slate-300 border border-slate-700">'
            '<span class="w-1.5 h-1.5 rounded-full bg-slate-400"></span> Afwachten (Vat op temperatuur)</span>'
        )

    # 5. Build decision details
    decision_details = {
        "status": status_str,
        "planner": "optimizer",
        "planned_mode": planned_mode,
        "box_title": "DHW Exacte DP Optimizer (48u Horizon)",
        "badge_html": badge_html,
        "comfort_card_title": "1️⃣ Comfort & Watertemperatuur",
        "comfort_text": explain_data["comfort_text"],
        "finance_card_title": "2️⃣ Kosten & Besparing",
        "finance_text": explain_data["finance_text"],
        "bullet_1": explain_data["bullet_1"],
        "bullet_2": explain_data["bullet_2"],
        "run_explanations": explain_data["run_explanations"],
        "explanation": explain_data["explanation"],
        "morning_dip_c": explain_data["morning_dip_c"],
        "morning_dip_time": explain_data["morning_dip_time"],
        "dynamic_peaks": peaks_list,
        "savings_eur": explain_data["total_savings_eur"],
        "cost_total_eur": opt_result.total_cost_eur,
        "electricity_cost_eur": opt_result.electricity_cost_eur,
        "start_cost_eur": opt_result.start_cost_eur,
        "salvage_value_eur": opt_result.salvage_value_eur,
        "net_objective_eur": opt_result.j_objective_eur,
        "j_objective_eur": opt_result.j_objective_eur,
        "comfort_margin_mode": getattr(params, "comfort_margin_mode", "p95"),
        "comfort_boundary_c": round(spec.comfort_min_temp_c + (getattr(params, "min_comfort_margin_c", 0.5) if getattr(params, "comfort_margin_mode", "p95") == "p50" else (getattr(params, "fixed_comfort_margin_c", 2.0) if getattr(params, "comfort_margin_mode", "p95") == "fixed" else getattr(params, "min_comfort_margin_c", 0.5))), 1),
        "trajectory": opt_result.trajectory,
        "unheated_trajectory": explain_data["unheated_trajectory"],
        "validation_issue": opt_result.validation_issue
    }

    # Format unheated trajectory list for legacy consumers if needed
    raw_unh_temps = explain_data["unheated_trajectory"].get("temperatures_c", [])
    unheated_traj_list = []
    for k in range(min(N, len(raw_unh_temps))):
        lbl = getattr(slots[k], "label", f"slot {k}") if k < N else "Nu"
        unheated_traj_list.append({
            "slot_idx": k,
            "label": lbl,
            "temperature_c": raw_unh_temps[k]
        })

    total_kwh_stroom = round(sum(r.kwh_el for r in opt_result.runs), 2)

    return DHWPlanSummary(
        planned_mode=planned_mode,
        planned_mode_label=planned_mode_label,
        color_hex=summary_meta.get("color_hex", "#94A3B8"),
        tailwind_class=summary_meta.get("tailwind_text", "text-slate-400"),
        target_temp_c=sww_target_temp,
        run_start=run_start,
        run_end=run_end,
        run_duration_min=run_dur_min,
        power_kw=sww_power_kw,
        total_stroom_kwh=total_kwh_stroom,
        spits_lockout_hours=spits_lockout_hours,
        dynamic_peaks=peaks_list,
        unheated_trajectory=unheated_traj_list,
        counterfactual_reason=explain_data["unheated_explanation"],
        arbitrage_saving_eur=explain_data["total_savings_eur"],
        decision_details=decision_details
    )


def apply_optimizer_to_dispatch_slots(
    dispatch_slots: List[DispatchPlanSlot],
    opt_result: DhwOptimizerResult,
    spec: Optional[DhwTankSpec] = None,
    model_parameters: Optional[Dict[str, Any]] = None,
) -> List[DispatchPlanSlot]:
    """
    Pure function: Overlays DhwOptimizerResult onto an existing list of CanonicalDispatchPlan slots.
    Updates dhw_kw, mode_code, and state metadata on each slot.
    """
    spec = spec or DhwTankSpec()
    t_star = opt_result.trajectory.get("temperatures_c", [])

    state_enum_map = {
        "normal": StandardizedState.NORMAL,
        "forced_on": StandardizedState.FORCED_ON,
        "max_on": StandardizedState.MAX_ON,
        "forced_off": StandardizedState.FORCED_OFF,
        "advised_off": StandardizedState.ADVISED_OFF,
        "advised_on": StandardizedState.ADVISED_ON,
    }

    for k, slot in enumerate(dispatch_slots):
        if k < len(opt_result.slot_modes):
            mode_code = opt_result.slot_modes[k]
            is_active = (k in opt_result.planned_slots)
            t_curr = t_star[k] if k < len(t_star) else 50.0

            state_enum = state_enum_map.get(mode_code, StandardizedState.NORMAL)
            meta = get_state_metadata(state_enum)

            out_t_k = float(getattr(slot, "outdoor_temp_c", getattr(slot, "outdoor_temp", 10.0)))
            slot.dhw_kw = spec.get_electric_power_kw(t_curr, outdoor_temp_c=out_t_k, params=model_parameters) if is_active else 0.0
            slot.mode_code = state_enum
            slot.mode_label = meta.get("label", "")
            slot.color_hex = meta.get("color_hex", "")
            slot.tailwind_class = meta.get("tailwind_text", "")

    return dispatch_slots
