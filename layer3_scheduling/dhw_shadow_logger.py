"""
Open HEMS — DHW Shadow Mode & Multi-Planner Observability Logger
================================================================
Layer 3 Scheduling Submodule:
Executes parallel shadow evaluation of Arbiter vs Optimizer under identical
thermodynamic and economic boundary conditions without altering live dispatch.

Logs results to:
1. DecisionAuditLogger (/config/open_hems_decisions.jsonl and InfluxDB)
2. /config/open_hems_dhw_shadow.json (structured summary and rolling audit log)
"""

import os
import json
import time
from pathlib import Path
from typing import Dict, List, Any, Optional
from datetime import datetime
from zoneinfo import ZoneInfo

from layer3_scheduling.dhw_optimizer import solve, evaluate_plan_metrics, DhwOptimizerParams
from layer3_scheduling.dhw_specs import DhwTankSpec
from layer3_scheduling.decision_audit import DecisionAuditLogger

AMS_TZ = ZoneInfo("Europe/Amsterdam")
SHADOW_FILE = Path("/config/open_hems_dhw_shadow.json")
MAX_SHADOW_RECORDS = 500


def get_dhw_planner_mode(model_parameters: Optional[Dict[str, Any]] = None) -> str:
    """
    Returns the configured DHW planner mode: 'optimizer' (default) or 'shadow' (diagnostic).
    """
    if model_parameters and "dhw_planner" in model_parameters:
        mode = str(model_parameters["dhw_planner"]).lower().strip()
        return "shadow" if mode == "shadow" else "optimizer"

    for p in [Path("/config/heatpump_config.json"), Path("/config/heatpump_model_parameters.json")]:
        if p.exists():
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
                if "dhw_planner" in data:
                    mode = str(data["dhw_planner"]).lower().strip()
                    return "shadow" if mode == "shadow" else "optimizer"
            except Exception:
                pass

    return "optimizer"


def run_dhw_shadow_comparison(
    slots: List[Any],
    current_dhw_temp: float,
    dynamic_peaks: List[Any],
    arbiter_planned_slots: List[int],
    dhw_model: Any,
    tariff_provider: Any,
    spec: Optional[DhwTankSpec] = None,
    params: Optional[DhwOptimizerParams] = None,
    dur_arb_ms: float = 0.0,
    now_dt: Optional[datetime] = None,
    run_state0: Any = None,
) -> Dict[str, Any]:
    """
    Executes parallel shadow evaluation of the optimizer alongside the active arbiter plan.
    Both plans are evaluated with the EXACT same cost function J and physical step dynamics.
    """
    spec = spec or DhwTankSpec()
    params = params or DhwOptimizerParams()
    now = now_dt or datetime.now(AMS_TZ)
    N = len(slots)

    # 1. Run optimizer with high-resolution timer
    t0_opt = time.perf_counter()
    res_opt = solve(
        slots=slots,
        t0_c=current_dhw_temp,
        run_state0=run_state0,
        dhw_model=dhw_model,
        tariff_provider=tariff_provider,
        spec=spec,
        params=params,
        dynamic_peaks=dynamic_peaks
    )
    dur_opt_ms = round((time.perf_counter() - t0_opt) * 1000.0, 2)

    # 2. Build binary action arrays
    u_arb = [1 if i in arbiter_planned_slots else 0 for i in range(N)]
    u_opt = [1 if i in res_opt.planned_slots else 0 for i in range(N)]

    # 3. Evaluate both plans with identical objective metric J
    m_arb = evaluate_plan_metrics(
        slots=slots,
        u_plan=u_arb,
        t0_c=current_dhw_temp,
        spec=spec,
        params=params,
        tariff_provider=tariff_provider,
        dhw_model=dhw_model,
        run_state0=run_state0
    )
    m_opt = evaluate_plan_metrics(
        slots=slots,
        u_plan=u_opt,
        t0_c=current_dhw_temp,
        spec=spec,
        params=params,
        tariff_provider=tariff_provider,
        dhw_model=dhw_model,
        run_state0=run_state0
    )

    plans_differ = bool(u_arb != u_opt)
    cost_diff_j = round(m_arb["j_objective"] - m_opt["j_objective"], 4)
    cost_diff_total = round(m_arb["total_cost_eur"] - m_opt["total_cost_eur"], 4)

    record = {
        "timestamp_iso": now.isoformat(),
        "plans_differ": plans_differ,
        "cost_j_arbiter": m_arb["j_objective"],
        "cost_j_optimizer": m_opt["j_objective"],
        "cost_diff_j_eur": cost_diff_j,
        "total_cost_arbiter_eur": m_arb["total_cost_eur"],
        "total_cost_optimizer_eur": m_opt["total_cost_eur"],
        "cost_diff_total_eur": cost_diff_total,
        "runs_arbiter": m_arb["runs_count"],
        "runs_optimizer": m_opt["runs_count"],
        "active_slots_arbiter": sum(u_arb),
        "active_slots_optimizer": sum(u_opt),
        "t_min_arbiter_c": m_arb["t_min_c"],
        "t_min_optimizer_c": m_opt["t_min_c"],
        "t_max_arbiter_c": m_arb["t_max_c"],
        "t_max_optimizer_c": m_opt["t_max_c"],
        "comfort_breached_arbiter": m_arb["comfort_breached"],
        "comfort_breached_optimizer": m_opt["comfort_breached"],
        "dur_arb_ms": round(dur_arb_ms, 1),
        "dur_opt_ms": dur_opt_ms,
        "optimizer_planned_slots": res_opt.planned_slots,
        "arbiter_planned_slots": arbiter_planned_slots,
        "optimizer_validation_issue": res_opt.validation_issue
    }

    # 4. Log to DecisionAuditLogger (persistent JSONL and InfluxDB)
    reason_str = (
        f"DHW Schaduw Evaluatie: Arbiter J=€{m_arb['j_objective']:.3f} vs. Optimizer J=€{m_opt['j_objective']:.3f} "
        f"({'Verschil €' + str(cost_diff_j) if plans_differ else 'Identiek plan'})"
    )
    explanation_str = (
        f"Arbiter plande {m_arb['runs_count']} run(s) ({sum(u_arb)} slots, Tmin={m_arb['t_min_c']}°C, {dur_arb_ms:.1f}ms). "
        f"Optimizer plande {m_opt['runs_count']} run(s) ({sum(u_opt)} slots, Tmin={m_opt['t_min_c']}°C, {dur_opt_ms:.1f}ms). "
        f"Potentiële besparing: €{max(0.0, cost_diff_j):.3f}."
    )

    try:
        DecisionAuditLogger.log_decision(
            domain="dhw_shadow",
            decision_type="planner_comparison",
            chosen_mode="shadow",
            target_temp_c=spec.target_setpoint_c,
            inputs={
                "t0_c": current_dhw_temp,
                "plans_differ": plans_differ,
                "j_arb": m_arb["j_objective"],
                "j_opt": m_opt["j_objective"],
                "runs_arb": m_arb["runs_count"],
                "runs_opt": m_opt["runs_count"],
                "dur_opt_ms": dur_opt_ms,
                "comfort_breached_opt": m_opt["comfort_breached"]
            },
            reason=reason_str,
            explanation=explanation_str,
            savings_estimate_eur=max(0.0, cost_diff_j),
            category="SHADOW_AUDIT"
        )
    except Exception as e_log:
        print(f"[WARN] Error logging DHW shadow decision audit: {e_log}")

    # 5. Append & update rotating JSON file in /config
    _save_shadow_record_json(record)

    return record


