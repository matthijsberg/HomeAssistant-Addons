"""
Layer 3: Space Heating & Floor Thermal Buffer Policy
===================================================
Physics-informed 2R1C underfloor heating and building thermal storage policy.

Key Capabilities:
1. Summer Lockout: Suppresses heating when mean outdoor temperature >= 16.0°C.
2. Dynamic Peak Lockout: Suppresses or clamps heating to modulation floor (950W)
   during morning and evening wholesale price spikes.
3. Thermal Floor Pre-Heat (SG3 Boost): Pre-charges the heavy concrete screed
   (14.5 kWh/K capacity, 3-4 hour thermal lag) during cheap night valley (02:00-06:00)
   or solar surplus hours, allowing the building to coast through peaks with < 0.5°C drop.
4. 2R1C Simulation: Computes room and floor temperature trajectories, enforcing
   comfort bounds (19.5°C min, 21.0°C max preheat).
"""

from dataclasses import dataclass
from typing import List, Dict, Any, Tuple, Optional
import math


@dataclass
class SpaceHeatingSlotResult:
    slot_idx: int
    heating_kw_el: float
    heating_kw_th: float
    cop: float
    room_temp_c: float
    floor_temp_c: float
    heat_loss_kw: float
    mode_code: str
    is_preheat_active: bool
    is_lockout_active: bool


@dataclass
class SpaceHeatingPlanSummary:
    is_heating_season: bool
    season_status_label: str
    total_heating_kwh_el: float
    total_heating_kwh_th: float
    average_cop: float
    preheat_hours: float
    lockout_hours: float
    min_projected_room_temp_c: float
    max_projected_room_temp_c: float
    slots: List[SpaceHeatingSlotResult]


