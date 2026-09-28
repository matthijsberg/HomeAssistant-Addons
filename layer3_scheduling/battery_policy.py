"""
Open HEMS: Home Battery Scheduling Policy (Priority 3)
======================================================
Implements loosely coupled waterfall planning with dynamic shadow pricing
for electrochemical storage (15 kWh / 5 kW / 87% roundtrip efficiency).

INVARIANTS:
1. Zero Home Assistant entity strings, zero brand names.
2. Pure mathematical optimization over discrete quarter-hour time slots.
3. Strictly Priority 3: operates strictly on residual load after Baseload, DHW, and Space Heating.
"""

from dataclasses import dataclass, field, asdict
from typing import List, Dict, Any, Optional, Set
import math
import numpy as np
from scipy.optimize import linprog


@dataclass
class BatterySpec:
    """Physical and economic specifications of the battery system."""
    capacity_kwh: float = 15.0
    usable_capacity_kwh: float = 13.5          # 10% to 100% (or 10% to 95%) DoD
    min_soc_pct: float = 10.0                   # Hard low-voltage protection limit
    max_soc_pct: float = 95.0                   # High-voltage cycle life preservation limit
    nominal_charge_kw: float = 2.5              # Geadviseerd nominaal laadvermogen (normale werking)
    max_charge_kw: float = 5.0                  # Maximaal omvormer laadvermogen (korte dalen / grote spread)
    nominal_discharge_kw: float = 2.5           # Geadviseerd nominaal ontlaadvermogen
    max_discharge_kw: float = 5.0               # Maximaal omvormer ontlaadvermogen
    boost_opportunity_spread_eur: float = 0.30  # Prijsdelta drempel (> €0.30) voor opschalen naar max vermogen
    roundtrip_efficiency: float = 0.87          # AC-DC-AC roundtrip efficiency
    degradation_cost_eur_kwh: float = 0.078     # LCOC based on €6000 / 76500 kWh throughput
    min_cycle_margin_eur_kwh: float = 0.085     # In-device adjustable profit margin slider
    # Lookahead & drempels (volledige 24-uurs planhorizon ipv starre 8-9 uur)
    reserve_lookahead_slots: int = 96           # 24 uur vooruitkijken voor piekreservering
    valley_lookahead_slots: int = 96            # 24 uur vooruitkijken voor daldetectie
    peak_price_delta_eur_kwh: float = 0.08      # Minimaal prijsverschil dat een slot 'piek' maakt
    reserve_price_delta_eur_kwh: float = 0.12   # Minimaal prijsverschil voor netlaad-reservering
    grid_charge_soc_ceiling_pct: float = 85.0   # Netladen stopt hier; de rest is voor zon
    max_grid_import_kw: float = 17.25           # Main grid connection fuse limit (3x25A = ~17.25 kW)
    charge_knee_kw: float = 2.5                 # Nominal piecewise linear knee for charging (synced to nominal_charge_kw)
    charge_tier2_penalty_eur_kwh: float = 0.005 # Penalty per kWh above knee to encourage smoothing
    charge_ramp_penalty_eur_kw: float = 0.001   # Penalty per kW step change to prevent zigzagging
    discharge_tier2_penalty_eur_kwh: float = 0.003 # Penalty per kWh above nominal_discharge_kw
    min_discharge_threshold_w: float = 400.0    # Minimale ontlaaddrempel in Watt (geen ontlading bij pure baselast)

    @classmethod
    def from_config(cls, cfg: Optional[Dict[str, Any]] = None, **overrides) -> "BatterySpec":
        if not cfg:
            return cls(**overrides)
        b_cfg = cfg.get("battery", {})
        nom_ch = float(b_cfg.get("nominal_charge_kw", 2.5))
        max_ch = float(b_cfg.get("max_charge_kw", 5.0))
        nom_dis = float(b_cfg.get("nominal_discharge_kw", 2.5))
        max_dis = float(b_cfg.get("max_discharge_kw", 5.0))
        boost_spread = float(b_cfg.get("boost_opportunity_spread_eur", 0.30))
        min_dis_w = float(b_cfg.get("min_discharge_threshold_w", 400.0))
        cap = float(b_cfg.get("capacity_kwh", 15.0))

        kwargs = {
            "capacity_kwh": cap,
            "usable_capacity_kwh": float(b_cfg.get("usable_capacity_kwh", cap * 0.9)),
            "min_soc_pct": float(b_cfg.get("min_soc_pct", 10.0)),
            "max_soc_pct": float(b_cfg.get("max_soc_pct", 95.0)),
            "nominal_charge_kw": nom_ch,
            "max_charge_kw": max_ch,
            "nominal_discharge_kw": nom_dis,
            "max_discharge_kw": max_dis,
            "boost_opportunity_spread_eur": boost_spread,
            "charge_knee_kw": nom_ch,
            "min_discharge_threshold_w": min_dis_w,
        }
        for k, v in overrides.items():
            if k in ("reserve_lookahead_slots", "valley_lookahead_slots"):
                kwargs[k] = int(v)
            else:
                kwargs[k] = v
        return cls(**kwargs)


