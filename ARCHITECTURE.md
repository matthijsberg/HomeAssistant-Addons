# Open HEMS — Master Architecture & Agent Scoping Blueprint
**Version:** `0.8.0` (Streamlined 4-Layer Modular Monorepo)

Open HEMS is structured as a **Contract-First Modular Monorepo**. Hardware actuation and safety guardrails are absorbed directly into **Device Hardware Adapters** (device-specific parameters such as dwell-time, SG contacts, and emergency thresholds) and **Policy Orchestration** (multi-device constraints such as 3x25A main grid peak-shaving and hydraulic heater interlocks).

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
│ LAAG 1: DATA & DEVICES  │   │ LAAG 2: CALIBRATION     │   │ LAAG 3: POLICIES & PLAN │
│ layer1_data_collection/ │   │ layer2_calibration/     │   │ layer3_scheduling/      │
├─────────────────────────┤   ├─────────────────────────┤   ├─────────────────────────┤
│ • InfluxDB & Secrets    │   │ • Solar K(h) OLS Matrix │   │ • 3 Policy Archetypes:  │
│ • MQTT Bus & Streaming  │   │ • Building UA_base      │   │   - Shiftable Consumer  │
│ • Device Source Adapters│   │ • Standby Loss (350L)   │   │   - Thermal Buffer      │
│ • Per-Device Safety:    │   │ • 80/20 EMA Smoothing   │   │   - Battery Arbitrage   │
│   - Compressor Dwell    │   │ • Sensor Downtime Masks │   │ • Multi-Device Limits:  │
│   - Max Device Wattage  │   │                         │   │   - 3x25A Peak Shaving  │
│   - Noodgrens (<38°C)   │   │                         │   │   - Hydraulic Cutoffs   │
│   - SG Relais Contacts  │   │                         │   │   - Surplus Waterfall   │
└───────────┬─────────────┘   └────────────┬────────────┘   └───────────┬─────────────┘
            │                              │                            │
            └──────────────────────────────┼────────────────────────────┘
                                           ▼
                              ┌─────────────────────────┐
                              │ LAAG 4: ANALYTICS       │
                              │ layer5_analytics/       │
                              ├─────────────────────────┤
                              │ • Realized Net Savings  │
                              │ • Solar Self-Consump %  │
                              │ • Seasonal COP & SCOP   │
                              │ • MAE Forecast Accuracy │
                              └─────────────────────────┘
```

---

## 🤖 AI Agent Scoping Guidelines

When prompting or spawning an AI Agent to work on this repository, **set the scope explicitly to the relevant layer**:

| Target Work | Target Directory | Dedicated Agent Spec | Permitted Actions | Forbidden Actions |
| :--- | :--- | :--- | :--- | :--- |
| **Data, Devices & Ingest** | `layer1_data_collection/` | `AGENT_SPEC.md` | I/O, API clients, InfluxDB, MQTT, device adapters, hardware limits | Macro-scheduling, tariff economics, analytics reporting |
| **Physical Calibration** | `layer2_calibration/` | `AGENT_SPEC.md` | OLS regression, thermal equations, sensor mask filtering | External network calls, relay switching, UI styling |
| **Policies & Peak Shaving** | `layer3_scheduling/` | `AGENT_SPEC.md` | Dynamic dispatch, tariff arbitration, multi-device peak shaving | Direct database writing, hardware I/O |
| **Analytics & Reporting** | `layer5_analytics/` | `AGENT_SPEC.md` | Financial KPIs, self-consumption %, COP calculations, export | Modifying optimization plans, writing device commands |
| **Universal Contracts** | `models/canonical.py` | `ARCHITECTURE.md` | Dataclass definitions, unit conversions, type annotations | Business logic, stateful code |

---

## 🔒 Architectural Guardrails
1. **Strict No-Mock-Data Integrity:** All calculations and reports must run against real verified data.
2. **Device vs. Policy Separation:**
   - Apparaat-specifieke parameters (minimale looptijd, fysiek SG contact, noodcomfort $<38^\circ\text{C}$) horen bij het **Device**.
   - Systeembrede totalen (3x25A netafname peak shaving, overschotprioritering, hydraulische uitsluiting) horen bij **Policies**.
3. **RAM Relais (0 EEPROM Wear):** Daikin Smart Grid relais uitsluitend aansturen via vluchtige binaire contacten S10S/S11S.
4. **Isolated Secrets Vault:** Wachtwoorden en API-sleutels leven in `/config/open_hems_secrets.json` (`0600`) en worden nooit in git of HTML gedeeld.
