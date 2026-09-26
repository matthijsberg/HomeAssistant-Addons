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


@dataclass(frozen=True)
class BatterySpec:
    """Physical and economic specifications of the battery system."""
    capacity_kwh: float = 15.0
    usable_capacity_kwh: float = 13.5          # 10% to 100% (or 10% to 95%) DoD
    min_soc_pct: float = 10.0                   # Hard low-voltage protection limit
    max_soc_pct: float = 95.0                   # High-voltage cycle life preservation limit
    max_charge_kw: float = 5.0                  # Inverter continuous AC charge limit
    max_discharge_kw: float = 5.0               # Inverter continuous AC discharge limit
    roundtrip_efficiency: float = 0.87          # AC-DC-AC roundtrip efficiency
    degradation_cost_eur_kwh: float = 0.078     # LCOC based on €6000 / 76500 kWh throughput
    min_cycle_margin_eur_kwh: float = 0.085     # In-device adjustable profit margin slider
    # Lookahead & drempels (volledige 24-uurs planhorizon ipv starre 8-9 uur)
    reserve_lookahead_slots: int = 96           # 24 uur vooruitkijken voor piekreservering
    valley_lookahead_slots: int = 96            # 24 uur vooruitkijken voor daldetectie
    peak_price_delta_eur_kwh: float = 0.08      # Minimaal prijsverschil dat een slot 'piek' maakt
    reserve_price_delta_eur_kwh: float = 0.12   # Minimaal prijsverschil voor netlaad-reservering
    grid_charge_soc_ceiling_pct: float = 85.0   # Netladen stopt hier; de rest is voor zon


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
        # Multi-Pass Optimization
        # -------------------------------------------------------------------
        # Pre-allocate dispatch arrays
        power_dispatch_kw = [0.0] * n_slots
        mode_codes = ["STANDBY"] * n_slots
        mode_labels = ["Standby"] * n_slots

        # --- PASS 1: Identify High-Value Peak Discharge Hours ---
        # Sort positive residual slots by import price descending
        peak_candidates = []
        for i in range(n_slots):
            if residual_demand_kw[i] > 0.05:
                # Prioritize explicit forced lockouts or high prices
                is_lockout = i in forced_lockouts
                peak_candidates.append((import_prices[i], is_lockout, i))

        # --- PASS 2: Simulate Solar Absorptions and Opportunistic Grid Valleys ---
        # We perform forward simulation with lookahead reserving
        sim_soc_kwh = cur_kwh

        # Identify valley hours suitable for grid pre-charging in winter
        # Find minimum price in the upcoming 12 hours
        valley_candidates = []
        for i in range(n_slots):
            p_in = import_prices[i]
            # Check if this hour is a distinct valley compared to future peak prices
            future_peaks = [
                import_prices[j]
                for j in range(i + 1, min(n_slots, i + spec.valley_lookahead_slots))
                if residual_demand_kw[j] > 0.1
            ]
            if future_peaks:
                max_future_p = max(future_peaks)
                spread = max_future_p - (p_in / spec.roundtrip_efficiency) - spec.degradation_cost_eur_kwh
                if spread >= spec.min_cycle_margin_eur_kwh:
                    valley_candidates.append((p_in, spread, i))

        valley_slots = {idx for _, _, idx in sorted(valley_candidates, key=lambda x: x[0])}

        # --- PASS 3: Forward Simulation with Strategic Reservation ---
        running_soc = cur_kwh

        for i in range(n_slots):
            res_kw = residual_demand_kw[i]
            p_in = import_prices[i]
            p_ex = export_prices[i]
            is_lockout = i in forced_lockouts

            # 1. Negative residual = Solar Surplus available
            if res_kw < -0.05:
                surplus_kw = abs(res_kw)
                max_intake_kw = min(surplus_kw, spec.max_charge_kw)
                # Headroom in battery
                headroom_kwh = max(0.0, max_kwh - running_soc)
                intake_kwh = min(max_intake_kw * step_hours * eta_one_way, headroom_kwh)
                act_kw = (intake_kwh / (step_hours * eta_one_way)) if step_hours > 0 else 0.0

                if act_kw > 0.05:
                    power_dispatch_kw[i] = round(act_kw, 3)
                    mode_codes[i] = "CHARGE_SOLAR"
                    mode_labels[i] = "Zon-absorptie"
                    running_soc += intake_kwh
                    continue

            # 2. Opportunistic Grid Valley Charging (Winter night charging)
            if i in valley_slots and running_soc < (max_kwh * (spec.grid_charge_soc_ceiling_pct / 100.0)):
                # Calculate needed reserve for upcoming peaks
                upcoming_peak_energy = sum(
                    residual_demand_kw[j] * step_hours
                    for j in range(i + 1, min(n_slots, i + spec.reserve_lookahead_slots))
                    if j in forced_lockouts or import_prices[j] >= p_in + spec.reserve_price_delta_eur_kwh
                )
                headroom_kwh = max(0.0, max_kwh - running_soc)
                charge_target_kwh = min(headroom_kwh, upcoming_peak_energy)
                if charge_target_kwh > 0.5:
                    charge_kw = min(spec.max_charge_kw, charge_target_kwh / step_hours)
                    in_kwh = min(charge_kw * step_hours * eta_one_way, headroom_kwh)
                    power_dispatch_kw[i] = round(charge_kw, 3)
                    mode_codes[i] = "CHARGE_GRID"
                    mode_labels[i] = "Nachtladen (Dal)"
                    running_soc += in_kwh
                    continue

            # 3. Positive residual = House needs energy
            if res_kw > 0.05:
                # Look ahead: Are there imminent higher-priced peaks within next 8 hours?
                future_lockouts = [
                    j for j in range(i + 1, min(n_slots, i + spec.reserve_lookahead_slots))
                    if (j in forced_lockouts or import_prices[j] > p_in + spec.peak_price_delta_eur_kwh)
                    and residual_demand_kw[j] > 0.1
                ]
                future_critical_kwh = sum(
                    min(residual_demand_kw[j], spec.max_discharge_kw) * step_hours
                    for j in future_lockouts
                )
                available_to_discharge_kwh = max(0.0, running_soc - min_kwh)

                # Economische poort, gelijk aan die van DISCHARGE_BUFFER: ontladen moet de
                # vermeden inkoop laten winnen van degradatie plus de exportwaarde van dezelfde kWh.
                is_profitable = (p_in - spec.degradation_cost_eur_kwh) > p_ex

                # If current slot is an explicit lockout OR there are no higher-priced peaks ahead:
                if is_profitable and (is_lockout or not future_lockouts):
                    # Full Peak Shaving
                    req_kwh = min(res_kw, spec.max_discharge_kw) * step_hours
                    dis_kwh = min(req_kwh, available_to_discharge_kwh * eta_one_way)
                    if dis_kwh > 0.02:
                        # dis_kwh is AC-energie; act_kw is dus AC-vermogen.
                        # Delen door eta hier zou de omvormerlimiet met 1/eta overschrijden
                        # en de cel met 1/eta^2 legen (de boekhouding deelt al door eta).
                        act_kw = dis_kwh / step_hours
                        power_dispatch_kw[i] = -round(act_kw, 3)
                        mode_codes[i] = "DISCHARGE_PEAK"
                        mode_labels[i] = "Spitsontlasting"
                        running_soc -= (dis_kwh / eta_one_way)
                        continue

                # Not a peak, but we have more energy than needed for future peaks:
                excess_kwh = available_to_discharge_kwh - (future_critical_kwh / eta_one_way)
                if excess_kwh > 0.5 and (p_in - spec.degradation_cost_eur_kwh) > p_ex:
                    # Off-peak residual discharge
                    req_kwh = min(res_kw, spec.max_discharge_kw) * step_hours
                    dis_kwh = min(req_kwh, excess_kwh * eta_one_way)
                    if dis_kwh > 0.02:
                        # dis_kwh is AC-energie; act_kw is dus AC-vermogen.
                        # Delen door eta hier zou de omvormerlimiet met 1/eta overschrijden
                        # en de cel met 1/eta^2 legen (de boekhouding deelt al door eta).
                        act_kw = dis_kwh / step_hours
                        power_dispatch_kw[i] = -round(act_kw, 3)
                        mode_codes[i] = "DISCHARGE_BUFFER"
                        mode_labels[i] = "Restontlading"
                        running_soc -= (dis_kwh / eta_one_way)
                        continue
                elif future_critical_kwh > 0.5 and available_to_discharge_kwh > 0:
                    # Explicit reservation for upcoming higher peak
                    mode_codes[i] = "HOLD_RESERVE"
                    mode_labels[i] = "Reserveren"
                    continue

            # Standby default
            mode_codes[i] = "STANDBY"
            mode_labels[i] = "Standby"

        # -------------------------------------------------------------------
        # Build Results & Financial Accounting
        # -------------------------------------------------------------------
        slot_results: List[BatterySlotResult] = []
        tot_ch_solar = 0.0
        tot_ch_grid = 0.0
        tot_dis = 0.0
        net_financial_saving = 0.0

        current_soc = cur_kwh
        min_proj_soc = initial_soc_pct
        max_proj_soc = initial_soc_pct

        for i in range(n_slots):
            kw = power_dispatch_kw[i]
            code = mode_codes[i]
            label = mode_labels[i]
            p_in = import_prices[i]
            p_ex = export_prices[i]

            cost_impact = 0.0
            if kw > 0:  # Charging
                kwh_in = kw * step_hours
                current_soc = min(max_kwh, current_soc + kwh_in * eta_one_way)
                if code == "CHARGE_SOLAR":
                    tot_ch_solar += kwh_in
                    # Opportunity cost = lost feed-in
                    cost_impact = kwh_in * p_ex
                else:
                    tot_ch_grid += kwh_in
                    cost_impact = kwh_in * p_in
                net_financial_saving -= cost_impact
            elif kw < 0:  # Discharging
                kwh_out = abs(kw) * step_hours
                current_soc = max(min_kwh, current_soc - (kwh_out / eta_one_way))
                tot_dis += kwh_out
                # Avoided retail grid import
                avoided_cost = kwh_out * p_in
                degradation = kwh_out * spec.degradation_cost_eur_kwh
                cost_impact = -(avoided_cost - degradation)
                net_financial_saving -= cost_impact

            soc_pct = round((current_soc / spec.capacity_kwh) * 100.0, 1)
            min_proj_soc = min(min_proj_soc, soc_pct)
            max_proj_soc = max(max_proj_soc, soc_pct)

            slot_results.append(
                BatterySlotResult(
                    slot_idx=i,
                    power_kw=kw,
                    mode_code=code,
                    mode_label=label,
                    soc_pct=soc_pct,
                    soc_kwh=round(current_soc, 3),
                    cost_impact_eur=round(cost_impact, 4),
                )
            )

        return BatteryPlanSummary(
            total_charged_solar_kwh=round(tot_ch_solar, 2),
            total_charged_grid_kwh=round(tot_ch_grid, 2),
            total_discharged_kwh=round(tot_dis, 2),
            net_financial_saving_eur=round(net_financial_saving, 2),
            initial_soc_pct=round(initial_soc_pct, 1),
            final_soc_pct=round((current_soc / spec.capacity_kwh) * 100.0, 1),
            min_projected_soc_pct=min_proj_soc,
            max_projected_soc_pct=max_proj_soc,
            slots=slot_results,
        )


