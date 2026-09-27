"""
Open HEMS: Hardware Integration Interfaces & Contracts
======================================================
Implements ADR-005, ADR-002 and ADR-003.
Defines pure ABCs and immutable dataclasses for device adapters,
telemetry readers, actuators, and capabilities.

INVARIANT: This module contains ZERO Home Assistant entity strings
and imports nothing from layer3, layer4, or specific driver implementations.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Dict, Any, List, Optional, Type, FrozenSet

from models.canonical import Measurement


class DeviceCapability(str, Enum):
    """Canonical hardware and physical device capabilities."""
    CAN_STORE = "can_store"            # Can store energy (battery, thermal mass)
    CAN_EXPORT = "can_export"          # Can export power to the grid (solar PV, battery)
    CAN_MODULATE = "can_modulate"      # Inverter / compressor can continuously vary power
    CAN_DELAY = "can_delay"            # Operation can be deferred / postponed
    HAS_DEADLINE = "has_deadline"      # Process must complete before a target time
    IS_THERMAL = "is_thermal"          # Involves thermodynamic heating / cooling capacity
    CAN_CURTAIL = "can_curtail"        # Can throttle or shut off production during negative tariffs
    HAS_MODULATION_FLOOR = "has_modulation_floor" # Non-zero minimum continuous operating power (e.g. 950W)
    HAS_HYDRAULIC_INTERLOCK = "has_hydraulic_interlock" # Requires mutual exclusion across hydraulic paths (CV vs SWW)
    HAS_MINIMUM_ACTIVATION_POWER = "has_minimum_activation_power" # Minimum threshold to engage (e.g. EV 6A / 1.4kW)
    AUTONOMOUS_REHEAT = "autonomous_reheat" # Hardware thermostat autonomous reheat capability


@dataclass(frozen=True)
class SourceContext:
    """Read-only context containing raw multi-protocol states and current timestamp."""
    ha_states: Dict[str, Any]
    mqtt_cache: Dict[str, Any]
    now: datetime


@dataclass(frozen=True)
class DeviceCommand:
    """Canonical device command passed to actuators."""
    mode_code: str
    target_power_kw: Optional[float] = None
    parameters: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ActuationResult:
    """Result of an actuation attempt."""
    success: bool
    effective_mode: str
    message: str = ""
    realized_power_kw: Optional[float] = None


@dataclass(frozen=True)
class EffectiveState:
    """Live state reported back by the physical device."""
    effective_mode: str
    realized_power_kw: float
    status_details: Dict[str, Any] = field(default_factory=dict)


class ITelemetryReader(ABC):
    """Abstract base class for reading telemetry from a bound device."""

    @abstractmethod
    def read(self, binding: "DeviceBinding", ctx: SourceContext) -> List[Measurement]:
        """
        Reads canonical measurements from the physical source.
        Returns an empty list if bindings or entities are missing. Never guesses.
        """
        pass


class IDeviceActuator(ABC):
    """Abstract base class for hardware actuation."""

    @abstractmethod
    def execute(self, binding: "DeviceBinding", command: DeviceCommand) -> ActuationResult:
        """Executes a canonical command on the target hardware."""
        pass

    @abstractmethod
    def safe_state(self, binding: "DeviceBinding") -> DeviceCommand:
        """The fallback state the device must assume upon shutdown, deletion, or watchdog trigger."""
        pass

    @abstractmethod
    def read_effective_state(self, binding: "DeviceBinding", ctx: SourceContext) -> EffectiveState:
        """Reads back the true physical mode and power rather than the requested command."""
        pass


class IInterlock(ABC):
    """Abstract base class for hardware-level safety interlocks."""

    @abstractmethod
    def check_interlocks(self, binding: "DeviceBinding", ctx: SourceContext, target_command: DeviceCommand) -> bool:
        """Returns True if the transition is physically and safely allowed."""
        pass


@dataclass(frozen=True)
class AdapterSpec:
    """Immutable specification of an integration driver."""
    slug: str                                   # e.g. "daikin_altherma", "generic_ha_sensor", "home_battery"
    supported_types: FrozenSet[str]             # Device types this adapter supports
    capabilities: FrozenSet[DeviceCapability]   # Capabilities provided by the adapter
    reader: Optional[Type[ITelemetryReader]] = None
    actuator: Optional[Type[IDeviceActuator]] = None
    interlocks: Optional[Type[IInterlock]] = None


@dataclass(frozen=True)
class DeviceBinding:
    """Binds a configured device from site_config.json to an AdapterSpec."""
    device: Dict[str, Any]                      # Raw config dict
    spec: Optional[AdapterSpec]                 # Resolved adapter spec (None if unknown)
    device_id: str
    device_type: str
    is_active: bool                             # installed and enabled
