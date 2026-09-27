"""
Open HEMS — Canonical Domestic Hot Water (DHW / SWW) Exact DP Optimizer
========================================================================
Layer 3 Scheduling Engine: Exact Dynamic Programming over Tank Temperature & Compressor State.

Model & Mathematics:
--------------------
Horizon: k = 0..N-1 quarter-hours (N = 192 for 48h), Delta_t = 0.25 h.
Tank State: T_k (°C) at start of slot k. Initial T_0 measured. Control u_k in {0, 1}.
Parameters:
  - C = spec.thermal_capacity_kwh_per_k (kWh / K)
  - UA = dhw_model.get_tank_ua() (W / K), T_amb = 18.0 °C
  - Q_tap,k = P50 demand, Q_tap95,k = P95 demand (kWh_th)
  - p_eff,k = effective electricity price (€/kWh) for heat pump in slot k
  - P_el(T) = spec.get_electric_power_kw(T)
  - T_comf = spec.comfort_min_temp_c (40.0°C), T_max = spec.boost_setpoint_c (60.0°C)

Dynamics (via canonical models/physics.py::dhw_step):
  T_{k+1} = T_k + ( u_k * P_el(T_k) * COP(T_k, T_out,k) * Delta_t - UA * (T_k - T_amb) * Delta_t / 1000 - Q_tap,k ) / C

Objective (Minimize):
  J = sum_k [ u_k * P_el(T_k) * Delta_t * p_eff,k ]
    + c_start * sum_k [ max(0, u_k - u_{k-1}) ]
    - C * (T_N - T_comf) * ( p_hat / COP_hat )
  where:
    p_hat = average of the cheapest 20% of p_eff across the horizon
    COP_hat = COP(50.0, average T_out)

Constraints:
  1. Comfort Buffer: T_k >= T_comf + m_k for all k.
     m_k = ( sum_{j=k}^{k+H} (Q_tap95,j - Q_tap,j) ) / C with H = 32 slots (8 h) by default.
  2. Bounding: T_k <= T_max.
  3. Minimum Run & Dwell Times:
     r in {OFF_FREE, OFF_DWELL(d) for d=1..D_min-1, ON_MANDATORY(l) for l=1..L_min-1, ON_FREE}.
     If compressor is currently running and has not reached L_min, u_0 = 1 is mandatory.
  4. Hard Lockouts: u_k = 0 in any slot marked as hard lockout.

Algorithm:
  Backward induction over discretized state space (T on 0.25°C grid, discrete r),
  with linear interpolation of V_{k+1}(T', r') on continuous T'.
  Forward pass reconstructs optimal trajectory (T*_k, r*_k, u*_k).
  If (T_0, r_0) is infeasible, gracefully degrades to emergency recovery outside lockouts.
"""

from dataclasses import dataclass, field
from typing import Dict, List, Any, Optional, Tuple, Union
from datetime import datetime, timezone, timedelta
import math
import time
import numpy as np

from models.physics import dhw_cop, dhw_heat_delivered_kwh, dhw_step
from layer2_calibration.dhw_thermal_model import DhwThermalModel
from layer3_scheduling.dhw_specs import DhwTankSpec
from layer3_scheduling.tariff_provider import TariffProvider
from layer3_scheduling.dhw_financials import calculate_slot_financials


@dataclass
class DhwOptimizerParams:
    """All parameters for the DHW dynamic programming solver (zero magic numbers)."""
    c_start: float = 0.05                 # Start cost (€) per compressor ignition
    min_run_slots: int = 3                # L_min (3 slots = 45 min default for backward-compatible replays)
    min_dwell_slots: int = 4              # D_min (4 slots = 60 min rest after run)
    use_dynamic_margin: bool = True       # True for dynamic stress margin, False for fixed
    comfort_margin_mode: str = "p60"      # "p60" (economical minimal buffer) | "p95" | "p50" | "fixed"
    tap_stress_factor: float = 1.15       # P60 factor (default 1.15, modest headroom without overheating)
    min_comfort_margin_c: float = 0.2     # Minimum floor margin (°C)
    fixed_comfort_margin_c: float = 2.0   # Fixed comfort buffer (°C) if mode == "fixed"
    dynamic_horizon_slots: int = 32       # H = 32 slots (8 hours lookahead)
    grid_step_c: float = 0.25             # Temperature discretization step (°C)
    terminal_cheap_percentile: float = 0.20 # Top 20% cheapest slots for salvage value p_hat
    t_amb_c: float = 18.0                 # Technical room ambient temperature (°C)

    @classmethod
    def from_config(cls, cfg: Optional[Dict[str, Any]] = None) -> "DhwOptimizerParams":
        if not cfg:
            return cls()
        opt_cfg = cfg.get("dhw_optimizer", {})
        cm = opt_cfg.get("comfort_margin", {})
        mode = str(cm.get("mode", "p60")).lower()
        if mode not in ("p60", "p95", "p50", "fixed"):
            mode = "p60"
        factor = float(cm.get("tap_stress_factor", 1.15 if mode == "p60" else 1.5))
        factor = max(1.0, min(3.0, factor))
        h_hours = float(cm.get("horizon_hours", 8.0))
        h_slots = max(4, min(96, int(round(h_hours * 4))))
        min_m = float(cm.get("min_margin_c", 0.2 if mode == "p60" else 0.5))
        min_m = max(0.0, min(5.0, min_m))
        fixed_m = float(cm.get("fixed_margin_c", 2.0))
        fixed_m = max(0.0, min(10.0, fixed_m))
        c_start_val = float(opt_cfg.get("c_start", 0.05))
        l_min = int(opt_cfg.get("min_run_slots", 3))
        d_min = int(opt_cfg.get("min_dwell_slots", 4))

        return cls(
            c_start=c_start_val,
            min_run_slots=l_min,
            min_dwell_slots=d_min,
            comfort_margin_mode=mode,
            use_dynamic_margin=(mode in ("p60", "p95")),
            tap_stress_factor=factor,
            dynamic_horizon_slots=h_slots,
            min_comfort_margin_c=min_m,
            fixed_comfort_margin_c=fixed_m,
        )