@dataclass
class BatterySlotResult:
    """Canonical dispatch decision for the battery within a 15-minute slot."""
    slot_idx: int
    power_kw: float                             # + = charging (load), - = discharging (source), 0 = idle
    mode_code: str                              # CHARGE_SOLAR, CHARGE_GRID, HOLD_RESERVE, DISCHARGE_PEAK, DISCHARGE_BUFFER, STANDBY
    mode_label: str
    soc_pct: float
    soc_kwh: float
    cost_impact_eur: float                      # Monetary cost/saving in this slot (+ cost, - saving)
    deficit_kw: float = 0.0                     # Unhedged house demand resulting in grid import
    soc_p05_pct: float = 0.0                    # Conservative lower bound SoC
    soc_p95_pct: float = 0.0                    # Optimistic upper bound SoC
    ch_solar_kw: float = 0.0                    # Active solar charging power
    ch_grid_kw: float = 0.0                     # Active grid charging power

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class BatteryPlanSummary:
    """Summary metrics of the 24h/48h battery schedule."""
    total_charged_solar_kwh: float
    total_charged_grid_kwh: float
    total_discharged_kwh: float
    net_financial_saving_eur: float
    initial_soc_pct: float
    final_soc_pct: float
    min_projected_soc_pct: float
    max_projected_soc_pct: float
    slots: List[BatterySlotResult]
    total_deficit_kwh: float = 0.0
    autonomy_pct: float = 100.0
    is_active: bool = True
    inactive_reason: str = ""

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["slots"] = [s.to_dict() for s in self.slots]
        return d


