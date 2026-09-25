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
from datetime import datetime, timedelta
from enum import Enum
from typing import Dict, Any, List, Optional, Tuple


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


class PolicyType(str, Enum):
    """The 3 fundamental HEMS policy archetypes."""
    SHIFTABLE_CONSUMER = "shiftable_consumer"  # Type 1: Verbruik zonder opslag (vaatwasser, wasmachine)
    THERMAL_BUFFER = "thermal_buffer"          # Type 2: Buffer zonder teruggave (350L SWW, CV vloerverwarming)
    BATTERY_ARBITRAGE = "battery_arbitrage"    # Type 3: Accu met teruggave & economische dode zone


@dataclass
class ShiftableConsumerPolicy:
    """Policy for shiftable non-storage appliances."""
    policy_id: str
    name: str
    target_device_id: str
    duration_minutes: int
    power_watts: float
    can_interrupt: bool = False
    window_start_hour: int = 8
    window_end_hour: int = 20
    prefer_solar_surplus: bool = True
    min_solar_surplus_watts: float = 1500.0


@dataclass
class ThermalBufferPolicy:
    """Policy for heat pumps and thermal buffers (one-way storage with leakage)."""
    policy_id: str
    name: str
    target_device_id: str
    storage_volume_liters: int = 350
    emergency_threshold_c: float = 38.0       # Hard safety guardrail (overrules all prices)
    deadband_reheat_c: float = 46.0           # Do not reheat if above this without solar
    target_temperature_c: float = 50.0        # Standard economic setpoint
    solar_boost_temperature_c: float = 60.0   # Maximum thermal battery boost
    min_run_time_minutes: int = 20            # Protect compressor against cycling
    morning_peak_lockout: bool = True         # SG1 forced off (07:00 - 08:30)
    evening_peak_lockout: bool = True         # SG1 forced off (17:30 - 20:30)
    isolate_space_heating_during_dhw: bool = True # Cut CV switch to prevent 9kW BUH activation


@dataclass
class BatteryArbitragePolicy:
    """Policy for bidirectional electrical batteries with economic deadband logic."""
    policy_id: str
    name: str
    target_device_id: str
    capacity_kwh: float = 10.0
    roundtrip_efficiency: float = 0.87        # 13% round-trip conversion loss
    lcos_depreciation_eur_kwh: float = 0.0741 # Cell degradation cost per throughput kWh
    min_price_spread_eur_kwh: float = 0.115   # Deadband: Do NOTHING if price delta < €0.115/kWh
    solar_surplus_priority: bool = True       # Charge from free solar before grid arbitrage
    min_soc_pct: float = 10.0                 # Reserve floor
    max_soc_pct: float = 95.0                 # Overcharge ceiling
    peak_shaving_threshold_amps: float = 20.0 # Discharge if grid phase exceeds 20A (3x25A connection)


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


def normalize_power_reading(
    value: Any,
    unit: Optional[str] = None,
    device_cfg: Optional[Dict[str, Any]] = None
) -> Optional[float]:
    """
    Deterministically normalizes any raw power reading from hardware, MQTT, or Home Assistant
    to canonical Watts (W).
    
    Borging Rules:
      1. Returns None for None, 'unavailable', 'unknown', or unparseable inputs.
      2. Device Contract (Priority 1): If device_cfg['native_unit'] == 'kW', strictly multiplies by 1000.0.
         (Crucial: Preserves 5-10W zero-grid import without threshold distortion).
      3. Metadata Unit (Priority 2): If unit_of_measurement == 'kW', multiplies by 1000.0.
      4. Direct Watt (Priority 3): If unit == 'W' or device_cfg['native_unit'] == 'W', returns raw float.
    """
    if value is None:
        return None
    if isinstance(value, str):
        val_clean = value.strip().lower()
        if val_clean in ["unknown", "unavailable", "none", "null", ""]:
            return None

    try:
        v = float(value)
    except (ValueError, TypeError):
        return None

    # 1. Hardware/Device configuration contract (highest priority)
    if device_cfg:
        native_unit = str(device_cfg.get("native_unit", "")).strip().lower()
        if native_unit in ["kw", "kilowatt", "kilo_watt"]:
            return v * 1000.0
        if native_unit in ["w", "watt"]:
            return v

    # 2. Explicit sensor metadata unit
    if unit:
        u_clean = str(unit).strip().lower()
        if u_clean in ["kw", "kilowatt"]:
            return v * 1000.0
        if u_clean in ["w", "watt"]:
            return v

    return v


