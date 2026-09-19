"""
Unit & Integration Tests for DHW Power Parameters (Deel A Acceptance Tests)
==========================================================================
Verifies:
1. test_power_params_are_read_from_file:
   Afwijkende p_nom_50 in model_parameters wordt daadwerkelijk gebruikt door de optimizer.
2. test_calibration_preserves_unknown_blocks:
   Calibrator overschrijft niet langer onbekende/andere blokken (dhw_cop, dhw_power).
3. test_live_params_file_contains_dhw_blocks:
   PARAMS_FILE (/config/heatpump_model_parameters.json) bevat zowel dhw_cop als dhw_power.
4. test_outdoor_temperature_reaches_power_function:
   Verschillende buitentemperaturen bereiken de vermogensfunctie en beïnvloeden P_el wanneer k_out != 0.
"""

import json
import pytest
from pathlib import Path
from datetime import datetime, timezone, timedelta

from api.secrets_store import PARAMS_FILE, load_json
from layer3_scheduling.dhw_specs import DhwTankSpec
from layer3_scheduling.dhw_optimizer import DhwOptimizerParams, solve
from layer3_scheduling.dhw_plan_adapter import adapt_optimizer_to_dhw_summary
from layer2_calibration.calibrator import ModelCalibrationEngine


class MockSlot:
    def __init__(self, idx, dt_val, price=0.20, solar=0.0, is_lockout=False, outdoor_temp=10.0):
        self.slot_idx = idx
        self.dt = dt_val
        self.label = dt_val.strftime("%H:%M")
        self.price_all_in = price
        self.export_price_eur_kwh = price * 0.5
        self.solar_kw = solar
        self.unallocated_kw = 0.3
        self.outdoor_temp_c = outdoor_temp
        self.is_hard_lockout = is_lockout


def make_test_slots(count=96, outdoor_temp=10.0):
    base_dt = datetime(2026, 9, 19, 0, 0, tzinfo=timezone.utc)
    return [MockSlot(i, base_dt + timedelta(minutes=15 * i), outdoor_temp=outdoor_temp) for i in range(count)]


def test_power_params_are_read_from_file(tmp_path):
    """
    A.3.1: Schrijf een parameterbestand met een afwijkende p_nom_50 (bijv 3.45 kW)
    en controleer dat de optimizer en adapter dat vermogen daadwerkelijk hanteren.
    """
    slots = make_test_slots(48, outdoor_temp=10.0)
    spec = DhwTankSpec(comfort_min_temp_c=40.0, target_setpoint_c=50.0)
    params = DhwOptimizerParams(min_run_slots=2, min_dwell_slots=2)

    custom_model_params = {
        "dhw_power": {
            "p_nom_50": 3.45,
            "t_ref_tank_c": 50.0,
            "k_t_tank": 0.0,
            "t_ref_out_c": 10.0,
            "k_out": 0.0,
            "p_min_kw": 1.0,
            "p_max_kw": 5.0
        }
    }

    # Solve with T0=40.0 (triggers immediate run)
    res = solve(
        slots=slots,
        t0_c=40.0,
        run_state0=("OFF_FREE", 0),
        spec=spec,
        params=params,
        model_parameters=custom_model_params
    )

    assert len(res.runs) >= 1
    # First run must reflect p_nom_50 = 3.45 kW
    # Energy for 2 slots at 3.45 kW is 3.45 * 0.5 = 1.725 kWh_el
    first_run = res.runs[0]
    expected_kwh = 3.45 * (first_run.end_idx - first_run.start_idx) * 0.25
    assert abs(first_run.kwh_el - expected_kwh) < 0.05, (
        f"Expected ~{expected_kwh} kWh_el with p_nom_50=3.45, but got {first_run.kwh_el}"
    )


