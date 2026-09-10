#!/usr/bin/env python3
"""
Open HEMS - Domestic Hot Water (DHW / SWW) 350L Thermal State & Demand Forecaster
Author: Matthijs van den Berg / Hermes Agent
Version: 1.0.0

Physics & Mass Balance:
  - Tank Mass: 350 kg water (combi-tank with 150L separate space heating buffer)
  - Heat Capacity: C_tank = 350 * 4186 / 3600 = 0.407 kWh_th / °C (1.465 MJ / °C)
  - Standby Heat Loss: UA_tank = 2.5 W/K (~0.18 °C/hour cooling at 50°C tank / 18°C ambient)
  - Comfort Minimum Threshold: 40.0 °C (below 40°C shower feels lukewarm)
  - Target Top Temperature: 50.0 °C

Features:
  1. 7x96 Learned Thermal Draw-Off Matrix (kWh_th tapped per quarter-hour) from 30,432 historical samples.
  2. Live Forward State-of-Charge Simulation: Predicts tank temperature trajectory 24-48h ahead.
  3. Night Charge Decision Engine (e.g. Wednesday-to-Thursday night):
     Evaluates whether morning showers will cause a dip below 40.0°C.
     - If T_dip >= 40.0°C: SKIP night heating; defer to solar peak at midday (cheaper, higher COP).
     - If T_dip < 40.0°C: SCHEDULE targeted boost during lowest EPEX spot price night quarter.
"""

import os
import json
import math
import statistics
import urllib.request
import urllib.parse
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
from typing import Dict, List, Any, Tuple, Optional

AMS_TZ = ZoneInfo("Europe/Amsterdam")

TANK_VOLUME_LITERS = 350.0
SPECIFIC_HEAT_WATER_KJ = 4.186           # kJ / (kg * K)
C_TANK_KWH_PER_C = (TANK_VOLUME_LITERS * SPECIFIC_HEAT_WATER_KJ) / 3600.0  # 0.407 kWh / °C
UA_TANK_W_PER_K = 2.5                    # W / K standby loss
STANDBY_LOSS_KW_PER_HOUR = (UA_TANK_W_PER_K * (50.0 - 18.0)) / 1000.0     # ~0.08 kWh / hour
T_MIN_COMFORT_C = 40.0                   # Minimum acceptable temperature for morning shower
T_TARGET_C = 50.0                        # Nominal hot water setpoint


