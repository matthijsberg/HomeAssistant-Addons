"""
Canonical Data Model for Home Energy Management System (HEMS)
============================================================
Defines the core domain primitives, measurements, forecasts, device capabilities,
and dispatch commands across all energy vectors and flows.

Principles:
  - Vector & Flow are first-class primitives.
  - Devices are bidirectional resources with explicit capabilities.
  - Decoupled from transport protocols (Modbus, P1/DSMR, MQTT, REST, SunSpec).
"""

from dataclasses import dataclass, field, asdict
from datetime import datetime
from enum import Enum
from typing import Dict, Any, List, Optional


class Vector(str, Enum):
    """Energy vector classification."""
    ELECTRICITY = "electricity"
    HEAT = "heat"
    GAS = "gas"
    WATER = "water"


class Flow(str, Enum):
    """Directional flow of energy."""
    IMPORT = "import"                  # Energy imported from external grid
    EXPORT = "export"                  # Energy fed back into external grid
    PRODUCTION = "production"          # On-site generation (e.g. PV solar)
    CONSUMPTION = "consumption"        # Energy consumed by appliances/building
    STORAGE_CHARGE = "storage_charge"  # Energy entering storage (battery, thermal buffer)
    STORAGE_DISCHARGE = "storage_discharge" # Energy leaving storage


class DeviceType(str, Enum):
    """Classification of physical or logical energy devices."""
    GRID_METER = "grid_meter"          # P1 DSMR / smart meter
    SOLAR_INVERTER = "solar_inverter"  # SolarEdge, Enphase, SMA
    HEAT_PUMP = "heat_pump"            # Daikin Altherma 3 H HT
    DHW_BOILER = "dhw_boiler"          # 350L SWW Tank (thermal storage)
    HOME_BATTERY = "home_battery"      # Deye SUN-10K + 48V LFP
    EV_CHARGER = "ev_charger"          # Future OCPP / Wallbox
    WEATHER_STATION = "weather_station" # Wittboy GW2000A / Open-Meteo
    BUILDING_ZONE = "building_zone"    # Living room, floor heating


class DeviceCapability(str, Enum):
    """Functional capabilities supported by a device adapter."""
    READ_POWER = "read_power"                # Real-time power measurement (W)
    READ_ENERGY = "read_energy"              # Cumulative energy counter (kWh)
    READ_TEMPERATURE = "read_temperature"    # Temperature sensors (°C)
    READ_STATE_OF_CHARGE = "read_soc"        # Battery or tank storage level (%)
    SET_POWER_LIMIT = "set_power_limit"      # Set continuous power ceiling/floor (W)
    SET_MODE = "set_mode"                    # Set discrete mode (e.g. SG1, SG2, SG3, SG4)
    PAUSE_RESUME = "pause_resume"            # Temporarily pause/resume operation
    CURTAIL_PRODUCTION = "curtail_production"# Dynamically curtail solar/inverter


class Quality(str, Enum):
    """Data quality and provenance flags."""
    GOOD = "good"                            # Live verified hardware reading
    ESTIMATED = "estimated"                  # Model calculation / regression output
    FORECAST = "forecast"                    # External forecast service
    STALE = "stale"                          # Cached data past nominal freshness TTL
    INTERPOLATED = "interpolated"            # Gap-filled data point
    EXCLUDED = "excluded"                    # Flagged as unreliable / maintenance window


@dataclass
class Measurement:
    """
    Canonical atomic telemetry measurement across any bus/protocol.
    """
    device_id: str
    vector: Vector
    flow: Flow
    value: float
    unit: str
    timestamp: datetime
    quality: Quality = Quality.GOOD
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["vector"] = self.vector.value
        d["flow"] = self.flow.value
        d["quality"] = self.quality.value
        d["timestamp"] = self.timestamp.isoformat()
        return d

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Measurement":
        return cls(
            device_id=data["device_id"],
            vector=Vector(data["vector"]),
            flow=Flow(data["flow"]),
            value=float(data["value"]),
            unit=data["unit"],
            timestamp=datetime.fromisoformat(data["timestamp"]),
            quality=Quality(data.get("quality", "good")),
            metadata=data.get("metadata", {})
        )


@dataclass
class DeviceState:
    """
    Bidirectional operational state of an energy resource.
    """
    device_id: str
    device_type: DeviceType
    capabilities: List[DeviceCapability]
    online: bool
    active_mode: str
    current_power_w: float
    setpoint_w: Optional[float] = None
    state_of_charge_pct: Optional[float] = None
    last_seen: datetime = field(default_factory=datetime.now)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "device_id": self.device_id,
            "device_type": self.device_type.value,
            "capabilities": [c.value for c in self.capabilities],
            "online": self.online,
            "active_mode": self.active_mode,
            "current_power_w": self.current_power_w,
            "setpoint_w": self.setpoint_w,
            "state_of_charge_pct": self.state_of_charge_pct,
            "last_seen": self.last_seen.isoformat(),
            "metadata": self.metadata
        }


@dataclass
class ForecastPoint:
    """
    Consolidated hourly or quarter-hourly forward projection.
    """
    timestamp: datetime
    solar_production_kw: float
    import_price_eur_kwh: float
    export_price_eur_kwh: float
    outdoor_temp_c: float
    space_heating_thermal_kw: float
    dhw_thermal_kw: float
    baseload_electric_kw: float
    quality: Quality = Quality.FORECAST

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["quality"] = self.quality.value
        d["timestamp"] = self.timestamp.isoformat()
        return d


@dataclass
class ScheduleSlot:
    """
    Discrete operational dispatch slot generated by the Planner/Optimizer.
    """
    timestamp: datetime
    slot_hour: int
    solar_kw: float
    baseload_kw: float
    boiler_kw: float
    cv_heating_kw: float
    battery_charge_kw: float       # Positive = charging, Negative = discharging
    grid_import_kw: float
    grid_export_kw: float
    heatpump_sg_mode: str          # SG1, SG2, SG3, SG4
    price_eur_kwh: float
    estimated_cost_eur: float

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["timestamp"] = self.timestamp.isoformat()
        return d


@dataclass
class DeviceCommand:
    """
    Command dispatched by the Controller to an actuator adapter.
    Enforces single-writer safety, explicit timeouts, and priority arbitration.
    """
    command_id: str
    device_id: str
    action: str                    # e.g., "set_mode", "set_power_limit", "pause"
    parameters: Dict[str, Any]
    priority: int                  # 1 = Safety/Emergency, 2 = Comfort/Constraint, 3 = Economic Optimization
    timeout_seconds: int = 30
    created_at: datetime = field(default_factory=datetime.now)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "command_id": self.command_id,
            "device_id": self.device_id,
            "action": self.action,
            "parameters": self.parameters,
            "priority": self.priority,
            "timeout_seconds": self.timeout_seconds,
            "created_at": self.created_at.isoformat()
        }
