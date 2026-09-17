"""
Unit Tests for ObservationNowcaster (Local Microclimate Nudging)
===============================================================
"""

import pytest
import math
from layer1_data_collection.nowcasting import ObservationNowcaster


def test_nudge_solar_forecast_anchoring():
    """Verify that slot 0 is anchored to live inverter solar power and decays smoothly."""
    raw_solar = [1.5, 1.6, 1.7, 1.8, 1.5, 1.0, 0.5, 0.0]
    live_solar = 1.0  # Real solar is 0.5 kW lower due to local clouds

    nudged = ObservationNowcaster.nudge_solar_forecast(
        raw_solar,
        live_solar_kw=live_solar,
        slot_hours=0.25,
        tau_hours=2.0
    )

    # Slot 0 must match live reading exactly
    assert nudged[0] == 1.0

    # Intermediate slots must be lower than raw, but converging back toward raw
    assert nudged[1] < raw_solar[1]
    diff_slot_1 = raw_solar[1] - nudged[1]
    diff_slot_4 = raw_solar[4] - nudged[4]
    assert diff_slot_1 > diff_slot_4, "Discrepancy must decay exponentially over time"

    # Night slot (0.0) remains strictly 0.0
    assert nudged[-1] == 0.0


def test_nudge_temperature_forecast_anchoring():
    """Verify that outdoor temperature anchors to local Wittboy reading."""
    raw_temps = [15.0, 15.2, 15.4, 15.6, 15.8, 16.0]
    live_temp = 17.0  # Local garden is 2.0C warmer

    nudged = ObservationNowcaster.nudge_temperature_forecast(
        raw_temps,
        live_temp_c=live_temp,
        slot_hours=0.25,
        tau_hours=3.0
    )

    assert nudged[0] == 17.0
    assert nudged[1] > raw_temps[1]
    assert nudged[-1] > raw_temps[-1]
    # By hour 1.25 (index 5), diff is smaller than 2.0
    assert (nudged[5] - raw_temps[5]) < 2.0


def test_nudge_with_none_preserves_raw():
    """Verify that missing sensor gracefully returns the unnudged raw forecast."""
    raw = [10.0, 11.0, 12.0]
    assert ObservationNowcaster.nudge_temperature_forecast(raw, None) == raw
    assert ObservationNowcaster.nudge_solar_forecast(raw, None) == raw
    assert ObservationNowcaster.nudge_wind_forecast(raw, None) == raw
