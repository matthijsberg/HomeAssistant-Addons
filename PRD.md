# Open HEMS — Product Requirements Document (PRD)
# Generic Residential Home Energy Management System

**Document Version:** 2.1.0  
**Status:** Approved Master Architecture  
**Scope:** Core Engine, Device-Agnostic Contracts & Execution Loops  
**Target License:** Open Source (MIT)  

---

## 1. Vision & Core Invariants

Open HEMS is a modular, device-agnostic, open-source Home Energy Management System. It coordinates thermal storage, space heating, solar production, dynamic electricity tariffs, battery storage, and EV charging to minimize operational energy cost and carbon footprint while guaranteeing occupant comfort and equipment longevity.

### 1.1 The Four Invariant Principles
1. **Core Autonomy (Host & Ecosystem Independent):**  
   Open HEMS is a standalone daemon. It runs natively on Linux, in Docker, or as a Home Assistant Add-on. Home Assistant is **strictly an adapter plugin** (one of many possible telemetry providers and actuation targets). If Home Assistant is restarted, upgraded, or absent, Open HEMS continues unhindered via native Modbus TCP, P1/DSMR, or MQTT connections.
2. **Device-Agnostic Dispatch Contract (Single Source of Truth):**  
   The planning engine computes a single, canonical, versioned dispatch plan (`CanonicalDispatchPlan`). Every dashboard, Lovelace card, actuator, and notification consumer reads from this central plan. View layers are **strictly presentation-only** (dumb views): they never calculate power, aggregate wattages, or invent state colors.
3. **Strict Data Integrity (Zero Mock Data in Production):**  
   Production decisions, analytics, and commands are backed by real, verified telemetry. If data is stale, missing, or corrupt, the system flags it explicitly (`Quality.STALE`, `Quality.INTERPOLATED`) and falls back to deterministic safety profiles. Fabricated or simulated data is forbidden in production runtime. Development and CI rely strictly on a **Replay Harness with Golden Plan Snapshots**.
4. **Separation of Core Logic from Site Profile:**  
   The core engine contains **zero hardware-specific entity IDs, geographic coordinates, brand names, or tariff constants**. All site-specific parameters are loaded declaratively from a validated `site_config.yaml` / `site_config.json`.

---

## 2. System Architecture & Execution Loops

Open HEMS operates on a **Dual-Loop Architecture** with explicit feedback:

```
┌────────────────────────────────────────────────────────────────────────┐
│ EXTERNAL HARDWARE & PROTOCOL INTEGRATIONS (integrations/<protocol>/)   │
│ Modbus TCP │ P1 DSMR Serial/MQTT │ EnergyZero API │ Open-Meteo │ HA WS │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │
                                    ▼
┌────────────────────────────────────────────────────────────────────────┐
│ LAYER 1: UNIFIED INGESTION (integrations/<protocol>/reader.py)         │
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
│ - Learns from REALIZED EFFECTIVE MODES (not unverified plan requests)  │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │
                                    ▼
┌────────────────────────────────────────────────────────────────────────┐
│ LAYER 3: POLICY-BASED OPTIMIZATION & SOLVER (layer3_scheduling)        │
│ - Planner.plan(frame, tariffs, devices, constraints) -> DispatchPlan   │
│ - Peak shaving & dynamic lockout detector (with winter comfort cap)    │
│ - Multi-device capability waterfall (Shiftable, Thermal, Battery, EV)  │
│ - Produces a frozen, immutable CanonicalDispatchPlan                   │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │
                                    ▼
┌────────────────────────────────────────────────────────────────────────┐
│ LAYER 3B: PLAN STORE (plan_store.py)                                   │
│ - Passed as injected dependency (thread-safe, testable, non-singleton) │
│ - In-memory plan cache + atomic disk snapshot + InfluxDB audit log     │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │
         ┌──────────────────────────┴──────────────────────────┐
         ▼                                                     ▼
┌────────────────────────────────┐   ┌───────────────────────────────────┐
│ LAYER 4: ACTUATION & FEEDBACK  │   │ LAYER 5: DUMB PRESENTATION        │
│ (integrations/<protocol>/)     │   │ (HTTP API, Web Cockpit, Lovelace) │
│ - Translates dispatch plan to  │   │ - Renders plan tokens and series  │
│   device-specific commands     │   │ - Zero math, zero color logic     │
│ - Enforces device invariants   │   │ - Styling & labels loaded from    │
│   (e.g. hydraulic interlocks)  │   │   static mode_catalog.json        │
│ - REPORTS BACK EFFECTIVE MODE  │   └───────────────────────────────────┘
│   & downgrade reason           │
└────────────────────────────────┘
```

