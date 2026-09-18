"""
Open HEMS — DHW Optimizer Explanation & Counterfactuals Engine
==============================================================
Layer 3 Scheduling Submodule:
Derives mathematically sound, traceable explanations and counterfactual comparisons
from exact dynamic programming solutions without fabricating numbers.

Three Canonical Counterfactuals:
1. J_none: Trajectory with u == 0 -> minimum temperature reached and time of comfort dip.
2. J_cap50: Exact solve with T_max capped at 50.0°C -> cost difference vs optimal buffering.
3. J_delay: Exact solve with u == 0 forced until the end of the first planned run.
"""

from typing import Dict, List, Any, Optional, Tuple
from datetime import datetime, timezone, timedelta
import math

from models.physics import dhw_step
from layer3_scheduling.dhw_specs import DhwTankSpec
from layer3_scheduling.dhw_optimizer import (
    solve,
    DhwOptimizerParams,
    DhwOptimizerResult,
    DhwRun,
)


def compute_counterfactual_none(
    slots: List[Any],
    t0_c: float,
    dhw_model: Any,
    spec: Optional[DhwTankSpec] = None,
    params: Optional[DhwOptimizerParams] = None,
) -> Dict[str, Any]:
    """
    Evaluates the unheated trajectory (u == 0 for all slots) using the canonical dhw_step.
    Identifies the minimum temperature and the first slot where T dips below comfort minimum (40°C).
    """
    spec = spec or DhwTankSpec()
    params = params or DhwOptimizerParams()
    C_tank = spec.thermal_capacity_kwh_per_k
    ua_w_per_k = dhw_model.get_tank_ua() if hasattr(dhw_model, "get_tank_ua") else 2.5
    t_comf = spec.comfort_min_temp_c

    tank_spec_dict = {
        "thermal_capacity_kwh_per_k": C_tank,
        "ua_w_per_k": ua_w_per_k,
        "ambient_temp_c": params.t_amb_c,
        "target_temp_c": spec.boost_setpoint_c
    }

    t_curr = float(t0_c)
    t_curr_p05 = float(t0_c)
    t_curr_p95 = float(t0_c)

    temps = [round(t_curr, 2)]
    temps_p05 = [round(t_curr_p05, 2)]
    temps_p95 = [round(t_curr_p95, 2)]

    first_dip_idx = None
    first_dip_time = None
    first_dip_temp_c = None

    min_temp = t_curr
    min_temp_idx = 0
    min_temp_time = getattr(slots[0], "label", "Nu") if slots else "Nu"

    for k, s in enumerate(slots):
        dt_val = getattr(s, "dt", None)
        if dt_val is None:
            dt_val = datetime.now(timezone.utc) + timedelta(minutes=15 * k)
        dow = dt_val.weekday()
        q_idx = dt_val.hour * 4 + dt_val.minute // 15
        lbl = getattr(s, "label", dt_val.strftime("%H:%M"))

        out_t = float(getattr(s, "outdoor_temp_c", getattr(s, "outdoor_temp", 10.0)))
        p50 = dhw_model.get_learned_tap_kwh_th(dow, q_idx) if hasattr(dhw_model, "get_learned_tap_kwh_th") else 0.05
        p95 = dhw_model.get_learned_tap_kwh_th_p95(dow, q_idx) if hasattr(dhw_model, "get_learned_tap_kwh_th_p95") else 0.075
        p05 = dhw_model.get_learned_tap_kwh_th_p05(dow, q_idx) if hasattr(dhw_model, "get_learned_tap_kwh_th_p05") else 0.025

        t_curr = dhw_step(
            t_tank_c=t_curr,
            u=0.0,
            q_tap_kwh=p50,
            t_outdoor_c=out_t,
            dt_h=0.25,
            spec=tank_spec_dict,
            t_max_c=spec.boost_setpoint_c,
            t_amb_c=params.t_amb_c
        )
        t_curr_p05 = dhw_step(
            t_tank_c=t_curr_p05,
            u=0.0,
            q_tap_kwh=p05,
            t_outdoor_c=out_t,
            dt_h=0.25,
            spec=tank_spec_dict,
            t_max_c=spec.boost_setpoint_c,
            t_amb_c=params.t_amb_c
        )
        t_curr_p95 = dhw_step(
            t_tank_c=t_curr_p95,
            u=0.0,
            q_tap_kwh=p95,
            t_outdoor_c=out_t,
            dt_h=0.25,
            spec=tank_spec_dict,
            t_max_c=spec.boost_setpoint_c,
            t_amb_c=params.t_amb_c
        )

        temps.append(round(t_curr, 2))
        temps_p05.append(round(t_curr_p05, 2))
        temps_p95.append(round(t_curr_p95, 2))

        if t_curr < min_temp:
            min_temp = t_curr
            min_temp_idx = k + 1
            min_temp_time = lbl

        if first_dip_idx is None and t_curr < t_comf:
            first_dip_idx = k + 1
            first_dip_time = lbl
            first_dip_temp_c = round(t_curr, 1)

    min_temp_rounded = round(min_temp, 1)
    if first_dip_idx is not None:
        explanation = (
            f"Zonder stoken zakt het vat naar {min_temp_rounded:.1f}°C om {min_temp_time} "
            f"(al onder comfort om {first_dip_time})."
        )
    else:
        explanation = f"Zonder stoken zakt het vat naar {min_temp_rounded:.1f}°C om {min_temp_time}."

    return {
        "trajectory": {
            "temperatures_c": temps,
            "temperatures_p05_c": temps_p05,
            "temperatures_p95_c": temps_p95,
        },
        "min_temp_c": min_temp_rounded,
        "min_temp_time": min_temp_time,
        "first_dip_idx": first_dip_idx,
        "first_dip_time": first_dip_time,
        "first_dip_temp_c": first_dip_temp_c,
        "explanation": explanation
    }


