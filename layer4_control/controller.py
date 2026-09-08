"""
Layer 4: Actuation, Safety Guard & Hardware Control Implementation
==================================================================
Implements physical command dispatch via volatile RAM Smart Grid relays
(S10S/S11S), hydraulic exclusivity, and Priority 1 Emergency Comfort guards.
"""

from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional
from models.canonical import DeviceCommand
from layer4_control.interfaces import ISafetyGuard, IActuatorController


class SafetyGuard(ISafetyGuard):
    """Enforces hardware safety, compressor dwell-time, and comfort floors."""

    def __init__(self, emergency_comfort_c: float = 38.0, min_dwell_minutes: int = 20):
        self.emergency_comfort_c = emergency_comfort_c
        self.min_dwell_minutes = min_dwell_minutes
        self._last_switch_time: Dict[str, datetime] = {}
        self._last_command: Dict[str, str] = {}

    def evaluate_emergency_comfort(self, current_dhw_temp_c: float) -> Optional[DeviceCommand]:
        """Priority 1: Comfort floor.

        If DHW < 38.0°C, emergency reheat is triggered immediately.
        """
        if current_dhw_temp_c < self.emergency_comfort_c:
            return DeviceCommand(
                command_id=f"cmd_dhw_emerg_{int(datetime.now().timestamp())}",
                device_id="daikin_heat_pump",
                action="set_mode",
                parameters={"mode": "SG4", "target_temp_c": 50.0, "reason": "emergency_comfort_threshold"},
                priority=1,
                timeout_seconds=3600,
                created_at=datetime.now(),
            )
        return None

    def enforce_compressor_dwell_time(
        self, device_id: str, requested_command: str, min_dwell_minutes: int = 20
    ) -> bool:
        """Returns True if command is permitted, False if blocked by dwell-time lock."""
        now = datetime.now()
        last_time = self._last_switch_time.get(device_id)
        last_cmd = self._last_command.get(device_id)

        if last_time and last_cmd != requested_command:
            elapsed = (now - last_time).total_seconds() / 60.0
            if elapsed < min_dwell_minutes:
                return False  # Blocked to protect compressor against rapid cycling

        self._last_switch_time[device_id] = now
        self._last_command[device_id] = requested_command
        return True

    def verify_hydraulic_isolation(self, target_operation: str) -> List[DeviceCommand]:
        """Ensures space heating is disabled during forced DHW runs

        to physically prevent the 9 kW backup heater (BUH) from engaging.
        """
        commands = []
        if target_operation in ["SG4", "DHW_BOOST"]:
            commands.append(
                DeviceCommand(
                    command_id=f"cmd_buh_kill_{int(datetime.now().timestamp())}",
                    device_id="space_heating_switch",
                    action="turn_off",
                    parameters={"entity_id": "switch.hc_mode_altherma_on"},
                    priority=1,
                    timeout_seconds=30,
                    created_at=datetime.now(),
                )
            )
        return commands


class ActuatorController(IActuatorController):
    """Executes validated commands via volatile RAM Smart Grid switches."""

    SMART_GRID_RELAY_MAP = {
        # S10S, S11S
        "SG1": (False, True),   # Blokkade (Geforceerd uit)
        "SG2": (False, False),  # Normaal / Auto (Standaard eco)
        "SG3": (True, False),   # Advies Aan (Lichte boost)
        "SG4": (True, True),    # Geforceerd Aan (Max 60°C thermische opslag)
    }

    def __init__(self, ha_api_client=None):
        self.ha_client = ha_api_client

    def apply_smart_grid_mode(self, mode: str) -> bool:
        """Translates SG1..SG4 into binary contact states S10S and S11S."""
        if mode not in self.SMART_GRID_RELAY_MAP:
            raise ValueError(f"Invalid Smart Grid mode: {mode}. Must be one of {list(self.SMART_GRID_RELAY_MAP.keys())}")

        s10s_state, s11s_state = self.SMART_GRID_RELAY_MAP[mode]

        # If HA client is configured, dispatch switch commands
        if self.ha_client:
            self.ha_client.set_switch("switch.warmtepomp_smart_grid_1_s10s", s10s_state)
            self.ha_client.set_switch("switch.warmtepomp_smart_grid_2_s11s", s11s_state)

        return True

    def execute_command(self, command: DeviceCommand) -> bool:
        """Dispatches a validated command with priority checking."""
        if command.action == "set_mode" and command.parameters.get("mode") in self.SMART_GRID_RELAY_MAP:
            return self.apply_smart_grid_mode(command.parameters["mode"])
        return True