### 2.1 The Dual-Loop Strategy (Macro Planner vs Micro Follower)
1. **Macro Loop (15-minute resolution):**
   The global economic and thermal dispatcher. Evaluates day-ahead wholesale prices, solar production curves, and thermal storage trajectories. Computes the contractual `CanonicalDispatchPlan` spanning 24–48 hours and establishes authorized operational mode windows (e.g. `CHARGE_SOLAR`, `FORCED_OFF`).
2. **Micro Loop (Real-time fast follower — Roadmap):**
   A secondary, high-frequency (5–10 second) control loop designed for instantaneous surplus tracking (e.g., modulating dynamic EV charging from 6A to 16A or fast inverter throttling based on live P1 telegrams).  
   *Current phase operational status:* Devices that require real-time modulation operate in `NORMAL` (autonomous hardware inverter tracking / internal BMS balancing) within the operational mode windows authorized by the 15-minute Macro Plan.

### 2.2 The Explicit Actuation Feedback Loop (`effective_mode`)
Actuation is physically imperfect and often lossy:
* Certain hardware interfaces have restricted states (e.g. Daikin Smart Grid contacts provide only 4 physical relay combinations: SG1 to SG4).
* In such cases, `ADVISED_OFF` cannot be mapped to an SG pin state and is downgraded to Stand 2 (`NORMAL`) with a software setpoint decrease or alert; `MAX_ON` is achieved by combining Stand 4 with a 60°C target temperature command.
* **Architecture Rule:** Hardware actuators in Layer 4 **must report back** `effective_mode`, `realized_power_kw`, and optional `downgrade_reason` into the telemetry stream. Layer 2 (Calibration & Residual Learning) trains **strictly on realized effective modes**, never on unverified plan desires.

### 2.3 Unified Integration Packages (`integrations/<protocol_or_device>/`)
Instead of separating a single device into disconnected Layer 1 providers and Layer 4 actuators, all hardware-specific code for a given device or protocol is co-located in `integrations/<protocol_or_device>/`:
```
integrations/daikin_altherma/
├── __init__.py
├── reader.py        # Telemetry ingestion (temperatures, power, states)
├── actuator.py      # Relay/service actuation (SG contacts, setpoints)
├── interlocks.py    # Device-level invariants (e.g., CV master OFF during SG4)
└── tests/           # Device-level invariant unit tests
```
* **Hardware Invariant Enforcement:** Specific physical safety rules (such as disabling the central heating master switch during domestic hot water runs to prevent backup heater activation) belong **inside the device integration package as a verified device invariant**, never hardcoded inside the general planning engine!

### 2.4 Layer 5 Presentation Specification (Dumb Views, Charting & Tokens)
Detailed in authoritative companion document `docs/PRD_CHARTING_AND_VISUALIZATION.md`:
* **Single Source of Truth:** Views perform ZERO math and zero separate interpolations. All data series stream from `plan.slots` (`CanonicalDispatchPlan`).
* **Central State & Sticky Filters:** Global toolbar controls (`OpenHEMSChartEngine`) synchronize all charts simultaneously (Staven vs. Lijn, 15m vs. 1h) and persist state across sessions via `localStorage`.
* **Standardized Tokens (`OpenHEMSTokens`):** Strict shared color palette: Solar (`#F59E0B`), Unallocated (`#3B82F6`), DHW (`#EC4899`), Space Heating (`#6366F1`), Battery Charge (`#10B981`), Netto (`#EF4444`), Price Line (`#38BDF8`).
* **Uniform Time Contract:** Slot labels always anchor to `Nu (HH:MM)` for current slot, `Za 00:00`/`Zo 00:00` for midnight boundaries, and enforce $0.00\text{ kW}$ solar production at night.

