"""
Layer 3: Space Heating & Floor Thermal Buffer Policy
===================================================
Physics-informed 2R1C underfloor heating and building thermal storage policy.

Key Capabilities:
1. Summer Lockout: Suppresses heating when mean outdoor temperature >= 16.0°C.
2. Dynamic Peak Lockout: Suppresses or clamps heating to modulation floor (950W)
   during morning and evening wholesale price spikes.
3. Thermal Floor Buffer Optimization (COP-Price Trade-off):
   Computes thermal energy cost (€/kWh_th = Price / COP) across slots.
   Pre-charges the heavy concrete screed (14.5 kWh/K capacity, 3-4 hour thermal lag)
   during cheap thermal slots or solar surplus windows before peaks.
4. Solar & Overshoot Resilience:
   Allows room temperature to buffer up to setpoint + 1.2°C during afternoon pre-peak
   charging even if solar radiation is already warming the room air, ensuring the
   concrete floor is sufficiently saturated before sunset to coast through evening peaks.
5. 2R1C Simulation: Computes room and floor temperature trajectories, enforcing
   comfort bounds (target - 0.4°C min comfort, floor max 28°C).
6. Hydraulic DHW Interlock: Space heating is strictly 0 kW during active DHW runs.
"""

from typing import List, Dict, Any, Tuple, Optional
import math
import json
from pathlib import Path

from models.canonical import SpaceHeatingSlotResult, SpaceHeatingPlanSummary