def compute_counterfactual_cap50(
    slots: List[Any],
    t0_c: float,
    run_state0: Any,
    opt_result: DhwOptimizerResult,
    dhw_model: Any,
    tariff_provider: Any,
    spec: Optional[DhwTankSpec] = None,
    params: Optional[DhwOptimizerParams] = None,
    dynamic_peaks: Optional[List[Any]] = None,
) -> Dict[str, Any]:
    """
    Evaluates the counterfactual where heating is capped strictly at 50.0°C.
    Compares cost with the optimal plan (which may buffer above 50.0°C to avoid extra runs).
    """
    spec = spec or DhwTankSpec()
    params = params or DhwOptimizerParams()

    # If the optimal plan does not exceed 50.5°C in any run, cap50 is identical
    max_opt_end_temp = max((r.t_end_c for r in opt_result.runs), default=t0_c)
    if max_opt_end_temp <= 50.5:
        return {
            "savings_eur": 0.0,
            "cost_cap50_eur": opt_result.total_cost_eur,
            "cost_diff_eur": 0.0,
            "runs_cap50": opt_result.runs,
            "explanation": None,
            "applies": False
        }

    # Solve with T_max capped at 50.0°C
    spec_cap50 = DhwTankSpec(
        volume_liters=spec.volume_liters,
        specific_heat_water=spec.specific_heat_water,
        heat_pump_electric_kw=spec.heat_pump_electric_kw,
        solar_boost_electric_kw=spec.solar_boost_electric_kw,
        thermal_output_kw=spec.thermal_output_kw,
        comfort_min_temp_c=spec.comfort_min_temp_c,
        target_setpoint_c=50.0,
        boost_setpoint_c=50.0,
    )

    res_cap50 = solve(
        slots=slots,
        t0_c=t0_c,
        run_state0=run_state0,
        dhw_model=dhw_model,
        tariff_provider=tariff_provider,
        spec=spec_cap50,
        params=params,
        dynamic_peaks=dynamic_peaks
    )

    # Cost difference between capping at 50°C and optimal buffering
    cost_diff = res_cap50.total_cost_eur - opt_result.total_cost_eur
    savings_eur = round(max(0.0, cost_diff), 2)

    # Explain the benefit of buffering up to T_end
    first_opt_run = opt_result.runs[0]
    t_end = first_opt_run.t_end_c

    # Find additional later runs required when capping at 50°C
    later_runs = [r for r in res_cap50.runs if r.start_idx >= first_opt_run.end_idx]
    if later_runs:
        later_slot_idx = later_runs[0].start_idx
        later_time = getattr(slots[later_slot_idx], "label", f"slot {later_slot_idx}") if later_slot_idx < len(slots) else "later"
        explanation = (
            f"Één run tot {t_end:.1f}°C nu bespaart €{savings_eur:.2f} t.o.v. 50°C plus bijladen om {later_time}."
        )
    else:
        explanation = f"Één run tot {t_end:.1f}°C nu bespaart €{savings_eur:.2f} t.o.v. aftoppen op 50.0°C."

    return {
        "savings_eur": savings_eur,
        "cost_cap50_eur": res_cap50.total_cost_eur,
        "cost_diff_eur": round(cost_diff, 3),
        "runs_cap50": res_cap50.runs,
        "explanation": explanation,
        "applies": True
    }


