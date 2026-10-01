---
id: architecture/decision_engine
title: "Laya System 1 Decision Engine Architecture"
type: "Architecture Decision"
description: "Normative specification of the two-question single-pass routing architecture, criteria wording rules, and confidence extraction."
status: active
trust: authoritative
generated:
  by: "human:matthijs"
  at: "2026-10-01T00:00:00Z"
sources:
  - id: laya-prd
    resource: "/config/projects/HomeAssistant-Addons/laya/PRD.md"
    title: "Laya Router PRD"
  - id: laya-docs
    resource: "https://nandhakishorm.github.io/laya/"
    title: "Laya Documentation"
---

# Laya System 1 Decision Engine Architecture

## Context & Purpose
Hermes Agent natively runs turns on a default model (such as `gemini-flash-latest`). Simple queries overpay in inference tokens and latency, while difficult reasoning tasks suffer from insufficient thinking effort.

Laya Router resolves this by running a sub-50ms local System 1 decision model before dispatching the request to the upstream provider.

## Architectural Invariants

### 1. Two-Question Single Forward Pass
Laya resolves two orthogonal questions in one single forward pass:
1. `task_family`: Categorizes the request into `quick`, `general`, `code`, or `deep`.
2. `effort`: Determines the reasoning depth as `light`, `normal`, or `deep`.

No model names are passed to Laya. This decouples task taxonomy from model provider mappings.

### 2. Strict Criteria Wording Rules
- **Task Naming:** Every criterion must explicitly describe an objective type of task. Superlatives (e.g. "best general-purpose model") are strictly prohibited because they absorb decisions indiscriminately.
- **No Boolean Traps:** No `yes`/`no` or `true`/`false` option keys in `choice` questions, preventing token bias.
- **No Score Questions:** `score` questions are avoided for routing because multilingual checkpoints show calibration drift on first-tier buckets.

### 3. State Framing & Context Window
- `request`: The active user prompt.
- `context`: Exactly up to the previous two turns formatted as text.
- Short conversational follow-ups (e.g. "ja, doe maar" or "continue") rely on `context` to preserve task semantics.

### 4. Confidence Gating & Fallback
The engine extracts `answer_confidence` for each decision. If confidence falls below the configured threshold (e.g. 0.60):
- If `task_family` is low confidence: keep default model and effort.
- If `effort` is low confidence: adopt default effort for that family.