### 2.5 Opportunistic In-Flight Run Merger (Smart Grid 60°C Promotion)
When an unscheduled draw-off (e.g. an afternoon shower) causes the DHW tank to dip below reheat threshold and triggers autonomous heat pump operation:
* Open HEMS evaluates whether an upcoming 60°C thermal storage / solar boost run (`max_on`) is already scheduled within the lookahead window ($\le 2\text{ hours}$).
* If cost-effective (current wholesale price is within $+€0.05/\text{kWh}$ or active solar surplus $\ge 1.0\text{ kW}$ is present, and no hard peak lockout is active):
  1. The running cycle is immediately promoted to `max_on` (target 60°C, SG4 boost).
  2. Hydraulic interlock is enforced (CV master switch turned OFF).
  3. The upcoming planned run in `PlanStore` is cancelled and reverted to `normal` (50°C).
  4. Benefit: Eliminates 1 compressor start/stop cycle, saves ~0.25 kWh compressor ramp-up overhead, and achieves full 24-hour thermal coverage without a second run.

---

## 3. Generic Data Model & Schema Contracts

### 3.1 Device Modeling via Capability Sets
Rather than forcing devices into rigid archetypes, devices are modeled as a set of orthogonal **capabilities**:

```python
class DeviceCapability(str, Enum):
    CAN_DELAY = "can_delay"          # Appliance run can be shifted in time
    CAN_MODULATE = "can_modulate"    # Power can be continuously adjusted (kW / Amps)
    CAN_STORE = "can_store"          # Buffers energy (thermal kWh or battery kWh)
    CAN_EXPORT = "can_export"        # Can feed energy back into the house/grid
    HAS_DEADLINE = "has_deadline"    # Must finish energy delivery before time T (e.g. EV departure)
    IS_THERMAL = "is_thermal"        # Stores heat/cold; irreversible to electricity
```

Devices declare their capabilities and bounds in `site_config.json`:
* **Domestic Hot Water Cylinder (350L):** `["can_delay", "can_store", "is_thermal"]`
* **Modulating Floor Heating (Heat Pump CV):** `["can_modulate", "can_store", "is_thermal"]`
* **Home Battery (10 kWh LFP):** `["can_modulate", "can_store", "can_export"]`
* **EV Charger (Smart Wallbox):** `["can_delay", "can_modulate", "has_deadline"]`
* **Shiftable Appliance (Dishwasher):** `["can_delay", "has_deadline"]`

The planning engine discovers devices dynamically from configuration by querying capabilities.

### 3.2 Energy Vector Scope
While the canonical domain primitives (`models/canonical.py`) mathematically support multiple energy vectors (`ELECTRICITY`, `HEAT`, `GAS`, `WATER`), the active operational scope of Open HEMS is focused on **Electricity and Heat** flows.

---

### 3.3 Decoupling Presentation from the Dispatch Plan (`mode_catalog.json`)

To preserve clean separation of concerns, the central dispatch plan (`CanonicalDispatchPlan`) carries **strictly language-neutral, programmatic mode codes** (`mode: "forced_off"`), target setpoints, and power allocations.

* All human-readable display labels (`Geforceerd uit (blok)`), local language descriptions, and UI presentation tokens (`#EF4444`, `text-red-400`) are stripped from the core planner.
* Presentation metadata resides in a static catalog: `static/mode_catalog.json` (or `models/mode_catalog.py`).
* View layers (Web console, Home Assistant Lovelace cards) join the plan tokens against `mode_catalog.json`. Changing a display color or fixing a translation never modifies or invalidates the planning engine.

#### Canonical Mode Codes
* **Thermal Buffers / Heat Pumps:** `forced_off`, `advised_off`, `normal`, `advised_on`, `forced_on`, `max_on`.
* **Battery Inverters:** `idle`, `charge_solar`, `charge_grid`, `discharge_self_consumption`, `discharge_arbitrage`, `lockout_hold`.
* **EV Chargers:** `disconnected`, `paused`, `solar_only`, `scheduled_charge`.

---

### 3.4 Tariff & Feed-In Model (`TariffProvider`)
Tariff calculation is encapsulated in a dedicated, declarative `TariffProvider` configured via `site_config.json`:

$$\text{Price}_{\text{all\_in}} = (\text{Price}_{\text{spot}} + \text{Markup}_{\text{supplier}} + \text{Tax}_{\text{energy}}) \times (1 + \text{VAT})$$

$$\text{Compensation}_{\text{export}} = f(\text{Price}_{\text{spot}}, \text{NetMeteringActive}, \text{FeedInMarkup})$$

The `TariffProvider` computes both the gross import tariff vector and the net export value vector, enabling correct economic arbitrage decisions as net-metering (*salderingsregeling*) phases out.

---

### 3.5 Core Planning Contract Signature
The central optimization interface is completely standardized:

