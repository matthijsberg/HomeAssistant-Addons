"""
Integrations: Daikin Altherma Actuator
======================================
Applies resolved hardware commands via Home Assistant service calls
or standalone direct GPIO/relay boards, and reports back effective mode.
Implements layer4_control.interfaces.IActuatorController contract.
"""

import time
from dataclasses import dataclass
from typing import Optional, Dict, Any, Callable
from layer4_control.interfaces import IActuatorController
from models.canonical import DeviceCommand
from integrations.daikin_altherma.interlocks import DaikinInterlock, DaikinHardwareCommand


@dataclass
class ActuationResult:
    success: bool
    requested_mode: str
    effective_mode: str
    downgrade_reason: Optional[str]
    command: DaikinHardwareCommand
    error_message: Optional[str] = None


class DaikinActuator(IActuatorController):
    """
    Executes hardware commands on Daikin Altherma and returns verified actuation results.
    Conforms to IActuatorController contract for modularity and non-HA/multi-device deployments.
    """

    SG_MODE_MAPPING = {
        "sg1": "forced_off",
        "sg2": "normal",
        "sg3": "advised_on",
        "sg4": "forced_on",
        "1": "forced_off",
        "2": "normal",
        "3": "advised_on",
        "4": "forced_on",
    }

    def __init__(
        self,
        switch_caller: Optional[Callable[[str, bool], bool]] = None,
        climate_caller: Optional[Callable[[str, float], bool]] = None,
        device_id: str = "heat_pump.daikin_altherma"
    ):
        self.switch_caller = switch_caller
        self.climate_caller = climate_caller
        self.device_id = device_id
        self._last_actuation_result: Optional[ActuationResult] = None

    @property
    def last_actuation_result(self) -> Optional[ActuationResult]:
        """Returns the most recent ActuationResult."""
        return self._last_actuation_result

    def apply_smart_grid_mode(
        self,
        mode: str,
        current_cv_switch_state: bool = True,
        target_temp: Optional[float] = None,
        current_continuous_lockout_mins: float = 0.0
    ) -> bool:
        """
        Switches physical relays S10S and S11S (SG1..SG4) and hydraulic switches.
        Conforms to IActuatorController.apply_smart_grid_mode.
        Evaluates interlocks strictly in volatile RAM (0 EEPROM wear).
        """
        normalized_mode = self._normalize_sg_mode(mode)
        cmd = DaikinInterlock.resolve_command(
            requested_mode=normalized_mode,
            current_cv_switch_state=current_cv_switch_state,
            target_temp=target_temp,
            current_continuous_lockout_mins=current_continuous_lockout_mins
        )

        success = True
        err = None

        if self.switch_caller:
            try:
                # S10S
                r1 = self.switch_caller("s10s", cmd.s10s_relay_on)
                # S11S
                r2 = self.switch_caller("s11s", cmd.s11s_relay_on)
                # Hydraulic interlock CV switch
                r3 = self.switch_caller("cv_master", cmd.cv_master_switch_on)
                if r1 is False or r2 is False or r3 is False:
                    success = False
                    err = "One or more relay switch calls returned False"
                if self.climate_caller:
                    if cmd.target_dhw_temp_c is not None:
                        self.climate_caller("dhw", cmd.target_dhw_temp_c)
                    elif cmd.target_room_temp_c is not None:
                        self.climate_caller("room", cmd.target_room_temp_c)
            except Exception as e:
                success = False
                err = str(e)

        self._last_actuation_result = ActuationResult(
            success=success,
            requested_mode=normalized_mode,
            effective_mode=cmd.effective_mode,
            downgrade_reason=cmd.downgrade_reason,
            command=cmd,
            error_message=err
        )
        return success

    def execute_command(self, command: DeviceCommand) -> bool:
        """
        Dispatches a validated DeviceCommand to hardware.
        Conforms to IActuatorController.execute_command.
        """
        if not isinstance(command, DeviceCommand):
            raise TypeError(f"Expected DeviceCommand, got {type(command).__name__}")

        params = command.parameters or {}
        requested_mode = (
            params.get("mode")
            or params.get("requested_mode")
            or params.get("smart_grid_mode")
            or command.action
        )
        current_cv_switch_state = bool(params.get("current_cv_switch_state", True))
        target_temp = params.get("target_temp")
        current_continuous_lockout_mins = float(params.get("current_continuous_lockout_mins", 0.0) or 0.0)

        return self.apply_smart_grid_mode(
            mode=str(requested_mode),
            current_cv_switch_state=current_cv_switch_state,
            target_temp=target_temp,
            current_continuous_lockout_mins=current_continuous_lockout_mins
        )

    def execute_mode(
        self,
        requested_mode: str,
        current_cv_switch_state: bool = True,
        target_temp: Optional[float] = None,
        current_continuous_lockout_mins: float = 0.0
    ) -> ActuationResult:
        """
        Backwards-compatible wrapper that constructs a canonical DeviceCommand,
        dispatches via execute_command(), and returns detailed ActuationResult.
        """
        cmd = DeviceCommand(
            command_id=f"cmd_daikin_{int(time.time() * 1000)}",
            device_id=self.device_id,
            action="set_mode",
            parameters={
                "mode": requested_mode,
                "requested_mode": requested_mode,
                "current_cv_switch_state": current_cv_switch_state,
                "target_temp": target_temp,
                "current_continuous_lockout_mins": current_continuous_lockout_mins,
            },
            priority=2
        )
        self.execute_command(cmd)

        if self._last_actuation_result is None:
            resolved = DaikinInterlock.resolve_command(
                requested_mode=requested_mode,
                current_cv_switch_state=current_cv_switch_state,
                target_temp=target_temp,
                current_continuous_lockout_mins=current_continuous_lockout_mins
            )
            return ActuationResult(
                success=False,
                requested_mode=requested_mode,
                effective_mode=resolved.effective_mode,
                downgrade_reason=resolved.downgrade_reason,
                command=resolved,
                error_message="Actuation result not recorded"
            )
        return self._last_actuation_result

    @classmethod
    def _normalize_sg_mode(cls, mode: str) -> str:
        """Normalizes SG1..SG4 / numeric modes to canonical Daikin mode strings."""
        cleaned = str(mode).strip().lower()
        return cls.SG_MODE_MAPPING.get(cleaned, mode)