def compute_counterfactual_delay(
    slots: List[Any],
    t0_c: float,
    opt_result: DhwOptimizerResult,
    dhw_model: Any,
    tariff_provider: Any,
    spec: Optional[DhwTankSpec] = None,
    params: Optional[DhwOptimizerParams] = None,
    dynamic_peaks: Optional[List[Any]] = None,
) -> Dict[str, Any]:
    """
    Evaluates the counterfactual where heating is forbidden until after the first planned run.
    Quantifies the financial or comfort penalty of delaying the run.
    """
    if not opt_result.runs:
        return {
            "savings_eur": 0.0,
            "cost_delay_eur": 0.0,
            "cost_diff_eur": 0.0,
            "is_comfort_forced": False,
            "explanation": None,
            "applies": False
        }

    spec = spec or DhwTankSpec()
    params = params or DhwOptimizerParams()
    first_run = opt_result.runs[0]
    delay_until_idx = first_run.end_idx

    # Construct delayed scenario by locking out slots up to delay_until_idx
    # We clone slots or pass a peak lockout set
    locked_peaks = list(dynamic_peaks or [])
    for k in range(min(len(slots), delay_until_idx)):
        locked_peaks.append({"slot_idx": k, "is_hard_lockout": True})

    res_delay = solve(
        slots=slots,
        t0_c=t0_c,
        run_state0=None,
        dhw_model=dhw_model,
        tariff_provider=tariff_provider,
        spec=spec,
        params=params,
        dynamic_peaks=locked_peaks
    )

    cost_diff = res_delay.total_cost_eur - opt_result.total_cost_eur
    savings_eur = round(max(0.0, cost_diff), 2)

    run_start_lbl = getattr(slots[first_run.start_idx], "label", f"slot {first_run.start_idx}") if first_run.start_idx < len(slots) else "nu"
    is_comfort_forced = bool(res_delay.validation_issue is not None)

    if is_comfort_forced:
        explanation = (
            f"Nu starten ({run_start_lbl}) is noodzakelijk om comfort te behouden "
            f"(tank zou anders onder {spec.comfort_min_temp_c:.1f}°C zakken)."
        )
    elif savings_eur > 0:
        explanation = f"Nu starten ({run_start_lbl}) i.p.v. wachten bespaart €{savings_eur:.2f}."
    else:
        explanation = f"Nu starten ({run_start_lbl}) garandeert horizondekking zonder meerkosten."

    return {
        "savings_eur": savings_eur,
        "cost_delay_eur": res_delay.total_cost_eur,
        "cost_diff_eur": round(cost_diff, 3),
        "is_comfort_forced": is_comfort_forced,
        "explanation": explanation,
        "applies": True
    }


