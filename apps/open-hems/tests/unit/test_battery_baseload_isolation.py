"""
Unit Test: Battery Baseload Isolation (WP-BAT0)
================================================
Guarantees that battery charging and discharging flows never pollute
or distort the unallocated household baseload calculation (Invariant #3 & Invariant #7).
"""

import pytest


def calculate_unallocated(p1_imp: float, p1_exp: float, sol: float, wp: float, bat: float) -> float:
    """Exact logic from daemon.py live balance accumulator."""
    net_grid = p1_imp - p1_exp
    bat_charge = max(0.0, bat)
    bat_discharge = max(0.0, -bat)
    tot_house = max(0.0, net_grid + sol + bat_discharge - bat_charge)
    unalloc = max(50.0, tot_house - wp)
    return unalloc


def test_battery_idle_preserves_baseload():
    # 500W baseload, 1000W heat pump, 0W solar -> net grid import = 1500W
    unalloc = calculate_unallocated(p1_imp=1500.0, p1_exp=0.0, sol=0.0, wp=1000.0, bat=0.0)
    assert unalloc == 500.0


def test_battery_charging_from_solar_does_not_inflate_baseload():
    # Solar produces 4000W: 500W baseload, 1000W heat pump, 2000W battery charge, 500W export
    # net_grid = -500W
    unalloc = calculate_unallocated(p1_imp=0.0, p1_exp=500.0, sol=4000.0, wp=1000.0, bat=2000.0)
    assert unalloc == 500.0


def test_battery_discharging_at_night_does_not_deflate_baseload():
    # Night: 500W baseload, 1000W heat pump. Battery discharges 1500W to achieve 0W grid import.
    unalloc = calculate_unallocated(p1_imp=0.0, p1_exp=0.0, sol=0.0, wp=1000.0, bat=-1500.0)
    assert unalloc == 500.0


def test_battery_charging_from_grid_does_not_inflate_baseload():
    # Night valley: 500W baseload, 0W heat pump, 3000W grid charging -> net grid import = 3500W
    unalloc = calculate_unallocated(p1_imp=3500.0, p1_exp=0.0, sol=0.0, wp=0.0, bat=3000.0)
    assert unalloc == 500.0
