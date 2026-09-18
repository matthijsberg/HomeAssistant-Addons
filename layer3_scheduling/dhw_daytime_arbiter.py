"""
Layer 3: DHW Daytime Dispatch & Economic Arbitrage Arbiter
==========================================================
Clean, deterministic daytime DHW scheduling engine (Clean Architecture).
Evaluates true 24-hour all-in electricity costs comparing 50°C vs 60°C runs
and simulated subsequent night runs without arbitrary solar thresholds.

Strictly preserves all operational and physical invariants:
- 350L DHW vat (0.407 kWh/K)
- No mock data in production
- Zero Home Assistant entity strings or brand names
- Full decision audit logging & traceability
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional, Tuple
import math

from layer2_calibration.dhw_thermal_model import DhwThermalModel
from layer3_scheduling.tariff_provider import TariffProvider
from layer3_scheduling.dhw_specs import DhwTankSpec


@dataclass
class EvaluatedPath:
    """Represents a fully evaluated 24-hour candidate dispatch trajectory."""
    path_id: str                          # e.g. "PAD_A1_DAY_50", "PAD_A2_DAY_60", "PAD_B1_STANDBY", "PAD_B2_BUFFER_60"
    name: str
    description: str
    day_target_temp_c: float
    day_slots: List[int] = field(default_factory=list)
    day_window_label: str = "N.v.t."
    day_cost_eur: float = 0.0
    day_power_kw: float = 1.8
    day_el_kwh: float = 0.0
    simulated_morning_dip_c: float = 50.0
    simulated_morning_dip_time: str = ""
    night_run_required: bool = False
    night_slots: List[int] = field(default_factory=list)
    night_window_label: str = "N.v.t."
    night_cost_eur: float = 0.0
    night_el_kwh: float = 0.0
    total_24h_cost_eur: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "path_id": self.path_id,
            "name": self.name,
            "description": self.description,
            "day_target_temp_c": self.day_target_temp_c,
            "day_slots": self.day_slots,
            "day_window_label": self.day_window_label,
            "day_cost_eur": round(self.day_cost_eur, 3),
            "day_power_kw": round(self.day_power_kw, 2),
            "day_el_kwh": round(self.day_el_kwh, 2),
            "simulated_morning_dip_c": round(self.simulated_morning_dip_c, 1),
            "simulated_morning_dip_time": self.simulated_morning_dip_time,
            "night_run_required": self.night_run_required,
            "night_slots": self.night_slots,
            "night_window_label": self.night_window_label,
            "night_cost_eur": round(self.night_cost_eur, 3),
            "night_el_kwh": round(self.night_el_kwh, 2),
            "total_24h_cost_eur": round(self.total_24h_cost_eur, 3)
        }


@dataclass
class DaytimeArbitrationResult:
    """The canonical outcome of daytime DHW economic arbitrage."""
    situation: str                        # "SITUATION_1_EVENING_COMFORT_RISK" or "SITUATION_2_COMFORT_SAFE"
    unheated_evening_dip_c: float
    evening_dip_time: str
    evaluated_paths: List[EvaluatedPath]
    selected_path: EvaluatedPath
    planned_mode: str                     # "forced_on", "forced_solar_boost_60", "normal"
    planned_mode_label: str
    target_temp_c: float
    power_kw: float
    planned_slots: List[int]
    savings_eur: float
    explanation: str

    def to_audit_dict(self) -> Dict[str, Any]:
        return {
            "decision_type": "DHW_DAYTIME_DISPATCH",
            "situation": self.situation,
            "unheated_evening_dip_c": round(self.unheated_evening_dip_c, 1),
            "evening_dip_time": self.evening_dip_time,
            "selected_path_id": self.selected_path.path_id,
            "planned_mode": self.planned_mode,
            "planned_mode_label": self.planned_mode_label,
            "target_temp_c": self.target_temp_c,
            "power_kw": self.power_kw,
            "planned_slots_count": len(self.planned_slots),
            "savings_eur": round(self.savings_eur, 2),
            "explanation": self.explanation,
            "evaluated_paths": [p.to_dict() for p in self.evaluated_paths]
        }


class DhwDaytimeArbiter:
    """
    Modular, pure-function suite for daytime DHW economic arbitration.
    """

    DHW_THERMAL_CAPACITY_KWH_PER_K = 0.407   # 350L * 4.186 / 3600
    DHW_STANDBY_LOSS_50_KW = 0.0589          # 58.9W at 50°C nominal
    DHW_STANDBY_LOSS_60_KW = 0.0850          # 85.0W at 60°C nominal
    DHW_HEAT_PUMP_ELECTRIC_KW = 1.8          # Nominal electric compressor power
    DHW_SOLAR_BOOST_ELECTRIC_KW = 2.4        # Boost compressor power
    DHW_COMFORT_MIN_TEMP_C = 40.0            # Minimum comfort temperature
    DHW_BUFFER_60_MAX_TANK_TEMP_C = 53.0     # Max tank temp to allow 60°C buffer (>=53°C is saturated)
    DHW_BUFFER_60_MIN_HEADROOM_C = 7.0      # Min temperature headroom required to justify 60°C run

    # Safety buffer: accounts for tank standby cooling during active heating run (~0.08 kW * run duration)
    # plus minor tap draw-offs, ensuring math.ceil does not leave the tank 0.2-0.5°C short of setpoint.
    RUN_THERMAL_BUFFER_KWH = 0.15

    @classmethod
    def calculate_required_slots(
        cls,
        current_temp: float,
        target_temp: float,
        spec: DhwTankSpec,
        step_hours: float = 0.25,
        min_slots: int = 2,
        max_slots: int = 8,
        min_delta_c: float = 1.0,
    ) -> int:
        """
        Calculates the required number of quarter-hour heating slots using the
        canonical thermodynamic thermal output: P_th = P_el * COP(target).
        Includes a small thermal safety buffer (0.15 kWh_th / ~0.37°C) to compensate for
        concomitant tank standby heat loss and minor tap draw-offs during the run.
        """
        th_output_kw = spec.get_thermal_output_kw(target_temp)
        delta_t = max(min_delta_c, target_temp - current_temp)
        th_need = (delta_t * spec.thermal_capacity_kwh_per_k) + cls.RUN_THERMAL_BUFFER_KWH
        slot_kwh_th = th_output_kw * step_hours
        return max(min_slots, min(max_slots, math.ceil(th_need / slot_kwh_th)))

    @classmethod
    def calculate_slot_financials(
        cls,
        solar_kw: float,
        unalloc_kw: float,
        el_demand_kw: float,
        price_all_in: float,
        step_hours: float = 0.25,
        tariff_provider: Optional[TariffProvider] = None
    ) -> Tuple[float, float, float, float]:
        """
        Pure function: Calculates electricity costs for a single quarter-hour slot.
        - Solar/battery self-consumption is valued at avoided feed-in price (derived via TariffProvider).
        - Grid import is valued at all-in consumer price (EPEX spot + energy tax + opslag + VAT).

        Returns: (cost_eur, self_kwh, grid_kwh, p_effective)
        """
        demand_kwh = max(0.0, el_demand_kw * step_hours)
        if demand_kwh <= 0.0:
            return 0.0, 0.0, 0.0, price_all_in

        surplus_kw = max(0.0, solar_kw - unalloc_kw)
        surplus_kwh = surplus_kw * step_hours

        self_kwh = min(demand_kwh, surplus_kwh)
        grid_kwh = max(0.0, demand_kwh - self_kwh)

        # Net feed-in tariff derived dynamically via TariffProvider
        tp = tariff_provider or TariffProvider()
        p_export = tp.calculate_export_value_from_import(price_all_in)
        cost_eur = (grid_kwh * price_all_in) + (self_kwh * p_export)
        p_effective = cost_eur / demand_kwh if demand_kwh > 0 else price_all_in

        return cost_eur, self_kwh, grid_kwh, p_effective

    @classmethod
    def evaluate_window_cost(
        cls,
        slots: List[Any],
        start_idx: int,
        n_req_slots: int,
        th_need_kwh: float,
        is_boost_60: bool = False,
        hours_until_target: float = 0.0,
        step_hours: float = 0.25,
        tariff_provider: Optional[TariffProvider] = None,
        tank_spec: Optional[DhwTankSpec] = None
    ) -> Tuple[float, float, float]:
        """
        Pure function: Evaluates the total electricity cost and standing losses of a candidate window.
        Takes into account outdoor-temperature-dependent COP and standing loss.

        Returns: (total_window_cost_eur, total_el_kwh, mean_cop)
        """
        spec = tank_spec or DhwTankSpec()
        end_idx = start_idx + n_req_slots
        window_slots = slots[start_idx:end_idx]
        if not window_slots:
            return 999.0, 0.0, 1.0

        total_cost = 0.0
        total_el = 0.0
        cops = []

        th_per_slot = th_need_kwh / max(1, n_req_slots)

        for s in window_slots:
            t_out = getattr(s, "outdoor_temp_c", 10.0)
            base_cop = max(1.8, min(4.5, 2.55 + (0.075 * t_out)))
            # COP is ~25% lower for high-temperature boost (50->60°C)
            cop_slot = base_cop * 0.75 if is_boost_60 else base_cop
            cops.append(cop_slot)

            el_slot_kwh = th_per_slot / cop_slot
            total_el += el_slot_kwh

            el_kw = el_slot_kwh / step_hours
            solar_kw = getattr(s, "solar_kw", 0.0)
            unalloc_kw = getattr(s, "unallocated_kw", 0.35)
            price_all_in = getattr(s, "price_all_in", getattr(s, "price_eur", 0.30))

            cost, _, _, _ = cls.calculate_slot_financials(solar_kw, unalloc_kw, el_kw, price_all_in, step_hours, tariff_provider=tariff_provider)
            total_cost += cost

        mean_cop = sum(cops) / len(cops) if cops else 2.8

        # Add standing loss penalty from end of window until target horizon
        if hours_until_target > 0.0:
            loss_kw = spec.standby_loss_60_kw if is_boost_60 else spec.standby_loss_50_kw
            extra_th_loss = hours_until_target * loss_kw
            extra_el_loss = extra_th_loss / mean_cop
            mean_price = sum(getattr(s, "price_all_in", getattr(s, "price_eur", 0.30)) for s in window_slots) / len(window_slots)
            total_cost += extra_el_loss * mean_price

        return total_cost, total_el, mean_cop

    @classmethod
    def find_optimal_heating_window(
        cls,
        slots: List[Any],
        search_start_idx: int,
        search_end_idx: int,
        n_req_slots: int,
        th_need_kwh: float,
        slot_lockout_map: Dict[int, Any],
        is_boost_60: bool = False,
        target_anchor_idx: Optional[int] = None,
        step_hours: float = 0.25,
        tariff_provider: Optional[TariffProvider] = None,
        tank_spec: Optional[DhwTankSpec] = None
    ) -> Optional[Dict[str, Any]]:
        """
        Pure function: Searches for the cheapest contiguous window of N slots outside peak lockouts.
        """
        candidate_windows = []
        max_start = min(len(slots) - n_req_slots, search_end_idx - n_req_slots)

        for start_idx in range(search_start_idx, max_start + 1):
            end_idx = start_idx + n_req_slots

            # Hard peak lockout check
            has_lockout = any(slot_lockout_map.get(k, {}).get("is_hard_lockout") for k in range(start_idx, end_idx))
            if has_lockout:
                continue

            hours_until = 0.0
            if target_anchor_idx is not None and target_anchor_idx > end_idx:
                hours_until = (target_anchor_idx - end_idx) * step_hours

            cost, el_kwh, cop = cls.evaluate_window_cost(
                slots, start_idx, n_req_slots, th_need_kwh, is_boost_60, hours_until, step_hours,
                tariff_provider=tariff_provider, tank_spec=tank_spec
            )
            candidate_windows.append({
                "start_idx": start_idx,
                "end_idx": end_idx,
                "cost_eur": cost,
                "el_kwh": el_kwh,
                "cop": cop
            })

        if not candidate_windows:
            return None

        candidate_windows.sort(key=lambda w: w["cost_eur"])
        return candidate_windows[0]

    @classmethod
    def simulate_path_and_evaluate_night(
        cls,
        slots: List[Any],
        current_dhw_temp: float,
        day_target_c: float,
        day_window: Optional[Dict[str, Any]],
        dynamic_peaks: List[Dict[str, Any]],
        slot_lockout_map: Dict[int, Any],
        dhw_model: DhwThermalModel,
        now_dt: datetime,
        step_hours: float = 0.25,
        tariff_provider: Optional[TariffProvider] = None,
        tank_spec: Optional[DhwTankSpec] = None
    ) -> Dict[str, Any]:
        """
        Pure function: Simulates post-dayrun trajectory with DhwThermalModel and determines
        whether a night run is genuinely required (< 40°C morning dip) and what it costs.
        Eliminates premature assumptions: if the morning dip stays >= 40°C, night cost is strictly €0.00.
        """
        spec = tank_spec or DhwTankSpec()
        n_slots = len(slots)
        day_slots = list(range(day_window["start_idx"], day_window["end_idx"])) if day_window else []

        # Run simulation across the full horizon with scheduled daytime slots
        horizon_hours = max(24, int(round(n_slots * step_hours)))
        sim = dhw_model.simulate_trajectory(
            t_start_c=current_dhw_temp,
            start_dt=now_dt,
            hours_ahead=horizon_hours,
            heat_pump_schedule_slots=day_slots,
            target_temp_c=day_target_c,
            heat_pump_power_kw=spec.solar_boost_electric_kw if day_target_c > 52.0 else spec.heat_pump_electric_kw
        )
        sim_temps = sim.get("temperatures_c", [])
        sim_labels = sim.get("labels", [])

        # Comfort check across simulated horizon: detect any drop below comfort threshold (40.0°C)
        search_start_dip = max(day_slots) + 1 if day_slots else 0
        dip_slots = [
            (idx, lbl, t) for idx, (lbl, t) in enumerate(zip(sim_labels, sim_temps))
            if t < spec.comfort_min_temp_c and idx >= search_start_dip
        ]

        if dip_slots:
            first_dip_idx, first_dip_time, _ = dip_slots[0]
            min_dip = min(dip_slots, key=lambda x: x[2])
            morn_dip_c = round(min_dip[2], 1)
            morn_dip_time = first_dip_time
            night_required = True
        else:
            morn_slots = [
                (idx, lbl, t) for idx, (lbl, t) in enumerate(zip(sim_labels, sim_temps))
                if ("06:00" <= lbl <= "09:45" and idx >= 16)
            ]
            if morn_slots:
                min_morn = min(morn_slots, key=lambda x: x[2])
                morn_dip_c = round(min_morn[2], 1)
                morn_dip_time = min_morn[1]
            else:
                morn_dip_c = round(min(sim_temps[:36]), 1) if sim_temps else current_dhw_temp
                morn_dip_time = "08:30"
            night_required = False
        night_cost = 0.0
        night_el = 0.0
        night_slots = []
        night_window_lbl = "N.v.t. (Ochtendcomfort gegarandeerd)"

        if night_required:
            # Plan night run to 50°C between 20:00 and 06:00
            # Find night search window (between 21:00 tonight and 06:00 tomorrow morning)
            night_start_idx = 0
            morn_start_idx = n_slots
            for idx, s in enumerate(slots):
                dt_val = getattr(s, "dt", None)
                if dt_val is None:
                    sl_iso = getattr(s, "dt_iso", "")
                    dt_val = datetime.fromisoformat(sl_iso).astimezone(now_dt.tzinfo) if sl_iso else (now_dt + timedelta(minutes=15 * idx))
                lbl = getattr(s, "label", getattr(s, "time_label", ""))
                h = int(lbl.split(":")[0]) if ":" in lbl and not lbl.startswith("Nu") else dt_val.hour
                if (h >= 21 or dt_val.hour >= 21) and night_start_idx == 0 and dt_val.date() == now_dt.date():
                    night_start_idx = idx
                if idx > 0 and (h >= 6 or dt_val.hour >= 6) and (dt_val.date() > now_dt.date() or now_dt.hour < 6):
                    morn_start_idx = idx
                    break

            # Temperature before night run (estimate from simulation around 03:00)
            t_night_est = max(34.0, morn_dip_c - 1.5)
            n_night_slots = cls.calculate_required_slots(
                current_temp=t_night_est,
                target_temp=spec.target_setpoint_c,
                spec=spec,
                step_hours=step_hours,
                min_slots=2,
                max_slots=6,
                min_delta_c=1.0,
            )
            th_need_night = max(1.0, spec.target_setpoint_c - t_night_est) * spec.thermal_capacity_kwh_per_k

            opt_night = cls.find_optimal_heating_window(
                slots=slots,
                search_start_idx=night_start_idx,
                search_end_idx=morn_start_idx,
                n_req_slots=n_night_slots,
                th_need_kwh=th_need_night,
                slot_lockout_map=slot_lockout_map,
                is_boost_60=False,
                target_anchor_idx=morn_start_idx,
                step_hours=step_hours,
                tariff_provider=tariff_provider,
                tank_spec=spec
            )

            if opt_night:
                night_cost = opt_night["cost_eur"]
                night_el = opt_night["el_kwh"]
                night_slots = list(range(opt_night["start_idx"], opt_night["end_idx"]))
                start_lbl = getattr(slots[opt_night["start_idx"]], "label", getattr(slots[opt_night["start_idx"]], "time_label", ""))
                end_lbl = getattr(slots[min(n_slots - 1, opt_night["end_idx"])], "label", getattr(slots[min(n_slots - 1, opt_night["end_idx"])], "time_label", ""))
                night_window_lbl = f"{start_lbl}–{end_lbl}"

        day_cost = day_window["cost_eur"] if day_window else 0.0
        day_el = day_window["el_kwh"] if day_window else 0.0
        total_24h_cost = day_cost + night_cost

        return {
            "day_slots": day_slots,
            "day_cost_eur": day_cost,
            "day_el_kwh": day_el,
            "morning_dip_c": morn_dip_c,
            "morning_dip_time": morn_dip_time,
            "night_run_required": night_required,
            "night_slots": night_slots,
            "night_window_label": night_window_lbl,
            "night_cost_eur": night_cost,
            "night_el_kwh": night_el,
            "total_24h_cost_eur": total_24h_cost
        }

    @classmethod
    def compute_optimal_horizon_target_temp(
        cls,
        slots: List[Any],
        current_dhw_temp: float,
        dhw_model: DhwThermalModel,
        now_dt: datetime,
        tank_spec: Optional[DhwTankSpec] = None,
        step_hours: float = 0.25
    ) -> Tuple[float, int, float, float, float, float]:
        """
        Pure function: The Backwards Horizon Solver.
        Calculates the exact minimum optimal target temperature needed to bridge
        until the next economically favorable heating window (tomorrow solar or night valley)
        without dropping below comfort minimum (40°C).
        """
        spec = tank_spec or DhwTankSpec()
        n_slots = len(slots)

        slot_dts = []
        for i, s in enumerate(slots):
            sl_dt = getattr(s, "dt", None)
            if sl_dt is None:
                sl_iso = getattr(s, "dt_iso", "")
                sl_dt = datetime.fromisoformat(sl_iso).astimezone(now_dt.tzinfo) if sl_iso else (now_dt + timedelta(minutes=15 * i))
            slot_dts.append(sl_dt)

        # 1. Search for next economic anchor (at least 3 hours ahead)
        # Prioritize tomorrow's solar surplus window, then night valley (01:00-05:00)
        next_anchor_idx = None
        for i in range(12, n_slots):
            s = slots[i]
            dt = slot_dts[i]
            surplus = getattr(s, "solar_kw", 0.0) - getattr(s, "unallocated_kw", 0.35)
            if dt.date() > now_dt.date() and surplus >= 0.8:
                next_anchor_idx = i
                break

        if next_anchor_idx is None:
            for i in range(8, n_slots):
                dt = slot_dts[i]
                if 1 <= dt.hour <= 5:
                    next_anchor_idx = i
                    break

        if next_anchor_idx is None:
            next_anchor_idx = min(95, n_slots - 1)

        hours_to_anchor = next_anchor_idx * step_hours

        # 2. Integrate thermal energy needs (draw-offs + standing loss) until anchor
        q_draw = sum(
            dhw_model.get_learned_tap_kwh_th(slot_dts[k].weekday(), slot_dts[k].hour * 4 + slot_dts[k].minute // 15)
            for k in range(next_anchor_idx)
        )
        q_loss = hours_to_anchor * spec.standby_loss_50_kw
        q_needed = q_draw + q_loss

        # 3. Calculate target temperature from 40°C comfort floor
        # T_target = T_min + (Q_needed / C_vat) + safety_margin (1.0°C)
        t_target_raw = spec.comfort_min_temp_c + (q_needed / spec.thermal_capacity_kwh_per_k) + 1.0
        t_target_opt = max(spec.target_setpoint_c, min(spec.boost_setpoint_c, round(t_target_raw * 2.0) / 2.0))

        return t_target_opt, next_anchor_idx, hours_to_anchor, q_needed, q_draw, q_loss

    @classmethod
    def evaluate_daytime_arbitrage(
        cls,
        slots: List[Any],
        current_dhw_temp: float,
        dynamic_peaks: List[Dict[str, Any]],
        dhw_model: DhwThermalModel,
        now_dt: datetime,
        step_hours: float = 0.25,
        tariff_provider: Optional[TariffProvider] = None,
        tank_spec: Optional[DhwTankSpec] = None
    ) -> DaytimeArbitrationResult:
        """
        Executes complete 24-hour economic arbitration comparing Situation 1 and Situation 2.
        """
        spec = tank_spec or DhwTankSpec()
        tp = tariff_provider or TariffProvider()
        n_slots = len(slots)
        slot_lockout_map = {idx: p for p in dynamic_peaks for idx in range(p.get("start_idx", 0), p.get("end_idx", 0))}

        # 1. Simulate unheated baseline until 22:00 (evening peak assessment)
        unheated_sim = dhw_model.simulate_trajectory(
            t_start_c=current_dhw_temp,
            start_dt=now_dt,
            hours_ahead=24,
            heat_pump_schedule_slots=[]
        )
        unheated_temps = unheated_sim.get("temperatures_c", [])
        unheated_labels = unheated_sim.get("labels", [])

        # 2. Dynamic Peak Boundaries & Inter-Peak Valley
        # Find the upcoming morning peak and upcoming evening peak in the rolling horizon
        morn_lockout_end_idx = 0
        eve_lockout_start_idx = n_slots

        for p in dynamic_peaks:
            if p.get("is_hard_lockout"):
                s_i = p.get("start_idx", 0)
                e_i = p.get("end_idx", n_slots)
                p_name = p.get("name", "")

                p_slot = slots[min(s_i, n_slots - 1)]
                p_dt = getattr(p_slot, "dt", None)
                if p_dt is None:
                    sl_iso = getattr(p_slot, "dt_iso", "")
                    p_dt = datetime.fromisoformat(sl_iso).astimezone(now_dt.tzinfo) if sl_iso else (now_dt + timedelta(minutes=15 * s_i))

                if "Ochtend" in p_name or p_dt.hour < 12:
                    morn_lockout_end_idx = max(morn_lockout_end_idx, e_i)
                elif "Avond" in p_name or "Middag" in p_name or p_dt.hour >= 12:
                    eve_lockout_start_idx = min(eve_lockout_start_idx, s_i)

        # 3. Dynamic Comfort Risk Assessment
        # Inspect temperature dip before/during the upcoming evening peak in the 24h rolling horizon
        evening_slots = []
        for i, (lbl, t) in enumerate(zip(unheated_labels, unheated_temps)):
            if i < n_slots:
                sl_dt = getattr(slots[i], "dt", None)
                if sl_dt is None:
                    sl_iso = getattr(slots[i], "dt_iso", "")
                    sl_dt = datetime.fromisoformat(sl_iso).astimezone(now_dt.tzinfo) if sl_iso else (now_dt + timedelta(minutes=15 * i))
                if 17 <= sl_dt.hour <= 22:
                    evening_slots.append((i, lbl, t))

        if evening_slots:
            min_eve = min(evening_slots, key=lambda x: x[2])
            unheated_evening_dip = round(min_eve[2], 1)
            evening_dip_time = min_eve[1]
        else:
            unheated_evening_dip = round(min(unheated_temps), 1) if unheated_temps else current_dhw_temp
            evening_dip_time = "19:30"

        # Determine situation: evening comfort at risk if unheated tank dips below comfort threshold before/during evening peak
        evening_comfort_risk = (unheated_evening_dip < spec.comfort_min_temp_c or current_dhw_temp <= 43.5)

        # 4. Pure Dynamic Search Window:
        # The daytime heating window is the dynamic valley between the morning peak and the evening peak
        day_start_idx = morn_lockout_end_idx
        day_end_idx = eve_lockout_start_idx

        # If no dynamic morning peak is present in horizon, find start of daytime (hour >= 9 or solar generation)
        if day_start_idx == 0:
            for idx, s in enumerate(slots):
                dt_val = getattr(s, "dt", None)
                if dt_val is None:
                    sl_iso = getattr(s, "dt_iso", "")
                    dt_val = datetime.fromisoformat(sl_iso).astimezone(now_dt.tzinfo) if sl_iso else (now_dt + timedelta(minutes=15 * idx))
                if dt_val.hour >= 9 or getattr(s, "solar_kw", 0.0) > 0.2:
                    day_start_idx = idx
                    break

        # If no dynamic evening peak is present in horizon, bound daytime before evening (hour >= 18)
        if day_end_idx == n_slots:
            for idx in range(day_start_idx, n_slots):
                s = slots[idx]
                dt_val = getattr(s, "dt", None)
                if dt_val is None:
                    sl_iso = getattr(s, "dt_iso", "")
                    dt_val = datetime.fromisoformat(sl_iso).astimezone(now_dt.tzinfo) if sl_iso else (now_dt + timedelta(minutes=15 * idx))
                if dt_val.hour >= 18:
                    day_end_idx = idx
                    break

        if day_start_idx >= day_end_idx:
            day_start_idx = 0
            day_end_idx = n_slots

        # Compute dynamic continuous optimal target temperature via Backwards Horizon Solver
        t_target_opt, next_anchor_idx, hours_to_anchor, q_needed, q_draw, q_loss = cls.compute_optimal_horizon_target_temp(
            slots, current_dhw_temp, dhw_model, now_dt, spec, step_hours
        )

        evaluated_paths: List[EvaluatedPath] = []

        if evening_comfort_risk:
            # === SITUATION 1: Evening comfort at risk -> Day heating is MANDATORY ===
            situation = "SITUATION_1_EVENING_COMFORT_RISK"

            # PAD A1: Overdag naar dynamische optimale doeltemperatuur + Natraject Simulatie
            n_slots_50 = cls.calculate_required_slots(
                current_temp=current_dhw_temp,
                target_temp=t_target_opt,
                spec=spec,
                step_hours=step_hours,
                min_slots=2,
                max_slots=8,
                min_delta_c=1.0,
            )
            th_need_50 = max(1.0, t_target_opt - current_dhw_temp) * spec.thermal_capacity_kwh_per_k
            opt_day_50 = cls.find_optimal_heating_window(
                slots=slots,
                search_start_idx=day_start_idx,
                search_end_idx=day_end_idx,
                n_req_slots=n_slots_50,
                th_need_kwh=th_need_50,
                slot_lockout_map=slot_lockout_map,
                is_boost_60=False,
                step_hours=step_hours,
                tariff_provider=tp,
                tank_spec=spec
            )

            res_a1 = cls.simulate_path_and_evaluate_night(
                slots, current_dhw_temp, t_target_opt, opt_day_50, dynamic_peaks, slot_lockout_map, dhw_model, now_dt, step_hours,
                tariff_provider=tp, tank_spec=spec
            )

            s_lbl_a1 = getattr(slots[opt_day_50["start_idx"]], "label", getattr(slots[opt_day_50["start_idx"]], "time_label", "")) if opt_day_50 else "--:--"
            e_lbl_a1 = getattr(slots[min(n_slots - 1, opt_day_50["end_idx"])], "label", getattr(slots[min(n_slots - 1, opt_day_50["end_idx"])], "time_label", "")) if opt_day_50 else "--:--"

            path_a1 = EvaluatedPath(
                path_id="PAD_A1_DAY_50",
                name=f"Pad A1: Optimale Horizon-Lading (tot {t_target_opt:.1f}°C)",
                description=f"Verwarmen tot {t_target_opt:.1f}°C dekt alle aftap en stilstand ({q_needed:.1f} kWh_th) voor de komende {hours_to_anchor:.1f}u zonder nachtrun.",
                day_target_temp_c=t_target_opt,
                day_slots=res_a1["day_slots"],
                day_window_label=f"{s_lbl_a1}–{e_lbl_a1}",
                day_cost_eur=res_a1["day_cost_eur"],
                day_power_kw=spec.heat_pump_electric_kw,
                day_el_kwh=res_a1["day_el_kwh"],
                simulated_morning_dip_c=res_a1["morning_dip_c"],
                simulated_morning_dip_time=res_a1["morning_dip_time"],
                night_run_required=res_a1["night_run_required"],
                night_slots=res_a1["night_slots"],
                night_window_label=res_a1["night_window_label"],
                night_cost_eur=res_a1["night_cost_eur"],
                night_el_kwh=res_a1["night_el_kwh"],
                total_24h_cost_eur=res_a1["total_24h_cost_eur"]
            )
            evaluated_paths.append(path_a1)

            # PAD A2: In één ruk doorwarmen naar 60°C (Buffer)
            n_slots_60 = cls.calculate_required_slots(
                current_temp=current_dhw_temp,
                target_temp=spec.boost_setpoint_c,
                spec=spec,
                step_hours=step_hours,
                min_slots=3,
                max_slots=8,
                min_delta_c=2.0,
            )
            th_need_60 = max(2.0, spec.boost_setpoint_c - current_dhw_temp) * spec.thermal_capacity_kwh_per_k
            opt_day_60 = cls.find_optimal_heating_window(
                slots=slots,
                search_start_idx=day_start_idx,
                search_end_idx=day_end_idx,
                n_req_slots=n_slots_60,
                th_need_kwh=th_need_60,
                slot_lockout_map=slot_lockout_map,
                is_boost_60=True,
                step_hours=step_hours,
                tariff_provider=tp,
                tank_spec=spec
            )

            res_a2 = cls.simulate_path_and_evaluate_night(
                slots, current_dhw_temp, spec.boost_setpoint_c, opt_day_60, dynamic_peaks, slot_lockout_map, dhw_model, now_dt, step_hours,
                tariff_provider=tp, tank_spec=spec
            )

            s_lbl_a2 = getattr(slots[opt_day_60["start_idx"]], "label", getattr(slots[opt_day_60["start_idx"]], "time_label", "")) if opt_day_60 else "--:--"
            e_lbl_a2 = getattr(slots[min(n_slots - 1, opt_day_60["end_idx"])], "label", getattr(slots[min(n_slots - 1, opt_day_60["end_idx"])], "time_label", "")) if opt_day_60 else "--:--"

            path_a2 = EvaluatedPath(
                path_id="PAD_A2_DAY_60",
                name=f"Pad A2: Doortrekken naar {spec.boost_setpoint_c:.0f}°C (Buffer)",
                description=f"In 1 run doorwarmen naar {spec.boost_setpoint_c:.0f}°C. Voorkomt extra compressorstart en overbrugt nacht.",
                day_target_temp_c=spec.boost_setpoint_c,
                day_slots=res_a2["day_slots"],
                day_window_label=f"{s_lbl_a2}–{e_lbl_a2}",
                day_cost_eur=res_a2["day_cost_eur"],
                day_power_kw=spec.solar_boost_electric_kw,
                day_el_kwh=res_a2["day_el_kwh"],
                simulated_morning_dip_c=res_a2["morning_dip_c"],
                simulated_morning_dip_time=res_a2["morning_dip_time"],
                night_run_required=res_a2["night_run_required"],
                night_slots=res_a2["night_slots"],
                night_window_label=res_a2["night_window_label"],
                night_cost_eur=res_a2["night_cost_eur"],
                night_el_kwh=res_a2["night_el_kwh"],
                total_24h_cost_eur=res_a2["total_24h_cost_eur"]
            )
            evaluated_paths.append(path_a2)

            # Decision Situatie 1: Min(Kosten A1, Kosten A2)
            if path_a2.total_24h_cost_eur < path_a1.total_24h_cost_eur:
                selected_path = path_a2
                savings = path_a1.total_24h_cost_eur - path_a2.total_24h_cost_eur
                planned_mode = "forced_solar_boost_60"
                planned_mode_label = f"Maximaal aan (doorverwarming tot {spec.boost_setpoint_c:.0f}°C)"
                explanation = (
                    f"In 1 run doorwarmen naar {spec.boost_setpoint_c:.0f}°C om {path_a2.day_window_label} is de voordeligste keuze. "
                    f"Dit overbrugt de hele nacht en bespaart €{savings:.2f} t.o.v. stoppen bij {t_target_opt:.1f}°C en nachtelijk bijladen."
                )
            else:
                selected_path = path_a1
                diff = path_a2.total_24h_cost_eur - path_a1.total_24h_cost_eur
                savings = round(diff, 2)
                planned_mode = "forced_on"
                planned_mode_label = f"Geforceerd aan (optimaal tot {t_target_opt:.1f}°C)"
                if t_target_opt >= (spec.boost_setpoint_c - 0.1):
                    savings_phrase = f"volledige lading naar {spec.boost_setpoint_c:.0f}°C is noodzakelijk om de horizon te overbruggen."
                else:
                    savings_phrase = f"bespaart €{diff:.2f} t.o.v. onnodig doorkoken naar {spec.boost_setpoint_c:.0f}°C (hogere COP en minder stilstand)."
                explanation = (
                    f"Opwarmen naar de berekende optimale doeltemperatuur van {t_target_opt:.1f}°C om {path_a1.day_window_label} is de voordeligste keuze (€{path_a1.total_24h_cost_eur:.2f} totaal). "
                    f"Dit dekt alle aftap en stilstand ({q_needed:.1f} kWh_th) voor de komende {hours_to_anchor:.1f} uur tot het volgende laadvenster en {savings_phrase}"
                )

        else:
            # === SITUATION 2: Evening comfort safe -> Pure economic arbitrage ===
            situation = "SITUATION_2_COMFORT_SAFE"

            # PAD B1: Niets doen overdag (Wachten op de nachtrun)
            res_b1 = cls.simulate_path_and_evaluate_night(
                slots, current_dhw_temp, spec.target_setpoint_c, None, dynamic_peaks, slot_lockout_map, dhw_model, now_dt, step_hours,
                tariff_provider=tp, tank_spec=spec
            )
            path_b1 = EvaluatedPath(
                path_id="PAD_B1_STANDBY",
                name="Pad B1: Niets doen overdag (Wachten op nacht)",
                description="Overdag standby. Het vat koelt langzaam af en het nachtscript laadt vannacht efficiënt bij.",
                day_target_temp_c=spec.target_setpoint_c,
                day_slots=[],
                day_window_label="Geen dagrun (Standby)",
                day_cost_eur=0.0,
                day_power_kw=0.0,
                day_el_kwh=0.0,
                simulated_morning_dip_c=res_b1["morning_dip_c"],
                simulated_morning_dip_time=res_b1["morning_dip_time"],
                night_run_required=res_b1["night_run_required"],
                night_slots=res_b1["night_slots"],
                night_window_label=res_b1["night_window_label"],
                night_cost_eur=res_b1["night_cost_eur"],
                night_el_kwh=res_b1["night_el_kwh"],
                total_24h_cost_eur=res_b1["total_24h_cost_eur"]
            )
            evaluated_paths.append(path_b1)

            # PAD B2: Overdag preventief bufferen naar 60°C
            headroom_60 = round(max(0.0, spec.boost_setpoint_c - current_dhw_temp), 1)
            is_tank_saturated = (current_dhw_temp >= cls.DHW_BUFFER_60_MAX_TANK_TEMP_C)

            if is_tank_saturated:
                path_b2 = EvaluatedPath(
                    path_id="PAD_B2_BUFFER_60",
                    name=f"Pad B2: Overdag Preventief Bufferen naar {spec.boost_setpoint_c:.0f}°C",
                    description=f"Vergrendeld: vat staat al op {current_dhw_temp:.1f}°C (marge naar {spec.boost_setpoint_c:.0f}°C is slechts {headroom_60}°C < {cls.DHW_BUFFER_60_MIN_HEADROOM_C}°C drempel).",
                    day_target_temp_c=spec.boost_setpoint_c,
                    day_slots=[],
                    day_window_label="Vergrendeld (Vat Al Verzadigd)",
                    day_cost_eur=999.0,
                    day_power_kw=0.0,
                    day_el_kwh=0.0,
                    simulated_morning_dip_c=res_b1["morning_dip_c"],
                    simulated_morning_dip_time=res_b1["morning_dip_time"],
                    night_run_required=res_b1["night_run_required"],
                    night_slots=res_b1["night_slots"],
                    night_window_label=res_b1["night_window_label"],
                    night_cost_eur=res_b1["night_cost_eur"],
                    night_el_kwh=res_b1["night_el_kwh"],
                    total_24h_cost_eur=999.0
                )
                evaluated_paths.append(path_b2)
                selected_path = path_b1
                savings = 0.0
                planned_mode = "normal"
                planned_mode_label = "Normaal (Standby — Vat Al Verzadigd)"
                explanation = (
                    f"Bufferen naar {spec.boost_setpoint_c:.0f}°C vergrendeld: het vat staat al op {current_dhw_temp:.1f}°C "
                    f"(laadruimte naar {spec.boost_setpoint_c:.0f}°C is slechts {headroom_60}°C < {cls.DHW_BUFFER_60_MIN_HEADROOM_C}°C drempel). "
                    f"Een extra zonnebuffer-run voor <7°C temperatuurstijging is energetisch onrendabel wegens lage COP (2,05) en "
                    f"compressor-startverliezen. Standby behouden tot natuurlijk warmwaterverbruik optreedt."
                )
            else:
                n_slots_60 = cls.calculate_required_slots(
                    current_temp=current_dhw_temp,
                    target_temp=spec.boost_setpoint_c,
                    spec=spec,
                    step_hours=step_hours,
                    min_slots=3,
                    max_slots=8,
                    min_delta_c=2.5,
                )
                th_need_60 = max(2.5, spec.boost_setpoint_c - current_dhw_temp) * spec.thermal_capacity_kwh_per_k
                opt_day_60 = cls.find_optimal_heating_window(
                    slots=slots,
                    search_start_idx=day_start_idx,
                    search_end_idx=day_end_idx,
                    n_req_slots=n_slots_60,
                    th_need_kwh=th_need_60,
                    slot_lockout_map=slot_lockout_map,
                    is_boost_60=True,
                    step_hours=step_hours,
                    tariff_provider=tp,
                    tank_spec=spec
                )

                res_b2 = cls.simulate_path_and_evaluate_night(
                    slots, current_dhw_temp, spec.boost_setpoint_c, opt_day_60, dynamic_peaks, slot_lockout_map, dhw_model, now_dt, step_hours,
                    tariff_provider=tp, tank_spec=spec
                )

                s_lbl_b2 = getattr(slots[opt_day_60["start_idx"]], "label", getattr(slots[opt_day_60["start_idx"]], "time_label", "")) if opt_day_60 else "--:--"
                e_lbl_b2 = getattr(slots[min(n_slots - 1, opt_day_60["end_idx"])], "label", getattr(slots[min(n_slots - 1, opt_day_60["end_idx"])], "time_label", "")) if opt_day_60 else "--:--"

                path_b2 = EvaluatedPath(
                    path_id="PAD_B2_BUFFER_60",
                    name=f"Pad B2: Overdag Preventief Bufferen naar {spec.boost_setpoint_c:.0f}°C",
                    description=f"Overdag economisch doorwarmen naar {spec.boost_setpoint_c:.0f}°C op goedkope/negatieve stroom of zonne-overschot.",
                    day_target_temp_c=spec.boost_setpoint_c,
                    day_slots=res_b2["day_slots"],
                    day_window_label=f"{s_lbl_b2}–{e_lbl_b2}",
                    day_cost_eur=res_b2["day_cost_eur"],
                    day_power_kw=spec.solar_boost_electric_kw,
                    day_el_kwh=res_b2["day_el_kwh"],
                    simulated_morning_dip_c=res_b2["morning_dip_c"],
                    simulated_morning_dip_time=res_b2["morning_dip_time"],
                    night_run_required=res_b2["night_run_required"],
                    night_slots=res_b2["night_slots"],
                    night_window_label=res_b2["night_window_label"],
                    night_cost_eur=res_b2["night_cost_eur"],
                    night_el_kwh=res_b2["night_el_kwh"],
                    total_24h_cost_eur=res_b2["total_24h_cost_eur"]
                )
                evaluated_paths.append(path_b2)

                # Decision Situatie 2: Kies B2 alleen als Kosten B2 < Kosten B1 - €0,05 drempel
                b2_savings = path_b1.total_24h_cost_eur - path_b2.total_24h_cost_eur
                if b2_savings >= 0.05:
                    selected_path = path_b2
                    savings = b2_savings
                    planned_mode = "forced_solar_boost_60"
                    planned_mode_label = f"Maximaal aan (doorverwarming tot {spec.boost_setpoint_c:.0f}°C)"
                    explanation = (
                        f"Overdag preventief bufferen naar {spec.boost_setpoint_c:.0f}°C om {path_b2.day_window_label} levert een netto besparing op van €{savings:.2f} "
                        f"t.o.v. afwachten tot de nacht (gunstige dagstroom compenseert stilstand en lagere COP ruimschoots)."
                    )
                else:
                    selected_path = path_b1
                    diff = path_b2.total_24h_cost_eur - path_b1.total_24h_cost_eur
                    savings = 0.0
                    planned_mode = "normal"
                    planned_mode_label = "Normaal (Standby — Wachten op nacht)"
                    explanation = (
                        f"Overdag niets doen (Standby). Het vat blijft vanavond ruim op comfort ({unheated_evening_dip}°C). "
                        f"Vannacht laden op daltarief is €{diff:.2f} voordeliger dan overdag forceren naar {spec.boost_setpoint_c:.0f}°C."
                    )

        # Determine final planned slots: combine day slots and scheduled night run
        final_planned_slots = list(selected_path.day_slots)
        final_mode = planned_mode
        final_label = planned_mode_label
        final_target_c = selected_path.day_target_temp_c
        final_power_kw = selected_path.day_power_kw

        if selected_path.night_run_required and selected_path.night_slots:
            for s_n in selected_path.night_slots:
                if s_n not in final_planned_slots:
                    final_planned_slots.append(s_n)
            final_planned_slots.sort()
            if not selected_path.day_slots:
                final_label = f"Normaal (Standby — Nachtlading gepland om {selected_path.night_window_label})"
                final_target_c = spec.target_setpoint_c
                final_power_kw = spec.heat_pump_electric_kw

        # Multi-run 48h horizon support: if horizon extends beyond 24h (n_slots > 96),
        # simulate with Run 1 active and plan a 2nd recharge run for Day 2 if comfort dips below 40°C
        if n_slots > 96:
            horizon_h = int(round(n_slots * step_hours))
            sim_with_run1 = dhw_model.simulate_trajectory(
                t_start_c=current_dhw_temp,
                start_dt=now_dt,
                hours_ahead=horizon_h,
                heat_pump_schedule_slots=final_planned_slots,
                target_temp_c=final_target_c,
                heat_pump_power_kw=final_power_kw
            )
            sim_1_temps = sim_with_run1.get("temperatures_c", [])
            sim_1_labels = sim_with_run1.get("labels", [])
            last_run1_slot = max(final_planned_slots) if final_planned_slots else 0

            # Find second dip in day 2 (at least 4 quarters after Run 1)
            dips_2 = [
                (idx, lbl, t) for idx, (lbl, t) in enumerate(zip(sim_1_labels, sim_1_temps))
                if t < spec.comfort_min_temp_c and idx > (last_run1_slot + 4)
            ]
            if dips_2:
                second_dip_idx = dips_2[0][0]
                # Search for optimal daytime solar window on Day 2 before second dip
                search_s2 = max(last_run1_slot + 4, 68)  # around 09:00 tomorrow
                search_e2 = min(n_slots, max(search_s2 + 8, second_dip_idx))

                opt_run_2 = cls.find_optimal_heating_window(
                    slots=slots,
                    search_start_idx=search_s2,
                    search_end_idx=search_e2,
                    n_req_slots=5,
                    th_need_kwh=3.2,
                    slot_lockout_map=slot_lockout_map,
                    is_boost_60=False,
                    step_hours=step_hours,
                    tariff_provider=tp,
                    tank_spec=spec
                )
                if opt_run_2:
                    run2_slots = list(range(opt_run_2["start_idx"], opt_run_2["end_idx"]))
                    for s2 in run2_slots:
                        if s2 not in final_planned_slots:
                            final_planned_slots.append(s2)
                    final_planned_slots.sort()
                    s2_lbl = getattr(slots[opt_run_2["start_idx"]], "label", getattr(slots[opt_run_2["start_idx"]], "time_label", ""))
                    e2_lbl = getattr(slots[min(n_slots - 1, opt_run_2["end_idx"])], "label", getattr(slots[min(n_slots - 1, opt_run_2["end_idx"])], "time_label", ""))
                    explanation += f" Daarnaast is voor morgen een 2e lading gepland om {s2_lbl}–{e2_lbl} (op voordelige zonne-/dagstroom à €{opt_run_2['cost_eur']:.2f}) om ook de avond en 2e nacht comfortabel te overbruggen."

        return DaytimeArbitrationResult(
            situation=situation,
            unheated_evening_dip_c=unheated_evening_dip,
            evening_dip_time=evening_dip_time,
            evaluated_paths=evaluated_paths,
            selected_path=selected_path,
            planned_mode=final_mode,
            planned_mode_label=final_label,
            target_temp_c=final_target_c,
            power_kw=final_power_kw,
            planned_slots=final_planned_slots,
            savings_eur=savings,
            explanation=explanation
        )