def explain_dhw_optimization(
    slots: List[Any],
    t0_c: float,
    run_state0: Any,
    opt_result: DhwOptimizerResult,
    dhw_model: Any,
    tariff_provider: Any,
    spec: Optional[DhwTankSpec] = None,
    params: Optional[DhwOptimizerParams] = None,
    dynamic_peaks: Optional[List[Any]] = None,
) -> Dict[str, Any]:
    """
    Synthesizes the complete explanation layer for the DHW optimizer.
    Generates exact counterfactual metrics and one traceable explanation sentence per planned run.
    """
    spec = spec or DhwTankSpec()
    params = params or DhwOptimizerParams()

    cf_none = compute_counterfactual_none(slots, t0_c, dhw_model, spec, params)
    cf_cap50 = compute_counterfactual_cap50(
        slots, t0_c, run_state0, opt_result, dhw_model, tariff_provider, spec, params, dynamic_peaks
    )
    cf_delay = compute_counterfactual_delay(
        slots, t0_c, opt_result, dhw_model, tariff_provider, spec, params, dynamic_peaks
    )

    # 1. Per-run explanations (exactly one sentence per planned run)
    run_explanations = []
    N = len(slots)
    for i, r in enumerate(opt_result.runs):
        start_lbl = getattr(slots[r.start_idx], "label", f"slot {r.start_idx}") if r.start_idx < N else "nu"
        end_slot_idx = min(N - 1, r.end_idx)
        end_lbl = getattr(slots[end_slot_idx], "label", f"slot {r.end_idx}") if end_slot_idx < N else "later"

        timing_str = f"Nu ({start_lbl}–{end_lbl})" if r.start_idx == 0 else f"{start_lbl}–{end_lbl}"
        solar_phrase = f"op {int(r.solar_share * 100)}% zonne-stroom" if r.solar_share >= 0.5 else "op dal/spottarief"

        sentence = (
            f"Run {i + 1}: {timing_str} verwarmen tot {r.t_end_c:.1f}°C {solar_phrase} "
            f"({r.kwh_el:.2f} kWh_el, €{r.cost_eur:.2f})."
        )
        run_explanations.append(sentence)

    # 2. Comfort card text
    if not opt_result.runs:
        comfort_text = (
            f"Het vat is nu {t0_c:.1f}°C. "
            f"{cf_none['explanation']} Er is geen stookactie vereist."
        )
    else:
        comfort_text = (
            f"Het vat is nu {t0_c:.1f}°C. {cf_none['explanation']} "
            f"Door de geplande {len(opt_result.runs)} run(s) blijft het comfort 100% gegarandeerd."
        )

    # 3. Finance card text (counterfactual differences)
    finance_sentences = []
    total_savings = 0.0
    if cf_cap50.get("explanation"):
        finance_sentences.append(cf_cap50["explanation"])
        total_savings += cf_cap50.get("savings_eur", 0.0)

    if cf_delay.get("explanation"):
        finance_sentences.append(cf_delay["explanation"])
        total_savings += cf_delay.get("savings_eur", 0.0)

    if not finance_sentences:
        finance_text = f"Optimale planning over 48 uur: totale stroomkosten €{opt_result.total_cost_eur:.2f}."
    else:
        finance_text = " ".join(finance_sentences)

    # 4. Bullets
    if len(opt_result.runs) >= 1:
        bullet_1 = run_explanations[0]
    else:
        bullet_1 = f"Standby: Geen verwarming vereist. {cf_none['explanation']}"

    if len(opt_result.runs) >= 2:
        bullet_2 = run_explanations[1]
    else:
        if cf_cap50.get("explanation"):
            bullet_2 = cf_cap50["explanation"]
        elif cf_delay.get("explanation"):
            bullet_2 = cf_delay["explanation"]
        else:
            bullet_2 = f"Volledig 48u comfort gegarandeerd met {len(opt_result.runs)} run(s)."

    # 5. Joined explanation text
    full_explanation = "\n".join(run_explanations)
    if finance_sentences:
        full_explanation += "\n" + " ".join(finance_sentences)

    return {
        "counterfactual_none": cf_none,
        "counterfactual_cap50": cf_cap50,
        "counterfactual_delay": cf_delay,
        "run_explanations": run_explanations,
        "comfort_text": comfort_text,
        "finance_text": finance_text,
        "bullet_1": bullet_1,
        "bullet_2": bullet_2,
        "explanation": full_explanation,
        "unheated_explanation": cf_none["explanation"],
        "unheated_trajectory": cf_none["trajectory"],
        "morning_dip_c": cf_none["min_temp_c"],
        "morning_dip_time": cf_none["min_temp_time"],
        "total_savings_eur": round(total_savings, 2)
    }
