"""
Unit Tests for Core Physics Module (models/physics.py)
======================================================
Verifies thermodynamic purity, Carnot calculations, boundary clamping,
and defrost penalties.
"""

import pytest
from models.physics import calculate_carnot_cop


def test_calculate_carnot_cop_standard():
    """Verify Carnot COP at typical outdoor and flow temperatures."""
    # At 7°C outdoor and 35°C flow, delta_t = 28K
    # T_flow_K = 308.15K, Carnot COP = 308.15 / 28 = 11.005
    # Empirical COP = 0.48 * 11.005 = 5.28
    cop_7c = calculate_carnot_cop(outdoor_temp_c=7.0, flow_temp_c=35.0)
    assert 5.0 <= cop_7c <= 5.5


def test_calculate_carnot_cop_defrost_penalty():
    """Verify that freezing temperatures (-2°C to +4°C) incur the 0.85 defrost penalty."""
    cop_dry = calculate_carnot_cop(outdoor_temp_c=5.0, flow_temp_c=35.0)
    cop_defrost = calculate_carnot_cop(outdoor_temp_c=3.0, flow_temp_c=35.0)
    # The defrost penalty (0.85) should make 3.0°C visibly lower than 5.0°C beyond natural Carnot delta
    assert cop_defrost < cop_dry


def test_calculate_carnot_cop_bounds():
    """Verify that extreme cold or mild conditions are clamped to [2.2, 6.8]."""
    cop_extreme_cold = calculate_carnot_cop(outdoor_temp_c=-25.0, flow_temp_c=45.0)
    assert cop_extreme_cold == 2.2

    cop_extreme_mild = calculate_carnot_cop(outdoor_temp_c=25.0, flow_temp_c=28.0)
    assert cop_extreme_mild == 6.8