@dataclass
class DhwRun:
    """Represents a continuous heat pump DHW heating cycle."""
    start_idx: int
    end_idx: int
    t_start_c: float
    t_end_c: float
    kwh_el: float
    cost_eur: float
    solar_share: float


@dataclass
class DhwOptimizerResult:
    """Complete results from DHW dynamic programming solver."""
    planned_slots: List[int]
    runs: List[DhwRun]
    trajectory: Dict[str, List[float]]
    total_cost_eur: float
    slot_modes: List[str]
    validation_issue: Optional[str] = None
    solve_duration_ms: float = 0.0
    electricity_cost_eur: float = 0.0
    start_cost_eur: float = 0.0
    salvage_value_eur: float = 0.0
    j_objective_eur: float = 0.0


# Internal discrete state representation for r:
# 0: OFF_FREE
# 1 .. D_min-1: OFF_DWELL(d)
# D_min .. D_min + L_min - 2: ON_MANDATORY(l)
# D_min + L_min - 1: ON_FREE
def _build_r_index_map(l_min: int, d_min: int):
    states = [("OFF_FREE", 0)]
    for d in range(1, d_min):
        states.append(("OFF_DWELL", d))
    for l in range(1, l_min):
        states.append(("ON_MANDATORY", l))
    states.append(("ON_FREE", 0))

    state_to_idx = {s: i for i, s in enumerate(states)}
    idx_to_state = {i: s for i, s in enumerate(states)}
    return states, state_to_idx, idx_to_state


def parse_initial_run_state(
    run_state0: Any,
    l_min: int,
    d_min: int,
    state_to_idx: Dict[Tuple[str, int], int]
) -> int:
    """Parses various caller representations of run_state0 into discrete r index."""
    if run_state0 is None:
        return state_to_idx[("OFF_FREE", 0)]

    if isinstance(run_state0, int):
        if 0 <= run_state0 < len(state_to_idx):
            return run_state0
        return state_to_idx[("OFF_FREE", 0)]

    if isinstance(run_state0, tuple) and len(run_state0) == 2:
        mode, count = str(run_state0[0]).upper(), int(run_state0[1])
        if mode in ("ON", "ON_MANDATORY", "ON_FREE"):
            if 0 < count < l_min:
                return state_to_idx[("ON_MANDATORY", count)]
            return state_to_idx[("ON_FREE", 0)]
        elif mode in ("OFF", "OFF_DWELL", "OFF_FREE", "STANDBY"):
            if 0 < count < d_min:
                return state_to_idx[("OFF_DWELL", count)]
            return state_to_idx[("OFF_FREE", 0)]

    if isinstance(run_state0, dict):
        is_on = run_state0.get("mode", "").upper() == "ON" or run_state0.get("is_running", False)
        elapsed = int(run_state0.get("elapsed_slots", run_state0.get("active_slots", 0)))
        if is_on:
            if 0 < elapsed < l_min:
                return state_to_idx[("ON_MANDATORY", elapsed)]
            return state_to_idx[("ON_FREE", 0)]
        else:
            if 0 < elapsed < d_min:
                return state_to_idx[("OFF_DWELL", elapsed)]
            return state_to_idx[("OFF_FREE", 0)]

    if not isinstance(run_state0, (tuple, dict, str, int)) and hasattr(run_state0, "mode") and hasattr(run_state0, "elapsed_slots"):
        mode = str(getattr(run_state0, "mode")).upper()
        elapsed = int(getattr(run_state0, "elapsed_slots"))
        if mode == "ON":
            if 0 < elapsed < l_min:
                return state_to_idx[("ON_MANDATORY", elapsed)]
            return state_to_idx[("ON_FREE", 0)]
        else:
            if 0 < elapsed < d_min:
                return state_to_idx[("OFF_DWELL", elapsed)]
            return state_to_idx[("OFF_FREE", 0)]

    if isinstance(run_state0, str):
        s = run_state0.strip().upper()
        if s.startswith("ON"):
            return state_to_idx[("ON_FREE", 0)]
        return state_to_idx[("OFF_FREE", 0)]

    return state_to_idx[("OFF_FREE", 0)]


def _interp_finite(t: float, T_grid: np.ndarray, v_col: np.ndarray, t_min_feas: float) -> float:
    """
    Interpolates cost-to-go on continuous temperature t, interpolating strictly over finite grid values.
    Returns +inf if t is below continuous feasible comfort boundary t_min_feas.
    Clamps to the first finite value if t is between t_min_feas and T_grid[fin[0]] to avoid boundary creep.
    """
    if t < (t_min_feas - 1e-4):
        return np.inf
    fin = np.isfinite(v_col)
    if not np.any(fin):
        return np.inf
    T_fin = T_grid[fin]
    V_fin = v_col[fin]
    return float(np.interp(t, T_fin, V_fin, left=V_fin[0], right=V_fin[-1]))


