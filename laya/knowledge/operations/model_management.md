---
id: operations/model_management
title: "Laya Model Weight Management & Compute Allocation"
type: "Operational Guide"
description: "Guidelines for persistent weight caching in /data/hf, revision pinning, memory footprint, and CPU core allocation."
status: active
trust: authoritative
generated:
  by: "human:matthijs"
  at: "2026-10-01T00:00:00Z"
sources:
  - id: laya-prd
    resource: "/config/projects/HomeAssistant-Addons/laya/PRD.md"
    title: "Laya Router PRD"
  - id: laya-repo
    resource: "https://github.com/NandhaKishorM/laya"
    title: "Laya GitHub Repository"
---

# Laya Model Weight Management & Compute Allocation

## Storage & Persistent Caching

### 1. Weights Directory (`/data/hf`)
Model weights must be stored in `/data/hf` (configured via `HF_HOME=/data/hf`). In Home Assistant OS, `/data` is the only volume that persists across container rebuilds and updates.
- **Initial Download:** ~3 GB total disk space for `english` (ModernBERT-large) and `multilingual` (mmBERT-base) in fp32.
- **First Boot Latency:** Container startup takes 1–3 minutes on initial run while weights download. Subsequent startups take <5 seconds.

### 2. Revision Pinning
To guarantee deterministic and reproducible routing:
- The Hugging Face repo revision is pinned via `laya_revision` in addon options.
- Base weights originate from `convaiinnovations/laya`.

## CPU Compute Allocation

### 1. PyTorch Intra-op Threads (`threads`)
- Default: `6` threads.
- **Rule:** Set `threads` to the number of physical CPU cores (or fewer). Oversubscribing logical hyperthreads introduces severe context-switching regressions.
- Leaves headroom for Home Assistant Core, InfluxDB, and other add-ons.

### 2. Startup Preload Gate
Both checkpoints (`english` and `multilingual`) must be preloaded into RAM during the FastAPI application lifespan.
- `/health` reports `{"ready": false, "status": "loading"}` while checkpoints load.
- `/v1/route` refuses traffic with HTTP 503 until preloading completes.
- Once resident, inference executes at sub-50ms latency on CPU.