def test_calibration_preserves_unknown_blocks(tmp_path):
    """
    A.3.2: Calibrator mag bestaande blokken (zoals dhw_cop en dhw_power) niet overschrijven/wissen.
    """
    fake_params_file = tmp_path / "model_params.json"
    initial_content = {
        "ua_base": 7.0,
        "dhw_cop": {"cop_50": 2.0, "k_t": 0.07},
        "dhw_power": {"p_nom_50": 2.72, "k_t_tank": 0.074},
        "custom_third_party_block": {"preserve_me": True}
    }
    fake_params_file.write_text(json.dumps(initial_content), encoding="utf-8")

    cal = ModelCalibrationEngine(params_path=str(fake_params_file))
    # Directly verify the merge behavior on save
    updated = {
        "ua_base": 7.5,
        "calibration_timestamp": "2026-09-19 12:00:00"
    }
    merged = dict(cal.params or {})
    merged.update(updated)
    cal.params = merged
    cal._save_params()

    saved_data = json.loads(fake_params_file.read_text(encoding="utf-8"))
    assert saved_data["ua_base"] == 7.5
    assert "dhw_cop" in saved_data, "Calibrator gewist: dhw_cop ontbreekt!"
    assert "dhw_power" in saved_data, "Calibrator gewist: dhw_power ontbreekt!"
    assert saved_data["custom_third_party_block"]["preserve_me"] is True


def test_live_params_file_contains_dhw_blocks():
    """
    A.3.3: Controleer aanwezigheid van dhw_cop en dhw_power in PARAMS_FILE.
    """
    assert PARAMS_FILE.exists(), f"PARAMS_FILE niet gevonden op {PARAMS_FILE}"
    data = load_json(PARAMS_FILE)
    assert "dhw_cop" in data, (
        f"PARAMS_FILE ({PARAMS_FILE}) mist het blok 'dhw_cop'. Zie PLAN-dhw-comfortmarge-en-uitlegfixes.md Deel A."
    )
    assert "dhw_power" in data, (
        f"PARAMS_FILE ({PARAMS_FILE}) mist het blok 'dhw_power'. Zie PLAN-dhw-comfortmarge-en-uitlegfixes.md Deel A."
    )
    p_cfg = data["dhw_power"]
    assert "p_nom_50" in p_cfg
    assert "k_t_tank" in p_cfg
    assert "k_out" in p_cfg


def test_outdoor_temperature_reaches_power_function():
    """
    A.3.4: Twee solves met dezelfde invoer maar verschillende buitentemperaturen
    moeten verschillende vermogens/kosten opleveren zodra k_out != 0.
    """
    spec = DhwTankSpec(comfort_min_temp_c=40.0, target_setpoint_c=50.0)
    params = DhwOptimizerParams(min_run_slots=2, min_dwell_slots=2)

    model_params = {
        "dhw_power": {
            "p_nom_50": 2.72,
            "t_ref_tank_c": 50.0,
            "k_t_tank": 0.0,
            "t_ref_out_c": 10.0,
            "k_out": 0.05,  # 50 W / K
            "p_min_kw": 1.0,
            "p_max_kw": 5.0
        }
    }

    # Cold day (-5°C) vs Warm day (+25°C): delta 30 K -> 30 * 0.05 = 1.5 kW verschil
    slots_cold = make_test_slots(48, outdoor_temp=-5.0)
    slots_warm = make_test_slots(48, outdoor_temp=25.0)

    res_cold = solve(slots=slots_cold, t0_c=40.0, run_state0=("OFF_FREE", 0), spec=spec, params=params, model_parameters=model_params)
    res_warm = solve(slots=slots_warm, t0_c=40.0, run_state0=("OFF_FREE", 0), spec=spec, params=params, model_parameters=model_params)

    assert len(res_cold.runs) >= 1
    assert len(res_warm.runs) >= 1

    p_cold = res_cold.runs[0].kwh_el
    p_warm = res_warm.runs[0].kwh_el
    assert p_cold > p_warm, (
        f"Koude run ({p_cold:.2f} kWh_el) hoort zwaarder te zijn dan warme run ({p_warm:.2f} kWh_el) bij k_out=0.05"
    )