```python
class ICentralPlanner(ABC):
    @abstractmethod
    def plan(
        self,
        frame: CleanTelemetryFrame,
        tariffs: TariffProvider,
        devices: List[DeviceConfig],
        constraints: Dict[str, Any]
    ) -> CanonicalDispatchPlan:
        """
        Computes the multi-device canonical dispatch plan.
        Enables seamless replacement of the rule-waterfall solver with a
        Mixed-Integer Linear Programming (MILP) solver in future releases.
        """
        pass
```

---

## 4. Replay Harness & Development Governance

### 4.1 Zero Mock Data & Golden Replay Harness
"Zero mock data in production" requires an airtight local testing workflow for developers and AI agents:
* **The Replay Harness (`tests/fixtures/golden/` & `test_replay_harness.py`):**  
  A recorded 24-hour production dataset (real EPEX prices, real weather radiation/temperature, real InfluxDB 7x96 profile, real tank starting temperature) is captured as a regression baseline.
* Any code change, refactoring, or solver upgrade is verified by executing `pytest tests/unit/test_replay_harness.py` against the golden plan snapshot. If an architectural drift or calculation error is introduced, the test fails with an exact diff.

### 4.2 Injected Dependency: `PlanStore`
* `PlanStore` is implemented as an instantiable, injectable class (`PlanStore(persistence_path=...)`).
* It supports passing mock or isolated storage instances into tests to allow concurrent scenario testing and deterministic verification without global state side-effects. A default shared instance is provided for the production daemon runtime.

---

## 5. Non-Functional Requirements (NFRs)

1. **Cold Boot & Graceful Recovery:** Daemon boots, loads fallback baselines, fetches live EPEX/weather, and publishes an initial plan within 5 seconds. Actuators remain in `NORMAL` failsafe until the first plan publishes.
2. **Dual-Tier Persistence:** In-memory plan serving (< 1 ms latency) + atomic local disk snapshot (`/config/open_hems_plan_snapshot.json`) + InfluxDB audit log (`hems_recommendations`).
3. **Daylight Saving Time (DST) Compliance:** Quarter-hour indexing handles 92 (spring), 96 (standard), and 100 (autumn) slots using timezone-aware `ZoneInfo` timestamps.
4. **Manual Operational Overrides:** Supported via API (`BOOST_NOW`, `VACATION_MODE`, `PAUSE_HEMS`).
5. **Security & Authentication:** Private network bind, Bearer token header check, reverse-proxy ingress header authentication. Zero cleartext secrets in git.

---

## 6. Delivery Structure & File Governance

```
open-hems/
├── PRD.md                                 # Generic Product Requirements (This document)
├── AGENTS.md                              # AI Agent & Developer Playbook
├── config/
│   ├── site_config.json                   # Declarative site configuration & devices
│   ├── site_example.yaml                  # Documented generic site template
│   └── mode_catalog.json                  # UI display labels, translations & HEX colors
├── docs/
│   ├── REFERENCE_SITE_CULEMBORG.md        # Matthijs's physical installation profile
│   └── adr/                               # Architecture Decision Records
├── integrations/                          # Co-located hardware read/write packages
│   ├── daikin_altherma/                   # Daikin P1P2 / SG integration & interlocks
│   ├── deye_inverter/                     # Modbus TCP battery & inverter integration
│   ├── p1_dsmr/                           # P1 serial/MQTT grid meter integration
│   └── homeassistant/                     # Optional HA Provider & Actuator plugin
├── layer1_data_collection/
│   ├── collector.py                       # Ingestion aggregator
│   └── sanitizer.py                       # TelemetrySanitizer (freshness, bounds)
├── layer2_calibration/
│   ├── learned_forecaster.py              # 7x96 EWMA matrix engine
│   └── dhw_thermal_model.py               # Thermal physics model
├── layer3_scheduling/
│   ├── central_planner.py                 # Multi-device policy solver
│   ├── tariff_provider.py                 # Dynamic tariff & export formula engine
│   └── plan_store.py                      # Injectable, thread-safe PlanStore
├── models/
│   └── canonical.py                       # Canonical dataclasses & schemas
├── tests/
│   ├── fixtures/golden/                   # Real recorded telemetry & golden snapshots
│   ├── unit/                              # Pure mathematical unit tests
│   └── architecture/                      # AST & Anti-drift guardrail tests
└── daemon.py                              # Dumb HTTP server & Ingress UI provider
```