def solve(
    slots: List[Any],
    t0_c: float,
    run_state0: Any = None,
    dhw_model: Optional[DhwThermalModel] = None,
    tariff_provider: Optional[TariffProvider] = None,
    spec: Optional[DhwTankSpec] = None,
    params: Optional[DhwOptimizerParams] = None,
    dynamic_peaks: Optional[List[Any]] = None,
    model_parameters: Optional[Dict[str, Any]] = None,
    in_flight_slots: int = 0,
    in_flight_target_c: float = 50.0,
) -> DhwOptimizerResult:
    """
    Pure function: Solves the DHW optimal scheduling problem via exact backward Dynamic Programming.

    Parameters:
      slots: List of 15-minute slot telemetry/forecast objects across horizon.
      t0_c: Currently measured DHW tank temperature (°C).
      run_state0: Current heat pump running status (e.g. ('ON_MANDATORY', 1) or 'OFF_FREE').
      dhw_model: DhwThermalModel instance (contains 7x96 tap matrices and tank UA).
      tariff_provider: TariffProvider for dynamic feed-in / import rate calculations.
      spec: DhwTankSpec encapsulating physical tank parameters.
      params: DhwOptimizerParams instance.
      dynamic_peaks: Optional list of dynamic peak lockouts.

    Returns:
      DhwOptimizerResult containing planned_slots, runs, trajectory, slot_modes, total_cost_eur.
    """
    start_perf = time.perf_counter()

    # 1. Defaults & instances
    spec = spec or DhwTankSpec()
    dhw_model = dhw_model or DhwThermalModel()
    tariff_provider = tariff_provider or TariffProvider()
    p = params or DhwOptimizerParams()

    N = len(slots)
    if N == 0:
        return DhwOptimizerResult(
            planned_slots=[],
            runs=[],
            trajectory={"temperatures_c": [t0_c], "temperatures_p05_c": [t0_c], "temperatures_p95_c": [t0_c]},
            total_cost_eur=0.0,
            slot_modes=[],
            validation_issue="Empty slots array",
            solve_duration_ms=0.0
        )

    delta_t = 0.25  # 15 minutes = 0.25 h
    C_tank = spec.thermal_capacity_kwh_per_k
    ua_w_per_k = dhw_model.get_tank_ua()
    t_comf = spec.comfort_min_temp_c
    t_max = spec.boost_setpoint_c

    # 2. Lockout map
    lockouts = [False] * N
    peak_lockout_set = set()
    if dynamic_peaks:
        for pk in dynamic_peaks:
            if isinstance(pk, dict) and pk.get("is_hard_lockout"):
                idx = pk.get("slot_idx")
                if idx is not None and 0 <= idx < N:
                    peak_lockout_set.add(idx)
            elif hasattr(pk, "is_hard_lockout") and getattr(pk, "is_hard_lockout"):
                idx = getattr(pk, "slot_idx", None)
                if idx is not None and 0 <= idx < N:
                    peak_lockout_set.add(idx)

    for k, s in enumerate(slots):
        is_locked = getattr(s, "is_hard_lockout", False) or (k in peak_lockout_set)
        lockouts[k] = bool(is_locked)

    # 3. Discrete states for compressor state r
    states_r, state_to_idx, idx_to_state = _build_r_index_map(p.min_run_slots, p.min_dwell_slots)
    R = len(states_r)
    idx_off_free = state_to_idx[("OFF_FREE", 0)]
    idx_on_free = state_to_idx[("ON_FREE", 0)]
    r0_idx = parse_initial_run_state(run_state0, p.min_run_slots, p.min_dwell_slots, state_to_idx)

    # 4. Extract slot environmental & tap demand variables
    # Pre-extract values for fast numpy inner operations
    out_temps = []
    q_tap_p50 = []
    q_tap_p95 = []
    q_tap_p05 = []
    prices_all_in = []
    solar_kws = []
    unalloc_kws = []

    for k, s in enumerate(slots):
        dt_val = getattr(s, "dt", None)
        if dt_val is None:
            dt_val = datetime.now(timezone.utc) + timedelta(minutes=15 * k)
        dow = dt_val.weekday()
        q_idx = dt_val.hour * 4 + dt_val.minute // 15

        out_t = float(getattr(s, "outdoor_temp_c", getattr(s, "outdoor_temp", 10.0)))
        out_temps.append(out_t)

        p50 = dhw_model.get_learned_tap_kwh_th(dow, q_idx)
        p95 = dhw_model.get_learned_tap_kwh_th_p95(dow, q_idx)
        p05 = dhw_model.get_learned_tap_kwh_th_p05(dow, q_idx)
        q_tap_p50.append(p50)
        q_tap_p95.append(p95)
        q_tap_p05.append(p05)

        prices_all_in.append(float(getattr(s, "price_all_in", 0.25)))
        solar_kws.append(float(getattr(s, "solar_kw", 0.0)))
        unalloc_kws.append(float(getattr(s, "unallocated_kw", 0.30)))

    # Compute dynamic comfort margins m_k
    m_margins = [0.0] * (N + 1)
    if p.comfort_margin_mode == "p95":
        min_floor = max(0.0, float(getattr(p, "min_comfort_margin_c", 0.5)))
        for k in range(N):
            window_end = min(N, k + p.dynamic_horizon_slots)
            stress_diff_sum = sum(q_tap_p95[j] - q_tap_p50[j] for j in range(k, window_end))
            m_dyn = stress_diff_sum / C_tank
            m_margins[k] = max(min_floor, round(m_dyn, 2))
        m_margins[N] = m_margins[N - 1]
    elif p.comfort_margin_mode == "p60":
        # P60 margin: modest headroom (+15-20% tap stress) avoiding unnecessary overheating to 60°C
        min_floor = max(0.0, float(getattr(p, "min_comfort_margin_c", 0.2)))
        for k in range(N):
            window_end = min(N, k + p.dynamic_horizon_slots)
            stress_diff_sum = sum(0.22 * (q_tap_p95[j] - q_tap_p50[j]) for j in range(k, window_end))
            m_dyn = stress_diff_sum / C_tank
            m_margins[k] = max(min_floor, round(m_dyn, 2))
        m_margins[N] = m_margins[N - 1]
    elif p.comfort_margin_mode == "fixed":
        fixed_val = max(0.0, float(getattr(p, "fixed_comfort_margin_c", 2.0)))
        for k in range(N + 1):
            m_margins[k] = fixed_val
    else:  # "p50" mode: pure median tap demand without stress buffer
        p50_margin = max(0.0, float(getattr(p, "min_comfort_margin_c", 0.0)))
        for k in range(N + 1):
            m_margins[k] = p50_margin

    # 5. Terminal condition (Salvage Value p_hat / COP_hat)
    # Estimate baseline p_eff across slots to derive cheapest 20%
    baseline_p_effs = []
    for k in range(N):
        _, _, _, p_eff = calculate_slot_financials(
            solar_kw=solar_kws[k],
            unalloc_kw=unalloc_kws[k],
            el_demand_kw=spec.heat_pump_electric_kw,
            price_all_in=prices_all_in[k],
            step_hours=delta_t,
            tariff_provider=tariff_provider
        )
        baseline_p_effs.append(p_eff)

    n_cheap = max(1, int(math.ceil(N * p.terminal_cheap_percentile)))
    cheapest_prices = sorted(baseline_p_effs)[:n_cheap]
    p_hat = sum(cheapest_prices) / n_cheap

    t_out_mean = sum(out_temps) / N if N > 0 else 10.0
    cop_hat = dhw_cop(50.0, t_out_mean, params=model_parameters)
    salvage_eur_per_kwh_th = p_hat / cop_hat if cop_hat > 0 else 0.08
    terminal_val_per_kelvin = C_tank * salvage_eur_per_kwh_th

    # 6. Temperature Grid
    t_min_grid = min(t_comf, math.floor(t0_c * 4) / 4.0)
    t_max_grid = max(t_max, math.ceil(t0_c * 4) / 4.0)
    T_grid = np.arange(t_min_grid, t_max_grid + 1e-5, p.grid_step_c)
    M = len(T_grid)

    # 7. Pre-compute transitions for compressor state r under action u in {0, 1}
    # next_r[r, u] -> r_next, start_cost[r, u] -> cost
    next_r = np.full((R, 2), -1, dtype=int)
    cost_start = np.zeros((R, 2))

    for r_idx, (r_mode, r_count) in enumerate(states_r):
        if r_mode == "OFF_FREE":
            next_r[r_idx, 0] = idx_off_free
            cost_start[r_idx, 0] = 0.0
            next_r[r_idx, 1] = state_to_idx[("ON_MANDATORY", 1)] if p.min_run_slots > 1 else idx_on_free
            cost_start[r_idx, 1] = p.c_start
        elif r_mode == "OFF_DWELL":
            # Mandatory u = 0
            d_next = r_count + 1
            next_r[r_idx, 0] = state_to_idx[("OFF_DWELL", d_next)] if d_next < p.min_dwell_slots else idx_off_free
            cost_start[r_idx, 0] = 0.0
            next_r[r_idx, 1] = -1  # Invalid
        elif r_mode == "ON_MANDATORY":
            # Mandatory u = 1
            l_next = r_count + 1
            next_r[r_idx, 0] = -1  # Invalid
            next_r[r_idx, 1] = state_to_idx[("ON_MANDATORY", l_next)] if l_next < p.min_run_slots else idx_on_free
            cost_start[r_idx, 1] = 0.0
        elif r_mode == "ON_FREE":
            next_r[r_idx, 0] = state_to_idx[("OFF_DWELL", 1)] if p.min_dwell_slots > 1 else idx_off_free
            cost_start[r_idx, 0] = 0.0
            next_r[r_idx, 1] = idx_on_free
            cost_start[r_idx, 1] = 0.0

    # 8. Backward Induction: Value Table V[k, m, r]
    # V[k, m, r] stores optimal cost-to-go from slot k with temperature T_grid[m] and compressor state r
    V = np.full((N + 1, M, R), np.inf, dtype=np.float64)

    # Terminal condition at k = N
    t_min_feas_N = t_comf + m_margins[N]
    for m in range(M):
        t_val = T_grid[m]
        if t_val >= t_min_feas_N:
            # Salvage value credited (negative cost)
            val = - terminal_val_per_kelvin * (t_val - t_comf)
            V[N, m, :] = val

    # Common physical spec dictionary passed to dhw_step
    tank_spec_dict = {
        "thermal_capacity_kwh_per_k": C_tank,
        "ua_w_per_k": ua_w_per_k,
        "ambient_temp_c": p.t_amb_c,
        "target_temp_c": t_max
    }

    # Backward recursion k = N-1 down to 0
    for k in range(N - 1, -1, -1):
        t_min_feas_k = t_comf + m_margins[k]
        t_out_k = out_temps[k]
        q_tap_k = q_tap_p50[k]
        is_locked_k = lockouts[k]

        # 8a. Vectorized evaluation of next temperatures for u=0 and u=1
        t_next_0 = np.empty(M, dtype=np.float64)
        t_next_1 = np.empty(M, dtype=np.float64)
        running_cost_1 = np.empty(M, dtype=np.float64)

        for m in range(M):
            t_curr = T_grid[m]
            # u = 0: cooling / standby step
            t_next_0[m] = dhw_step(
                t_tank_c=t_curr,
                u=0.0,
                q_tap_kwh=q_tap_k,
                t_outdoor_c=t_out_k,
                dt_h=delta_t,
                spec=tank_spec_dict,
                params=model_parameters,
                t_max_c=t_max,
                t_amb_c=p.t_amb_c
            )

            # u = 1: heating step
            p_el = spec.get_electric_power_kw(t_curr, outdoor_temp_c=t_out_k, params=model_parameters)
            tank_spec_step = dict(tank_spec_dict)
            tank_spec_step["heat_pump_power_kw"] = p_el
            t_next_1[m] = dhw_step(
                t_tank_c=t_curr,
                u=1.0,
                q_tap_kwh=q_tap_k,
                t_outdoor_c=t_out_k,
                dt_h=delta_t,
                spec=tank_spec_step,
                params=model_parameters,
                t_max_c=t_max,
                t_amb_c=p.t_amb_c
            )

            # Calculate electricity cost for slot k with compressor power p_el
            c_el, _, _, _ = calculate_slot_financials(
                solar_kw=solar_kws[k],
                unalloc_kw=unalloc_kws[k],
                el_demand_kw=p_el,
                price_all_in=prices_all_in[k],
                step_hours=delta_t,
                tariff_provider=tariff_provider
            )
            running_cost_1[m] = c_el

        # 8b. Interpolate V_{k+1}(T', r')
        # For each possible next state r', build interpolated cost array
        v_interp_0 = np.full((R, M), np.inf, dtype=np.float64)
        v_interp_1 = np.full((R, M), np.inf, dtype=np.float64)

        t_min_feas_next = t_comf + m_margins[k + 1]

        for r_prime in range(R):
            v_next_col = V[k + 1, :, r_prime]
            fin = np.isfinite(v_next_col)
            if np.any(fin):
                T_fin = T_grid[fin]
                V_fin = v_next_col[fin]
                # Interpolate for u=0
                feas_mask_0 = t_next_0 >= (t_min_feas_next - 1e-4)
                if np.any(feas_mask_0):
                    v_interp_0[r_prime, feas_mask_0] = np.interp(
                        t_next_0[feas_mask_0],
                        T_fin,
                        V_fin,
                        left=V_fin[0],
                        right=V_fin[-1]
                    )

                # Interpolate for u=1
                feas_mask_1 = t_next_1 >= (t_min_feas_next - 1e-4)
                if np.any(feas_mask_1):
                    v_interp_1[r_prime, feas_mask_1] = np.interp(
                        t_next_1[feas_mask_1],
                        T_fin,
                        V_fin,
                        left=V_fin[0],
                        right=V_fin[-1]
                    )

        # 8c. Bellman update for each state (m, r)
        for r in range(R):
            r_next_0 = next_r[r, 0]
            r_next_1 = next_r[r, 1]

            # Candidate cost for u = 0
            if r_next_0 >= 0:
                cost_0 = cost_start[r, 0] + v_interp_0[r_next_0, :]
            else:
                cost_0 = np.full(M, np.inf)

            # Candidate cost for u = 1 (forbidden if hard lockout)
            if (not is_locked_k) and (r_next_1 >= 0):
                cost_1 = cost_start[r, 1] + running_cost_1 + v_interp_1[r_next_1, :]
            else:
                cost_1 = np.full(M, np.inf)

            # Bellman min
            best_cost = np.minimum(cost_0, cost_1)

            # Mask out temperatures below comfort buffer at slot k
            infeas_mask = T_grid < (t_min_feas_k - 1e-4)
            best_cost[infeas_mask] = np.inf

            V[k, :, r] = best_cost

        # Defensive guard: replace any NaN with inf
        if np.isnan(V[k]).any():
            V[k] = np.where(np.isnan(V[k]), np.inf, V[k])

    # 9. Forward Pass: Track continuous trajectory from (T_0, r_0)
    current_t = float(t0_c)
    current_r = r0_idx

    planned_u = []
    t_star = [round(current_t, 2)]
    is_fallback = False
    validation_issue = None

    for k in range(N):
        is_locked_k = lockouts[k]
        t_out_k = out_temps[k]
        q_tap_k = q_tap_p50[k]
        t_min_feas_next = t_comf + m_margins[k + 1]

        r_next_0 = next_r[current_r, 0]
        r_next_1 = next_r[current_r, 1]

        # Evaluate candidate action u = 0
        cand_cost_0 = np.inf
        t_next_cand_0 = dhw_step(
            t_tank_c=current_t,
            u=0.0,
            q_tap_kwh=q_tap_k,
            t_outdoor_c=t_out_k,
            dt_h=delta_t,
            spec=tank_spec_dict,
            params=model_parameters,
            t_max_c=t_max,
            t_amb_c=p.t_amb_c
        )
        if r_next_0 >= 0:
            val_0 = _interp_finite(t_next_cand_0, T_grid, V[k + 1, :, r_next_0], t_min_feas_next)
            if np.isfinite(val_0):
                cand_cost_0 = cost_start[current_r, 0] + val_0

        # Evaluate candidate action u = 1
        cand_cost_1 = np.inf
        p_el = spec.get_electric_power_kw(current_t, outdoor_temp_c=t_out_k, params=model_parameters)
        tank_spec_step = dict(tank_spec_dict)
        tank_spec_step["heat_pump_power_kw"] = p_el
        t_next_cand_1 = dhw_step(
            t_tank_c=current_t,
            u=1.0,
            q_tap_kwh=q_tap_k,
            t_outdoor_c=t_out_k,
            dt_h=delta_t,
            spec=tank_spec_step,
            params=model_parameters,
            t_max_c=t_max,
            t_amb_c=p.t_amb_c
        )
        if (not is_locked_k) and (r_next_1 >= 0):
            val_1 = _interp_finite(t_next_cand_1, T_grid, V[k + 1, :, r_next_1], t_min_feas_next)
            if np.isfinite(val_1):
                c_el, _, _, _ = calculate_slot_financials(
                    solar_kw=solar_kws[k],
                    unalloc_kw=unalloc_kws[k],
                    el_demand_kw=p_el,
                    price_all_in=prices_all_in[k],
                    step_hours=delta_t,
                    tariff_provider=tariff_provider
                )
                cand_cost_1 = cost_start[current_r, 1] + c_el + val_1

        # Decision
        if k < in_flight_slots:
            # Physical In-Flight Run Commitment: Heat pump is already running and must complete cycle
            u_opt = 1
            current_t = t_next_cand_1
            current_r = r_next_1 if r_next_1 >= 0 else idx_on_free
        elif math.isinf(cand_cost_0) and math.isinf(cand_cost_1):
            # Degradation / Fallback recovery:
            # If initial tank was below comfort or no feasible path exists,
            # activate heat pump as fast as possible outside hard lockouts.
            is_fallback = True
            if validation_issue is None:
                validation_issue = f"Initial state T0={t0_c:.1f}°C below comfort boundary or infeasible; degraded to emergency recovery."
            
            if not is_locked_k and current_t < t_max:
                u_opt = 1
                current_t = t_next_cand_1
                current_r = r_next_1 if r_next_1 >= 0 else idx_on_free
            else:
                u_opt = 0
                current_t = t_next_cand_0
                current_r = r_next_0 if r_next_0 >= 0 else idx_off_free
        else:
            if cand_cost_1 < cand_cost_0 - 1e-6:
                u_opt = 1
                current_t = t_next_cand_1
                current_r = r_next_1
            else:
                u_opt = 0
                current_t = t_next_cand_0
                current_r = r_next_0

        planned_u.append(u_opt)
        t_star.append(round(current_t, 2))

    # 10. Generate P05 (light) and P95 (heavy) uncertainty trajectories using exact same policy
    t_p05 = [round(float(t0_c), 2)]
    t_p95 = [round(float(t0_c), 2)]
    curr_05 = float(t0_c)
    curr_95 = float(t0_c)

    for k in range(N):
        u_k = planned_u[k]
        t_out_k = out_temps[k]
        p_el_05 = spec.get_electric_power_kw(curr_05, outdoor_temp_c=t_out_k, params=model_parameters)
        p_el_95 = spec.get_electric_power_kw(curr_95, outdoor_temp_c=t_out_k, params=model_parameters)

        spec_05 = dict(tank_spec_dict, heat_pump_power_kw=p_el_05)
        spec_95 = dict(tank_spec_dict, heat_pump_power_kw=p_el_95)

        curr_05 = dhw_step(
            t_tank_c=curr_05,
            u=float(u_k),
            q_tap_kwh=q_tap_p05[k],
            t_outdoor_c=t_out_k,
            dt_h=delta_t,
            spec=spec_05,
            params=model_parameters,
            t_max_c=t_max,
            t_amb_c=p.t_amb_c
        )
        curr_95 = dhw_step(
            t_tank_c=curr_95,
            u=float(u_k),
            q_tap_kwh=q_tap_p95[k],
            t_outdoor_c=t_out_k,
            dt_h=delta_t,
            spec=spec_95,
            params=model_parameters,
            t_max_c=t_max,
            t_amb_c=p.t_amb_c
        )
        t_p05.append(round(curr_05, 2))
        t_p95.append(round(curr_95, 2))

    # 11. Extract Planned Slots, Discrete Runs, Costs and Slot Modes
    planned_slots = [k for k, u in enumerate(planned_u) if u == 1]
    runs: List[DhwRun] = []
    slot_modes: List[str] = ["normal"] * N
    total_cost_eur = 0.0

    in_run = False
    run_start = 0
    run_el_kwh = 0.0
    run_cost = 0.0
    run_self_kwh = 0.0

    for k in range(N):
        is_locked_k = lockouts[k]
        u_k = planned_u[k]
        t_k = t_star[k]

        if is_locked_k and u_k == 0:
            slot_modes[k] = "forced_off"
        elif u_k == 1:
            slot_modes[k] = "max_on" if t_k >= 52.0 else "forced_on"

            # Compute slot financials
            p_el = spec.get_electric_power_kw(t_k, outdoor_temp_c=out_temps[k], params=model_parameters)
            c_slot, self_kwh, grid_kwh, _ = calculate_slot_financials(
                solar_kw=solar_kws[k],
                unalloc_kw=unalloc_kws[k],
                el_demand_kw=p_el,
                price_all_in=prices_all_in[k],
                step_hours=delta_t,
                tariff_provider=tariff_provider
            )
            el_kwh = p_el * delta_t
            total_cost_eur += c_slot

            if not in_run:
                in_run = True
                run_start = k
                run_el_kwh = el_kwh
                run_cost = c_slot + p.c_start
                run_self_kwh = self_kwh
            else:
                run_el_kwh += el_kwh
                run_cost += c_slot
                run_self_kwh += self_kwh
        else:
            if in_run:
                # Close run
                in_run = False
                runs.append(DhwRun(
                    start_idx=run_start,
                    end_idx=k,
                    t_start_c=round(t_star[run_start], 1),
                    t_end_c=round(t_star[k], 1),
                    kwh_el=round(run_el_kwh, 2),
                    cost_eur=round(run_cost, 3),
                    solar_share=round(run_self_kwh / run_el_kwh, 2) if run_el_kwh > 0 else 0.0
                ))

    # Close any open run at horizon end
    if in_run:
        runs.append(DhwRun(
            start_idx=run_start,
            end_idx=N,
            t_start_c=round(t_star[run_start], 1),
            t_end_c=round(t_star[N], 1),
            kwh_el=round(run_el_kwh, 2),
            cost_eur=round(run_cost, 3),
            solar_share=round(run_self_kwh / run_el_kwh, 2) if run_el_kwh > 0 else 0.0
        ))

    solve_dur_ms = round((time.perf_counter() - start_perf) * 1000.0, 2)

    electricity_cost = round(total_cost_eur, 3)
    start_cost = round(len(runs) * p.c_start, 3)
    total_cost_combined = round(electricity_cost + start_cost, 3)
    terminal_temp = t_star[-1] if t_star else t0_c
    salvage_val = round(terminal_val_per_kelvin * max(0.0, terminal_temp - t_comf), 3)
    j_obj = round(total_cost_combined - salvage_val, 3)

    return DhwOptimizerResult(
        planned_slots=planned_slots,
        runs=runs,
        trajectory={
            "temperatures_c": t_star,
            "temperatures_p05_c": t_p05,
            "temperatures_p95_c": t_p95,
            "demand_kwh_th": [round(q, 4) for q in q_tap_p50],
            "demand_p95_kwh_th": [round(q, 4) for q in q_tap_p95],
        },
        total_cost_eur=total_cost_combined,
        slot_modes=slot_modes,
        validation_issue=validation_issue,
        solve_duration_ms=solve_dur_ms,
        electricity_cost_eur=electricity_cost,
        start_cost_eur=start_cost,
        salvage_value_eur=salvage_val,
        j_objective_eur=j_obj,
    )