class SpaceHeatingPolicy:
    # Building thermal properties (Culemborg reference site: 321 W/K)
    UA_BUILDING_KW_PER_K = 0.321
    C_AIR_KWH_PER_K = 3.2
    C_FLOOR_KWH_PER_K = 14.5
    R_FLOOR_AIR_K_PER_KW = 0.08
    SOLAR_GAIN_COEFFICIENT = 0.18  # Fraction of PV-equivalent solar radiation entering as thermal gain
    WIND_INFILTRATION_COEFF_KW_PER_K_PER_MS = 0.015  # 15 W/K per m/s wind above 2 m/s

    # Temperatures & Comfort bounds
    TARGET_ROOM_TEMP_C = 20.0
    MIN_COMFORT_DELTA_C = 0.6      # Max allowable drop below target during peaks (target - 0.6°C)
    MAX_PREHEAT_OVERSHOOT_C = 1.2  # Max allowable preheat overshoot (target + 1.2°C)
    MAX_FLOOR_TEMP_C = 28.0        # Upper safety limit for underfloor heating
    SUMMER_LOCKOUT_OUTDOOR_C = 16.0

    # Heat pump parameters
    MODULATION_FLOOR_KW_EL = 0.85
    MODULATION_MAX_KW_EL = 4.2
    PREHEAT_BOOST_KW_EL = 2.2
    FLOW_TEMP_CV_C = 35.0
    CARNOT_EFFICIENCY = 0.42

    # Daikin Altherma 3 H HT 18kW Empirical Modulation (Fitted over 7,370 real historical 15m intervals)
    REGRESSION_A = 2858.6           # Intercept at 0°C outdoor (~2,858 W)
    REGRESSION_B = 136.8            # Slope: decreases 136.8 W per degree outdoor warming
    MIN_RUN_SLOTS = 8               # Minimum run duration once started: 8 slots = 2.0 hours
    STARTUP_BOOST_SLOTS = 2         # Initial flow delta-T buildup phase: 2 slots = 30 mins

    @classmethod
    def get_active_parameters(cls) -> Dict[str, Any]:
        """Loads live calibrated parameters from heatpump_model_parameters.json with fallback to class constants."""
        params_file = Path("/config/heatpump_model_parameters.json")
        ua = cls.UA_BUILDING_KW_PER_K
        reg_a = cls.REGRESSION_A
        reg_b = cls.REGRESSION_B
        c_wind = cls.WIND_INFILTRATION_COEFF_KW_PER_K_PER_MS
        c_solar = cls.SOLAR_GAIN_COEFFICIENT
        c_floor = cls.C_FLOOR_KWH_PER_K
        if params_file.exists():
            try:
                p = json.loads(params_file.read_text(encoding="utf-8"))
                b = p.get("building", {})
                ua_w = b.get("ua_base_w_per_k")
                if ua_w is not None and float(ua_w) > 0:
                    ua = float(ua_w) / 1000.0  # W/K to kW/K
                c_wind_w = b.get("c_wind_w_per_k_ms")
                if c_wind_w is not None and float(c_wind_w) > 0:
                    c_wind = float(c_wind_w) / 1000.0  # W/(K*m/s) to kW/(K*m/s)
                c_sol = b.get("c_solar_passive")
                if c_sol is not None and float(c_sol) > 0:
                    c_solar = float(c_sol)
                fl_cap = b.get("floor_capacity_kwh_per_k")
                if fl_cap is not None and float(fl_cap) > 0:
                    c_floor = float(fl_cap)
                mod_curve = p.get("heat_pump", {}).get("modulation_curve", "")
                if mod_curve and "-" in mod_curve:
                    parts = mod_curve.split("-")
                    reg_a = float(parts[0].strip())
                    clean_b = parts[1].replace("·T", "").replace("*T", "").replace("·t", "").replace("*t", "").replace("W", "").strip()
                    reg_b = float(clean_b)
            except Exception:
                pass
        return {
            "ua_kw_per_k": ua,
            "reg_a": reg_a,
            "reg_b": reg_b,
            "c_wind_kw_per_k_ms": c_wind,
            "c_solar_passive": c_solar,
            "floor_capacity_kwh_per_k": c_floor
        }

    @classmethod
    def calculate_modulating_power(
        cls,
        outdoor_temp_c: float,
        run_slot_idx: int = 1,
        is_preheat: bool = False
    ) -> float:
        """
        Calculates realistic inverter compressor power (kW_el) based on outdoor temperature,
        run phase (startup burst vs steady modulation vs warm floor taper), and preheat status.
        """
        act_p = cls.get_active_parameters()
        p_raw_w = act_p["reg_a"] - (act_p["reg_b"] * outdoor_temp_c)
        p_steady_kw = max(cls.MODULATION_FLOOR_KW_EL, min(cls.MODULATION_MAX_KW_EL, p_raw_w / 1000.0))

        if run_slot_idx <= cls.STARTUP_BOOST_SLOTS:
            p_kw = min(3.2, p_steady_kw * 1.25)
        elif is_preheat:
            p_kw = min(2.8, max(p_steady_kw, 2.0))
        elif run_slot_idx > 12:
            taper_factor = max(0.65, 1.0 - 0.05 * (run_slot_idx - 12))
            p_kw = max(cls.MODULATION_FLOOR_KW_EL, p_steady_kw * taper_factor)
        else:
            p_kw = p_steady_kw

        return round(p_kw, 2)

    @classmethod
    def calculate_carnot_cop(cls, outdoor_temp_c: float, flow_temp_c: float = 35.0) -> float:
        """Calculates temperature-dependent Carnot COP with empirical scaling via models.physics."""
        from models.physics import calculate_carnot_cop as _calc_cop
        return _calc_cop(outdoor_temp_c, flow_temp_c=flow_temp_c, carnot_efficiency=cls.CARNOT_EFFICIENCY)

    @classmethod
    def calculate_thermal_cost(
        cls,
        price_eur: float,
        cop: float,
        solar_kw: float = 0.0
    ) -> float:
        """
        Calculates effective cost per thermal kWh (€/kWh_th = Price_eff / COP).
        Solar surplus power has a lower opportunity cost (feed-in tariff ~€0.07/kWh).
        """
        effective_price = price_eur
        if solar_kw >= 1.2:
            effective_price = min(price_eur, 0.075)
        elif solar_kw >= 0.5:
            effective_price = min(price_eur, 0.12)
        
        return round(effective_price / max(1.0, cop), 4)

    @classmethod
    def _is_buffer_needed_for_peak(
        cls,
        current_slot: int,
        t_room_cur: float,
        t_floor_cur: float,
        outdoor_temps_c: List[float],
        solar_kw: List[float],
        dynamic_peaks: List[Dict[str, Any]],
        min_comfort_room_c: float,
        step_hours: float = 0.25,
        wind_speeds_ms: Optional[List[float]] = None
    ) -> bool:
        """
        Model Predictive Control (MPC) check:
        Simulates passive building coasting (zero heating) from current_slot through the upcoming peak.
        Returns True if unheated passive temperature drops below min_comfort_room_c during the peak.
        """
        for peak in dynamic_peaks:
            p_start = peak.get("start_idx", 0)
            p_end = peak.get("end_idx", p_start)
            # Only evaluate if we are in the preheat runway before this peak (up to 3.5h before)
            if current_slot < p_start and (p_start - current_slot) <= int(3.5 / step_hours):
                t_r = t_room_cur
                t_f = t_floor_cur
                act_p = cls.get_active_parameters()
                ua_base = act_p["ua_kw_per_k"]
                c_wind = act_p.get("c_wind_kw_per_k_ms", cls.WIND_INFILTRATION_COEFF_KW_PER_K_PER_MS)
                c_solar = act_p.get("c_solar_passive", cls.SOLAR_GAIN_COEFFICIENT)
                c_floor = act_p.get("floor_capacity_kwh_per_k", cls.C_FLOOR_KWH_PER_K)
                for step_idx in range(current_slot, p_end + 1):
                    if step_idx >= len(outdoor_temps_c):
                        break
                    t_out = outdoor_temps_c[step_idx]
                    v_wind = wind_speeds_ms[step_idx] if (wind_speeds_ms and step_idx < len(wind_speeds_ms)) else 0.0
                    effective_ua = ua_base + c_wind * max(0.0, v_wind - 2.0)
                    q_loss = effective_ua * max(0.0, t_r - t_out)
                    q_solar = c_solar * solar_kw[step_idx]
                    r_fl = cls.R_FLOOR_AIR_K_PER_KW if t_f >= t_r else 2.75
                    q_fl = (t_f - t_r) / r_fl
                    dt_f = ((-q_fl) / c_floor) * step_hours
                    dt_r = ((q_fl + q_solar - q_loss) / cls.C_AIR_KWH_PER_K) * step_hours
                    t_f += dt_f
                    t_r += dt_r
                    # If temperature dips below comfort during the peak lockout:
                    if step_idx >= p_start and t_r < min_comfort_room_c:
                        return True
        return False

    @classmethod
    def plan_space_heating(
        cls,
        outdoor_temps_c: List[float],
        prices_eur: List[float],
        solar_kw: List[float],
        active_dhw_slots: List[int],
        dynamic_peaks: List[Dict[str, Any]],
        current_room_temp_c: Optional[float] = None,
        current_floor_temp_c: Optional[float] = None,
        target_room_temp_c: Optional[float] = None,
        wind_speeds_ms: Optional[List[float]] = None,
        is_heating_enabled: bool = True,
        step_hours: float = 0.25
    ) -> SpaceHeatingPlanSummary:
        """
        Computes 24h space heating dispatch with 2R1C thermal floor buffering.
        """
        n_slots = len(outdoor_temps_c)
        mean_outdoor = sum(outdoor_temps_c) / n_slots if n_slots > 0 else 15.0

        target_room = round(target_room_temp_c, 1) if target_room_temp_c is not None else cls.TARGET_ROOM_TEMP_C
        min_comfort_room = round(target_room - cls.MIN_COMFORT_DELTA_C, 2)
        max_preheat_room = round(target_room + cls.MAX_PREHEAT_OVERSHOOT_C, 2)

        # Check summer lockout and master enable status
        is_heating_season = (mean_outdoor < cls.SUMMER_LOCKOUT_OUTDOOR_C) and is_heating_enabled

        # 1. Map dynamic peak lockouts
        lockout_slot_map = {}
        for peak in dynamic_peaks:
            s_idx = peak.get("start_idx", 0)
            e_idx = peak.get("end_idx", 0)
            for idx in range(s_idx, min(n_slots, e_idx + 1)):
                lockout_slot_map[idx] = peak

        # 2. Compute COP and thermal cost for every slot
        slot_cops = [cls.calculate_carnot_cop(outdoor_temps_c[i], cls.FLOW_TEMP_CV_C) for i in range(n_slots)]
        thermal_costs = [
            cls.calculate_thermal_cost(prices_eur[i], slot_cops[i], solar_kw[i])
            for i in range(n_slots)
        ]

        # 3. Identify strategic buffer / pre-heat windows
        preheat_candidate_slots = set()
        if is_heating_season:
            for peak in dynamic_peaks:
                p_start = peak.get("start_idx", 0)
                # Ensure the 2.5 - 3.0 hours immediately preceding the peak are buffered
                runway_slots = int(3.0 / step_hours)
                for idx in range(max(0, p_start - runway_slots), p_start):
                    if idx not in active_dhw_slots and idx not in lockout_slot_map:
                        preheat_candidate_slots.add(idx)

                # In addition, include daytime solar surplus slots before the peak
                earlier_lookback = int(6.0 / step_hours)
                window_start = max(0, p_start - earlier_lookback)
                for idx in range(window_start, max(0, p_start - runway_slots)):
                    if idx not in active_dhw_slots and idx not in lockout_slot_map:
                        if solar_kw[idx] >= 1.0 or thermal_costs[idx] <= 0.05:
                            preheat_candidate_slots.add(idx)

            # Also evaluate cheap night valley slots (02:00 - 05:30) before morning wake-up
            min_night_cost = min([thermal_costs[idx] for idx in range(min(n_slots, 24))] or [0.06])
            for idx in range(min(n_slots, 24)):
                slot_in_day = idx % 96
                if 8 <= slot_in_day <= 22:  # 02:00 to 05:30
                    if thermal_costs[idx] <= min_night_cost + 0.015:
                        preheat_candidate_slots.add(idx)

        # 4. Simulate 2R1C thermal model slot by slot with Inverter Run State Machine
        t_room = current_room_temp_c if current_room_temp_c is not None else target_room
        t_floor = current_floor_temp_c if current_floor_temp_c is not None else (t_room - 0.2 if not is_heating_season else t_room + 1.2)

        # Inverter state machine
        is_running = False
        run_duration_slots = 0
        is_preheat_active = False

        slot_results: List[SpaceHeatingSlotResult] = []
        total_kwh_el = 0.0
        total_kwh_th = 0.0
        preheat_slots_count = 0
        lockout_slots_count = 0

        for i in range(n_slots):
            t_room_slot = t_room
            t_floor_slot = t_floor
            t_out = outdoor_temps_c[i]
            cop = slot_cops[i]
            c_th = thermal_costs[i]

            # Instantaneous building heat loss with wind infiltration correction
            v_wind = wind_speeds_ms[i] if (wind_speeds_ms and i < len(wind_speeds_ms)) else 0.0
            act_p = cls.get_active_parameters()
            c_wind = act_p.get("c_wind_kw_per_k_ms", cls.WIND_INFILTRATION_COEFF_KW_PER_K_PER_MS)
            c_solar = act_p.get("c_solar_passive", cls.SOLAR_GAIN_COEFFICIENT)
            c_floor = act_p.get("floor_capacity_kwh_per_k", cls.C_FLOOR_KWH_PER_K)

            effective_ua = act_p["ua_kw_per_k"] + c_wind * max(0.0, v_wind - 2.0)
            q_loss = effective_ua * max(0.0, t_room - t_out)
            # Passive window solar thermal gain
            q_solar_gain = c_solar * solar_kw[i]

            # Check constraints
            is_dhw_running = (i in active_dhw_slots)
            peak_info = lockout_slot_map.get(i)
            is_hard_lockout = peak_info and peak_info.get("is_hard_lockout")

            heating_el = 0.0
            mode_code = "normal"
            is_preheat = False
            is_lockout = False

            if not is_heating_season:
                is_running = False
                run_duration_slots = 0
                heating_el = 0.0
                mode_code = "normal"
            elif is_dhw_running:
                # Hydraulic interlock: CV completely paused during DHW heating
                is_running = False
                run_duration_slots = 0
                heating_el = 0.0
                mode_code = "forced_on"  # DHW has precedence
            elif is_hard_lockout:
                # Hard peak lockout: Compressor forced off for CV (SG1)
                is_running = False
                run_duration_slots = 0
                heating_el = 0.0
                mode_code = "forced_off"
                is_lockout = True
                lockout_slots_count += 1
            else:
                # Normal or Pre-heat window: evaluate state transitions
                if not is_running:
                    # Can we trigger a new run?
                    comfort_trigger = (t_room <= target_room - 0.35)
                    # Model Predictive Control (MPC) Pre-heat condition:
                    # 1. Deficit buffer: unheated coasting would violate comfort during upcoming peak
                    # 2. Solar buffer: free solar surplus (>=1.5 kW)
                    # 3. Valley buffer: cheap night valley only if room is below target setpoint
                    needs_deficit_buffer = cls._is_buffer_needed_for_peak(
                        i, t_room, t_floor, outdoor_temps_c, solar_kw, dynamic_peaks, min_comfort_room, step_hours, wind_speeds_ms
                    )
                    is_solar_surplus = (solar_kw[i] >= 1.5)

                    if is_solar_surplus or needs_deficit_buffer:
                        preheat_allowed = (t_room < max_preheat_room)
                    else:
                        preheat_allowed = False

                    # Guarantee preheat runway: never start an orphan micro-run (< 1 hour) before a lockout or DHW run
                    min_runway_slots = int(1.0 / step_hours)  # 4 slots = 1 hour
                    has_preheat_runway = all(
                        (idx not in lockout_slot_map and idx not in active_dhw_slots)
                        for idx in range(i, min(n_slots, i + min_runway_slots))
                    )

                    preheat_trigger = (
                        i in preheat_candidate_slots and
                        t_floor < cls.MAX_FLOOR_TEMP_C and
                        preheat_allowed and
                        has_preheat_runway
                    )

                    if comfort_trigger or preheat_trigger:
                        is_running = True
                        run_duration_slots = 1
                        is_preheat_active = preheat_trigger and not comfort_trigger
                else:
                    # We are currently running. Check if we should shut off:
                    if run_duration_slots >= cls.MIN_RUN_SLOTS:
                        # Minimum run commitment (2.0h) satisfied:
                        if is_preheat_active:
                            if t_room >= max_preheat_room or t_floor >= cls.MAX_FLOOR_TEMP_C:
                                is_running = False
                                run_duration_slots = 0
                                is_preheat_active = False
                            else:
                                run_duration_slots += 1
                        else:
                            if t_room >= target_room:
                                is_running = False
                                run_duration_slots = 0
                            else:
                                run_duration_slots += 1
                    else:
                        run_duration_slots += 1

                # If running, calculate modulating power
                if is_running:
                    if peak_info and not is_hard_lockout:
                        # Soft peak: clamp to modulation floor
                        heating_el = cls.MODULATION_FLOOR_KW_EL
                        mode_code = "advised_off"
                        is_lockout = True
                        lockout_slots_count += 1
                    else:
                        heating_el = cls.calculate_modulating_power(
                            t_out,
                            run_slot_idx=run_duration_slots,
                            is_preheat=is_preheat_active
                        )
                        if is_preheat_active:
                            mode_code = "advised_on"
                            is_preheat = True
                            preheat_slots_count += 1
                        else:
                            mode_code = "normal"
                else:
                    heating_el = 0.0
                    mode_code = "normal"

            # Thermal heat delivered into the floor
            q_heat_th = heating_el * cop

            # Advance 2R1C model
            # Downward heat transfer (warm room air above cooler floor) is convection-suppressed (h ~ 2.5 W/m2K)
            if t_floor >= t_room:
                r_floor_air = cls.R_FLOOR_AIR_K_PER_KW
            else:
                r_floor_air = 2.75  # ~0.36 kW/K downward thermal resistance for ~145 m2
            q_floor_to_room = (t_floor - t_room) / r_floor_air
            dt_floor = ((q_heat_th - q_floor_to_room) / c_floor) * step_hours
            dt_room = ((q_floor_to_room + q_solar_gain - q_loss) / cls.C_AIR_KWH_PER_K) * step_hours

            t_floor = round(t_floor + dt_floor, 2)
            t_room = round(t_room + dt_room, 2)

            total_kwh_el += heating_el * step_hours
            total_kwh_th += q_heat_th * step_hours

            slot_results.append(
                SpaceHeatingSlotResult(
                    slot_idx=i,
                    heating_kw_el=heating_el,
                    heating_kw_th=round(q_heat_th, 2),
                    cop=cop,
                    room_temp_c=round(t_room_slot, 2),
                    floor_temp_c=round(t_floor_slot, 2),
                    heat_loss_kw=round(q_loss, 2),
                    mode_code=mode_code,
                    is_preheat_active=is_preheat,
                    is_lockout_active=is_lockout,
                    cost_th_eur_per_kwh=c_th,
                    outdoor_temp_c=round(t_out, 1)
                )
            )

        # Determine the first slot where active heating occurs
        first_heat_idx = None
        for i, s in enumerate(slot_results):
            if s.heating_kw_el > 0.05:
                first_heat_idx = i
                break

        unheated_room_temps = []
        if first_heat_idx is None:
            # No heating occurs: unheated is 100% identical to the planned trajectory
            unheated_room_temps = [s.room_temp_c for s in slot_results]
        else:
            # Before heating starts: unheated is 100% identical to the planned trajectory
            for i in range(first_heat_idx):
                unheated_room_temps.append(slot_results[i].room_temp_c)
            # From first heating slot onwards: simulate passive cooling without heating
            if first_heat_idx > 0:
                t_r_unh = slot_results[first_heat_idx - 1].room_temp_c
                t_f_unh = slot_results[first_heat_idx - 1].floor_temp_c
            else:
                t_r_unh = current_room_temp_c if current_room_temp_c is not None else target_room
                t_f_unh = current_floor_temp_c if current_floor_temp_c is not None else t_r_unh

            for i in range(first_heat_idx, n_slots):
                t_out = outdoor_temps_c[i]
                v_wind = wind_speeds_ms[i] if (wind_speeds_ms and i < len(wind_speeds_ms)) else 0.0
                act_p = cls.get_active_parameters()
                eff_ua = act_p["ua_kw_per_k"] + cls.WIND_INFILTRATION_COEFF_KW_PER_K_PER_MS * max(0.0, v_wind - 2.0)
                q_loss = eff_ua * max(0.0, t_r_unh - t_out)
                q_solar = cls.SOLAR_GAIN_COEFFICIENT * solar_kw[i]
                r_fl = cls.R_FLOOR_AIR_K_PER_KW if t_f_unh >= t_r_unh else 2.75
                q_fl = (t_f_unh - t_r_unh) / r_fl
                dt_f = ((-q_fl) / cls.C_FLOOR_KWH_PER_K) * step_hours
                dt_r = ((q_fl + q_solar - q_loss) / cls.C_AIR_KWH_PER_K) * step_hours
                t_f_unh = round(t_f_unh + dt_f, 2)
                t_r_unh = round(t_r_unh + dt_r, 2)
                unheated_room_temps.append(round(t_r_unh, 2))

        # Compute Prediction Uncertainty Margin Funnel (P05 lower bound, P95 upper bound)
        room_p05 = []
        room_p95 = []
        unh_p05 = []
        unh_p95 = []
        for i, s in enumerate(slot_results):
            spread = 0.40 * (i / max(1, n_slots - 1)) ** 0.65
            room_p05.append(round(s.room_temp_c - spread, 2))
            room_p95.append(round(s.room_temp_c + spread, 2))
            unh_t = unheated_room_temps[i]
            unh_p05.append(round(unh_t - spread, 2))
            unh_p95.append(round(unh_t + spread, 2))

        all_r_temps = [s.room_temp_c for s in slot_results]
        if not is_heating_enabled:
            season_label = "Verwarming Uitgeschakeld (Thermostaat Uit)"
        elif not is_heating_season:
            season_label = "Zomersluiting (CV Vergrendeld)"
        else:
            season_label = "Stookseizoen Actief (Slimme Vloerbuffer)"
        return SpaceHeatingPlanSummary(
            is_heating_season=is_heating_season,
            season_status_label=season_label,
            total_heating_kwh_el=round(total_kwh_el, 2),
            total_heating_kwh_th=round(total_kwh_th, 2),
            average_cop=round(sum(slot_cops) / len(slot_cops), 2) if slot_cops else 4.2,
            preheat_hours=round(preheat_slots_count * step_hours, 1),
            lockout_hours=round(lockout_slots_count * step_hours, 1),
            min_projected_room_temp_c=min(all_r_temps) if all_r_temps else target_room,
            max_projected_room_temp_c=max(all_r_temps) if all_r_temps else target_room,
            slots=slot_results,
            target_room_temp_c=target_room,
            min_comfort_room_c=min_comfort_room,
            max_preheat_room_c=max_preheat_room,
            max_floor_temp_c=cls.MAX_FLOOR_TEMP_C,
            unheated_room_temps_c=unheated_room_temps,
            room_temps_p05_c=room_p05,
            room_temps_p95_c=room_p95,
            unheated_temps_p05_c=unh_p05,
            unheated_temps_p95_c=unh_p95
        )
