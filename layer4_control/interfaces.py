"""
Layer 4: Actuation, Safety Guard & Hardware Control Interfaces
==============================================================
Defines the contracts for safely translating dispatch schedules into physical
actuator commands, enforcing compressor protection, hydraulic isolation,
and comfort safety floors.
"""

from abc import ABC, abstractmethod
from typing import Dict, Any, List, Optional
from models.canonical import DeviceCommand


class ISafetyGuard(ABC):
    """Abstract interface for hardware safety and comfort guardrails."""

    @abstractmethod
    def evaluate_emergency_comfort(self, current_dhw_temp_c: float) -> Optional[DeviceCommand]:
        """Enforces Priority 1 Emergency Comfort if DHW tank < 38.0°C.

        Overrules all spot prices, tariffs, and peak lockouts.
        """
        pass

    @abstractmethod
    def enforce_compressor_dwell_time(
        self, device_id: str, requested_command: str, min_dwell_minutes: int = 20
    ) -> bool:
        """Protects compressor against rapid cycling."""
        pass

    @abstractmethod
    def verify_hydraulic_isolation(self, target_operation: str) -> List[DeviceCommand]:
        """Enforces disabling space heating switch during forced DHW runs

        to prevent the 9 kW electrical backup heater (BUH) from activating.
        """
        pass


class IActuatorController(ABC):
    """
    Device-specific interface for Daikin Smart Grid relay actuation.
    NOTE: This is a specialized device controller contract. The generalized,
    device-agnostic hardware actuation contract for Open HEMS is IDeviceActuator
    defined in `integrations.interfaces` (ADR-005).
    """

    @abstractmethod
    def apply_smart_grid_mode(self, mode: str) -> bool:
        """Switches physical relays S10S and S11S (SG1..SG4).

        Evaluated strictly in volatile RAM (0 EEPROM wear).
        """
        pass

    @abstractmethod
    def execute_command(self, command: DeviceCommand) -> bool:
        """Dispatches a validated command to hardware with timeout and acknowledgment."""
        pass