def evaluate_plan_metrics(
    slots: List[Any],
    u_plan: List[int],
    t0_c: float,
    spec: Optional[DhwTankSpec] = None,
    params: Optional[DhwOptimizerParams] = None,
    tariff_provider: Any = None,
    dhw_model: Any = None,
    run_state0: Any = None,
    model_parameters: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Pure function: Evaluates any arbitrary binary heating plan u_plan across slots with the exact
    same cost function J and physical dynamics as the DP optimizer.
    Enables strict apple-to-apple objective comparison between Arbiter and Optimizer.
    """
    spec = spec or DhwTankSpec()
    params = params or DhwOptimizerParams()
    dhw_model = dhw_model or DhwThermalModel()
    tp = tariff_provider or TariffProvider()

    N = len(slots)
    delta_t = 0.25
    C_tank = spec.thermal_capacity_kwh_per_k
    ua_w_per_k = dhw_model.get_tank_ua() if hasattr(dhw_model, "get_tank_ua") else 2.5
    t_comf = spec.comfort_min_temp_c
    t_max = spec.boost_setpoint_c

    tank_spec_dict = {
        "thermal_capacity_kwh_per_k": C_tank,
        "ua_w_per_k": ua_w_per_k,
        "ambient_temp_c": params.t_amb_c,
        "target_temp_c": t_max,
    }

    out_temps = []
    q_tap_p50 = []
    prices_all_in = []
    solar_kws = []
    unalloc_kws = []

    for k, s in enumerate(slots):
        dt_val = getattr(s, "dt", None)
        if dt_val is None:
            dt_val = datetime.now(timezone.utc) + timedelta(minutes=15 * k)
        dow = dt_val.weekday()
        q_idx = dt_val.hour * 4 + dt_val.minute // 15
        out_temps.append(float(getattr(s, "outdoor_temp_c", getattr(s, "outdoor_temp", 10.0))))
        q_tap_p50.append(dhw_model.get_learned_tap_kwh_th(dow, q_idx) if hasattr(dhw_model, "get_learned_tap_kwh_th") else 0.05)
        prices_all_in.append(float(getattr(s, "price_all_in", 0.25)))
        solar_kws.append(float(getattr(s, "solar_kw", 0.0)))
        unalloc_kws.append(float(getattr(s, "unallocated_kw", 0.30)))

    # Compute terminal salvage value per Kelvin
    baseline_p_effs = []
    for k in range(N):
        _, _, _, p_eff = calculate_slot_financials(
            solar_kw=solar_kws[k],
            unalloc_kw=unalloc_kws[k],
            el_demand_kw=spec.heat_pump_electric_kw,
            price_all_in=prices_all_in[k],
            step_hours=delta_t,
            tariff_provider=tp
        )
        baseline_p_effs.append(p_eff)

    n_cheap = max(1, int(math.ceil(N * params.terminal_cheap_percentile)))
    cheapest_prices = sorted(baseline_p_effs)[:n_cheap]
    p_hat = sum(cheapest_prices) / n_cheap
    t_out_mean = sum(out_temps) / N if N > 0 else 10.0
    cop_hat = dhw_cop(50.0, t_out_mean, params=model_parameters)
    salvage_eur_per_kwh_th = p_hat / cop_hat if cop_hat > 0 else 0.08
    terminal_val_per_kelvin = C_tank * salvage_eur_per_kwh_th

    # Forward simulate plan
    curr_t = float(t0_c)
    temps = [round(curr_t, 2)]
    total_el_cost = 0.0
    total_start_cost = 0.0
    total_kwh_el = 0.0

    in_run = False
    runs = []
    run_start = 0
    run_cost = 0.0
    run_kwh = 0.0

    prev_u = 1 if (isinstance(run_state0, str) and "ON" in run_state0.upper()) or (isinstance(run_state0, tuple) and "ON" in str(run_state0[0]).upper()) else 0

    for k in range(N):
        u_k = int(u_plan[k]) if k < len(u_plan) else 0

        # Start cost detection
        if u_k == 1 and prev_u == 0:
            total_start_cost += params.c_start
            in_run = True
            run_start = k
            run_cost = params.c_start
            run_kwh = 0.0
        elif u_k == 0 and prev_u == 1 and in_run:
            in_run = False
            runs.append({
                "start_idx": run_start,
                "end_idx": k,
                "t_start_c": round(temps[run_start], 1),
                "t_end_c": round(temps[k], 1),
                "kwh_el": round(run_kwh, 2),
                "cost_eur": round(run_cost, 3),
            })

        # Electricity cost & physical step
        p_el = spec.get_electric_power_kw(curr_t, outdoor_temp_c=out_temps[k], params=model_parameters) if u_k == 1 else 0.0
        step_dict = dict(tank_spec_dict, heat_pump_power_kw=p_el)
        curr_t = dhw_step(
            t_tank_c=curr_t,
            u=float(u_k),
            q_tap_kwh=q_tap_p50[k],
            t_outdoor_c=out_temps[k],
            dt_h=delta_t,
            spec=step_dict,
            params=model_parameters,
            t_max_c=t_max,
            t_amb_c=params.t_amb_c
        )
        temps.append(round(curr_t, 2))

        if u_k == 1:
            c_slot, self_kwh, grid_kwh, _ = calculate_slot_financials(
                solar_kw=solar_kws[k],
                unalloc_kw=unalloc_kws[k],
                el_demand_kw=p_el,
                price_all_in=prices_all_in[k],
                step_hours=delta_t,
                tariff_provider=tp
            )
            total_el_cost += c_slot
            total_kwh_el += p_el * delta_t
            if in_run:
                run_cost += c_slot
                run_kwh += p_el * delta_t

        prev_u = u_k

    if in_run:
        runs.append({
            "start_idx": run_start,
            "end_idx": N,
            "t_start_c": round(temps[run_start], 1),
            "t_end_c": round(temps[N], 1),
            "kwh_el": round(run_kwh, 2),
            "cost_eur": round(run_cost, 3),
        })

    t_end = temps[-1]
    salvage_value = max(0.0, terminal_val_per_kelvin * (t_end - t_comf))
    total_cost = total_el_cost + total_start_cost
    j_objective = total_cost - salvage_value

    min_t = min(temps)
    max_t = max(temps)
    comfort_breached = bool(min_t < t_comf)

    return {
        "j_objective": round(j_objective, 4),
        "j_objective_eur": round(j_objective, 4),
        "total_cost_eur": round(total_cost, 3),
        "electricity_cost_eur": round(total_el_cost, 3),
        "start_cost_eur": round(total_start_cost, 3),
        "salvage_value_eur": round(salvage_value, 4),
        "runs_count": len(runs),
        "runs": runs,
        "t_min_c": round(min_t, 1),
        "t_max_c": round(max_t, 1),
        "t_end_c": round(t_end, 1),
        "temperatures_c": temps,
        "comfort_breached": comfort_breached,
        "total_kwh_el": round(total_kwh_el, 2)
    }
