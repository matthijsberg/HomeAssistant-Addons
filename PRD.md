# Open HEMS — Product Requirements Document (PRD)
# Generic Residential Home Energy Management System

**Document Version:** 2.0.0-PROPOSAL  
**Status:** Approved Architecture Draft  
**Scope:** Core Engine & Device-Agnostic Energy Management Platform  
**Target License:** Open Source (MIT)  

---

## 1. Vision & Core Principles

Open HEMS is a modular, device-agnostic, open-source Home Energy Management System. It coordinates thermal storage, space heating, solar production, dynamic electricity tariffs, and battery storage to minimize operational energy cost and carbon footprint while guaranteeing occupant comfort and equipment longevity.

### 1.1 The Four Invariant Principles
1. **Core Autonomy (Host & Ecosystem Independent):**  
   Open HEMS is a standalone daemon. It runs natively on Linux, in Docker, or as a Home Assistant Add-on. Home Assistant is **strictly an adapter** (one of many possible telemetry providers and actuation targets). If Home Assistant is restarted, upgraded, or absent, Open HEMS continues unhindered via native Modbus TCP, P1/DSMR, or MQTT connections.
2. **Device-Agnostic Dispatch Contract (Single Source of Truth):**  
   The planning engine computes a single, canonical, versioned dispatch plan (`CanonicalDispatchPlan`). Every dashboard, Lovelace card, actuator, and notification consumer reads from this central plan. View layers are **strictly presentation-only** (dumb views): they never calculate power, aggregate wattages, or invent state colors.
3. **Strict Data Integrity (Zero Mock Data):**  
   Production decisions, analytics, and commands are backed by real, verified telemetry. If data is stale, missing, or corrupt, the system flags it explicitly (`Quality.STALE`, `Quality.INTERPOLATED`) and falls back to deterministic safety profiles. Fabricated or simulated data is forbidden in production runtime.
4. **Separation of Core Logic from Site Profile:**  
   The core engine contains **zero hardware-specific entity IDs, geographic coordinates, brand names, or tariff constants**. All site-specific parameters are loaded declaratively from a validated `site_config.yaml` / `site_config.json`.

---

## 2. System Architecture: The 5-Layer Pipeline

Open HEMS follows a strict, unidirectional data pipeline:

```
┌────────────────────────────────────────────────────────────────────────┐
│ EXTERNAL PROTOCOLS & INTEGRATIONS                                      │
│ Modbus TCP │ P1 DSMR Serial/MQTT │ EnergyZero API │ Open-Meteo │ HA WS │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │
                                    ▼
┌────────────────────────────────────────────────────────────────────────┐
│ LAYER 1: INGESTION & HARDWARE ADAPTERS (layer1_data_collection)        │
│ Protocol-specific collectors emit raw, unvalidated telemetry streams.  │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │
                                    ▼
┌────────────────────────────────────────────────────────────────────────┐
│ LAYER 1B: TELEMETRY SANITIZER & QUALITY ASSURANCE (sanitizer.py)       │
│ - Freshness watchdog (< 15 min TTL)                                    │
│ - Universal 15-minute grid alignment from 'Now'                        │
│ - Physical boundary clamping (rejection of impossible readings)        │
│ - Emits a validated, contractual CleanTelemetryFrame                   │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │
                                    ▼
┌────────────────────────────────────────────────────────────────────────┐
│ LAYER 2: SELF-LEARNING & BEHAVIORAL CALIBRATION (layer2_calibration)   │
│ - 7×96 baseline quarterly load profiles (unallocated household demand) │
│ - EWMA residual tracking for adaptive drift correction                 │
│ - Local microclimate sensor assimilation (e.g. POA irradiance nudging) │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │
                                    ▼
┌────────────────────────────────────────────────────────────────────────┐
│ LAYER 3: POLICY-BASED OPTIMIZATION & SOLVER (layer3_scheduling)        │
│ - Peak shaving & dynamic lockout detector (with winter comfort cap)    │
│ - Multi-device policy waterfall (Shiftable, Thermal, Battery, EV)      │
│ - Roadmap: Mixed-Integer Linear Programming (MILP) solver              │
│ - Produces a frozen, immutable CanonicalDispatchPlan                   │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │
                                    ▼
┌────────────────────────────────────────────────────────────────────────┐
│ LAYER 3B: PLAN STORE & RECOMMENDATION REGISTRY (plan_store.py)         │
│ - Thread-safe in-memory singleton + persistent disk snapshot           │
│ - Historical publication logging in InfluxDB (hems_recommendations)    │
│ - Single Source of Truth for all actuators and presentation consumers  │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │
         ┌──────────────────────────┴──────────────────────────┐
         ▼                                                     ▼
┌────────────────────────────────┐   ┌───────────────────────────────────┐
│ LAYER 4: ACTUATION & CONTROL   │   │ LAYER 5: DUMB PRESENTATION        │
│ (layer4_control)               │   │ (HTTP API, Web Cockpit, Lovelace) │
│ - Translates dispatch plan to  │   │ - Renders plan tokens and series  │
│   device-specific commands     │   │ - Zero math, zero color logic     │
│ - Standalone Modbus/GPIO       │   │ - Direct JSON delivery            │
│ - HA Service Actuator plugin   │   │                                   │
│ - Fail-safe fallback           │   │                                   │
└────────────────────────────────┘   └───────────────────────────────────┘
```

