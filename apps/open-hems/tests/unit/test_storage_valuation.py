"""
Unit Tests for Storage Energy Valuation (DHW & Thuisbatterij)
============================================================
Verifies:
1. DHW usable thermal and electrical storage calculation against setpoint - 10°C (40°C min).
2. Clamping when DHW temperature is below minimum threshold (<= 40°C).
3. Battery usable storage calculation against minimum charge level (10% SoC).
4. Clamping when Battery SoC is below minimum level (<= 10%).
5. Buying value vs actual value (at current EPEX tariff) and arbitrage margin.
"""

import pytest
from layer3_scheduling.storage_valuation import calculate_storage_valuation


def test_dhw_storage_valuation_above_minimum():
    val = calculate_storage_valuation(
        dhw_tank_temp_c=50.0,
        dhw_setpoint_c=50.0,
        dhw_volume_l=350.0,
        dhw_cop=3.1,
        dhw_charging_price_eur=0.18,
        battery_soc_pct=50.0,
        battery_capacity_kwh=15.0,
        battery_min_soc_pct=10.0,
        battery_charging_price_eur=0.16,
        battery_efficiency=0.90,
        current_epex_price_eur=0.30
    )

    dhw = val["dhw"]
    # 50°C - 40°C = 10°C usable
    assert dhw["min_temp_c"] == 40.0
    assert dhw["usable_temp_delta_c"] == 10.0
    # 350L * 1.163 * 10 / 1000 = 4.07 kWh_th
    assert pytest.approx(dhw["stored_th_kwh"], 0.05) == 4.07
    # 4.07 / 3.1 = 1.31 kWh_el
    assert pytest.approx(dhw["stored_el_kwh"], 0.05) == 1.31
    # Buying value: 1.31 * 0.18 = €0.24
    assert pytest.approx(dhw["buying_value_eur"], 0.02) == 0.24
    # Actual value: 1.31 * 0.30 = €0.39
    assert pytest.approx(dhw["actual_value_eur"], 0.02) == 0.39
    # Delta: €0.39 - €0.24 = +€0.15
    assert dhw["delta_value_eur"] > 0
    assert dhw["is_profitable"] is True


def test_dhw_storage_valuation_below_minimum_clamped():
    val = calculate_storage_valuation(
        dhw_tank_temp_c=38.5,
        dhw_setpoint_c=50.0,
        dhw_volume_l=350.0,
        current_epex_price_eur=0.30
    )
    dhw = val["dhw"]
    assert dhw["usable_temp_delta_c"] == 0.0
    assert dhw["stored_th_kwh"] == 0.0
    assert dhw["stored_el_kwh"] == 0.0
    assert dhw["buying_value_eur"] == 0.0
    assert dhw["actual_value_eur"] == 0.0
    assert dhw["delta_value_eur"] == 0.0


def test_battery_storage_valuation_above_minimum():
    val = calculate_storage_valuation(
        battery_soc_pct=50.0,
        battery_capacity_kwh=15.0,
        battery_min_soc_pct=10.0,
        battery_charging_price_eur=0.16,
        battery_efficiency=0.90,
        current_epex_price_eur=0.32
    )

    bat = val["battery"]
    # 50% - 10% = 40% usable = 6.0 kWh
    assert bat["usable_soc_pct"] == 40.0
    assert bat["usable_kwh"] == 6.0
    # Buying value: 6.0 kWh * 0.16 = €0.96
    assert pytest.approx(bat["buying_value_eur"], 0.02) == 0.96
    # Actual value: 6.0 kWh * 0.90 * 0.32 = €1.73
    assert pytest.approx(bat["actual_value_eur"], 0.02) == 1.73
    # Delta: €1.73 - €0.96 = +€0.77
    assert pytest.approx(bat["delta_value_eur"], 0.02) == 0.77
    assert bat["is_profitable"] is True


def test_battery_storage_valuation_below_minimum_clamped():
    val = calculate_storage_valuation(
        battery_soc_pct=8.0,
        battery_capacity_kwh=15.0,
        battery_min_soc_pct=10.0,
        current_epex_price_eur=0.30
    )
    bat = val["battery"]
    assert bat["usable_soc_pct"] == 0.0
    assert bat["usable_kwh"] == 0.0
    assert bat["buying_value_eur"] == 0.0
    assert bat["actual_value_eur"] == 0.0
    assert bat["delta_value_eur"] == 0.0
