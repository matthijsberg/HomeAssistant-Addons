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
    assert cmd_max.dhw_master_switch_on is True
    assert cmd_max.effective_mode == "max_on"
    assert cmd_max.target_dhw_temp_c == 60.0


def test_hydraulic_interlock_forced_space_heating_dhw_off():
    """Verify that forced space heating engages SG4 with DHW turned OFF so 3-way valve routes to CV."""
    cmd = DaikinInterlock.resolve_command(requested_mode="forced_space_heating", current_cv_switch_state=True)
    assert cmd.s10s_relay_on is True
    assert cmd.s11s_relay_on is True
    assert cmd.cv_master_switch_on is True
    assert cmd.dhw_master_switch_on is False
    assert cmd.effective_mode == "forced_space_heating"


def test_lossy_mapping_advised_off():
    """Verify that advised_off downgrades to normal and explains downgrade."""
    cmd = DaikinInterlock.resolve_command(requested_mode="advised_off", current_cv_switch_state=True)
    assert cmd.s10s_relay_on is False
    assert cmd.s11s_relay_on is False
    assert cmd.effective_mode == "normal"
    assert cmd.downgrade_reason is not None
    assert "No physical SG" in cmd.downgrade_reason or "SG contacts lack" in cmd.downgrade_reason


def test_hydraulic_interlock_cv_master_restored_in_normal():
    """Verify that when DHW finishes and mode transitions to normal, cv_master_switch_on is restored to True."""
    # Even if current_cv_switch_state was False during DHW run, normal mode must restore it to True
    cmd = DaikinInterlock.resolve_command(requested_mode="normal", current_cv_switch_state=False)
    assert cmd.s10s_relay_on is False
    assert cmd.s11s_relay_on is False
    assert cmd.cv_master_switch_on is True
    assert cmd.effective_mode == "normal"


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


def test_daikin_reader_space_heating_enabled():
    """Verify circuit enable multi-entity rule."""
    from integrations.daikin_altherma.reader import DaikinReader

    # Case 1: All on
    states_on = {
        "switch.hc_mode_altherma_on": {"state": "on"},
        "climate.hc_room_room_heating": {"state": "heat"},
        "climate.woonkamer_climate_daikin": {"state": "heat"}
    }
    assert DaikinReader.is_space_heating_circuit_enabled(states_on) is True

    # Case 2: Room heating climate off
    states_circuit_off = {
        "switch.hc_mode_altherma_on": {"state": "on"},
        "climate.hc_room_room_heating": {"state": "off"},
        "climate.woonkamer_climate_daikin": {"state": "heat"}
    }
    assert DaikinReader.is_space_heating_circuit_enabled(states_circuit_off) is False

    # Case 3: Master switch off
    states_master_off = {
        "switch.hc_mode_altherma_on": {"state": "off"},
        "climate.hc_room_room_heating": {"state": "heat"},
        "climate.woonkamer_climate_daikin": {"state": "heat"}
    }
    assert DaikinReader.is_space_heating_circuit_enabled(states_master_off) is False


def test_daikin_reader_dhw_circuit_detection():
    """Verify that DaikinReader correctly extracts DHW master enable/disable status."""
    from integrations.daikin_altherma.reader import DaikinReader

    # Case 1: DHW on
    states_on = {
        "climate.hc_dhw_dhw_setpoint": {"state": "heat"}
    }
    assert DaikinReader.is_dhw_circuit_enabled(states_on) is True

    # Case 2: DHW off (e.g. vacation)
    states_off = {
        "climate.hc_dhw_dhw_setpoint": {"state": "off"}
    }
    assert DaikinReader.is_dhw_circuit_enabled(states_off) is False

    # Case 3: Empty states map defaults to True
    assert DaikinReader.is_dhw_circuit_enabled({}) is True


def test_daikin_actuator_implements_iactuator_controller_contract():
    """
    Verify DaikinActuator conforms to the IActuatorController interface contract.
    Ensures:
    1. isinstance(actuator, IActuatorController) and issubclass
    2. apply_smart_grid_mode(mode) switches relays correctly and returns bool
    3. execute_command(command: DeviceCommand) executes and returns bool
    4. execute_mode maintains 100% backwards-compatibility and returns ActuationResult
    """
    from layer4_control.interfaces import IActuatorController
    from models.canonical import DeviceCommand

    executed_switches = {}
    executed_climates = {}

    def mock_switch(name: str, state: bool):
        executed_switches[name] = state
        return True

    def mock_climate(name: str, temp: float):
        executed_climates[name] = temp
        return True

    actuator = DaikinActuator(switch_caller=mock_switch, climate_caller=mock_climate)

    # 1. Interface conformance
    assert isinstance(actuator, IActuatorController)
    assert issubclass(DaikinActuator, IActuatorController)

    # 2. Test apply_smart_grid_mode directly across modes:
    # SG1 (forced_off / Spitsblok): S10S=False, S11S=True, CV=False
    ok_sg1 = actuator.apply_smart_grid_mode("SG1")
    assert ok_sg1 is True
    assert executed_switches["s10s"] is False
    assert executed_switches["s11s"] is True
    assert executed_switches["cv_master"] is False
    assert actuator.last_actuation_result is not None
    assert actuator.last_actuation_result.effective_mode == "forced_off"

    # SG2 (normal / Eco): S10S=False, S11S=False, CV=True
    ok_sg2 = actuator.apply_smart_grid_mode("SG2", current_cv_switch_state=True)
    assert ok_sg2 is True
    assert executed_switches["s10s"] is False
    assert executed_switches["s11s"] is False
    assert executed_switches["cv_master"] is True
    assert actuator.last_actuation_result.effective_mode == "normal"

    # SG3 (advised_on / Pre-heat): S10S=True, S11S=False, CV=True
    ok_sg3 = actuator.apply_smart_grid_mode("SG3")
    assert ok_sg3 is True
    assert executed_switches["s10s"] is True
    assert executed_switches["s11s"] is False
    assert executed_switches["cv_master"] is True
    assert actuator.last_actuation_result.effective_mode == "advised_on"

    # SG4 (forced_on / DHW run): S10S=True, S11S=True, CV=False (hydraulic interlock!)
    ok_sg4 = actuator.apply_smart_grid_mode("SG4")
    assert ok_sg4 is True
    assert executed_switches["s10s"] is True
    assert executed_switches["s11s"] is True
    assert executed_switches["cv_master"] is False
    assert actuator.last_actuation_result.effective_mode == "forced_on"
    assert executed_climates.get("dhw") == 50.0

    # 3. Test execute_command via canonical DeviceCommand
    cmd = DeviceCommand(
        command_id="cmd_unit_test_01",
        device_id="heat_pump.daikin_altherma",
        action="set_mode",
        parameters={
            "mode": "max_on",
            "current_cv_switch_state": True
        },
        priority=2
    )
    ok_cmd = actuator.execute_command(cmd)
    assert ok_cmd is True
    assert executed_switches["s10s"] is True
    assert executed_switches["s11s"] is True
    assert executed_switches["cv_master"] is False
    assert executed_climates.get("dhw") == 60.0
    assert actuator.last_actuation_result.effective_mode == "max_on"

    # 4. Test backwards-compatible execute_mode
    res = actuator.execute_mode("forced_space_heating", current_cv_switch_state=True)
    assert res.success is True
    assert res.requested_mode == "forced_space_heating"
    assert res.effective_mode == "forced_space_heating"
    assert executed_switches["s10s"] is True
    assert executed_switches["s11s"] is True
    assert executed_switches["cv_master"] is True