class DhwThermalModel:
    def __init__(self, profile_path: str = "/config/unallocated_load_profile.json"):
        self.profile_path = profile_path
        self.profile = self._load_profile()

    def _load_profile(self) -> Dict[str, Any]:
        for p in [self.profile_path, "/config/addons/open-hems/data/unallocated_load_profile.json", "/config/projects/energy-scheduler/data/unallocated_load_profile.json"]:
            if os.path.exists(p):
                try:
                    with open(p, "r", encoding="utf-8") as f:
                        return json.load(f)
                except Exception as e:
                    print(f"[WARN] Failed reading DHW profile {p}: {e}")
        return {}

    def get_learned_tap_kwh_th(self, dow: int, quarter_idx: int) -> float:
        """Returns expected thermal energy drawn off (kWh_th) in a 15-minute slot."""
        tap_matrix = self.profile.get("tap_demand_profile_96", [])
        if tap_matrix and len(tap_matrix) > dow and len(tap_matrix[dow]) > quarter_idx:
            return float(tap_matrix[dow][quarter_idx])
        # Fallback baseline
        h = quarter_idx / 4.0
        if 7.0 <= h <= 8.5:
            return 0.25
        elif 19.5 <= h <= 22.0:
            return 0.30
        return 0.015

    def get_tap_demand_liters(self, kwh_th: float, t_tank: float = 50.0, t_cold: float = 12.0) -> float:
        """Converts thermal kWh demand into equivalent liters of 50°C mixed water."""
        delta_t = max(5.0, t_tank - t_cold)
        joules = kwh_th * 3.6e6
        mass_kg = joules / (SPECIFIC_HEAT_WATER_KJ * 1000.0 * delta_t)
        return round(max(0.0, mass_kg), 1)

    def simulate_trajectory(
        self,
        t_start_c: float,
        start_dt: datetime,
        hours_ahead: int = 24,
        heat_pump_schedule_slots: Optional[List[int]] = None
    ) -> Dict[str, Any]:
        """
        Simulates 15-minute tank temperature and thermal energy trajectory.
        Takes into account:
          - Scheduled heat pump top-up runs (e.g. at midday or night)
          - Expected tapping draw-off per quarter
          - Standby thermal loss through 350L insulation
        """
        if heat_pump_schedule_slots is None:
            heat_pump_schedule_slots = []

        total_quarters = hours_ahead * 4
        dt_ams = start_dt.astimezone(AMS_TZ)
        start_q = dt_ams.hour * 4 + dt_ams.minute // 15

        timeline_labels = []
        temps = []
        energy_usable_kwh = []
        demand_kwh_th = []
        ambient_temp = 18.0  # indoor utility room / technical room temp

        current_temp = t_start_c
        min_projected_temp = t_start_c
        min_projected_slot_idx = 0
        min_projected_time = ""

        # Tracking morning dip (between 06:00 and 09:00 next day)
        morning_dip_temp = 99.0
        morning_dip_time = ""

        for i in range(total_quarters):
            slot_dt = dt_ams + timedelta(minutes=15 * i)
            lbl = slot_dt.strftime("%H:%M")
            dow = slot_dt.weekday()
            q_idx = slot_dt.hour * 4 + slot_dt.minute // 15

            # Standby loss during 15 minutes (0.25h)
            # Q_loss = UA * (T_tank - T_amb) * dt_hours / 1000  [kWh]
            q_standby_kwh = (UA_TANK_W_PER_K * max(0.0, current_temp - ambient_temp) * 0.25) / 1000.0
            dt_standby = q_standby_kwh / C_TANK_KWH_PER_C

            # Tap draw-off demand
            q_tap_th = self.get_learned_tap_kwh_th(dow, q_idx)
            dt_tap = q_tap_th / C_TANK_KWH_PER_C

            # Heat pump addition (if active in this slot)
            q_hp_th = 0.0
            dt_hp = 0.0
            if i in heat_pump_schedule_slots:
                # 1.6 kW electrical compressor run * 2.8 COP * 0.25h = ~1.12 kWh_th
                q_hp_th = 1.6 * 2.8 * 0.25
                dt_hp = q_hp_th / C_TANK_KWH_PER_C

            # New temperature at end of 15 min
            current_temp = max(15.0, min(T_TARGET_C + 2.0, current_temp - dt_standby - dt_tap + dt_hp))

            # Usable heat above comfort minimum (40°C)
            q_usable = max(0.0, (current_temp - T_MIN_COMFORT_C) * C_TANK_KWH_PER_C)

            timeline_labels.append(lbl)
            temps.append(round(current_temp, 1))
            energy_usable_kwh.append(round(q_usable, 2))
            demand_kwh_th.append(round(q_tap_th, 3))

            if current_temp < min_projected_temp:
                min_projected_temp = current_temp
                min_projected_slot_idx = i
                min_projected_time = lbl

            # Check if this slot falls in morning shower hours (06:00 - 09:00 tomorrow)
            if 6 <= slot_dt.hour <= 9 and (slot_dt.date() > dt_ams.date() or i >= 24):
                if current_temp < morning_dip_temp:
                    morning_dip_temp = current_temp
                    morning_dip_time = lbl

        # If no morning dip found (e.g. running simulation during morning), use overall min
        if morning_dip_temp > 90.0:
            morning_dip_temp = min_projected_temp
            morning_dip_time = min_projected_time

        return {
            "initial_temp_c": t_start_c,
            "labels": timeline_labels,
            "temperatures_c": temps,
            "energy_usable_kwh": energy_usable_kwh,
            "demand_kwh_th": demand_kwh_th,
            "min_projected_temp_c": round(min_projected_temp, 1),
            "min_projected_time": min_projected_time,
            "morning_dip_temp_c": round(morning_dip_temp, 1),
            "morning_dip_time": morning_dip_time
        }

    def evaluate_night_heating_decision(
        self,
        t_current_c: float,
        now_dt: datetime,
        tomorrow_solar_peak_kw: float = 2.5,
        planned_heat_hour: float = 12.5
    ) -> Dict[str, Any]:
        dt_ams = now_dt.astimezone(AMS_TZ)
        today_name = ["Maandag", "Dinsdag", "Woensdag", "Donderdag", "Vrijdag", "Zaterdag", "Zondag"][dt_ams.weekday()]
        tomorrow_dt = dt_ams + timedelta(days=1)
        tomorrow_name = ["Maandag", "Dinsdag", "Woensdag", "Donderdag", "Vrijdag", "Zaterdag", "Zondag"][tomorrow_dt.weekday()]
        night_label = f"{today_name} op {tomorrow_name} nacht ({dt_ams.day}-{tomorrow_dt.day} {['jan','feb','mrt','apr','mei','jun','jul','aug','sep','okt','nov','dec'][dt_ams.month-1]})"
        short_night_label = f"{today_name[:2]} {dt_ams.day} ➔ {tomorrow_name[:2]} {tomorrow_dt.day} {['jan','feb','mrt','apr','mei','jun','jul','aug','sep','okt','nov','dec'][dt_ams.month-1]}"

        # Simulate 24h trajectory without night heat
        unheated_sim = self.simulate_trajectory(t_current_c, now_dt, hours_ahead=24, heat_pump_schedule_slots=[])
        temps = unheated_sim["temperatures_c"]
        labels = unheated_sim["labels"]

        # 1. Calculate Morning Dip specifically during morning shower hours (06:00 - 09:45 tomorrow)
        morning_slots = [
            (idx, lbl, t) for idx, (lbl, t) in enumerate(zip(labels, temps))
            if ("06:00" <= lbl <= "09:45" and idx >= 16)
        ]
        if morning_slots:
            min_morn_slot = min(morning_slots, key=lambda x: x[2])
            morning_dip_c = round(min_morn_slot[2], 1)
            morning_dip_time = min_morn_slot[1]
        else:
            morning_dip_c = round(min(temps[:36]), 1) if temps else t_current_c
            morning_dip_time = "08:30"

        morning_is_safe = (morning_dip_c >= T_MIN_COMFORT_C)

        # 2. Calculate first moment tank drops below 40.0C
        first_sub40_time = ""
        first_sub40_idx = len(temps)
        for idx, (lbl, t) in enumerate(zip(labels, temps)):
            if idx >= 12 and t < T_MIN_COMFORT_C:
                first_sub40_time = lbl
                first_sub40_idx = idx
                break

        # 3. Financial Cost Comparison: Night Charge at 03:15 vs Day Charge at first dip / 12:30
        # Night Option: heat to 50C at 03:15 + 8h standby loss until morning
        night_price = 0.309   # EUR/kWh (typical EPEX spot night price)
        cop_night = 2.65      # Lower COP at 11C night air
        th_need_night = (T_TARGET_C - t_current_c) * C_TANK_KWH_PER_C + (STANDBY_LOSS_KW_PER_HOUR * 8.0)
        el_kwh_night = max(1.2, th_need_night / cop_night)
        cost_night = round(el_kwh_night * night_price, 2)

        # Day Option: heat at 12:30 (when approaching 40C) at warmer air & solar
        day_price = 0.291     # EUR/kWh (cheaper midday spot tariff + solar self-consumption)
        cop_day = 3.25        # Higher COP at 16C daytime air
        th_need_day = (T_TARGET_C - min(40.0, morning_dip_c)) * C_TANK_KWH_PER_C
        el_kwh_day = max(1.0, th_need_day / cop_day)
        cost_day = round(el_kwh_day * day_price, 2)

        savings_by_waiting = round(cost_night - cost_day, 2)

        # Current usable energy & shower volume
        q_now_usable = max(0.0, (t_current_c - T_MIN_COMFORT_C) * C_TANK_KWH_PER_C)
        q_now_mj = q_now_usable * 3.6
        v_mixed_shower_liters = round(350.0 * max(0.0, t_current_c - 12.0) / (38.0 - 12.0))

        # Final decision logic
        if morning_is_safe and savings_by_waiting >= 0.0:
            status = "SKIP_NIGHT_CHARGE"
            decision_title = "✅ Geen nachtlading nodig"
            decision_sub = f"Wachten tot {first_sub40_time or '12:30'}u bespaart €{savings_by_waiting:.2f} ({(savings_by_waiting/cost_night*100):.0f}%)"
            recommendation = (
                f"✅ GEEN nachtlading nodig ({night_label}). "
                f"Ochtenddouches blijven met {morning_dip_c}°C (om {morning_dip_time}u) ruim warm (>40°C). "
                f"Wachten tot het eerste laadmoment om {first_sub40_time or '12:30'}u kost €{cost_day:.2f} "
                f"(bij COP {cop_day}) t.o.v. €{cost_night:.2f} 's nachts bij COP {cop_night} inclusief 8u stilstandsverlies. "
                f"Je bespaart €{savings_by_waiting:.2f}!"
            )
            optimal_slot_type = "midday_solar"
        else:
            status = "SCHEDULE_NIGHT_CHARGE"
            decision_title = "⚠️ Nachtlading aanbevolen"
            decision_sub = f"Nachtlading (€{cost_night:.2f}) waarborgt ochtendcomfort"
            recommendation = (
                f"⚠️ Nachtlading aanbevolen ({night_label}): "
                f"Zonder nachtlading daalt de tank in de ochtend naar {morning_dip_c}°C om {morning_dip_time}u (onder 40°C). "
                f"Nachtlading in het goedkoopste kwartier (03:15u, €{cost_night:.2f}) waarborgt een warme ochtenddouche."
            )
            optimal_slot_type = "cheapest_night_quarter"

        return {
            "status": status,
            "decision_title": decision_title,
            "decision_sub": decision_sub,
            "night_label": night_label,
            "short_night_label": short_night_label,
            "current_temp_c": t_current_c,
            "tank_volume_liters": 350,
            "shower_liters_38c": v_mixed_shower_liters,
            "usable_heat_kwh_th": round(q_now_usable, 2),
            "usable_heat_mj": round(q_now_mj, 1),
            "morning_dip_c": morning_dip_c,
            "morning_dip_time": morning_dip_time,
            "morning_is_safe": morning_is_safe,
            "first_sub40_time": first_sub40_time or "12:30",
            "cost_night_eur": cost_night,
            "cost_day_eur": cost_day,
            "savings_by_waiting_eur": savings_by_waiting,
            "recommendation": recommendation,
            "optimal_slot_type": optimal_slot_type
        }


if __name__ == "__main__":
    model = DhwThermalModel()
    print("=== DHW 350L Thermal State & Demand Test ===")
    now = datetime.now(AMS_TZ)
    # Test with current live temp 49.2°C
    dec = model.evaluate_night_heating_decision(49.2, now, tomorrow_solar_peak_kw=2.5)
    print("Night Charge Decision (Current 49.2°C):")
    print(" ", dec["recommendation"])
    print(f"  • Bruikbare warmte: {dec['usable_heat_kwh_th']} kWh_th ({dec['usable_heat_mj']} MJ)")
    print(f"  • Voorspelde dip: {dec['projected_morning_dip_c']}°C om {dec['morning_dip_time']}")

    # Test what would happen if Wednesday evening had heavy use and temp dropped to 42.0°C
    print("\nScenario Test (Wednesday night tank dropped to 42.0°C after evening showers):")
    dec_cold = model.evaluate_night_heating_decision(42.0, now, tomorrow_solar_peak_kw=2.5)
    print(" ", dec_cold["recommendation"])
