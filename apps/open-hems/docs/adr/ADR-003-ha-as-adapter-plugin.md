# ADR-003: Home Assistant as an Optional Adapter Plugin

## Status
Accepted — Implementation tracked in `docs/plans/PLAN-adapter-register.md` (2026-09-25)

## Context
Initial code directly queried Home Assistant entities and services inside the daemon loop. This created a hard dependency on Home Assistant, making Open HEMS unusable on standalone Linux servers or Docker setups without Home Assistant.

## Decision
Decouple Home Assistant into an optional plugin:
- `HAEntityCollector` implements `ITelemetryProvider`.
- `HAServiceActuator` implements `IDeviceActuator`.
- Open HEMS provides native standalone adapters (`ModbusTcpCollector`, `P1SerialCollector`, `GpioRelayActuator`, `ModbusRegisterActuator`).
- If Home Assistant restarts, fails, or is absent, the core planning engine operates unaffected.

## Consequences
- Open HEMS is a genuine standalone open-source product.
- Home Assistant users enjoy turn-key Ingress integration, while standalone users run via CLI / Docker.
