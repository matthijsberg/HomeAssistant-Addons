"""
Tests for ForecastSolarProvider
===============================
"""

import pytest
from datetime import datetime
from zoneinfo import ZoneInfo
from layer1_data_collection.forecast_solar import ForecastSolarProvider


def test_forecast_solar_interpolation_and_calibration():
    # Mock watts map
    provider = ForecastSolarProvider(
        lat=51.9537,
        lon=5.2320,
        tilt=34.0,
        azimuth_deg_south=45.0,
        kwp=5.76,
        inverter_max_kw=5.5,
        calibration_factor=1.18
    )

    # Pre-populate cached watts
    provider._cached_watts = {
        "2026-09-12 11:00:00": 1000.0,
        "2026-09-12 12:00:00": 2000.0,
        "2026-09-12 13:00:00": 3000.0
    }
    provider._last_fetch_time = datetime.now(ZoneInfo("Europe/Amsterdam"))

    start_dt = datetime.fromisoformat("2026-09-12T11:00:00+02:00")
    slots = provider.get_calibrated_quarter_slots(start_dt, horizon_slots=8, step_mins=15)

    assert len(slots) == 8
    # 11:00: raw 1000W * 1.18 = 1180W = 1.18 kW
    assert slots[0]["solar_kw"] == 1.180

    # 11:30 (halfway between 1000 and 2000 = 1500W * 1.18 = 1770W = 1.77 kW)
    assert slots[2]["solar_kw"] == 1.770

    # 12:00: raw 2000W * 1.18 = 2.36 kW
    assert slots[4]["solar_kw"] == 2.360


def test_inverter_ceiling_clamping():
    provider = ForecastSolarProvider(
        inverter_max_kw=5.5,
        calibration_factor=1.20
    )
    provider._cached_watts = {
        "2026-09-12 13:00:00": 6000.0  # Would be 7.2 kW with 1.2x
    }
    provider._last_fetch_time = datetime.now(ZoneInfo("Europe/Amsterdam"))

    start_dt = datetime.fromisoformat("2026-09-12T13:00:00+02:00")
    slots = provider.get_calibrated_quarter_slots(start_dt, horizon_slots=1)

    # Must be clamped strictly to 5.5 kW
    assert slots[0]["solar_kw"] == 5.500
