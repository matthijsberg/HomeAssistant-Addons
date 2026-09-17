"""
Layer 1: Local Microclimate Nowcasting & Observation Nudging
============================================================
Performs exponential observation nudging (data assimilation) between live
local weather station observations (Wittboy GW2000A / rooftop solar inverter)
and regional numerical weather predictions (Open-Meteo / Forecast.Solar).

Mathematical Formulation:
    Delta_X = X_local(0) - X_nwp(0)
    X_assimilated(t) = X_nwp(t) + Delta_X * exp(-t / tau)

Parameters:
    tau_solar = 2.0 hours (cloud persistence timescale)
    tau_temp  = 3.0 hours (ambient thermal inertia timescale)
    tau_wind  = 1.5 hours (wind gust/front timescale)
"""

import math
from typing import List, Optional


class ObservationNowcaster:
    TAU_SOLAR_HOURS = 2.0
    TAU_TEMP_HOURS = 3.0
    TAU_WIND_HOURS = 1.5

    @classmethod
    def nudge_solar_forecast(
        cls,
        raw_solar_kw: List[float],
        live_solar_kw: Optional[float],
        slot_hours: float = 0.25,
        tau_hours: Optional[float] = None
    ) -> List[float]:
        """
        Nudges forward solar PV generation forecast based on actual live inverter generation.
        Anchors slot 0 to live reality and exponentially decays the discrepancy over tau_hours.
        Enforces physical night bounds (0.0 kW).
        """
        if not raw_solar_kw:
            return []
        if live_solar_kw is None or live_solar_kw < 0.0:
            return list(raw_solar_kw)

        raw_at_zero = raw_solar_kw[0]
        if raw_at_zero <= 0.01 and live_solar_kw <= 0.01:
            return list(raw_solar_kw)

        delta_solar = live_solar_kw - raw_at_zero
        tau = tau_hours or cls.TAU_SOLAR_HOURS

        assimilated = []
        for i, raw_val in enumerate(raw_solar_kw):
            t_hours = i * slot_hours
            weight = math.exp(-t_hours / tau)

            if raw_val <= 0.001 and i > 4:
                # Strictly enforce night physical limit
                assimilated.append(0.0)
            else:
                nudged = round(max(0.0, raw_val + (delta_solar * weight)), 3)
                assimilated.append(nudged)

        return assimilated

    @classmethod
    def nudge_temperature_forecast(
        cls,
        raw_temps_c: List[float],
        live_temp_c: Optional[float],
        slot_hours: float = 0.25,
        tau_hours: Optional[float] = None
    ) -> List[float]:
        """
        Nudges outdoor temperature forecast based on local Wittboy temperature sensor.
        Anchors slot 0 to live local temperature and decays toward regional NWP over tau_hours.
        """
        if not raw_temps_c:
            return []
        if live_temp_c is None:
            return list(raw_temps_c)

        delta_temp = live_temp_c - raw_temps_c[0]
        tau = tau_hours or cls.TAU_TEMP_HOURS

        assimilated = []
        for i, raw_val in enumerate(raw_temps_c):
            t_hours = i * slot_hours
            weight = math.exp(-t_hours / tau)
            nudged = round(raw_val + (delta_temp * weight), 1)
            assimilated.append(nudged)

        return assimilated

    @classmethod
    def nudge_wind_forecast(
        cls,
        raw_winds_ms: List[float],
        live_wind_ms: Optional[float],
        slot_hours: float = 0.25,
        tau_hours: Optional[float] = None
    ) -> List[float]:
        """
        Nudges wind speed forecast based on local Wittboy anemometer.
        """
        if not raw_winds_ms:
            return []
        if live_wind_ms is None or live_wind_ms < 0.0:
            return list(raw_winds_ms)

        delta_wind = live_wind_ms - raw_winds_ms[0]
        tau = tau_hours or cls.TAU_WIND_HOURS

        assimilated = []
        for i, raw_val in enumerate(raw_winds_ms):
            t_hours = i * slot_hours
            weight = math.exp(-t_hours / tau)
            nudged = round(max(0.0, raw_val + (delta_wind * weight)), 1)
            assimilated.append(nudged)

        return assimilated