def _save_shadow_record_json(record: Dict[str, Any]):
    """Appends record to /config/open_hems_dhw_shadow.json and maintains cumulative statistics."""
    try:
        data = {"summary": {}, "history": []}
        if SHADOW_FILE.exists():
            try:
                data = json.loads(SHADOW_FILE.read_text(encoding="utf-8"))
            except Exception:
                data = {"summary": {}, "history": []}

        history = data.get("history", [])
        history.append(record)
        if len(history) > MAX_SHADOW_RECORDS:
            history = history[-MAX_SHADOW_RECORDS:]

        # Compute cumulative summary metrics across history
        n_total = len(history)
        n_differ = sum(1 for h in history if h.get("plans_differ"))
        savings_j_sum = sum(h.get("cost_diff_j_eur", 0.0) for h in history)
        savings_tot_sum = sum(h.get("cost_diff_total_eur", 0.0) for h in history)
        comfort_breaches_opt = sum(1 for h in history if h.get("comfort_breached_optimizer"))
        avg_dur_arb = sum(h.get("dur_arb_ms", 0.0) for h in history) / n_total if n_total else 0.0
        avg_dur_opt = sum(h.get("dur_opt_ms", 0.0) for h in history) / n_total if n_total else 0.0
        avg_runs_arb = sum(h.get("runs_arbiter", 0) for h in history) / n_total if n_total else 0.0
        avg_runs_opt = sum(h.get("runs_optimizer", 0) for h in history) / n_total if n_total else 0.0

        summary = {
            "total_cycles": n_total,
            "different_plans_count": n_differ,
            "different_plans_pct": round((n_differ / n_total) * 100.0, 1) if n_total else 0.0,
            "total_savings_j_eur": round(savings_j_sum, 3),
            "avg_savings_j_eur": round(savings_j_sum / n_total, 4) if n_total else 0.0,
            "total_savings_direct_eur": round(savings_tot_sum, 3),
            "avg_runs_arbiter": round(avg_runs_arb, 2),
            "avg_runs_optimizer": round(avg_runs_opt, 2),
            "comfort_breaches_optimizer": comfort_breaches_opt,
            "avg_duration_arb_ms": round(avg_dur_arb, 1),
            "avg_duration_opt_ms": round(avg_dur_opt, 1),
            "last_updated_iso": record["timestamp_iso"]
        }

        output = {
            "summary": summary,
            "history": history
        }

        tmp_path = f"{SHADOW_FILE}.tmp.{os.getpid()}"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(output, f, indent=2)
        os.replace(tmp_path, str(SHADOW_FILE))
    except Exception as e_sh:
        print(f"[WARN] Error updating {SHADOW_FILE}: {e_sh}")
