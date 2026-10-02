import pytest
from layer2_calibration.dhw_thermal_model import DhwThermalModel


def test_dhw_startup_transient_calculation():
    model = DhwThermalModel()

    # At 40°C initial tank temp: startup = 2.5 + 0.06 * 40 = 4.9 min
    t_start, e_start = model.calculate_startup_penalty(tank_temp_c=40.0)
    assert 4.7 <= t_start <= 5.1
    assert 0.08 <= e_start <= 0.13  # kWh


def test_dhw_total_heating_duration_to_50c():
    model = DhwThermalModel()

    # From 40°C to 50°C: 4.9 min startup + 10 * 3.75 = 42.4 min (approx 3 quarter-hours)
    duration_min = model.calculate_heating_duration_minutes(start_temp_c=40.0, target_temp_c=50.0)
    assert 40.0 <= duration_min <= 45.0
    slots_needed = model.calculate_required_slots(start_temp_c=40.0, target_temp_c=50.0)
    assert slots_needed == 3


def test_dhw_total_heating_duration_to_60c_realistic():
    model = DhwThermalModel()

    # From 46°C to 60°C: startup (5.2m) + (50-46)*3.75 (15m) + 10*4.30 (43m) = ~63 min
    # Must be under 75 minutes (definitely under 2 hours!)
    duration_min = model.calculate_heating_duration_minutes(start_temp_c=46.0, target_temp_c=60.0)
    assert 58.0 <= duration_min <= 68.0
    slots_needed = model.calculate_required_slots(start_temp_c=46.0, target_temp_c=60.0)
    assert slots_needed in (4, 5)
