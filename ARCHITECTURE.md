# Open HEMS — Master Architecture & Layer Specification
**Document Version:** `2.0.0`  
**Reference Document:** See `PRD.md` for full functional and non-functional requirements.

---

## 1. Architectural Philosophy: The 5-Layer Unidirectional Pipeline

Open HEMS enforces a contract-first, unidirectional architecture:

```
                                      models/canonical.py
                       (Universal Contract: Single Source of Truth)
            Vector, Flow, CleanTelemetryFrame, CanonicalDispatchPlan, DeviceCommand
                                               │
         ┌─────────────────────────────────────┼─────────────────────────────────────┐
         ▼                                     ▼                                     ▼
┌─────────────────────────┐         ┌─────────────────────────┐           ┌─────────────────────────┐
│ LAAG 1: INGESTIE        │         │ LAAG 2: KALIBRATIE      │           │ LAAG 3: PLANNING        │
│ layer1_data_collection/ │         │ layer2_calibration/     │           │ layer3_scheduling/      │
├─────────────────────────┤         ├─────────────────────────┤           ├─────────────────────────┤
│ • Hardware Collectors   │         │ • 7×96 Quarters Matrix  │           │ • Multi-Device Policy   │
│ • Protocol Adapters     │ ──────> │ • EWMA Residual Learning│  ───────> │   Waterfall             │
│ • TelemetrySanitizer    │         │ • Wittboy Assimilation  │           │ • Dynamic Peak Detector │
│ • Freshness & Bounds    │         │ • Physical Thermal Loss │           │ • PlanStore Singleton   │
└─────────────────────────┘         └─────────────────────────┘           └────────────┬────────────┘
                                                                                       │
                                    ┌──────────────────────────────────────────────────┴──────────────────────┐
                                    ▼                                                                         ▼
                         ┌─────────────────────────┐                                               ┌─────────────────────────┐
                         │ LAAG 4: ACTUATIE        │                                               │ LAAG 5: PRESENTATIE     │
                         │ layer4_control/         │                                               │ (daemon.py & Lovelace)  │
                         ├─────────────────────────┤                                               ├─────────────────────────┤
                         │ • Standalone Modbus     │                                               │ • Web Cockpit (Port     │
                         │ • Standalone GPIO       │                                               │   8099)                 │
                         │ • Home Assistant Plugin │                                               │ • Ingress Integration   │
                         │ • Fail-safe Fallback    │                                               │ • Pure Rendering (Dumb) │
                         └─────────────────────────┘                                               └─────────────────────────┘
```

---

## 2. Core Separation of Responsibilities

1. **Layer 1: Telemetry Collection & Sanitization (`layer1_data_collection/`)**
   * Emits raw telemetric streams from Modbus, P1, MQTT, and weather APIs.
   * `TelemetrySanitizer` enforces physical boundaries, checks freshness (< 15 min), and synchronizes all streams to 15-minute grid slots starting from `Now`.
   * Produces a contract-bound `CleanTelemetryFrame`.

2. **Layer 2: Calibration & Learning (`layer2_calibration/`)**
   * Learns baseline non-dispatchable household patterns (7×96 quarters matrix).
   * Applies Exponentially Weighted Moving Average (EWMA) tracking for residual drift correction.
   * Assimilates local on-site weather sensors (Wittboy) against satellite irradiance forecasts.

3. **Layer 3: Central Planning & Plan Store (`layer3_scheduling/`)**
   * Solves multi-device energy dispatch over a 24–48 hour horizon based on dynamic wholesale tariffs.
   * Identifies economic price spikes and enforces anti-hunting and comfort caps (max 2.5h winter lockout).
   * Emits an immutable, versioned `CanonicalDispatchPlan`.
   * Publishes to `PlanStore` (thread-safe in-memory singleton + persistent disk snapshot + InfluxDB audit log).

4. **Layer 4: Actuation & Control (`layer4_control/`)**
   * Translates dispatch commands into physical device actions.
   * Connects via standalone Modbus/GPIO or via the optional Home Assistant Service Actuator.
   * Guarantees fail-safe fallback to normal mode upon connection loss.

5. **Layer 5: Presentation & Analytics (`daemon.py` / Lovelace)**
   * Pure presentation layer.
   * Consumes `PlanStore.get_plan()` directly via REST `/api/schedule/chart-data`, `/api/model/decomposition`, and `/api/health/consistency`.
   * Contains **zero** mathematical modeling, zero private aggregation, and zero state color logic.

---

## 3. Architecture Decision Records (ADRs)
* `docs/adr/ADR-001-5-layer-pipeline.md`: 5-Layer Unidirectional Architecture Pipeline.
* `docs/adr/ADR-002-device-agnostic-dispatch-contract.md`: Generic Device-Agnostic Dispatch Plan.
* `docs/adr/ADR-003-ha-as-adapter-plugin.md`: Home Assistant as an Optional Adapter Plugin.