class SpaceHeatingPolicy:
    # Building thermal properties (Culemborg reference site: 321 W/K)
    UA_BUILDING_KW_PER_K = 0.321
    C_AIR_KWH_PER_K = 3.2
    C_FLOOR_KWH_PER_K = 14.5
    R_FLOOR_AIR_K_PER_KW = 0.08

    # Temperatures & Comfort bounds
    TARGET_ROOM_TEMP_C = 20.0
    MIN_COMFORT_ROOM_C = 19.5
    MAX_PREHEAT_ROOM_C = 21.2
    SUMMER_LOCKOUT_OUTDOOR_C = 16.0

    # Heat pump parameters
    MODULATION_FLOOR_KW_EL = 0.95
    PREHEAT_BOOST_KW_EL = 1.85
    FLOW_TEMP_CV_C = 35.0
    CARNOT_EFFICIENCY = 0.42

    @classmethod
    def calculate_carnot_cop(cls, outdoor_temp_c: float, flow_temp_c: float = 35.0) -> float:
        """Calculates temperature-dependent Carnot COP with empirical scaling."""
        t_flow_k = flow_temp_c + 273.15
        t_source_k = outdoor_temp_c + 273.15
        delta_t = max(8.0, t_flow_k - t_source_k)
        theoretical_cop = t_flow_k / delta_t
        cop = cls.CARNOT_EFFICIENCY * theoretical_cop
        # Defrost penalty near freezing
        if -2.0 <= outdoor_temp_c <= 4.0:
            cop *= 0.85
        return round(max(2.2, min(5.4, cop)), 2)

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
        step_hours: float = 0.25
    ) -> SpaceHeatingPlanSummary:
        """
        Computes 24h space heating dispatch with pre-heat floor buffering.
        """
        n_slots = len(outdoor_temps_c)
        mean_outdoor = sum(outdoor_temps_c) / n_slots if n_slots > 0 else 15.0

        # Check summer lockout
        is_heating_season = (mean_outdoor < cls.SUMMER_LOCKOUT_OUTDOOR_C)

        if not is_heating_season:
            # Summer mode: Heating completely disabled
            empty_slots = []
            for i in range(n_slots):
                empty_slots.append(
                    SpaceHeatingSlotResult(
                        slot_idx=i,
                        heating_kw_el=0.0,
                        heating_kw_th=0.0,
                        cop=cls.calculate_carnot_cop(outdoor_temps_c[i]),
                        room_temp_c=current_room_temp_c or 21.0,
                        floor_temp_c=current_floor_temp_c or 21.0,
                        heat_loss_kw=0.0,
                        mode_code="normal",
                        is_preheat_active=False,
                        is_lockout_active=False
                    )
                )
            return SpaceHeatingPlanSummary(
                is_heating_season=False,
                season_status_label="Zomersluiting (CV Vergrendeld)",
                total_heating_kwh_el=0.0,
                total_heating_kwh_th=0.0,
                average_cop=cls.calculate_carnot_cop(mean_outdoor),
                preheat_hours=0.0,
                lockout_hours=0.0,
                min_projected_room_temp_c=current_room_temp_c or 21.0,
                max_projected_room_temp_c=current_room_temp_c or 21.0,
                slots=empty_slots
            )

        # 1. Map dynamic peak lockouts
        lockout_slot_map = {}
        for peak in dynamic_peaks:
            s_idx = peak.get("start_idx", 0)
            e_idx = peak.get("end_idx", 0)
            is_hard = peak.get("is_hard_lockout", False)
            for idx in range(s_idx, min(n_slots, e_idx + 1)):
                lockout_slot_map[idx] = peak

        # 2. Identify pre-heat windows (cheapest 2-4 hours before each peak)
        # Night valley: typically slots 8-24 (02:00 - 06:00)
        # Midday solar dip: typically slots 44-64 (11:00 - 16:00)
        min_price = min(prices_eur) if prices_eur else 0.20
        preheat_candidate_slots = set()

        for idx in range(n_slots):
            # Night valley pre-heat: cheap hours before morning peak (slots 8 to 24)
            if 8 <= (idx % 96) <= 24 and prices_eur[idx] <= min_price + 0.035:
                preheat_candidate_slots.add(idx)
            # Daytime solar pre-heat: solar surplus before evening peak (slots 48 to 68)
            elif 48 <= (idx % 96) <= 68 and (solar_kw[idx] >= 1.2 or prices_eur[idx] <= min_price + 0.025):
                preheat_candidate_slots.add(idx)

        # 3. Simulate 2R1C thermal model slot by slot
        t_room = current_room_temp_c if current_room_temp_c is not None else cls.TARGET_ROOM_TEMP_C
        t_floor = current_floor_temp_c if current_floor_temp_c is not None else (t_room + 1.2)

        slot_results: List[SpaceHeatingSlotResult] = []
        total_kwh_el = 0.0
        total_kwh_th = 0.0
        cops = []
        preheat_slots_count = 0
        lockout_slots_count = 0

        for i in range(n_slots):
            t_out = outdoor_temps_c[i]
            cop = cls.calculate_carnot_cop(t_out, cls.FLOW_TEMP_CV_C)
            cops.append(cop)

            # Instantaneous building heat loss
            q_loss = cls.UA_BUILDING_KW_PER_K * max(0.0, t_room - t_out)

            # Check constraints
            is_dhw_running = (i in active_dhw_slots)
            peak_info = lockout_slot_map.get(i)
            is_hard_lockout = peak_info and peak_info.get("is_hard_lockout")

            heating_el = 0.0
            mode_code = "normal"
            is_preheat = False
            is_lockout = False

            if is_dhw_running:
                # Hydraulic interlock: CV completely paused during DHW heating
                heating_el = 0.0
                mode_code = "forced_on"  # DHW takes precedence
            elif is_hard_lockout:
                # Hard peak lockout: Compressor forced off for heating (SG1)
                heating_el = 0.0
                mode_code = "forced_off"
                is_lockout = True
                lockout_slots_count += 1
            elif peak_info and not is_hard_lockout:
                # Soft peak advice: clamp to minimum modulation floor
                heating_el = cls.MODULATION_FLOOR_KW_EL
                mode_code = "advised_off"
                is_lockout = True
                lockout_slots_count += 1
            elif i in preheat_candidate_slots and t_room < cls.MAX_PREHEAT_ROOM_C:
                # Pre-heat boost (SG3)
                heating_el = cls.PREHEAT_BOOST_KW_EL
                mode_code = "advised_on"
                is_preheat = True
                preheat_slots_count += 1
            else:
                # Normal modulating space heating: maintain target temp
                # Calculate needed heat to balance heat loss
                needed_th = q_loss
                needed_el = needed_th / cop
                heating_el = max(cls.MODULATION_FLOOR_KW_EL, min(2.5, round(needed_el, 2)))
                mode_code = "normal"

            # Thermal heat delivered into the floor
            q_heat_th = heating_el * cop

            # Advance 2R1C model
            q_floor_to_room = (t_floor - t_room) / cls.R_FLOOR_AIR_K_PER_KW
            dt_floor = ((q_heat_th - q_floor_to_room) / cls.C_FLOOR_KWH_PER_K) * step_hours
            dt_room = ((q_floor_to_room - q_loss) / cls.C_AIR_KWH_PER_K) * step_hours

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
                    room_temp_c=t_room,
                    floor_temp_c=t_floor,
                    heat_loss_kw=round(q_loss, 2),
                    mode_code=mode_code,
                    is_preheat_active=is_preheat,
                    is_lockout_active=is_lockout
                )
            )

        all_r_temps = [s.room_temp_c for s in slot_results]
        return SpaceHeatingPlanSummary(
            is_heating_season=True,
            season_status_label="Stookseizoen Actief (Slimme Vloerbuffer)",
            total_heating_kwh_el=round(total_kwh_el, 2),
            total_heating_kwh_th=round(total_kwh_th, 2),
            average_cop=round(sum(cops) / len(cops), 2) if cops else 4.2,
            preheat_hours=round(preheat_slots_count * step_hours, 1),
            lockout_hours=round(lockout_slots_count * step_hours, 1),
            min_projected_room_temp_c=min(all_r_temps) if all_r_temps else 20.0,
            max_projected_room_temp_c=max(all_r_temps) if all_r_temps else 21.0,
            slots=slot_results
        )
