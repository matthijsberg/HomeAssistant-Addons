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

import math

def calc_percentile(data: List[float], p: float) -> float:
    """Calculates percentile from list of floats (pure python, deterministic)."""
    if not data:
        return 0.0
    s = sorted(data)
    k = (len(s) - 1) * (p / 100.0)
    f = math.floor(k)
    c = math.ceil(k)
    if f == c:
        return float(s[int(k)])
    return float(s[int(f)] * (c - k) + s[int(c)] * (k - f))


def detect_dynamic_price_peaks(
    timeline_items: List[Dict[str, Any]],
    step_mins: int = 15,
    max_lockout_mins: int = 150,
    past_continuous_lockout_mins: int = 0,
    mins_since_last_lockout: int = 999
) -> Tuple[List[Dict[str, Any]], Dict[int, Dict[str, Any]]]:
    """
    Verfijnde Dynamische Spitsdetector op kwartierbasis met Winter Comfort Safeguard:
      1. Micro-piek filter: Negeert rimpels korter dan 30 min (filtert pendelstops weg).
      2. Piek-Kam Prominentie: Een continue harde blokkade wordt gecapt op maximaal 150 min (2,5 uur)
         op de absolute top van de prijsgolf om afkoeling van de vloer/woning in de winter te voorkomen.
      3. Flank-degradatie: Schouder-uren buiten de 2,5u top-kam worden 'Economisch Blokadvies' (⚠️),
         waarin de warmtepomp op minimale modulatie (950W) mag doorpruttelen indien nodig.
      4. Strikte Spitsuren Afbakening: Alleen echte Ochtendspits (06:00-10:00) en Avondspits (17:00-21:00)
         mogen een harde blokkade vormen. Nachtelijke uren (21:00-06:00) worden nooit hard geblokkeerd.
      5. Historie- en Dwell-bewust: Houdt rekening met reeds verstreken blokkadeduren en dwingt minimaal
         120 min hersteltijd af tussen twee opeenvolgende blokkades.
    """
    if not timeline_items:
        return [], {}

    prices = [float(it.get("price", 0.0)) for it in timeline_items]
    p_med = calc_percentile(prices, 50)
    p75 = calc_percentile(prices, 75)
    p85 = calc_percentile(prices, 85)

    # 1. Kandidaat slots: moet significant boven mediaan liggen en in hoogste kwartiel
    is_cand = []
    for it in timeline_items:
        p = float(it.get("price", 0.0))
        cand = (p >= p75) and ((p - p_med) >= 0.035)
        is_cand.append(cand)

    # 2. Overbrug 1-slot dipjes binnen een bredere piek
    n = len(is_cand)
    bridged = list(is_cand)
    p70 = calc_percentile(prices, 70)
    for i in range(1, n - 1):
        if not bridged[i] and bridged[i-1] and bridged[i+1]:
            if float(timeline_items[i].get("price", 0.0)) >= p70:
                bridged[i] = True

    # 3. Cluster aaneengesloten pieken
    raw_events = []
    in_event = False
    start_idx = 0
    for i in range(n):
        if bridged[i] and not in_event:
            in_event = True
            start_idx = i
        elif not bridged[i] and in_event:
            in_event = False
            raw_events.append((start_idx, i - 1))
    if in_event:
        raw_events.append((start_idx, n - 1))

    # 3b. Macro-clustering: overbrug korte rimpels/gaten (gap <= 4 slots / 60 min) binnen hetzelfde spitsvenster
    # Voorkomt gefragmenteerde "uit / aan / uit" cycli en garandeert één coherente spitsblokkade per dagdeel.
    events = []
    for ev in raw_events:
        if not events:
            events.append(ev)
        else:
            prev_start, prev_end = events[-1]
            cur_start, cur_end = ev
            gap = cur_start - prev_end - 1
            h_prev = timeline_items[prev_start]["dt"].hour
            h_cur = timeline_items[cur_start]["dt"].hour
            same_window = (
                ((6 <= h_prev < 11) and (6 <= h_cur < 11)) or
                ((16 <= h_prev < 22) and (16 <= h_cur < 22))
            )
            if gap <= 4 and same_window:
                events[-1] = (prev_start, cur_end)
            else:
                events.append(ev)

    # 4. Formuleer Peak Events met Duur-Cap en Crest Focus
    peak_objects = []
    slot_lockout_map = {}
    last_hard_end_slot = -999
    if past_continuous_lockout_mins > 0:
        last_hard_end_slot = -1
    elif mins_since_last_lockout < 120:
        last_hard_end_slot = -int(mins_since_last_lockout / step_mins)

    for s_idx, e_idx in events:
        cluster_len = e_idx - s_idx + 1
        dur_mins = cluster_len * step_mins

        # Micro-peak filter: negeer pieken korter dan 30 min (2 kwartieren)
        if dur_mins < 30:
            continue

        cluster_prices = [float(timeline_items[k].get("price", 0.0)) for k in range(s_idx, e_idx + 1)]
        max_p = max(cluster_prices)
        avg_p = sum(cluster_prices) / cluster_len
        dt_start = timeline_items[s_idx]["dt"]
        dt_end = timeline_items[e_idx]["dt"] + timedelta(minutes=step_mins)

        h = dt_start.hour
        # Strikte spitsuren: Ochtendspits (06:00-10:00) en Avondspits (17:00-21:00).
        # Buiten deze vensters (21:00-06:00 en 10:00-17:00) NOOIT harde blokkade!
        is_spits_window = (6 <= h < 10) or (17 <= h < 21)
        name = "Ochtendspits" if 6 <= h < 11 else ("Middagpiek" if 11 <= h < 17 else ("Avondspits" if 17 <= h < 21 else "Nachttarief"))

        # Bepaal of deze piek een harde blokkade rechtvaardigt
        is_hard_cluster = (max_p >= p85) and ((max_p - p_med) >= 0.050) and is_spits_window

        # Dwell time safeguard: als de vorige blokkade korter dan 120 minuten geleden eindigde,
        # mag er niet direct opnieuw een harde blokkade starten (afkoelbeveiliging)
        if s_idx == 0 and mins_since_last_lockout < 120 and past_continuous_lockout_mins == 0:
            is_hard_cluster = False

        # Cumulatieve duur-cap met verleden: als dit cluster aansluit op een al lopende blokkade
        effective_max_mins = max_lockout_mins
        if s_idx == 0 and past_continuous_lockout_mins > 0:
            effective_max_mins = max(0, max_lockout_mins - past_continuous_lockout_mins)
            if effective_max_mins < 30:  # minder dan 30 min resterend = direct opheffen
                is_hard_cluster = False

        max_slots_cap = max(0, effective_max_mins // step_mins)

        # Vind de top-kam binnen het cluster
        if is_hard_cluster and max_slots_cap > 0 and cluster_len > max_slots_cap:
            best_sub_start = s_idx
            best_sub_avg = -1.0
            for w_start in range(s_idx, e_idx - max_slots_cap + 2):
                w_end = w_start + max_slots_cap
                sub_avg = sum(float(timeline_items[k].get("price", 0.0)) for k in range(w_start, w_end)) / max_slots_cap
                if sub_avg > best_sub_avg:
                    best_sub_avg = sub_avg
                    best_sub_start = w_start
            hard_start_idx = best_sub_start
            hard_end_idx = best_sub_start + max_slots_cap - 1
        else:
            hard_start_idx = s_idx if (is_hard_cluster and max_slots_cap > 0) else -1
            hard_end_idx = e_idx if (is_hard_cluster and max_slots_cap > 0) else -1

        # Inter-event dwell time safeguard: dwing minimaal 120 min hersteltijd af tussen harde blokkades
        if is_hard_cluster and hard_start_idx >= 0:
            if (hard_start_idx - last_hard_end_slot) * step_mins < 120:
                is_hard_cluster = False
                hard_start_idx = -1
                hard_end_idx = -1
            else:
                last_hard_end_slot = hard_end_idx

        h_start_lbl = timeline_items[hard_start_idx]["dt"].strftime("%H:%M") if hard_start_idx >= 0 else None
        h_end_lbl = (timeline_items[hard_end_idx]["dt"] + timedelta(minutes=step_mins)).strftime("%H:%M") if hard_end_idx >= 0 else None
        h_dur_mins = (hard_end_idx - hard_start_idx + 1) * step_mins if hard_start_idx >= 0 else 0

        peak_obj = {
            "start_idx": s_idx,
            "end_idx": e_idx,
            "start_time": dt_start.strftime("%H:%M"),
            "end_time": dt_end.strftime("%H:%M"),
            "duration_mins": dur_mins,
            "slots_count": cluster_len,
            "name": name,
            "severity": "HARD_LOCKOUT" if (is_hard_cluster and hard_start_idx >= 0) else "SOFT_ADVICE",
            "severity_label": "Harde Spitsblokkade 🔒" if (is_hard_cluster and hard_start_idx >= 0) else "Economisch Blokadvies ⚠️",
            "is_hard_lockout": (is_hard_cluster and hard_start_idx >= 0),
            "hard_start_time": h_start_lbl,
            "hard_end_time": h_end_lbl,
            "hard_duration_mins": h_dur_mins,
            "max_price": round(max_p, 4),
            "avg_price": round(avg_p, 4),
            "delta_median": round(max_p - p_med, 4)
        }
        peak_objects.append(peak_obj)

        for k in range(s_idx, e_idx + 1):
            is_hard = (hard_start_idx <= k <= hard_end_idx and hard_start_idx >= 0)
            slot_entry = dict(peak_obj)
            slot_entry["is_hard_lockout"] = is_hard
            slot_entry["severity"] = "HARD_LOCKOUT" if is_hard else "SOFT_ADVICE"
            slot_entry["severity_label"] = "Harde Spitsblokkade 🔒" if is_hard else "Economisch Blokadvies ⚠️"
            slot_entry["peak_obj"] = peak_obj
            slot_lockout_map[k] = slot_entry

    return peak_objects, slot_lockout_map


class StandardizedState(str, Enum):
    """
    Strict 6-state taxonomy for all HEMS devices, timeline bars, legends, and cards.
    """
    FORCED_OFF = "forced_off"   # 1. Geforceerd uit (blok) - Rood (#EF4444)
    ADVISED_OFF = "advised_off" # 2. Geadviseerd uit - Oranje (#F59E0B)
    NORMAL = "normal"           # 3. Normaal - Grijs (#1E293B)
    ADVISED_ON = "advised_on"   # 4. Geadviseerd aan - Lichtgroen gestreept (#4ADE80)
    FORCED_ON = "forced_on"     # 5. Geforceerd aan (50°C) - Groen (#10B981)
    MAX_ON = "max_on"           # 6. Maximaal aan (60°C) - Paars (#A855F7)


STATE_METADATA = {
    StandardizedState.FORCED_OFF: {
        "code": "forced_off",
        "label": "Geforceerd uit (blok)",
        "color_hex": "#EF4444",
        "tailwind_text": "text-red-400",
        "description": "Harde spitsvergrendeling: compressor SG4 vergrendeld tegen piektarieven."
    },
    StandardizedState.ADVISED_OFF: {
        "code": "advised_off",
        "label": "Geadviseerd uit",
        "color_hex": "#F59E0B",
        "tailwind_text": "text-amber-400",
        "description": "Schouderpiek / verhoogd tarief: uitstel van grote verbruikers aanbevolen."
    },
    StandardizedState.NORMAL: {
        "code": "normal",
        "label": "Normaal",
        "color_hex": "#1E293B",
        "tailwind_text": "text-slate-400",
        "description": "Vrijloopvenster: warmtepomp en boiler in normale werking."
    },
    StandardizedState.ADVISED_ON: {
        "code": "advised_on",
        "label": "Geadviseerd aan",
        "color_hex": "#4ADE80",
        "tailwind_text": "text-emerald-300",
        "description": "Voordelig venster: warmtepomp mag hoger doorverwarmen voor CV vloerbuffer."
    },
    StandardizedState.FORCED_ON: {
        "code": "forced_on",
        "label": "Geforceerd aan",
        "color_hex": "#10B981",
        "tailwind_text": "text-emerald-400",
        "description": "Verwarmen naar setpoint (50°C): actieve stookrun voor boiler of CV."
    },
    StandardizedState.MAX_ON: {
        "code": "max_on",
        "label": "Maximaal aan (60°C)",
        "color_hex": "#A855F7",
        "tailwind_text": "text-purple-300",
        "description": "Zonnebuffer doorverwarming naar 60°C bij hoge zonne-overschotten."
    }
}


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
    mode_code: str
    mode_label: str
    color_hex: str
    tailwind_class: str
    description: str


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
    heating_summary: Optional[SpaceHeatingPlanSummary] = None
    validation_issues: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

