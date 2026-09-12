# ADR-001: 5-Layer Unidirectional Architecture Pipeline

## Status
Accepted

## Context
Initial implementations had logic dispersed across HTTP handlers in `daemon.py`, calibration modules, and frontend JavaScript. This led to conflicting calculation moments, diverging colors, and race conditions.

## Decision
Adopt a strict 5-layer unidirectional data pipeline:
1. **Layer 1:** Ingestion & Hardware Collectors (emits raw stream)
2. **Layer 1B:** Telemetry Sanitizer & Quality Assurance (grid alignment, freshness, bounds checking; emits `CleanTelemetryFrame`)
3. **Layer 2:** Calibration & Self-Learning (7x96 matrix, EWMA residual learning)
4. **Layer 3:** Central Optimization Planner (produces immutable `CanonicalDispatchPlan`)
5. **Layer 3B:** Plan Store (thread-safe singleton + persistent snapshot)
6. **Layer 4 & 5:** Consumers (Layer 4 Actuators execute commands; Layer 5 UI renders plan tokens).

No consumer (UI tab, API route, actuator) may calculate values independently or alter plan tokens.

## Consequences
- Guaranteed zero drift between views.
- Easy to test each layer in isolation.
- UI changes cannot introduce calculation bugs.
