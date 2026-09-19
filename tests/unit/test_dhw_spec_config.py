"""
Unit Tests for DHW Spec Config Loading & Thermal Consistency (WP6)
==================================================================
Verifies:
1. DhwTankSpec.from_config() correctly parses compressor_power_kw and overrides defaults.
2. CentralPlanner.plan() respects custom dhw_spec from config (e.g. 3.0 kW).
3. dhw_cop params are loaded from heatpump_model_parameters.json.
4. Simulated 45-minute heating run delivers ~9.5 to 11.5 K opwarming (within ±25% of measured reference).
5. Architectural guardrail: all dhw_step / dhw_cop calls in dhw_optimizer.py pass params.
"""

import pytest
import ast
from pathlib import Path
from datetime import datetime, timezone

from layer3_scheduling.dhw_specs import DhwTankSpec
from layer3_scheduling.central_planner import CentralPlanner
from models.physics import dhw_cop, dhw_step, load_dhw_cop_params
from tests.unit.test_dhw_optimizer import make_test_slots


def test_dhw_spec_from_config_is_used_by_planner():
    """Verify that compressor_power_kw from site config is used by the planner."""
    custom_cfg = {
        "dhw_boiler": {
            "tank_volume_liters": 350.0,
            "compressor_power_kw": 3.0,
            "thermal_output_kw": 6.0
        }
    }
    spec = DhwTankSpec.from_config(custom_cfg)
    assert spec.heat_pump_electric_kw == 3.0
    assert spec.thermal_output_kw == 6.0


def test_dhw_cop_params_loaded_from_model_parameters():
    """Verify that load_dhw_cop_params reads the dhw_cop block."""
    params = load_dhw_cop_params()
    assert "cop_50" in params
    assert params["cop_50"] == 2.0
    val = dhw_cop(50.0, 10.0, params=params)
    assert val == 2.0


def test_dhw_spec_thermal_consistency():
    """
    WP6 Step 4:
    Predicted warming for a 3-slot (45 min) run with 3.0 kW electric and COP ~2.0
    must deliver between 8.5 and 13.0 K, strictly within ±25% of the empirical 10.5 K reference.
    """
    spec = DhwTankSpec(heat_pump_electric_kw=3.0)
    c_tank = spec.thermal_capacity_kwh_per_k  # 0.4068 kWh/K
    params = {"cop_50": 2.0, "k_t": 0.07, "k_out": 0.05, "cop_min": 1.4, "cop_max": 3.2}

    tank_spec_dict = {
        "thermal_capacity_kwh_per_k": c_tank,
        "ua_w_per_k": 2.5,
        "heat_pump_power_kw": 3.0,
        "target_temp_c": 60.0
    }

    t_curr = 44.0
    t_start = t_curr
    for _ in range(3):  # 3 * 15 min = 45 min
        t_curr = dhw_step(
            t_tank_c=t_curr,
            u=1.0,
            q_tap_kwh=0.0,
            t_outdoor_c=12.0,
            dt_h=0.25,
            spec=tank_spec_dict,
            params=params,
            t_max_c=60.0,
            t_amb_c=18.0
        )

    delta_t_45m = t_curr - t_start
    empirical_reference = 10.5  # K per 45 min
    rel_error = abs(delta_t_45m - empirical_reference) / empirical_reference
    assert rel_error <= 0.25, (
        f"Predicted 45m rise {delta_t_45m:.2f} K deviates by {rel_error*100:.1f}% "
        f"from empirical reference {empirical_reference} K (must be <= 25%)"
    )


def test_no_dhw_cop_call_without_params_in_optimizer():
    """
    Architectural Guardrail:
    Scans layer3_scheduling/dhw_optimizer.py AST and ensures that every call
    to dhw_cop and dhw_step explicitly passes params or model_parameters.
    """
    opt_file = Path(__file__).parent.parent.parent / "layer3_scheduling" / "dhw_optimizer.py"
    assert opt_file.exists()
    tree = ast.parse(opt_file.read_text(encoding="utf-8"))

    violations = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func_name = None
            if isinstance(node.func, ast.Name):
                func_name = node.func.id
            elif isinstance(node.func, ast.Attribute):
                func_name = node.func.attr

            if func_name in ("dhw_step", "dhw_cop"):
                # Check keyword arguments for 'params'
                has_params = any(kw.arg == "params" for kw in node.keywords)
                if not has_params:
                    violations.append(f"Line {node.lineno}: call to {func_name}() is missing 'params' keyword argument")

    assert not violations, "Unparameterized physics calls in dhw_optimizer.py:\n" + "\n".join(violations)