---

## 3. Generic Data Model & Schema Contracts

### 3.1 Device Taxonomy & Archetypes
Every physical device belongs to one of three universal energy archetypes:
1. **`ShiftableConsumer` (Type 1):** Energy consumption that can be delayed in time, but cannot store or return energy (e.g., washing machine, dishwasher, pool pump).
2. **`ThermalBuffer` (Type 2):** Energy converted into heat/cold and stored in a thermal mass (e.g., DHW cylinder, floor screed, chilled water buffer). Cannot return electricity to the grid.
3. **`BatteryStorage` (Type 3):** Bidirectional electrical storage (e.g., 48V LFP home battery, bidirectional EV). Can charge from PV or grid, and discharge to supply the house or arbitrage to grid.

### 3.2 Canonical State Taxonomy
Instead of coupling states to a single manufacturer's relay inputs (such as Daikin SG), states are defined per functional archetype:

#### A. Thermal Buffers & Heat Pumps
* `FORCED_OFF` (Hard Lockout): Compressor or heating element locked against high prices.
* `ADVISED_OFF` (Soft Restraint): Elevated price flank; maintain lowest baseline modulation floor.
* `NORMAL` (Standard Operation): Autonomous thermostat-driven modulation.
* `ADVISED_ON` (Pre-heat Opportunity): Low tariff / solar window; elevate flow/setpoint to store energy.
* `FORCED_ON` (Mandatory Run): Forced run to guarantee comfort setpoint (e.g. domestic hot water run).
* `MAX_ON` (Boost Storage): Maximum power run up to upper thermal limit (e.g. solar boost to 60°C).

#### B. Battery Inverters
* `IDLE`: Standby, inverter in sleep mode.
* `CHARGE_SOLAR`: Charging strictly from local PV surplus.
* `CHARGE_GRID`: Forced charging from grid during night/cheap tariff dips.
* `DISCHARGE_SELF_CONSUMPTION`: Discharging to cover real-time household baseload.
* `DISCHARGE_ARBITRAGE`: Discharging at maximum rating to grid during extreme price peaks.
* `LOCKOUT_HOLD`: Discharge inhibited to preserve SOC for anticipated higher peak.

#### C. EV Chargers
* `DISCONNECTED`: No vehicle connected.
* `PAUSED`: Connected, awaiting low-tariff or solar window.
* `SOLAR_ONLY`: Dynamically matched to solar surplus (6–16A single/three-phase).
* `SCHEDULED_CHARGE`: Fast charge to departure target.

---

### 3.3 Schema Contract: `CanonicalDispatchPlan` (v1.0.0)

All fields are JSON-serializable and immutable (`frozen=True`):

