# Open HEMS — Master Architecture & Agent Scoping Blueprint
**Version:** `0.4.1` (Modular Monorepo Architecture)

Open HEMS is structured as a **Contract-First Modular Monorepo**. It achieves full decoupling between data ingestion, empirical physics, economic optimization, and hardware actuation, allowing specialized AI agents and developers to work within a bounded, well-defined scope without breaking adjacent layers.

---

## 🏛️ The 4-Layer Architecture

```
                                  models/canonical.py
                       (Universal Contract: Single Source of Truth)
                       Vector, Flow, Measurement, Policies, Commands
                                           │
         ┌─────────────────────────────────┼─────────────────────────────────┐
         ▼                                 ▼                                 ▼
┌─────────────────────────┐   ┌─────────────────────────┐   ┌─────────────────────────┐
│ LAAG 1: INGESTION       │   │ LAAG 2: CALIBRATION     │   │ LAAG 3: OPTIMIZER       │
│ layer1_data_collection/ │   │ layer2_calibration/     │   │ layer3_scheduling/      │
├─────────────────────────┤   ├─────────────────────────┤   ├─────────────────────────┤
│ • InfluxDB Line Protocol│   │ • Solar K(h) OLS Matrix │   │ • 3 Policy Archetypes:  │
│ • MQTT Message Broker   │   │ • Building UA_base      │   │   - Shiftable Consumer  │
│ • EPEX Spot & Meteo APIs│   │ • Standby Loss (350L)   │   │   - Thermal Buffer      │
│ • Home Assistant Poll   │   │ • 80/20 EMA Smoothing   │   │   - Battery Deadband    │
│ • Atomic Hot Cache      │   │ • Physical Clamping     │   │ • Waterfall Priority    │
│                         │   │                         │   │ • 24h Dispatch Schedule │
└───────────┬─────────────┘   └────────────┬────────────┘   └───────────┬─────────────┘
            │                              │                            │
            └──────────────────────────────┼────────────────────────────┘
                                           ▼
                              ┌─────────────────────────┐
                              │ LAAG 4: ACTUATION       │
                              │ layer4_control/         │
                              ├─────────────────────────┤
                              │ • RAM Relais (SG1..SG4) │
                              │ • Compressor Dwell-Time │
                              │ • Hydraulic BUH Cutoff  │
                              │ • Emergency Comfort <38°│
                              └─────────────────────────┘
```

---

## 🤖 AI Agent Scoping Guidelines

When prompting or spawning an AI Agent to work on this repository, **set the scope explicitly to the relevant layer**:

| Target Work | Target Directory | Dedicated Agent Spec | Permitted Actions | Forbidden Actions |
| :--- | :--- | :--- | :--- | :--- |
| **Data & Connectivity** | `layer1_data_collection/` | `AGENT_SPEC.md` | I/O, API clients, InfluxDB, MQTT, Line Protocol, caching | Optimization, scheduling, relay switching |
| **Physics & Calibration** | `layer2_calibration/` | `AGENT_SPEC.md` | OLS regression, $K(h)$ matrix, $UA$, 80/20 damping, clamping | Network calls, live hardware access, scheduling |
| **Policies & Solvers** | `layer3_scheduling/` | `AGENT_SPEC.md` | Waterfall dispatch, policy archetypes, battery deadband ($\Delta P \ge 0{,}115$) | Direct hardware control, raw sensor polling |
| **Hardware & Safety** | `layer4_control/` | `AGENT_SPEC.md` | Relay truth table (S10S/S11S), dwell-time locks, hydraulic isolation | Modifying solvers, continuous EEPROM bus writes |

---

## 🔒 Non-Negotiable Directives

1. **No-Mock-Data Rule:** Production pipelines and analyses must NEVER use synthetic or fabricated data. Tests must use explicit fixtures.
2. **Volatile RAM Control (EEPROM Protection):** Daikin compressor control must strictly use physical Smart Grid contacts S10S/S11S evaluated in volatile RAM.
3. **Priority 1 Emergency Comfort:** DHW temperature $< 38{,}0^\circ\text{C}$ immediately triggers emergency reheat, overruling all economic optimizations.
4. **Hydraulic Separation:** Space heating must be turned OFF during SG4 forced DHW runs to eliminate 9 kW backup heater (BUH) engagement.
