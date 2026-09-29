---
id: clickhouse-retention
title: "ClickHouse and SeaweedFS 30-Day Retention Invariant"
type: "Operational Standard"
status: "active"
trust: "human_reviewed"
tags: [clickhouse, seaweedfs, retention, storage-hygiene]
---

# ClickHouse and SeaweedFS 30-Day Retention Invariant

## Context & Rationale
Langfuse's native retention engine is an Enterprise-only proprietary feature. In an unattended Home Assistant environment on home storage (SSD/NVMe), unbounded trace ingestion from autonomous agents would eventually exhaust host disk space and bloat cold backup snapshots.

## Normative Contracts & Invariants

1. **Scheduled Pruning Window:**
   - A dedicated S6-managed retention scheduler executes nightly at 03:30 local time.
2. **ClickHouse Pruning:**
   - Lightweight mutation queries execute against trace, generation, observation, and score tables:
     `ALTER TABLE <table_name> DELETE WHERE timestamp < now() - INTERVAL ${RETENTION_DAYS} DAY`
   - Optimization jobs (`OPTIMIZE TABLE <table_name> FINAL`) are deferred to off-peak hours and run only when deleted row count exceeds 10,000 to prevent CPU spikes.
3. **SeaweedFS Expiry:**
   - Bucket lifecycle policies or filer TTL sweeps prune raw event blobs older than `retention_days` under the `events/` prefix.
4. **Log Reporting:**
   - Each retention run must log start timestamp, affected tables, estimated rows deleted, and execution duration.
   - If `retention_days` is set to `0`, retention enforcement is bypassed (keep forever).
