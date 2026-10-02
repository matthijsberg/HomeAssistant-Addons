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

## Hardware Acceleration: Intel Arc / Arrow Lake iGPU (`device: xpu`)

### 1. Architectural Topology
The container accesses the Intel integrated graphics via `/dev/dri` passthrough (`/dev/dri/renderD128` and `/dev/dri/card0`) backed by Intel oneAPI Level Zero runtime (`libze1`) and OpenCL userspace drivers (`intel-opencl-icd`).

### 2. Runtime Fallback & JIT Warmup
When `device: "xpu"` is selected in configuration:
- The engine checks `torch.xpu.is_available()`.
- If the hardware accelerator is accessible, checkpoints execute directly on the Intel Xe GPU execution units.
- **Startup JIT Warmup:** A synthetic forward pass executes during boot. This triggers the one-time Level-Zero SPIR-V kernel compilation in the background, preventing a first-turn latency penalty for users.
- If GPU access fails or hardware is absent, the engine logs a warning and falls back silently to CPU mode without failing turns.

### 3. Empirical Latency Benchmarks (Intel Core Ultra 5 225H)
- **Deep Reasoning / Mathematics:** 55.3 ms (XPU) vs 218 ms (CPU) -> **4.0x speedup**
- **Code Generation / Refactoring:** 89.3–120.0 ms (XPU) vs 450–481 ms (CPU) -> **3.8x–5.4x speedup**
- **Home Assistant Commands / Quick Facts:** 116.7–117.8 ms (XPU) vs 310–449 ms (CPU) -> **2.6x–3.8x speedup**
- **Mean Overall Latency:** **120.2 ms** (XPU) vs **364.6 ms** (CPU) -> **3.0x overall speedup**
