"""
Layer 3: DHW Autonomous Baseline Simulator (WP7)
================================================
Simulates the autonomous baseline behavior of the Daikin Altherma heat pump:
- The heat pump starts autonomously when tank temperature drops to (setpoint - auto_start_delta_c) (typically 50°C - 10K = 40°C).
- The heat pump stops when tank temperature reaches setpoint_c (typically 50°C).
- Hard lockouts (spitsblokkades SG1) suppress compressor operation.
- Evaluates trajectory, runs, and the exact objective function J via evaluate_plan_metrics().

This baseline represents the true counterfactual: "What happens if Open HEMS does nothing?".
The optimizer must beat this baseline on objective J to justify intervention.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Any, Optional

from layer3_scheduling.dhw_specs import DhwTankSpec
from layer3_scheduling.dhw_optimizer import (
    DhwOptimizerParams,
    evaluate_plan_metrics,
)
from models.physics import dhw_step


@dataclass
class DhwBaselineResult:
    """
    Result of the autonomous heat pump operation simulation.
    """
    u_plan: List[int]
    temperatures_c: List[float]
    temperatures_p05_c: List[float]
    temperatures_p95_c: List[float]
    demand_kwh_th: List[float]
    runs: List[Dict[str, Any]]
    electricity_cost_eur: float
    start_cost_eur: float
    salvage_value_eur: float
    j_objective_eur: float
    total_cost_eur: float
    min_temp_c: float
    first_run_start_slot: Optional[int] = None
    first_run_start_time: Optional[str] = None


def simulate_autonomous(
    slots: List[Any],
    t0_c: float,
    setpoint_c: float = 50.0,
    auto_start_delta_c: float = 10.0,
    dhw_model: Any = None,
    spec: Optional[DhwTankSpec] = None,
    params: Optional[DhwOptimizerParams] = None,
    model_parameters: Optional[Dict[str, Any]] = None,
) -> DhwBaselineResult:
    """
    Pure simulation of autonomous thermostat behavior.
    """
    spec = spec or DhwTankSpec()
    params = params or DhwOptimizerParams()
    N = len(slots)
    if N == 0:
        return DhwBaselineResult(
            u_plan=[],
            temperatures_c=[round(t0_c, 2)],
            temperatures_p05_c=[round(t0_c, 2)],
            temperatures_p95_c=[round(t0_c, 2)],
            demand_kwh_th=[],
            runs=[],
            electricity_cost_eur=0.0,
            start_cost_eur=0.0,
            salvage_value_eur=0.0,
            j_objective_eur=0.0,
            total_cost_eur=0.0,
            min_temp_c=round(t0_c, 2),
        )

    start_threshold_c = setpoint_c - auto_start_delta_c
    stop_threshold_c = setpoint_c

    C_tank = spec.thermal_capacity_kwh_per_k
    ua_w_per_k = dhw_model.get_tank_ua() if (dhw_model and hasattr(dhw_model, "get_tank_ua")) else 2.5
    tank_spec_dict = {
        "thermal_capacity_kwh_per_k": C_tank,
        "ua_w_per_k": ua_w_per_k,
        "ambient_temp_c": params.t_amb_c,
        "target_temp_c": spec.boost_setpoint_c,
    }

    p_nom = spec.heat_pump_electric_kw
    u_plan: List[int] = []
    is_heating = False
    dwell_counter = 0
    min_dwell = max(1, params.min_run_slots)

    t_curr = t0_c
    temps = [round(t_curr, 2)]

    for k, s in enumerate(slots):
        dt_val = getattr(s, "dt", None)
        if dt_val is None:
            dt_val = datetime.now(timezone.utc) + timedelta(minutes=15 * k)
        dow = dt_val.weekday()
        q_idx = dt_val.hour * 4 + dt_val.minute // 15
        p50 = dhw_model.get_learned_tap_kwh_th(dow, q_idx) if (dhw_model and hasattr(dhw_model, "get_learned_tap_kwh_th")) else 0.05
        out_t = float(getattr(s, "outdoor_temp_c", getattr(s, "outdoor_temp", 10.0)))
        is_locked = bool(getattr(s, "is_hard_lockout", False))

        # Thermostat logic
        if is_locked:
            # Spitsblokkade suppresses heating
            u = 0
            is_heating = False
            dwell_counter = 0
        else:
            if not is_heating:
                if t_curr <= start_threshold_c + 1e-4:
                    is_heating = True
                    dwell_counter = 1
                    u = 1
                else:
                    u = 0
            else:
                dwell_counter += 1
                # Stop if setpoint reached AND minimum run duration satisfied
                if t_curr >= stop_threshold_c - 1e-4 and dwell_counter >= min_dwell:
                    is_heating = False
                    dwell_counter = 0
                    u = 0
                else:
                    u = 1

        u_plan.append(u)

        # Advance physics
        t_curr = dhw_step(
            t_tank_c=t_curr,
            u=float(u),
            q_tap_kwh=p50,
            t_outdoor_c=out_t,
            dt_h=0.25,
            spec=spec,
            params=model_parameters,
            t_max_c=spec.boost_setpoint_c,
            t_amb_c=params.t_amb_c
        )
        temps.append(round(t_curr, 2))

    # Evaluate full J metrics using canonical evaluation
    metrics = evaluate_plan_metrics(
        slots=slots,
        u_plan=u_plan,
        t0_c=t0_c,
        spec=spec,
        params=params,
        tariff_provider=None,
        dhw_model=dhw_model,
        model_parameters=model_parameters
    )

    first_start = None
    first_time = None
    for k, u in enumerate(u_plan):
        if u == 1:
            first_start = k
            dt_val = getattr(slots[k], "dt", None)
            first_time = getattr(slots[k], "label", dt_val.strftime("%H:%M") if dt_val else f"slot {k}")
            break

    return DhwBaselineResult(
        u_plan=u_plan,
        temperatures_c=metrics.get("temperatures_c", temps),
        temperatures_p05_c=metrics.get("temperatures_p05_c", temps),
        temperatures_p95_c=metrics.get("temperatures_p95_c", temps),
        demand_kwh_th=metrics.get("demand_kwh_th", []),
        runs=metrics.get("runs", []),
        electricity_cost_eur=metrics.get("electricity_cost_eur", 0.0),
        start_cost_eur=metrics.get("start_cost_eur", 0.0),
        salvage_value_eur=metrics.get("salvage_value_eur", 0.0),
        j_objective_eur=metrics.get("j_objective", metrics.get("j_objective_eur", 0.0)),
        total_cost_eur=metrics.get("total_cost_eur", 0.0),
        min_temp_c=min(metrics.get("temperatures_c", temps)),
        first_run_start_slot=first_start,
        first_run_start_time=first_time,
    )