class StandardizedState(str, Enum):
    """
    Strict 6-state taxonomy for all HEMS devices, timeline bars, legends, and cards.
    """
    FORCED_OFF = "forced_off"   # 1. Geforceerd uit (blok)
    ADVISED_OFF = "advised_off" # 2. Geadviseerd uit
    NORMAL = "normal"           # 3. Normaal
    ADVISED_ON = "advised_on"   # 4. Geadviseerd aan
    FORCED_ON = "forced_on"     # 5. Geforceerd aan (50°C)
    MAX_ON = "max_on"           # 6. Maximaal aan (60°C)


def get_state_metadata(state: Optional[StandardizedState] = None) -> Any:
    """
    Returns presentation metadata dynamically loaded from config/mode_catalog.json.
    Single Source of Truth: Invariant #1.
    """
    from models.mode_catalog import load_mode_catalog, get_mode_meta
    catalog = load_mode_catalog()
    tb = catalog.get("archetypes", {}).get("thermal_buffer", {})
    if state is not None:
        return tb.get(state.value, get_mode_meta(state.value, archetype="thermal_buffer"))

    return {
        st: tb.get(st.value, get_mode_meta(st.value, archetype="thermal_buffer"))
        for st in StandardizedState
    }


def __getattr__(name: str) -> Any:
    """Dynamic resolution for legacy STATE_METADATA access without hardcoded dictionary."""
    if name == "STATE_METADATA":
        return get_state_metadata()
    raise AttributeError(f"module '{__name__}' has no attribute '{name}'")


@dataclass
class DeviceSlotDispatch:
    """
    Canonical per-device dispatch decision within a 15-minute slot (ADR-002, ADR-005).
    """
    device_id: str
    device_type: str
    mode_code: str                     # StandardizedState or archetype mode
    mode_label: str                    # Human-readable mode description
    electric_kw: float                 # Electrical power: + = load/charge, - = discharge/export
    payload: Dict[str, Any] = field(default_factory=dict) # Subsystem specifics (e.g. soc, temp_target)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "DeviceSlotDispatch":
        return cls(
            device_id=data["device_id"],
            device_type=data["device_type"],
            mode_code=data["mode_code"],
            mode_label=data.get("mode_label", ""),
            electric_kw=float(data.get("electric_kw", 0.0)),
            payload=dict(data.get("payload", {})),
        )


@dataclass
class DispatchPlanSlot:
    """A single canonical dispatch slot in the published plan."""
    slot_idx: int
    time_label: str
    dt_iso: str
    price_eur: float
    solar_kw: float
    unallocated_kw: float
    heating_kw: float
    dhw_kw: float
    net_import_kw: float
    mode_code: StandardizedState
    mode_label: str
    color_hex: str = ""
    tailwind_class: str = ""
    description: str = ""
    device_dispatches: Dict[str, DeviceSlotDispatch] = field(default_factory=dict)

    def __post_init__(self):
        if not isinstance(self.mode_code, StandardizedState):
            raw = str(self.mode_code)
            legacy_map = {
                "peak_lockout": StandardizedState.FORCED_OFF,
                "peak_advice": StandardizedState.ADVISED_OFF,
                "forced_standard_50": StandardizedState.FORCED_ON,
                "forced_night_50": StandardizedState.FORCED_ON,
                "forced_solar_boost_60": StandardizedState.MAX_ON,
            }
            if raw in legacy_map:
                self.mode_code = legacy_map[raw]
            else:
                self.mode_code = StandardizedState(raw)

        # Ensure color_hex and tailwind_class are strictly consistent with mode_code (Invariant #1)
        meta = get_state_metadata(self.mode_code)
        if not self.color_hex or self.color_hex != meta.get("color_hex"):
            self.color_hex = meta.get("color_hex", "")
        if not self.tailwind_class or self.tailwind_class != meta.get("tailwind_text"):
            self.tailwind_class = meta.get("tailwind_text", "")


@dataclass
class DHWPlanSummary:
    """Canonical summary of DHW boiler dispatch decision."""
    planned_mode: str
    planned_mode_label: str
    color_hex: str
    tailwind_class: str
    target_temp_c: float
    run_start: str
    run_end: str
    run_duration_min: int
    power_kw: float
    total_stroom_kwh: float
    spits_lockout_hours: float
    dynamic_peaks: List[Dict[str, Any]]
    unheated_trajectory: List[Dict[str, Any]]
    counterfactual_reason: str
    arbitrage_saving_eur: float
    decision_details: Optional[Dict[str, Any]] = None


@dataclass
class SpaceHeatingSlotResult:
    """A single quarter-hour simulation slot for 2R1C space heating dispatch."""
    slot_idx: int
    heating_kw_el: float
    heating_kw_th: float
    cop: float
    room_temp_c: float
    floor_temp_c: float
    heat_loss_kw: float
    mode_code: str
    is_preheat_active: bool
    is_lockout_active: bool
    cost_th_eur_per_kwh: float = 0.0
    outdoor_temp_c: float = 10.0


