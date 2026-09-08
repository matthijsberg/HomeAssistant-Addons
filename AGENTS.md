# AGENTS.md — Master AI Agent & Developer Architecture Reference
**Platform:** Open HEMS Framework
**Version:** `0.5.0` (Modular Monorepo Architecture)

This repository is structured as a **Contract-First Modular Monorepo**. To ensure high-quality, bug-free development when deploying autonomous AI agents, work must strictly follow the **Layer Scoping Protocol** below.

---

## 🏛️ Monorepo Directory Layout & Layer Boundaries

```
/config/projects/energy-scheduler/
├── models/                     # UNIVERSAL CONTRACT (Single Source of Truth)
│   ├── canonical.py            # Vector, Flow, Measurement, Policies, ScheduleSlot, DeviceCommand
│   └── __init__.py
│
├── layer1_data_collection/     # LAAG 1: INGESTION, BROKERS & TIME-SERIES STORAGE
│   ├── interfaces.py           # IDataCollector, ITimeSeriesStorage, IMqttBroker
│   ├── collector.py            # Realtime ingestion, InfluxDB Line Protocol, MQTT
│   ├── README.md               # Functional and setup documentation
│   └── AGENT_SPEC.md           # Strict AI Agent scope & directives for Layer 1
│
├── layer2_calibration/         # LAAG 2: EMPIRICAL CALIBRATION & LEARNING
│   ├── interfaces.py           # IModelCalibrator, ISmoothedClamping
│   ├── calibrator.py           # OLS regression, solar K(h), building UA, DHW standby loss
│   ├── README.md               # Thermodynamic modeling documentation
│   └── AGENT_SPEC.md           # Strict AI Agent scope & directives for Layer 2
│
├── layer3_scheduling/          # LAAG 3: DISPATCH OPTIMIZATION & POLICY ENGINE
│   ├── interfaces.py           # IPolicyEvaluator, IDispatchOptimizer
│   ├── scheduler.py            # 24h / 96-quarter waterfall solver, battery deadband logic
│   ├── README.md               # Policy archetypes & economic dispatch documentation
│   └── AGENT_SPEC.md           # Strict AI Agent scope & directives for Layer 3
│
├── layer4_control/             # LAAG 4: ACTUATION, SAFETY GUARDS & HARDWARE RELAYS
│   ├── interfaces.py           # IActuatorController, ISafetyGuard
│   ├── controller.py           # SG1..SG4 binaire relais (RAM), hydraulic exclusivity, 38°C guard
│   ├── README.md               # Hardware protection & relay map documentation
│   └── AGENT_SPEC.md           # Strict AI Agent scope & directives for Layer 4
│
├── runners/                    # Standalone automation triggers & cron entrypoints
│   ├── run_daily_optimizer.py     # 4x daily dispatch calculation (06:30, 11:30, 14:30, 18:30)
│   ├── run_weekly_calibration.py  # Weekly parameter auto-tuning (Sunday 08:00)
│   └── sync_energy_taxes.py       # Statutory tax delta detection
│
├── config/                     # Declarative configuration & schema examples
├── data/                       # Local atomic hot cache (energy_feed_cache.json)
└── tests/unit/                 # Automated unit test suite per layer
```

---

## 🎯 AI Agent Scoping Protocol (Multi-Agent Development)

When assigning a task to an AI Agent in this repository, **strictly set the scope to the relevant layer**:

1. **Layer 1 Tasks (Data & Connectivity):**
   * Scope: `layer1_data_collection/` (Refer to `AGENT_SPEC.md`).
   * Permitted: Network I/O, InfluxDB 1.8/2.x Line Protocol, MQTT brokers, EPEX/Meteo API clients, Home Assistant REST polling, caching.
   * FORBIDDEN: Scheduling decisions, battery charge/discharge optimization, direct relay switching.

2. **Layer 2 Tasks (Physics & Calibration):**
   * Scope: `layer2_calibration/` (Refer to `AGENT_SPEC.md`).
   * Permitted: Pure statistical/thermodynamic algorithms, OLS regression, solar matrix $K(h)$, building $UA_{\text{base}}$, defrost penalty, 80/20 EMA damping, physical clamping.
   * FORBIDDEN: Network I/O, live API calls, hardware actuation, modifying dispatch policies.

3. **Layer 3 Tasks (Policies & Optimization):**
   * Scope: `layer3_scheduling/` (Refer to `AGENT_SPEC.md`).
   * Permitted: Multi-asset priority waterfall solvers, policy evaluation (Shiftable, ThermalBuffer, BatteryArbitrage with economic deadband $\Delta P \ge €0{,}115/\text{kWh}$).
   * FORBIDDEN: Direct hardware communication, raw sensor polling, bus writes.

4. **Layer 4 Tasks (Actuation & Hardware Safety):**
   * Scope: `layer4_control/` (Refer to `AGENT_SPEC.md`).
   * Permitted: Hardware command dispatch, volatile RAM Smart Grid contacts (S10S/S11S), compressor dwell-time protection (20 min), hydraulic BUH lockout, Priority 1 emergency comfort ($<38^\circ\text{C}$).
   * FORBIDDEN: Modifying optimization math, guessing physical parameters, non-volatile EEPROM writes.

---

## 🔒 Universal Directives (Apply to ALL Agents & Layers)

1. **Absolute Ban on Mock or Fabricated Data:**
   All inputs, calculations, and operational decisions must be backed by genuine production telemetry or explicit test fixtures. NEVER fabricate synthetic wattages, fake curves, or placeholder temperatures.
2. **Strict Contract Adherence:**
   All layers must exchange data strictly via `models.canonical` dataclasses (`Measurement`, `DeviceState`, `DeviceCommand`, `ScheduleSlot`).
3. **RAM vs. EEPROM Protection:**
   Daikin heat pump control must strictly use physical Smart Grid contacts S10S/S11S evaluated in volatile RAM (0 EEPROM write cycles).
4. **Hydraulic BUH Isolation:**
   During SG4 forced DHW heating, space heating (`switch.hc_mode_altherma_on`) MUST be disabled to physically lock out the 9 kW backup heater (BUH).
5. **Emergency Comfort Floor:**
   If DHW tank temperature drops below $38{,}0^\circ\text{C}$, reheat is triggered with Priority 1, overruling all spot prices and peak lockouts.

---

## 🧪 Testing Commands
```bash
# Run complete test suite (23 unit tests across all 4 layers)
PYTHONPATH=/config/projects/energy-scheduler pytest /config/projects/energy-scheduler/tests/unit/ -v

# Run individual layer runners
python3 /config/projects/energy-scheduler/runners/run_daily_optimizer.py
python3 /config/projects/energy-scheduler/runners/run_weekly_calibration.py
```
