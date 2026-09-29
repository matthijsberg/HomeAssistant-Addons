---
okf_version: "0.2"
title: "Langfuse v4 Home Assistant Add-on Knowledge Bundle"
description: "Authoritative architectural contracts, invariants, and operational patterns for ha-addon-langfuse."
last_updated: "2026-09-29"
---

# Knowledge Index — ha-addon-langfuse

This bundle contains the normative architectural decisions, security boundaries, and data invariants governing the **Langfuse v4 Home Assistant Add-on**.

## 1. Architecture Contracts
- [`s6-supervision`](architecture/s6_supervision.md) — Explicit S6-overlay v3 dependency graph, initialization ordering, and clean shutdown gates.
- [`ingress-basepath`](architecture/ingress_basepath.md) — Build-time placeholder substitution and runtime path resolution for Home Assistant Ingress.
- [`dual-nginx-listeners`](architecture/dual_nginx_listeners.md) — Dual listener topology separating isolated Ingress traffic (:8099) from optional LAN/API traffic (:3000).

## 2. Data Retention & Backups
- [`clickhouse-retention`](data_retention/clickhouse_retention.md) — 30-day rolling data retention for ClickHouse OLAP tables and SeaweedFS blob stores.

## 3. Integrations
- [`trace-ingestion`](hermes_integration/trace_ingestion.md) — Zero-latency trace ingestion contract between Hermes Agent and local Langfuse v4.