```json
{
  "$schema": "https://open-hems.org/schemas/v1/dispatch_plan.json",
  "schema_version": "1.0.0",
  "generated_at": "2026-09-12T10:30:00Z",
  "horizon_hours": 24.0,
  "resolution_minutes": 15,
  "is_fresh": true,
  "freshness_age_seconds": 18.4,
  "validation_issues": [],
  "summary": {
    "total_pv_generation_kwh": 18.2,
    "total_grid_import_kwh": 6.4,
    "total_grid_export_kwh": 4.1,
    "total_energy_cost_eur": 1.84,
    "peak_lockout_hours": 2.0,
    "devices": {
      "heat_pump_cv": { "active_hours": 8.5, "energy_kwh": 8.2 },
      "boiler_350l": { "run_window": "13:00 - 14:30", "target_temp_c": 60.0, "mode": "max_on" },
      "home_battery": { "charge_solar_kwh": 5.2, "discharge_kwh": 4.8, "end_soc_pct": 65 }
    }
  },
  "slots": [
    {
      "slot_idx": 0,
      "time_label": "Now (10:30)",
      "dt_iso": "2026-09-12T10:30:00+02:00",
      "price_eur_per_kwh": 0.1425,
      "solar_production_kw": 2.85,
      "unallocated_demand_kw": 0.42,
      "net_grid_flow_kw": -2.43,
      "device_dispatches": {
        "boiler_350l": {
          "power_kw": 0.0,
          "mode": "normal",
          "mode_label": "Normaal (Standby)",
          "color_hex": "#1E293B",
          "setpoint_c": 50.0
        },
        "heat_pump_cv": {
          "power_kw": 0.0,
          "mode": "advised_on",
          "mode_label": "Geadviseerd aan (Doorverwarmen)",
          "color_hex": "#4ADE80",
          "flow_temp_bias_k": 2.0
        },
        "home_battery": {
          "power_kw": 2.2,
          "mode": "charge_solar",
          "mode_label": "Zonneladen",
          "color_hex": "#10B981",
          "soc_pct": 45.0
        }
      }
    }
  ]
}
```

---

## 4. Policy Engine & Optimization Strategy

Open HEMS avoids hardcoded hours in core logic. Behavior is governed by declarative, configurable policies:

### 4.1 Peak Shaving & Lockout Policy (`PeakLockoutPolicy`)
* **Trigger:** Percentile threshold ($P_{85}$) and minimum delta above daily median ($\Delta P \ge \text{€0.030–€0.050/kWh}$).
* **Anti-Hunting (Micro-peak Filter):** Events shorter than 30 minutes are ignored.
* **Winter Comfort Cap:** To prevent room and floor screed cooling during prolonged winter peaks, continuous hard lockouts (`FORCED_OFF`) are strictly capped (default: 150 minutes / 2.5 hours). Surrounding elevated hours transition to `ADVISED_OFF` (low modulation floor).

### 4.2 Thermal Buffer Policy (`ThermalBufferPolicy`)
* **Daytime Energy Arbitrage:** If daylight hours remain and solar surplus or low tariffs are forecast, daytime buffer runs are prioritized over nighttime recovery.
* **Solar Boost Threshold:** When projected solar surplus exceeds the thermal requirement ($E_{\text{surplus}} \ge \Delta T \cdot C_{\text{th}}$), target temperature elevates to the storage maximum (e.g., 60°C).
* **Night Valley Tie-Breaker:** When wholesale prices are flat across night hours, dispatch ties are resolved towards the statistical minimum window (default: 03:30) rather than arbitrarily choosing 00:00 or 01:00.

### 4.3 Solver Evolution Roadmap
* **Phase 1 (Current):** Deterministic Rule-Waterfall Policy Solver. Evaluates Shiftable -> Thermal -> Battery hierarchically in $O(N)$ time.
* **Phase 2 (Roadmap v1.2.0):** Mixed-Integer Linear Programming (MILP) solver (using PuLP / HiGHS). Solves co-optimized battery SOC, heat pump modulation, and EV charging against time-varying prices and grid export limits.

---

## 5. Non-Functional Requirements (NFRs)

### 5.1 Cold Boot & Restart Resilience
* When the daemon starts without existing InfluxDB or HA history:
  1. It loads fallback baseline profiles from `data/default_profiles.json`.
  2. It immediately fetches current spot prices and satellite weather.
  3. It generates an initial plan within 5 seconds of startup.
  4. Device actuators remain in `SAFE_NORMAL` until the first verified plan is published.