@dataclass
class SpaceHeatingPlanSummary:
    """Canonical summary of space heating and underfloor buffer dispatch."""
    is_heating_season: bool
    season_status_label: str
    total_heating_kwh_el: float
    total_heating_kwh_th: float
    average_cop: float
    preheat_hours: float
    lockout_hours: float
    min_projected_room_temp_c: float
    max_projected_room_temp_c: float
    slots: List[SpaceHeatingSlotResult]
    target_room_temp_c: float = 20.0
    min_comfort_room_c: float = 19.6
    max_preheat_room_c: float = 21.2
    max_floor_temp_c: float = 28.0
    unheated_room_temps_c: List[float] = field(default_factory=list)
    room_temps_p05_c: List[float] = field(default_factory=list)
    room_temps_p95_c: List[float] = field(default_factory=list)
    unheated_temps_p05_c: List[float] = field(default_factory=list)
    unheated_temps_p95_c: List[float] = field(default_factory=list)


@dataclass
class CanonicalDispatchPlan:
    """The central immutable published dispatch plan for the entire HEMS system."""
    generated_at: str
    horizon_hours: float
    resolution_mins: int
    is_fresh: bool
    freshness_age_seconds: float
    slots: List[DispatchPlanSlot]
    dhw_summary: DHWPlanSummary
    dynamic_peaks: List[Dict[str, Any]]
    schema_version: str = "1.1.0"
    heating_summary: Optional[SpaceHeatingPlanSummary] = None
    validation_issues: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def get_projected_slots(self, is_15m: bool = True) -> List[Dict[str, Any]]:
        """
        Single Source of Truth resolution projection for all downstream consumers.
        Converts 96 quarters to 96 (15m) or 24 (1h) slots with 100% unified domain invariants.
        Guarantees that heating, DHW, solar, unallocated, net_import, mode_code, and overlays
        never disagree between any charts or APIs.
        """
        if is_15m:
            res = []
            for i, s in enumerate(self.slots):
                res.append({
                    "slot_idx": i,
                    "time_label": s.time_label,
                    "dt_iso": s.dt_iso,
                    "price_eur": s.price_eur,
                    "solar_kw": s.solar_kw,
                    "unallocated_kw": s.unallocated_kw,
                    "heating_kw": s.heating_kw,
                    "dhw_kw": s.dhw_kw,
                    "net_import_kw": s.net_import_kw,
                    "mode_code": s.mode_code,
                    "mode_label": s.mode_label,
                    "is_heating": s.heating_kw > 0.02,
                    "is_spitsblok": (s.mode_code == "forced_off"),
                })
            return res
        else:
            # 1-hour canonical downsampling
            n_hours = min(24, len(self.slots) // 4)
            res = []
            for h in range(n_hours):
                idx = h * 4
                chunk = self.slots[idx:idx + 4]
                if not chunk:
                    break
                avg_solar = round(sum(s.solar_kw for s in chunk) / len(chunk), 3)
                avg_unalloc = round(sum(s.unallocated_kw for s in chunk) / len(chunk), 3)
                avg_heat = round(sum(s.heating_kw for s in chunk) / len(chunk), 3)
                avg_dhw = round(sum(s.dhw_kw for s in chunk) / len(chunk), 3)
                avg_net = round(sum(s.net_import_kw for s in chunk) / len(chunk), 3)
                avg_price = round(sum(s.price_eur for s in chunk) / len(chunk), 4)

                # Canonical mode determination for 1h:
                is_spits = any(s.mode_code == "forced_off" for s in chunk)
                if is_spits:
                    mode_c = "forced_off"
                    mode_l = "Geforceerd uit (blok)"
                    # Strict Physical Invariant: Cannot heat during forced lockout
                    avg_heat = 0.0
                elif avg_dhw > 0.05:
                    mode_c = "forced_on"
                    mode_l = "Geforceerd aan (Tapwater)"
                elif avg_heat > 0.05:
                    mode_c = "advised_on"
                    mode_l = "Geadviseerd aan (Verwarmt)"
                else:
                    mode_c = chunk[0].mode_code
                    mode_l = chunk[0].mode_label

                res.append({
                    "slot_idx": h,
                    "time_label": chunk[0].time_label,
                    "dt_iso": chunk[0].dt_iso,
                    "price_eur": avg_price,
                    "solar_kw": avg_solar,
                    "unallocated_kw": avg_unalloc,
                    "heating_kw": avg_heat,
                    "dhw_kw": avg_dhw,
                    "net_import_kw": avg_net,
                    "mode_code": mode_c,
                    "mode_label": mode_l,
                    "is_heating": (avg_heat > 0.02),
                    "is_spitsblok": is_spits,
                })
            return res

