# ADR-005: Adapter Registry & Device Binding Architecture

* **Status:** Accepted
* **Date:** 2026-09-25
* **Author:** Matthijs van den Berg & Open HEMS Architecture Team
* **Deciders:** Open HEMS Core Architecture Board
* **Implements:** Complements ADR-002 (Device-Agnostic Dispatch Contract) and ADR-003 (Home Assistant as Optional Adapter Plugin)
* **Implementation Plan:** `docs/plans/PLAN-adapter-register.md`

---

## Context

Open HEMS currently interacts with hardware devices (heat pumps, meters, solar inverters, and batteries) through hardcoded entity strings scattered across `daemon.py`, `api/context.py`, and `api/energy_feed.py`. 
Although `ADR-002` (Device-Agnostic Dispatch Plan Contract) and `ADR-003` (Home Assistant as an Optional Adapter Plugin) were accepted, their canonical contracts (`DeviceSlotDispatch`, `ITelemetryReader`, `IDeviceActuator`) were never formally integrated into an explicit registration and binding mechanism.

As a consequence:
1. Devices declared in `config/site_config.json` carry `adapter` and `capabilities` fields that are completely unread by runtime scheduling.
2. Ingestion is duplicated across two parallel paths (`daemon.py` background collector vs. `api/context.py` plan frame builder), creating synchronization and drift risks.
3. Actuation in Layer 4 is currently a monolithic Daikin-specific procedure without device iteration or generic fallback handling.
4. Adding upcoming assets (such as a 15 kWh home battery or deferrable appliances) would force adding further hardcoded `if/elif` branches and ad-hoc slot attributes, violating Invariant #2 (Core Entity Isolation) and ADR-002.

---

## Decision

We establish an explicit, type-safe **Adapter Registry & Binding Architecture** in `integrations/`:

### 1. Explicit Adapter Registration (`AdapterSpec`)
Each integration package (e.g. `integrations/daikin_altherma/`, `integrations/generic_ha/`, `integrations/home_battery/`) exports an immutable `AdapterSpec`:
* `slug: str`: Unique identifier (e.g. `"daikin_altherma"`, `"generic_ha_sensor"`).
* `supported_types: frozenset[str]`: Device types covered by this driver.
* `capabilities: frozenset[DeviceCapability]`: Physical capabilities inherently supported by the adapter (`can_store`, `can_export`, `can_modulate`, `can_delay`, `has_deadline`, `is_thermal`).
* `reader: Optional[Type[ITelemetryReader]]`: Class for extracting canonical `Measurement` streams.
* `actuator: Optional[Type[IDeviceActuator]]`: Class for dispatching canonical commands and querying effective states.
* `interlocks: Optional[Type[IInterlock]]`: Hardware-level safety interlocks.

Registrations are strictly explicit (listed in `integrations/__init__.py`), rejecting dynamic filesystem scanning or implicit magic.

### 2. Device Binding (`DeviceBinding`)
Configuration entries from `site_config.json` are bound to registered `AdapterSpecs` at initialization:
* `bind_all(cfg)` produces a typed list of `DeviceBinding` objects.
* Filtering is performed by functional capability (`by_capability(cfg, DeviceCapability.CAN_STORE)`) rather than manufacturer strings.
* Unbound or unconfigured devices fail loudly in health diagnostics (`/api/health/consistency`), never silently through guessed entity names.

### 3. Separation: `integrations/` vs. `site_adapters/`
* `integrations/<family>/`: Generic drivers providing `ITelemetryReader` and `IDeviceActuator`.
* `site_adapters/<protocol>/`: Site-specific signal interpretation and power disaggregation (e.g. `DaikinP1P2StateClassifier`), used *by* readers, but never registered as standalone device adapters.

### 4. Canonical State & Device Dispatch Contract
* `DispatchPlanSlot` includes `device_dispatches: Dict[str, DeviceSlotDispatch]`, enabling per-device schedule tracking while preserving aggregated power flows at the slot level.
* All actuators declare an explicit `safe_state(binding)` to ensure safe fallback during shutdown, device deletion, or watchdog timeout.

---

## Consequences

### Positive
* **Zero Entity Leakage:** Mathematical scheduling layers operate purely on abstract capabilities and normalized measurements.
* **Seamless Battery Integration:** The 15 kWh home battery integrates cleanly into `device_dispatches` without mutating legacy slot contracts.
* **Unified Ingestion:** A single set of readers feeds both telemetry storage and planning frames, eliminating discrepancies.
* **Strict Backward Compatibility:** When `device_dispatches` is empty or unpopulated, dispatch plans remain 100% bit-identical to legacy outputs.

### Neutral / Negative
* Initial migration requires refactoring readers and declaring explicit `AdapterSpec`s.