class BatteryPolicy:
    """
    Solves optimal electrochemical battery storage dispatch using loosely coupled
    multi-pass forward-backward dynamic shadow pricing.
    """

    @classmethod
    def _inactive_plan(cls, n_slots: int, reason: str) -> "BatteryPlanSummary":
        """Neutral plan: no dispatch, no financial claim, explicit reason."""
        return BatteryPlanSummary(
            total_charged_solar_kwh=0.0,
            total_charged_grid_kwh=0.0,
            total_discharged_kwh=0.0,
            net_financial_saving_eur=0.0,
            initial_soc_pct=0.0,
            final_soc_pct=0.0,
            min_projected_soc_pct=0.0,
            max_projected_soc_pct=0.0,
            slots=[
                BatterySlotResult(
                    slot_idx=i,
                    power_kw=0.0,
                    mode_code="STANDBY",
                    mode_label="Standby",
                    soc_pct=0.0,
                    soc_kwh=0.0,
                    cost_impact_eur=0.0,
                )
                for i in range(max(0, n_slots))
            ],
            is_active=False,
            inactive_reason=reason,
        )

    @classmethod
    def optimize(
        cls,
        residual_demand_kw: List[float],
        import_prices: List[float],
        export_prices: List[float],
        spec: Optional[BatterySpec] = None,
        initial_soc_pct: Optional[float] = None,
        step_hours: float = 0.25,
        forced_off_indices: Optional[Set[int]] = None,
    ) -> BatteryPlanSummary:
        """
        Calculates the optimal 15-minute battery dispatch.

        residual_demand_kw: P_unalloc + P_dhw + P_heat - P_solar.
          - If positive: net house deficit (house needs power from battery or grid).
          - If negative: net solar surplus (excess solar available for battery or export).

        initial_soc_pct MUST come from live telemetry. There is no default: a plan
        built on an assumed state of charge is open-loop and diverges within hours.
        When the measurement is missing or stale, pass None and the policy returns an
        inactive all-STANDBY plan with a reason (Invariant 3: never invent a value).
        """
        if spec is None:
            spec = BatterySpec()

        n_slots = len(residual_demand_kw)

        if initial_soc_pct is None:
            return cls._inactive_plan(
                n_slots,
                "State of charge ontbreekt of is verouderd; batterij blijft in standby."
            )
        if n_slots == 0:
            return BatteryPlanSummary(
                total_charged_solar_kwh=0.0,
                total_charged_grid_kwh=0.0,
                total_discharged_kwh=0.0,
                net_financial_saving_eur=0.0,
                initial_soc_pct=initial_soc_pct,
                final_soc_pct=initial_soc_pct,
                min_projected_soc_pct=initial_soc_pct,
                max_projected_soc_pct=initial_soc_pct,
                slots=[],
            )

        # Ensure equal length arrays
        if len(import_prices) < n_slots:
            last_pr = import_prices[-1] if import_prices else 0.25
            import_prices = list(import_prices) + [last_pr] * (n_slots - len(import_prices))
        if len(export_prices) < n_slots:
            last_ex = export_prices[-1] if export_prices else 0.05
            export_prices = list(export_prices) + [last_ex] * (n_slots - len(export_prices))

        forced_lockouts = forced_off_indices if forced_off_indices is not None else set()

        # Physical limits
        eta_one_way = math.sqrt(spec.roundtrip_efficiency)
        min_kwh = spec.capacity_kwh * (spec.min_soc_pct / 100.0)
        max_kwh = spec.capacity_kwh * (spec.max_soc_pct / 100.0)
        cur_kwh = max(min_kwh, min(max_kwh, spec.capacity_kwh * (initial_soc_pct / 100.0)))

        # -------------------------------------------------------------------
        # Linear Programming (LP) Global Optimization (Priority 3)
        # -------------------------------------------------------------------
        dt = step_hours
        eta_ch = eta_one_way
        eta_dis = eta_one_way
        c_deg = spec.degradation_cost_eur_kwh

        # Terminal valuation based on reload cost across the TAIL of the horizon:
        # Multi-day/full horizons (>= 96 slots / 24h): last 24h tail captures Day 2 reload cost into Day 3.
        # Short sub-day horizons (< 96 slots): global min prevents artificial terminal arbitrage.
        if n_slots >= 96:
            tail_slots = min(96, n_slots // 2)
            min_tail_reload = float(np.min(import_prices[-tail_slots:]))
        else:
            min_tail_reload = float(np.min(import_prices))
        lambda_term = (max(0.0, min_tail_reload) / eta_ch) * 0.98

        N = n_slots
        # Decision variables (8 blocks of size N):
        # 0*N: P_ch_solar (kW)
        # 1*N: P_ch_grid (kW)
        # 2*N: P_dis (kW)
        # 3*N: P_imp (kW)
        # 4*N: P_exp (kW)
        # 5*N: P_ch_high (kW above nominal_charge_kw)
        # 6*N: r (kW ramp step change)
        # 7*N: P_dis_high (kW above nominal_discharge_kw)
        n_vars = 8 * N
        c = np.zeros(n_vars)
        for t in range(N):
            p_in = import_prices[t]
            p_ex = export_prices[t]
            c[0*N + t] = -lambda_term * dt * eta_ch - 1e-5 * dt + (1e-7 * t * dt)
            c[1*N + t] = -lambda_term * dt * eta_ch + (1e-7 * (N - t) * dt)
            c[2*N + t] = (c_deg * dt + lambda_term * (dt / eta_dis)) - 1e-6 * (N - t) * dt
            c[3*N + t] = p_in * dt
            c[4*N + t] = -p_ex * dt
            c[5*N + t] = spec.charge_tier2_penalty_eur_kwh * dt
            c[6*N + t] = spec.charge_ramp_penalty_eur_kw
            c[7*N + t] = spec.discharge_tier2_penalty_eur_kwh * dt

        A_eq = np.zeros((N, n_vars))
        b_eq = np.zeros(N)
        for t in range(N):
            A_eq[t, 3*N + t] = 1.0   # P_imp
            A_eq[t, 4*N + t] = -1.0  # -P_exp
            A_eq[t, 2*N + t] = 1.0   # +P_dis
            A_eq[t, 1*N + t] = -1.0  # -P_ch_grid
            A_eq[t, 0*N + t] = -1.0  # -P_ch_solar
            b_eq[t] = residual_demand_kw[t]

        # Inequality constraints:
        # 1. SoC upper bound (N)
        # 2. SoC lower bound (N)
        # 3. Tier-2 charge knee: P_ch_solar(t) + P_ch_grid(t) - P_ch_high(t) <= nominal_charge_kw (N)
        # 4. Tier-2 discharge knee: P_dis(t) - P_dis_high(t) <= nominal_discharge_kw (N)
        # 5. Ramping up: P_ch(t+1) - P_ch(t) - r(t) <= 0 (N-1)
        # 6. Ramping down: P_ch(t) - P_ch(t+1) - r(t) <= 0 (N-1)
        n_ub = 4 * N + 2 * (N - 1)
        A_ub = np.zeros((n_ub, n_vars))
        b_ub = np.zeros(n_ub)

        row = 0
        for t in range(N):
            for k in range(t + 1):
                A_ub[row, 0*N + k] = dt * eta_ch
                A_ub[row, 1*N + k] = dt * eta_ch
                A_ub[row, 2*N + k] = -dt / eta_dis
            b_ub[row] = max_kwh - cur_kwh
            row += 1

        for t in range(N):
            for k in range(t + 1):
                A_ub[row, 0*N + k] = -dt * eta_ch
                A_ub[row, 1*N + k] = -dt * eta_ch
                A_ub[row, 2*N + k] = dt / eta_dis
            b_ub[row] = cur_kwh - min_kwh
            row += 1

        for t in range(N):
            A_ub[row, 0*N + t] = 1.0
            A_ub[row, 1*N + t] = 1.0
            A_ub[row, 5*N + t] = -1.0
            b_ub[row] = spec.nominal_charge_kw
            row += 1

        for t in range(N):
            A_ub[row, 2*N + t] = 1.0
            A_ub[row, 7*N + t] = -1.0
            b_ub[row] = spec.nominal_discharge_kw
            row += 1

        for t in range(N - 1):
            A_ub[row, 0*N + t + 1] = 1.0
            A_ub[row, 1*N + t + 1] = 1.0
            A_ub[row, 0*N + t] = -1.0
            A_ub[row, 1*N + t] = -1.0
            A_ub[row, 6*N + t] = -1.0
            b_ub[row] = 0.0
            row += 1

        for t in range(N - 1):
            A_ub[row, 0*N + t] = 1.0
            A_ub[row, 1*N + t] = 1.0
            A_ub[row, 0*N + t + 1] = -1.0
            A_ub[row, 1*N + t + 1] = -1.0
            A_ub[row, 6*N + t] = -1.0
            b_ub[row] = 0.0
            row += 1

        bounds = []
        for t in range(N):
            surplus = max(0.0, -residual_demand_kw[t])
            bounds.append((0, min(spec.max_charge_kw, surplus)))
        for t in range(N):
            if t in forced_lockouts:
                bounds.append((0, 0))
            else:
                bounds.append((0, spec.max_charge_kw))
        thresh_kw = max(0.0, float(getattr(spec, "min_discharge_threshold_w", 400.0) or 0.0) / 1000.0)
        for t in range(N):
            req = max(0.0, residual_demand_kw[t])
            if req < thresh_kw:
                bounds.append((0, 0))
            else:
                bounds.append((0, min(spec.max_discharge_kw, req)))
        for t in range(N):
            # Physical grid connection fuse limit from spec (default 17.25 kW for 3x25A)
            limit = max(spec.max_grid_import_kw, residual_demand_kw[t] + 1.0)
            bounds.append((0, limit))
        for t in range(N):
            bounds.append((0, None))
        for t in range(N):
            bounds.append((0, None))
        for t in range(N):
            bounds.append((0, None))
        for t in range(N):
            bounds.append((0, None))

        res = linprog(c, A_ub=A_ub, b_ub=b_ub, A_eq=A_eq, b_eq=b_eq, bounds=bounds, method='highs')
        if not res.success:
            return cls._inactive_plan(n_slots, f"LP solver failed: {res.message}")

        p_ch_solar = res.x[0*N:1*N]
        p_ch_grid = res.x[1*N:2*N]
        p_dis = res.x[2*N:3*N]
        p_imp = res.x[3*N:4*N]
        p_exp = res.x[4*N:5*N]

        # Calculate P05 and P95 SoC bounds based on load/solar uncertainty (±25%)
        res_p95 = [round(r * 1.25, 3) if r > 0 else round(r * 0.75, 3) for r in residual_demand_kw]
        res_p05 = [round(r * 0.75, 3) if r > 0 else round(r * 1.25, 3) for r in residual_demand_kw]

        def _sim_path(res_list):
            r_soc = cur_kwh
            res_soc = []
            for j in range(N):
                r = res_list[j]
                if r < -0.05:
                    surp = abs(r)
                    head = max(0.0, max_kwh - r_soc)
                    intake = min(min(surp, spec.max_charge_kw) * dt * eta_ch, head)
                    r_soc += intake
                elif r > 0.05:
                    avail = max(0.0, r_soc - min_kwh)
                    if (import_prices[j] - c_deg) > export_prices[j] and avail > 0.01:
                        rq = min(r, spec.max_discharge_kw) * dt
                        d_kwh = min(rq, avail * eta_dis)
                        r_soc -= (d_kwh / eta_dis)
                res_soc.append(round((r_soc / spec.capacity_kwh) * 100.0, 1))
            return res_soc

        p05_soc_list = _sim_path(res_p95)
        p95_soc_list = _sim_path(res_p05)

        # -------------------------------------------------------------------
        # Build Results & Financial Accounting
        # -------------------------------------------------------------------
        slot_results: List[BatterySlotResult] = []
        tot_ch_solar = 0.0
        tot_ch_grid = 0.0
        tot_dis = 0.0
        tot_deficit = 0.0
        current_soc = cur_kwh
        min_proj_soc = initial_soc_pct
        max_proj_soc = initial_soc_pct

        for i in range(n_slots):
            ch_s = max(0.0, float(p_ch_solar[i]))
            ch_g = max(0.0, float(p_ch_grid[i]))
            dis = max(0.0, float(p_dis[i]))
            imp = max(0.0, float(p_imp[i]))
            exp = max(0.0, float(p_exp[i]))
            p_in = import_prices[i]
            p_ex = export_prices[i]
            res_kw = residual_demand_kw[i]

            tot_ch_solar += ch_s * dt
            tot_ch_grid += ch_g * dt
            tot_dis += dis * dt

            total_ch = ch_s + ch_g
            if total_ch > 0.02:
                pwr = round(float(total_ch), 3)
                if ch_s > 0.02 and ch_g > 0.02:
                    code = "CHARGE_GRID" if ch_g >= ch_s else "CHARGE_SOLAR"
                    label = "Zon + Netladen"
                elif ch_g > 0.02:
                    code = "CHARGE_GRID"
                    label = "Netladen (Dal)"
                else:
                    code = "CHARGE_SOLAR"
                    label = "Zon-absorptie"
                delta_soc = total_ch * dt * eta_ch
            elif dis > 0.02:
                pwr = -round(float(dis), 3)
                if i in forced_lockouts or p_in >= 0.28:
                    code = "DISCHARGE_PEAK"
                    label = "Spitsontlasting"
                else:
                    code = "DISCHARGE_BUFFER"
                    label = "Huisontlasting"
                delta_soc = -(dis * dt / eta_dis)
            else:
                pwr = 0.0
                delta_soc = 0.0
                if current_soc > (min_kwh + 0.5) and any(p_dis[k] > 0.1 for k in range(i + 1, n_slots)):
                    code = "HOLD_RESERVE"
                    label = "Reserveren"
                else:
                    code = "STANDBY"
                    label = "Standby"

            base_imp = max(0.0, res_kw)
            base_exp = max(0.0, -res_kw)
            base_cost = (p_in * base_imp - p_ex * base_exp) * dt
            actual_cost = (p_in * imp - p_ex * exp + c_deg * dis) * dt
            cost_impact = round(actual_cost - base_cost, 4)

            current_soc = min(max_kwh, max(min_kwh, current_soc + delta_soc))
            soc_pct = round((current_soc / spec.capacity_kwh) * 100.0, 1)
            min_proj_soc = min(min_proj_soc, soc_pct)
            max_proj_soc = max(max_proj_soc, soc_pct)

            deficit = max(0.0, float(res_kw - dis)) if res_kw > 0.05 else 0.0
            tot_deficit += deficit * dt

            slot_results.append(
                BatterySlotResult(
                    slot_idx=i,
                    power_kw=pwr,
                    mode_code=code,
                    mode_label=label,
                    soc_pct=soc_pct,
                    soc_kwh=round(current_soc, 3),
                    cost_impact_eur=cost_impact,
                    deficit_kw=round(deficit, 3),
                    soc_p05_pct=p05_soc_list[i],
                    soc_p95_pct=p95_soc_list[i],
                    ch_solar_kw=round(ch_s, 3),
                    ch_grid_kw=round(ch_g, 3)
                )
            )

        tot_demand = tot_dis + tot_deficit
        autonomy_pct = round((tot_dis / tot_demand) * 100.0, 1) if tot_demand > 0 else 100.0
        net_financial_saving = round(-sum(s.cost_impact_eur for s in slot_results), 2)

        return BatteryPlanSummary(
            total_charged_solar_kwh=round(tot_ch_solar, 2),
            total_charged_grid_kwh=round(tot_ch_grid, 2),
            total_discharged_kwh=round(tot_dis, 2),
            net_financial_saving_eur=net_financial_saving,
            initial_soc_pct=round(initial_soc_pct, 1),
            final_soc_pct=round((current_soc / spec.capacity_kwh) * 100.0, 1),
            min_projected_soc_pct=min_proj_soc,
            max_projected_soc_pct=max_proj_soc,
            slots=slot_results,
            total_deficit_kwh=round(tot_deficit, 2),
            autonomy_pct=autonomy_pct
        )


def extract_battery_overlay_ranges(slots: List[BatterySlotResult], history_count: int = 0, is_15m: bool = True) -> Dict[str, List[Dict[str, Any]]]:
    """
    Unified overlay extractor for battery operation windows (charging, discharging, holding).
    Guarantees 15m vs 1h parity without double downsampling, with optional history_count offset.
    """
    if not slots:
        return {"charge": [], "discharge": [], "hold": []}

    needs_downsample = (not is_15m) and len(slots) > 48
    n = len(slots) // 4 if needs_downsample else len(slots)

    def scan_ranges(predicate, category_name, default_mode=None):
        res = []
        in_block = False
        start_idx = 0
        cur_modes = []
        cur_powers = []
        for i in range(n):
            if not needs_downsample:
                matches = predicate(slots[i])
                mode = slots[i].mode_code
                p = slots[i].power_kw
            else:
                block_slots = [slots[i * 4 + k] for k in range(4) if (i * 4 + k) < len(slots)]
                matches = any(predicate(s) for s in block_slots)
                mode = max(set(s.mode_code for s in block_slots), key=lambda m: sum(1 for s in block_slots if s.mode_code == m)) if block_slots else "STANDBY"
                p = sum(s.power_kw for s in block_slots) / len(block_slots) if block_slots else 0.0

            if matches and not in_block:
                in_block = True
                start_idx = i
                cur_modes = [mode]
                cur_powers = [p]
            elif matches and in_block:
                cur_modes.append(mode)
                cur_powers.append(p)
            elif not matches and in_block:
                in_block = False
                res.append({
                    "start_idx": start_idx + history_count,
                    "end_idx": i - 1 + history_count,
                    "mode_code": default_mode or cur_modes[0],
                    "power_kw": round(sum(cur_powers) / len(cur_powers), 2),
                    "name": category_name
                })
        if in_block:
            res.append({
                "start_idx": start_idx + history_count,
                "end_idx": n - 1 + history_count,
                "mode_code": default_mode or cur_modes[0],
                "power_kw": round(sum(cur_powers) / len(cur_powers), 2),
                "name": category_name
            })
        return res

    solar_charge_ranges = scan_ranges(lambda s: s.mode_code == "CHARGE_SOLAR", "BATTERY_SOLAR_CHARGE", "CHARGE_SOLAR")
    grid_charge_ranges = scan_ranges(lambda s: s.mode_code == "CHARGE_GRID", "BATTERY_GRID_CHARGE", "CHARGE_GRID")
    discharge_ranges = scan_ranges(lambda s: s.mode_code in ["DISCHARGE_PEAK", "DISCHARGE_BUFFER"], "BATTERY_DISCHARGE", "DISCHARGE")
    hold_ranges = scan_ranges(lambda s: s.mode_code == "HOLD_RESERVE", "HOLD_RESERVE", "HOLD_RESERVE")

    return {
        "charge": solar_charge_ranges + grid_charge_ranges,
        "solar_charge": solar_charge_ranges,
        "grid_charge": grid_charge_ranges,
        "discharge": discharge_ranges,
        "hold": hold_ranges
    }

