---
okf_version: "0.2"
title: "Laya Router Home Assistant Add-on Knowledge Bundle"
description: "Authoritative architectural contracts, invariants, and operational patterns for ha-addon-laya."
last_updated: "2026-10-01"
---

# Knowledge Index — ha-addon-laya

This bundle contains the normative architectural decisions, security boundaries, and data invariants governing the **Laya Router Home Assistant Add-on**.

## 1. Architecture Contracts
- [`decision-engine`](architecture/decision_engine.md) — Single-forward-pass System 1 routing, two-question design (`task_family`, `effort`), confidence gating, multi-provider model mappings, and hardware latency benchmarks.
- [`hermes-integration`](architecture/hermes_integration.md) — Integration contract between Hermes Agent (`laya-router` plugin) and local Laya HA app (`/v1/route`), single-endpoint limitation, and gateway topology (LiteLLM/OpenRouter).

## 2. Operations & Model Management
- [`model-management`](operations/model_management.md) — Persistent weight caching in `/data/hf`, pinned revision downloads, Intel Arc iGPU (XPU) hardware passthrough, and empirical CPU vs XPU latency benchmarks.
