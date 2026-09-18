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
from pathlib import Path
from typing import Dict, List, Any, Tuple, Optional
from models.physics import calculate_dhw_cop

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

    @classmethod
    def get_tank_ua(cls) -> float:
        p_file = Path("/config/heatpump_model_parameters.json")
        if p_file.exists():
            try:
                p = json.loads(p_file.read_text(encoding="utf-8"))
                val = p.get("dhw_tank", {}).get("standby_loss_w_per_k")
                if val is not None and float(val) > 0:
                    return float(val)
            except Exception:
                pass
        return UA_TANK_W_PER_K

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

    @classmethod
    def compute_historical_draw_offs(
        cls,
        sorted_timestamps: List[str],
        temperature_map: Any,
        heatpump_el_kwh_map: Any,
        interval_h: float = 0.25,
        c_tank_kwh_per_k: float = 0.407,
        q_standby_kw: float = 0.055,
        cop_heating: Optional[float] = None
    ) -> List[float]:
        """
        Thermodynamic energy balance on 350L stratified DHW storage (First Law):

        1. When heat pump is OFF (kwh_el <= 0.05):
           Any temperature drop beyond standby loss is genuine tap water consumption:
           Q_tap = max(0.0, -Q_standby - C_vat * (T_t - T_{t-1}))

        2. When heat pump is ON (kwh_el > 0.05):
           Heat pump charges the internal coil and breaks stratification.
           If tank temperature is rising (T_t >= T_{t-1}), heat is absorbed into the
           thermal mass (stratification charging); tap water is 0.0 unless there is an
           active temperature drop (indicating draw-off exceeding heat pump output).
        """
        prev_t = None
        demands_kwh_th = []
        q_standby_quarter = q_standby_kw * interval_h

        for ts in sorted_timestamps:
            t_val = temperature_map.get(ts)
            cur_t = float(t_val) if t_val is not None else (prev_t or 50.0)
            kwh_el = float(heatpump_el_kwh_map.get(ts) or 0.0)

            if prev_t is not None and cur_t is not None:
                delta_t = cur_t - prev_t
                delta_e = c_tank_kwh_per_k * delta_t

                if kwh_el > 0.05:
                    # Heat pump active
                    avg_t = (cur_t + prev_t) / 2.0
                    effective_cop = cop_heating or max(1.8, min(3.2, 4.6 - 0.05 * avg_t))
                    th_in = kwh_el * effective_cop
                    if delta_t < 0:
                        # Temperature fell despite heat pump running -> heavy tap draw-off
                        q_tap = max(0.0, th_in - q_standby_quarter - delta_e)
                    else:
                        # Temperature rising -> tank mass is absorbing heat; 0 phantom tap water
                        q_tap = 0.0
                else:
                    # Heat pump standby/idle
                    # Loss due to tapping equals thermal decrease minus standby
                    q_tap = max(0.0, -q_standby_quarter - delta_e)

                q_tap = max(0.0, round(q_tap, 2))
                if q_tap < 0.05:
                    q_tap = 0.0
            else:
                q_tap = 0.0

            prev_t = cur_t
            demands_kwh_th.append(q_tap)

        return demands_kwh_th

    def simulate_trajectory(
        self,
        t_start_c: float,
        start_dt: datetime,
        hours_ahead: int = 24,
        heat_pump_schedule_slots: Optional[List[int]] = None,
        target_temp_c: float = 50.0,
        heat_pump_power_kw: float = 1.8
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
        current_p05 = t_start_c  # Minimal tap usage (P05) - Upper boundary
        current_p95 = t_start_c  # Heavy tap usage (P95) - Lower boundary
        min_projected_temp = t_start_c
        min_projected_slot_idx = 0
        min_projected_time = ""

        temps_p05 = []
        temps_p95 = []

        # Tracking morning dip (between 06:00 and 09:00 next day)
        morning_dip_temp = 99.0
        morning_dip_time = ""

        DUTCH_DAYS_SHORT = ["Ma", "Di", "Wo", "Do", "Vr", "Za", "Zo"]
        prev_dt = None
        for i in range(total_quarters):
            slot_dt = dt_ams + timedelta(minutes=15 * i)
            if i == 0:
                lbl = slot_dt.strftime("Nu (%H:%M)")
            elif prev_dt is not None and slot_dt.day != prev_dt.day:
                day_str = DUTCH_DAYS_SHORT[slot_dt.weekday()]
                lbl = f"{day_str} {slot_dt.strftime('%H:%M')}"
            else:
                lbl = slot_dt.strftime("%H:%M")
            prev_dt = slot_dt
            dow = slot_dt.weekday()
            q_idx = slot_dt.hour * 4 + slot_dt.minute // 15

            # Heat pump addition (only if active AND tank has not yet reached target_temp_c!)
            q_hp_th = 0.0
            dt_hp = 0.0
            if i in heat_pump_schedule_slots and current_temp < (target_temp_c - 0.1):
                cop_run = calculate_dhw_cop(target_temp_c)
                q_hp_th = heat_pump_power_kw * cop_run * 0.25
                max_dt = max(0.0, target_temp_c - current_temp)
                dt_hp = min(q_hp_th / C_TANK_KWH_PER_C, max_dt)

            dt_hp_p05 = min(dt_hp, max(0.0, target_temp_c - current_p05)) if i in heat_pump_schedule_slots else 0.0
            dt_hp_p95 = min(dt_hp, max(0.0, target_temp_c - current_p95)) if i in heat_pump_schedule_slots else 0.0

            # Tap draw-off demand: Normal (P50), Minimal (P05), Heavy (P95)
            q_tap_th = self.get_learned_tap_kwh_th(dow, q_idx)
            q_tap_p05 = q_tap_th * 0.25  # Light usage
            q_tap_p95 = q_tap_th * 1.50  # Heavy usage (multiple long showers)

            # Standby losses
            ua_tank = self.get_tank_ua()
            dt_standby = ((ua_tank * max(0.0, current_temp - ambient_temp) * 0.25) / 1000.0) / C_TANK_KWH_PER_C
            dt_standby_p05 = ((ua_tank * max(0.0, current_p05 - ambient_temp) * 0.25) / 1000.0) / C_TANK_KWH_PER_C
            dt_standby_p95 = ((ua_tank * max(0.0, current_p95 - ambient_temp) * 0.25) / 1000.0) / C_TANK_KWH_PER_C

            # Usable heat above comfort minimum (40°C) for current slot
            q_usable = max(0.0, (current_temp - T_MIN_COMFORT_C) * C_TANK_KWH_PER_C)

            # Record state at the beginning of slot i (ensures slot 0 'Nu' matches t_start_c exactly)
            timeline_labels.append(lbl)
            temps.append(round(current_temp, 1))
            temps_p05.append(round(current_p05, 1))
            temps_p95.append(round(current_p95, 1))
            energy_usable_kwh.append(round(q_usable, 2))
            demand_kwh_th.append(round(q_tap_th, 3))

            # Advance temperatures for next slot i+1: strictly physical bounded
            current_temp = max(15.0, min(75.0, current_temp - dt_standby - (q_tap_th / C_TANK_KWH_PER_C) + dt_hp))
            current_p05 = max(15.0, min(75.0, current_p05 - dt_standby_p05 - (q_tap_p05 / C_TANK_KWH_PER_C) + dt_hp_p05))
            current_p95 = max(15.0, min(75.0, current_p95 - dt_standby_p95 - (q_tap_p95 / C_TANK_KWH_PER_C) + dt_hp_p95))

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
            "temperatures_p05_c": temps_p05,
            "temperatures_p95_c": temps_p95,
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
        planned_heat_hour: float = 12.5,
        prices_map: Optional[Dict[str, float]] = None,
        lockout_min_delta: float = 0.04
    ) -> Dict[str, Any]:
        dt_ams = now_dt.astimezone(AMS_TZ)
        today_name = ["Maandag", "Dinsdag", "Woensdag", "Donderdag", "Vrijdag", "Zaterdag", "Zondag"][dt_ams.weekday()]
        tomorrow_dt = dt_ams + timedelta(days=1)
        tomorrow_name = ["Maandag", "Dinsdag", "Woensdag", "Donderdag", "Vrijdag", "Zaterdag", "Zondag"][tomorrow_dt.weekday()]
        night_label = f"{today_name} op {tomorrow_name} nacht ({dt_ams.day}-{tomorrow_dt.day} {['jan','feb','mrt','apr','mei','jun','jul','aug','sep','okt','nov','dec'][dt_ams.month-1]})"
        short_night_label = f"{today_name[:2]} {dt_ams.day} ➔ {tomorrow_name[:2]} {tomorrow_dt.day} {['jan','feb','mrt','apr','mei','jun','jul','aug','sep','okt','nov','dec'][dt_ams.month-1]}"

        # Simulate standard (P50) 24h trajectory without night heat
        unheated_sim = self.simulate_trajectory(t_current_c, now_dt, hours_ahead=24, heat_pump_schedule_slots=[])
        temps = unheated_sim["temperatures_c"]
        labels = unheated_sim["labels"]

        # 1. P50 Morning Dip (06:00 - 09:45 tomorrow)
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

        # 2. P95 Morning Dip (Stochastic Heavy Shower Stress Scenario: +45% tap water draw-off)
        # Calculates conservative dip when multiple family members take long showers or wash hair
        p95_extra_kwh_th = 0.65  # ~35L additional 38C shower water
        morning_dip_p95_c = round(max(30.0, morning_dip_c - (p95_extra_kwh_th / C_TANK_KWH_PER_C)), 1)
        p95_is_safe = (morning_dip_p95_c >= T_MIN_COMFORT_C)
        morning_is_safe = (morning_dip_c >= T_MIN_COMFORT_C)

        # 3. Calculate first moment tank drops below 40.0C
        first_sub40_time = ""
        first_sub40_idx = len(temps)
        for idx, (lbl, t) in enumerate(zip(labels, temps)):
            if idx >= 12 and t < T_MIN_COMFORT_C:
                first_sub40_time = lbl
                first_sub40_idx = idx
                break

        # 4. Dynamic Spits-Lockout Detection based on Real Spot Prices
        min_night_p = 0.309
        max_morn_p = 0.380
        min_day_p = 0.291
        if prices_map:
            night_vals = [v for k, v in prices_map.items() if any(k.endswith(f"{h:02d}:{m:02d}") or f" {h:02d}:{m:02d}" in k for h in range(0, 6) for m in (0, 15, 30, 45))]
            morn_vals = [v for k, v in prices_map.items() if any(k.endswith(f"{h:02d}:{m:02d}") or f" {h:02d}:{m:02d}" in k for h in range(7, 10) for m in (0, 15, 30, 45))]
            day_vals = [v for k, v in prices_map.items() if any(k.endswith(f"{h:02d}:{m:02d}") or f" {h:02d}:{m:02d}" in k for h in range(11, 16) for m in (0, 15, 30, 45))]
            if night_vals: min_night_p = min(night_vals)
            if morn_vals: max_morn_p = max(morn_vals)
            if day_vals: min_day_p = min(day_vals)

        morn_delta_p = round(max_morn_p - min_night_p, 4)
        has_morn_lockout = (morn_delta_p >= lockout_min_delta)

        # Tank only "drops in morning peak" if that morning peak is an ACTUAL economic lockout
        # And if the dip occurs strictly before 09:30
        drops_in_morning_peak = has_morn_lockout and ("06:30" <= (first_sub40_time or "12:00") <= "09:30")

        # 5. Financial Cost Comparison: Night Charge vs Daytime Charge
        night_price = min_night_p
        cop_night = 2.65      # Lower COP at 11C night air
        th_need_night = (T_TARGET_C - t_current_c) * C_TANK_KWH_PER_C + (STANDBY_LOSS_KW_PER_HOUR * 8.0)
        el_kwh_night = max(1.2, th_need_night / cop_night)
        cost_night = round(el_kwh_night * night_price, 2)

        day_price = min_day_p     # Cheaper midday spot tariff + solar self-consumption
        cop_day = 3.25        # Higher COP at 16C daytime air
        th_need_day = (T_TARGET_C - min(40.0, morning_dip_c)) * C_TANK_KWH_PER_C
        el_kwh_day = max(1.0, th_need_day / cop_day)
        cost_day = round(el_kwh_day * day_price, 2)

        savings_by_waiting = round(cost_night - cost_day, 2)
        night_premium_pct = round(((cost_night - cost_day) / max(0.01, cost_day)) * 100.0, 1)

        # 6. Cost Tolerance & Comfort Hedging Rule:
        # If night heating is less than 20% more expensive than daytime, AND either:
        #   (a) P95 morning dip falls below 40.5C (tight safety margin), OR
        #   (b) First dip occurs before 11:30 (dangerously close to morning peak)
        # -> TRIGGER NIGHT CHARGING AS COMFORT INSURANCE!
        comfort_hedge_triggered = False
        if night_premium_pct <= 20.0 and (morning_dip_p95_c < 40.5 or (first_sub40_time and first_sub40_time < "11:30")):
            comfort_hedge_triggered = True

        # Current usable energy & shower volume
        q_now_usable = max(0.0, (t_current_c - T_MIN_COMFORT_C) * C_TANK_KWH_PER_C)
        q_now_mj = q_now_usable * 3.6
        v_mixed_shower_liters = round(350.0 * max(0.0, t_current_c - 12.0) / (38.0 - 12.0))

        # 7. Final Policy Decision Logic
        can_safely_defer = (not has_morn_lockout or morning_dip_time >= "09:30") and morning_dip_c >= 38.5 and (cost_day <= cost_night)

        if drops_in_morning_peak:
            status = "SCHEDULE_NIGHT_CHARGE"
            decision_title = "⚠️ Nachtlading vereist (Ochtendcomfort)"
            decision_sub = f"Tank zakt om {morning_dip_time}u onder 40°C in economische ochtendspits (+€{morn_delta_p:.2f}/kWh)"
            recommendation = (
                f"⚠️ NACHTLADING VEREIST ({night_label}): "
                f"Zonder nachtlading daalt de tank tijdens de ochtendspits naar {morning_dip_c}°C om {morning_dip_time}u (<40°C). "
                f"Omdat de ochtendspits een echte prijspiek heeft (+€{morn_delta_p:.3f}/kWh), voorkomt nachtlading in het dalkwartier (€{cost_night:.2f}) een koude douche en dure piekinkoop."
            )
            optimal_slot_type = "cheapest_night_quarter"
        elif can_safely_defer:
            status = "SKIP_NIGHT_CHARGE"
            decision_title = "✅ Geen nachtlading nodig (Dynamische Vrijgave)"
            decision_sub = f"Ochtendverschil slechts €{morn_delta_p:.3f}/kWh (< €{lockout_min_delta:.2f} drempel) · Verwarmen om 10:00/12:00u bespaart €{savings_by_waiting:.2f}"
            recommendation = (
                f"✅ GEEN nachtlading nodig ({night_label}). "
                f"Ochtendtarief heeft geen significante prijspiek (ΔP = €{morn_delta_p:.3f}/kWh, onder de €{lockout_min_delta:.2f} drempel). "
                f"De tank blijft met {morning_dip_c}°C comfortabel tot {morning_dip_time}u. "
                f"Door pas rond 10:00–12:00u met dagzon (€{day_price:.3f}/kWh, COP {cop_day}) te laden, bespaar je €{savings_by_waiting:.2f} en voorkom je onnodig nachtverlies!"
            )
            optimal_slot_type = "midday_solar"
        elif not morning_is_safe and morning_dip_c < 38.0:
            status = "SCHEDULE_NIGHT_CHARGE"
            decision_title = "⚠️ Nachtlading vereist (Diepe Ochtenddip)"
            decision_sub = f"Tank zakt naar {morning_dip_c}°C (<38°C noodgrens) om {morning_dip_time}u"
            recommendation = (
                f"⚠️ NACHTLADING VEREIST ({night_label}): "
                f"Zonder nachtlading zakt de tank naar {morning_dip_c}°C om {morning_dip_time}u. Om koud water te voorkomen wordt nachtlading bekrachtigd."
            )
            optimal_slot_type = "cheapest_night_quarter"
        elif comfort_hedge_triggered:
            status = "SCHEDULE_NIGHT_CHARGE"
            decision_title = "🛡️ Nachtlading aanbevolen (Comfortverzekering)"
            decision_sub = f"Nacht meerkosten slechts {night_premium_pct}% (<20% drempel) · P95 dip {morning_dip_p95_c}°C"
            recommendation = (
                f"🛡️ NACHTLADING AANBEVOLEN ({night_label}) via Comfort-Hedging beleid: "
                f"Nachtladen kost €{cost_night:.2f} t.o.v. €{cost_day:.2f} overdag (+{night_premium_pct}%, onder de 20% tolerantiedrempel). "
                f"Omdat de P95-risicodip {morning_dip_p95_c}°C is en de tank om {first_sub40_time or '12:00'}u leeg dreigt te raken, "
                f"wordt 's nachts preventief bijgeladen om het gezin 100% warm water te garanderen zonder spits-risico."
            )
            optimal_slot_type = "cheapest_night_quarter"
        else:
            status = "SKIP_NIGHT_CHARGE"
            decision_title = "✅ Geen nachtlading nodig"
            decision_sub = f"Wachten tot {first_sub40_time or '12:30'}u bespaart €{savings_by_waiting:.2f} ({abs(night_premium_pct):.0f}%)"
            recommendation = (
                f"✅ GEEN nachtlading nodig ({night_label}). "
                f"Ochtenddouches blijven met {morning_dip_c}°C (P95: {morning_dip_p95_c}°C om {morning_dip_time}u) ruim warm (>40°C). "
                f"De ochtendspits (07:00–09:30) wordt veilig overbrugd. "
                f"Wachten tot {first_sub40_time or '12:30'}u (buiten de spits) kost €{cost_day:.2f} bij COP {cop_day} + zon "
                f"t.o.v. €{cost_night:.2f} 's nachts bij COP {cop_night}. Je bespaart €{savings_by_waiting:.2f}!"
            )
            optimal_slot_type = "midday_solar"

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
            "morning_dip_p95_c": morning_dip_p95_c,
            "morning_dip_time": morning_dip_time,
            "morning_is_safe": morning_is_safe,
            "p95_is_safe": p95_is_safe,
            "first_sub40_time": first_sub40_time or "12:30",
            "drops_in_morning_peak": drops_in_morning_peak,
            "cost_night_eur": cost_night,
            "cost_day_eur": cost_day,
            "savings_by_waiting_eur": savings_by_waiting,
            "night_premium_pct": night_premium_pct,
            "comfort_hedge_triggered": comfort_hedge_triggered,
            "recommendation": recommendation,
            "optimal_slot_type": optimal_slot_type,
            "recalculation_policy": "Continu her-overwogen (elke 60s via live tanksensor, definitieve executie om 02:00u)",
            "spits_lockout_morning": "07:00 - 09:30 (strikte blokkade)",
            "spits_lockout_evening": "17:00 - 20:00 (strikte blokkade)",
            "cost_tolerance_threshold": "Nachtlading < 20% duurder dan daglading activeert comfort-verzekering"
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
