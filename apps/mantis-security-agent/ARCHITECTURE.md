# Architecture Specification — Mantis Security Agent

**Component:** Home Assistant Add-on (`mantis_security_agent`)  
**Transport:** Model Context Protocol (MCP) Streamable HTTP (`/mcp`) & Legacy SSE (`/sse`)  
**Ingress Web UI:** Home Assistant Ingress on port 8000 (`/`)  
**Security Model:** Static-first, read-only Supervisor mounts, zero host execution, human-in-the-loop Ingress egress approval.

---

## 1. Boundary & File Isolation

The add-on operates as an unprivileged container with no Docker socket and no root host privileges.

```
Host Filesystem (HAOS)             Mantis Container Boundary
┌───────────────────────────┐     ┌────────────────────────────────────┐
│ /addons/ (local source)   │────▶│ /addons (read-only)                │
│ /homeassistant/ (config)  │────▶│ /homeassistant (read-only)         │
│ /addon_configs/           │────▶│ /addon_configs (read-only)         │
│ /share/projects/          │────▶│ /share (read-only)                 │
└───────────────────────────┘     └────────────────────────────────────┘
                                  Private Write-Isolated Paths:
                                  - /data/jobs.db (SQLite state queue)
                                  - /data/cache/ (AST & threat model cache)
                                  - /data/workspaces/ (Ephemeral scratch)
```

---

## 2. Ingress Egress Approval Flow

```
┌──────────────┐          ┌───────────────────────┐          ┌───────────────────┐
│ Hermes Agent │          │ Mantis Security Agent │          │ Human Operator    │
└──────┬───────┘          └──────────┬────────────┘          └─────────┬─────────┘
       │                             │                                 │
       │ mantis_start_audit(...)     │                                 │
       │────────────────────────────▶│                                 │
       │                             │ [1] Deterministic Pre-Pass      │
       │                             │ [2] Redact secrets              │
       │                             │ [3] Build Egress Manifest       │
       │                             │ [4] Status: waiting_confirmation│
       │                             │                                 │
       │ Return approval URL+warning │                                 │
       │◀────────────────────────────│                                 │
       │                             │                                 │
       │ Relay warning to Telegram   │                                 │
       │──────────────────────────────────────────────────────────────▶│
       │                             │                                 │
       │                             │ Click Ingress Approval Link     │
       │                             │ (Authenticated via HA Login)    │
       │                             │◀────────────────────────────────│
       │                             │                                 │
       │                             │ [5] First Cloud LLM Call        │
       │                             │ [6] Critique & Patch Check      │
       │                             │ [7] Status: completed           │
       │                             │                                 │
       │ mantis_get_report(job_id)   │                                 │
       │────────────────────────────▶│                                 │
       │ Return findings + diffs     │                                 │
       │◀────────────────────────────│                                 │
```
