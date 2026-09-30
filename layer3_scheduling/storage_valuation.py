"""
Open HEMS - Storage Energy Valuation
===================================
Calculates the purchase value (inkoopwaarde) and actual replacement value
(actuele marktwaarde t.o.v. live EPEX tarief) for energy stored in:
1. DHW Boilervat (usable above setpoint - 10°C, min 40°C).
2. Thuisbatterij (usable above minimum SoC charge level, default 10%).

Clean Architecture Layer 3 Domain Logic: Zero HA entities, zero external dependencies.
"""

from typing import Dict, Any


def calculate_storage_valuation(
    dhw_tank_temp_c: float = 48.0,
    dhw_setpoint_c: float = 50.0,
    dhw_volume_l: float = 350.0,
    dhw_cop: float = 3.1,
    dhw_charging_price_eur: float = 0.18,
    battery_soc_pct: float = 50.0,
    battery_capacity_kwh: float = 15.0,
    battery_min_soc_pct: float = 10.0,
    battery_charging_price_eur: float = 0.16,
    battery_efficiency: float = 0.90,
    current_epex_price_eur: float = 0.28
) -> Dict[str, Any]:
    """
    Computes buying vs actual valuation of energy stored in DHW tank and battery.
    """
    # -------------------------------------------------------------
    # 1. Domestic Hot Water (DHW / Boilervat)
    # -------------------------------------------------------------
    # Minimum comfort threshold is setpoint - 10°C (default 50 - 10 = 40°C)
    dhw_min_temp = max(35.0, dhw_setpoint_c - 10.0)
    dhw_usable_delta_c = max(0.0, dhw_tank_temp_c - dhw_min_temp)

    # Specific heat capacity of water: 1.163 Wh / (L * K)
    dhw_stored_th_kwh = round((dhw_volume_l * 1.163 * dhw_usable_delta_c) / 1000.0, 2)
    dhw_cop_safe = max(1.0, dhw_cop)
    dhw_stored_el_kwh = round(dhw_stored_th_kwh / dhw_cop_safe, 2)

    dhw_buying_val_eur = round(dhw_stored_el_kwh * dhw_charging_price_eur, 2)
    dhw_actual_val_eur = round(dhw_stored_el_kwh * current_epex_price_eur, 2)
    dhw_delta_val_eur = round(dhw_actual_val_eur - dhw_buying_val_eur, 2)

    dhw_result = {
        "current_temp_c": round(dhw_tank_temp_c, 1),
        "setpoint_c": round(dhw_setpoint_c, 1),
        "min_temp_c": round(dhw_min_temp, 1),
        "usable_temp_delta_c": round(dhw_usable_delta_c, 1),
        "stored_th_kwh": dhw_stored_th_kwh,
        "stored_el_kwh": dhw_stored_el_kwh,
        "cop": round(dhw_cop, 2),
        "charging_price_eur": round(dhw_charging_price_eur, 4),
        "current_price_eur": round(current_epex_price_eur, 4),
        "buying_value_eur": dhw_buying_val_eur,
        "actual_value_eur": dhw_actual_val_eur,
        "delta_value_eur": dhw_delta_val_eur,
        "is_profitable": dhw_delta_val_eur > 0
    }

    # -------------------------------------------------------------
    # 2. Thuisbatterij (Home Battery)
    # -------------------------------------------------------------
    bat_usable_soc_pct = max(0.0, battery_soc_pct - battery_min_soc_pct)
    bat_usable_kwh = round(battery_capacity_kwh * (bat_usable_soc_pct / 100.0), 2)
    bat_stored_kwh = round(battery_capacity_kwh * (battery_soc_pct / 100.0), 2)

    bat_buying_val_eur = round(bat_usable_kwh * battery_charging_price_eur, 2)
    # Actual value reflects avoided retail grid import upon discharge (accounting for discharge efficiency)
    bat_actual_val_eur = round(bat_usable_kwh * battery_efficiency * current_epex_price_eur, 2)
    bat_delta_val_eur = round(bat_actual_val_eur - bat_buying_val_eur, 2)

    battery_result = {
        "current_soc_pct": round(battery_soc_pct, 1),
        "min_soc_pct": round(battery_min_soc_pct, 1),
        "usable_soc_pct": round(bat_usable_soc_pct, 1),
        "capacity_kwh": round(battery_capacity_kwh, 1),
        "stored_kwh": bat_stored_kwh,
        "usable_kwh": bat_usable_kwh,
        "charging_price_eur": round(battery_charging_price_eur, 4),
        "current_price_eur": round(current_epex_price_eur, 4),
        "buying_value_eur": bat_buying_val_eur,
        "actual_value_eur": bat_actual_val_eur,
        "delta_value_eur": bat_delta_val_eur,
        "is_profitable": bat_delta_val_eur > 0
    }

    # -------------------------------------------------------------
    # 3. Aggregates
    # -------------------------------------------------------------
    tot_stored_el_kwh = round(dhw_stored_el_kwh + bat_usable_kwh, 2)
    tot_buying_val_eur = round(dhw_buying_val_eur + bat_buying_val_eur, 2)
    tot_actual_val_eur = round(dhw_actual_val_eur + bat_actual_val_eur, 2)
    tot_delta_val_eur = round(tot_actual_val_eur - tot_buying_val_eur, 2)

    return {
        "dhw": dhw_result,
        "battery": battery_result,
        "total_stored_kwh_el": tot_stored_el_kwh,
        "total_buying_value_eur": tot_buying_val_eur,
        "total_actual_value_eur": tot_actual_val_eur,
        "total_delta_value_eur": tot_delta_val_eur,
        "current_price_eur": round(current_epex_price_eur, 4)
    }
