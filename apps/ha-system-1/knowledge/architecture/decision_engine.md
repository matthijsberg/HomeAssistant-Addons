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

### 5. Decoupled Provider & Model Resolution
While Laya's internal forward pass operates exclusively on abstract task categories (`quick`, `general`, `code`, `deep`), the add-on maps these categories to concrete model identifiers according to the selected `provider`:
- **Gemini (Native):** `quick` -> `gemini-3.5-flash-lite`, `general` -> `gemini-flash-latest`, `code` -> `gemini-flash-latest`, `deep` -> `gemini-2.5-pro`.
- **OpenRouter:** `quick` -> `google/gemini-3.5-flash-lite`, `general` -> `google/gemini-flash-1.5`, `code` -> `anthropic/claude-3.5-sonnet`, `deep` -> `anthropic/claude-3.7-sonnet`.
- **LiteLLM / Custom:** Custom model tags routed through a local or remote OpenAI-compatible gateway.

### 6. Per-Family Execution Parameters & Omission Rules
Each family profile in `families:` can define upstream model execution parameters:
- `model`: Target LLM model identifier.
- `effort`: Reasoning effort level (`low`, `medium`, `high`).
- `max_tokens` (optional): Hard output context ceiling. Omitted/null uses provider default.
- `temperature` (optional): Sampling temperature. Omitted/null uses provider default.
- `thinking_budget` (optional): Discrete reasoning thinking tokens. Omitted/null (standard for `code` and `deep`) allows unconstrained thinking; set to `0` for `quick` to bypass thinking latency.

### 7. Hardware Inference Latency (Intel Arc iGPU vs CPU)
Empirical latency benchmarks measured on Intel Core Ultra 5 225H:
- **Intel Arc iGPU (XPU):** 55–120 ms (averaging 120.2 ms across full suite, ~55 ms on deep reasoning tasks).
- **CPU (6 intra-op threads):** 218–481 ms.
- XPU yields a **3.0x to 5.4x latency improvement**, comfortably satisfying the sub-500ms p95 objective.