### 5.2 Persistence & Audit Logging
* **In-Memory Store:** Instant access for API requests (< 1 ms latency).
* **Disk Snapshot:** Every published plan is atomically written to `/config/open_hems_plan_snapshot.json` to survive daemon restarts.
* **InfluxDB Audit Log:** If InfluxDB is configured, planned dispatch trajectories and actual actuator executions are written to measurement `hems_recommendations` for model residual tracking.

### 5.3 Daylight Saving Time (DST) Invariance
* Time calculations use timezone-aware Python `datetime` objects (`ZoneInfo`).
* On the autumn 25-hour transition day, the plan spans 100 quarters; on the spring 23-hour day, it spans 92 quarters. The API and UI consume explicit ISO timestamps rather than fixed array indices.

### 5.4 Manual Overrides
* The system supports temporary manual overrides that bypass optimization until a specified expiry:
  * `BOOST_NOW` (Run DHW/CV immediately for X minutes).
  * `VACATION_MODE` (Lower setpoints, disable daily DHW runs except legionella safety).
  * `PAUSE_HEMS` (Relinquish control; return all devices to native thermostat schedules).

### 5.5 Security & API Authentication
* Web console port 8099 supports:
  * Local private network access (RFC 1918).
  * Bearer token authentication via `open_hems_secrets.json`.
  * Reverse-proxy header validation (`X-Remote-User-Id` / HA Ingress signature check).
* Zero storage of cleartext secrets in git repositories. Pre-commit secret scanning enforced.

---

## 6. Anti-Drift Guardrails & Verification Suite

To prevent architectural degradation over time, the project enforces three automated gates:

1. **AST Handler Audit Test (`test_architecture_ast.py`):**
   * Parses `daemon.py` with Python's Abstract Syntax Tree (`ast`).
   * **Fails CI immediately** if any HTTP request handler executes `urllib.request`, raw math calculations, or reads raw database queries directly instead of calling `PlanStore.get_plan()`.
2. **Entity Isolation Test (`test_entity_isolation.py`):**
   * Scans core packages (`layer1_data_collection/`, `layer2_calibration/`, `layer3_scheduling/`, `models/`).
   * **Fails CI immediately** if strings like `sensor.`, `climate.`, `switch.`, `altherma`, or specific coordinates are found outside `site_adapters/` or documentation.
3. **Vector Uniformity Test (`test_vector_uniformity.py`):**
   * Asserts that `/api/schedule/chart-data`, `/api/model/decomposition`, and the actuation output yield 100% identical numbers for all 96 slots.

---

## 7. Delivery Structure & File Governance

The repository is organized into distinct, modular packages:

```
open-hems/
├── PRD.md                                 # Generic Product Requirements (This document)
├── config/
│   ├── site_config.json                   # Active site configuration
│   └── site_example.yaml                  # Documented generic example configuration
├── docs/
│   ├── REFERENCE_SITE_CULEMBORG.md        # Matthijs's physical installation profile
│   └── adr/                               # Architecture Decision Records
│       ├── ADR-001-5-layer-pipeline.md
│       ├── ADR-002-device-agnostic-contract.md
│       └── ADR-003-ha-as-adapter-plugin.md
├── layer1_data_collection/
│   ├── collector.py                       # Polling & ring-buffer collector
│   └── sanitizer.py                       # TelemetrySanitizer (freshness, bounds)
├── layer2_calibration/
│   ├── learned_forecaster.py              # 7x96 EWMA matrix engine
│   └── dhw_thermal_model.py               # Thermal physics model
├── layer3_scheduling/
│   ├── central_planner.py                 # Multi-device policy solver
│   └── plan_store.py                      # Thread-safe Singleton PlanStore
├── layer4_control/
│   ├── controller.py                      # Actuation coordinator
│   └── adapters/                          # Hardware actuation adapters
│       ├── ha_service_actuator.py         # Optional Home Assistant service caller
│       └── modbus_actuator.py             # Direct Modbus register writer
├── models/
│   └── canonical.py                       # Canonical dataclasses & schemas
├── tests/
│   ├── unit/                              # Pure mathematical unit tests
│   └── architecture/                      # AST & Anti-drift guardrail tests
└── daemon.py                              # Dumb HTTP server & Ingress UI provider
```
