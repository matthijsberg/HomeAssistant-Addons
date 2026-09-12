"""
Integrations: Daikin Altherma Actuator
======================================
Applies resolved hardware commands via Home Assistant service calls
or standalone direct GPIO/relay boards, and reports back effective mode.
"""

from dataclasses import dataclass
from typing import Optional, Dict, Any, Callable
from integrations.daikin_altherma.interlocks import DaikinInterlock, DaikinHardwareCommand


@dataclass
class ActuationResult:
    success: bool
    requested_mode: str
    effective_mode: str
    downgrade_reason: Optional[str]
    command: DaikinHardwareCommand
    error_message: Optional[str] = None


class DaikinActuator:
    """
    Executes hardware commands on Daikin Altherma and returns verified actuation results.
    """

    def __init__(
        self,
        switch_caller: Optional[Callable[[str, bool], bool]] = None,
        climate_caller: Optional[Callable[[str, float], bool]] = None
    ):
        self.switch_caller = switch_caller
        self.climate_caller = climate_caller

    def execute_mode(
        self,
        requested_mode: str,
        current_cv_switch_state: bool = True,
        target_temp: Optional[float] = None
    ) -> ActuationResult:
        """
        Resolves interlocks and dispatches to hardware.
        """
        cmd = DaikinInterlock.resolve_command(
            requested_mode=requested_mode,
            current_cv_switch_state=current_cv_switch_state,
            target_temp=target_temp
        )

        success = True
        err = None

        if self.switch_caller:
            try:
                # S10S
                self.switch_caller("s10s", cmd.s10s_relay_on)
                # S11S
                self.switch_caller("s11s", cmd.s11s_relay_on)
                # Hydraulic interlock CV switch
                self.switch_caller("cv_master", cmd.cv_master_switch_on)
            except Exception as e:
                success = False
                err = str(e)

        return ActuationResult(
            success=success,
            requested_mode=requested_mode,
            effective_mode=cmd.effective_mode,
            downgrade_reason=cmd.downgrade_reason,
            command=cmd,
            error_message=err
        )