def extract_battery_overlay_ranges(slots: List[BatterySlotResult], is_15m: bool = True) -> Dict[str, List[Dict[str, Any]]]:
    """
    Unified overlay extractor for battery operation windows (charging, discharging, holding).
    Guarantees 15m vs 1h parity without double downsampling.
    """
    if not slots:
        return {"charge": [], "discharge": [], "hold": []}

    needs_downsample = (not is_15m) and len(slots) > 48
    n = len(slots) // 4 if needs_downsample else len(slots)

    def scan_ranges(predicate, category_name):
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
                    "start_idx": start_idx,
                    "end_idx": i - 1,
                    "mode_code": cur_modes[0],
                    "power_kw": round(sum(cur_powers) / len(cur_powers), 2),
                    "name": category_name
                })
        if in_block:
            res.append({
                "start_idx": start_idx,
                "end_idx": n - 1,
                "mode_code": cur_modes[0],
                "power_kw": round(sum(cur_powers) / len(cur_powers), 2),
                "name": category_name
            })
        return res

    charge_ranges = scan_ranges(lambda s: s.mode_code in ["CHARGE_SOLAR", "CHARGE_GRID"], "BATTERY_CHARGE")
    discharge_ranges = scan_ranges(lambda s: s.mode_code in ["DISCHARGE_PEAK", "DISCHARGE_BUFFER"], "BATTERY_DISCHARGE")
    hold_ranges = scan_ranges(lambda s: s.mode_code == "HOLD_RESERVE", "HOLD_RESERVE")

    return {
        "charge": charge_ranges,
        "discharge": discharge_ranges,
        "hold": hold_ranges
    }

