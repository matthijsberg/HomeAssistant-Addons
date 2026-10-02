---
id: s6-supervision
title: "S6-Overlay v3 Supervision and Process Lifecycle"
type: "Architectural Decision"
status: "active"
trust: "human_reviewed"
tags: [s6-overlay, lifecycle, supervisor, postgresql, clickhouse, seaweedfs]
---

# S6-Overlay v3 Supervision and Process Lifecycle

## Context & Rationale
Langfuse v4 is a stateful multi-service stack comprising PostgreSQL, Redis, ClickHouse, SeaweedFS, Langfuse Web, and Langfuse Worker. Home Assistant add-ons must not rely on monolithic entrypoint scripts running blind background sleeps. S6-overlay v3 provides an explicit acyclic dependency graph (`s6-rc.d`), bounded readiness barriers, and deterministic shutdown ordering.

## Normative Contracts & Invariants

1. **Ordering Invariant:**
   - Storage engines (`postgres`, `redis`, `clickhouse`, `seaweedfs`) must initialize and pass readiness checks before `langfuse-web` starts.
   - `langfuse-web` must complete its internal database and ClickHouse migrations and pass `/api/public/health` before `langfuse-worker` and `nginx` start.
2. **Readiness Verification Gates:**
   - PostgreSQL readiness: `pg_isready -h 127.0.0.1 -p 5432 -U langfuse` with a 30s timeout.
   - ClickHouse readiness: HTTP query `http://127.0.0.1:8123/ping` returning `Ok.`
   - SeaweedFS S3 readiness: HTTP `GET http://127.0.0.1:8333` returning HTTP status < 500.
   - Langfuse Web readiness: HTTP `GET http://127.0.0.1:3100/api/public/health` returning 200 OK.
3. **Shutdown Grace Period:**
   - Add-on manifest declares `timeout: 120`.
   - On `SIGTERM`, S6 stops services in strict reverse order, ensuring ClickHouse flushes memory parts to disk and PostgreSQL performs a clean checkpoint before container termination.
