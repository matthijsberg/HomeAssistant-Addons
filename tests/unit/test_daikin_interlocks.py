"""
Tests for Daikin Altherma Hydraulic Interlocks & Relay Mapping
=============================================================
Verifies:
1. Hydraulic Interlock: During active DHW (Stand 4), CV Master switch MUST be False.
2. Lossy Mapping: ADVISED_OFF downgrades to NORMAL with explicit downgrade reason.
3. Actuator Feedback: ActuationResult reports effective_mode accurately.
"""

import pytest
from integrations.daikin_altherma.interlocks import DaikinInterlock
from integrations.daikin_altherma.actuator import DaikinActuator


def test_hydraulic_interlock_cv_master_off_during_dhw():
    """Verify that requesting forced_on forces cv_master_switch_on=False."""
    # Even if CV was用意, requesting forced DHW run must enforce CV Master OFF and SG4 (S10S=True, S11S=True)
    cmd = DaikinInterlock.resolve_command(requested_mode="forced_on", current_cv_switch_state=True)
    assert cmd.s10s_relay_on is True
    assert cmd.s11s_relay_on is True
    assert cmd.cv_master_switch_on is False
    assert cmd.effective_mode == "forced_on"
    assert cmd.target_dhw_temp_c == 50.0

    # Same for max_on (60C solar boost: SG4)
    cmd_max = DaikinInterlock.resolve_command(requested_mode="max_on", current_cv_switch_state=True)
    assert cmd_max.s10s_relay_on is True
    assert cmd_max.s11s_relay_on is True
    assert cmd_max.cv_master_switch_on is False
    assert cmd_max.effective_mode == "max_on"
    assert cmd_max.target_dhw_temp_c == 60.0


def test_lossy_mapping_advised_off():
    """Verify that advised_off downgrades to normal and explains downgrade."""
    cmd = DaikinInterlock.resolve_command(requested_mode="advised_off", current_cv_switch_state=True)
    assert cmd.s10s_relay_on is False
    assert cmd.s11s_relay_on is False
    assert cmd.effective_mode == "normal"
    assert cmd.downgrade_reason is not None
    assert "No physical SG" in cmd.downgrade_reason or "SG contacts lack" in cmd.downgrade_reason


def test_daikin_actuator_feedback():
    """Verify actuator tracks effective_mode feedback."""
    executed_switches = {}

    def mock_switch(name: str, state: bool):
        executed_switches[name] = state
        return True

    actuator = DaikinActuator(switch_caller=mock_switch)
    result = actuator.execute_mode("forced_on", current_cv_switch_state=True)

    assert result.success is True
    assert result.requested_mode == "forced_on"
    assert result.effective_mode == "forced_on"
    assert executed_switches["cv_master"] is False
    assert executed_switches["s11s"] is True
    assert executed_switches["s10s"] is True
