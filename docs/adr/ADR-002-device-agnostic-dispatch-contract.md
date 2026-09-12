# ADR-002: Device-Agnostic Dispatch Plan Contract

## Status
Accepted

## Context
Early versions of `DispatchPlanSlot` contained hardcoded fields (`boiler_kw`, `heating_kw`), making it impossible to introduce home batteries, EV chargers, or secondary heat pumps without modifying the central data contract.

## Decision
Refactor `CanonicalDispatchPlan` to be completely device-agnostic:
- Each slot contains `device_dispatches: Dict[str, DeviceSlotDispatch]` keyed by `device_id` from `site_config.json`.
- Aggregated flow quantities (production, consumption, import, export, battery charge/discharge) are computed at the slot level.
- Status codes are defined per functional archetype (Thermal Buffer, Battery Inverter, EV Charger, Shiftable Appliance) rather than hardcoded to a specific manufacturer's relays. Manufacturer-specific states (e.g. Daikin SG1-SG4) are mapped inside Layer 4 hardware adapters.

## Consequences
- The central contract supports arbitrarily many appliances, batteries, and heat pumps.
- The schema is versioned (`schema_version: "1.0.0"`).
