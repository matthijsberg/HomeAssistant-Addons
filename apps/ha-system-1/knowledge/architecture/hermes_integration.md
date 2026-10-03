---
id: architecture/hermes_integration
title: "Hermes Agent & Laya Add-on Integration Contract"
type: "Integration Contract"
description: "HTTP interface, latency budgets, fail-open behavior, and security boundaries between Hermes Agent and the Laya Add-on."
status: active
trust: authoritative
generated:
  by: "human:matthijs"
  at: "2026-10-01T00:00:00Z"
sources:
  - id: laya-prd
    resource: "/config/projects/HomeAssistant-Addons/laya/PRD.md"
    title: "Laya Router PRD"
---

# Hermes Agent & Laya Add-on Integration Contract

## Topology & Boundaries
The Laya Add-on runs as an isolated Home Assistant container on the internal Docker network. It exposes port 8000 internally.

Hermes Agent connects via the `laya-router` plugin over HTTP:
```
Hermes Turn ──► laya-router plugin ──► Laya HA App (POST /v1/route)
                    │                       ▲
                    │                       │ (family, effort, confidence)
                    ▼                       │
              Gemini Provider  ─────────────┘
```

## Normative Service Contracts

### 1. Fail-Open Guarantee
The primary operational directive is: **Zero failed turns caused by the router.**
If the Laya Add-on times out (threshold: 1.0s), returns an HTTP 5xx error, or fails network connectivity, the Hermes plugin must fail open and leave the provider request completely unmodified.

### 2. Turn-Level Memoization & Immutability
- The router is invoked strictly on the **first request** of a user turn.
- The routing outcome (`model` and `reasoning_effort`) is memoized for the remainder of that turn's tool execution loop.
- Mid-turn model switching is prohibited to protect provider thought signatures and state consistency.

### 3. Stickiness Policy
- **Model Upgrades:** Dynamic upgrades (e.g. Flash to Pro) take effect immediately on a new turn.
- **Model Downgrades:** Downgrading within an existing conversation session is prevented to avoid cache thrashing.

### 4. Wire Protocol (`POST /v1/route`)
- **Request:**
  ```json
  {
    "prompt": "Bereken de annuïteit voor een hypotheek van 400k",
    "recent_turns": [
      {"role": "user", "content": "Wat kost een huis?"},
      {"role": "assistant", "content": "Dat hangt af van..."}
    ],
    "question_set": "hermes-v1",
    "session_id": "tg_thread_4842"
  }
  ```
- **Response:**
  ```json
  {
    "family": "deep",
    "effort": "high",
    "confidence": {
      "family": 0.88,
      "effort": 0.82
    },
    "checkpoint": "multilingual",
    "latency_ms": 42.5,
    "question_set": "hermes-v1",
    "provider": "gemini",
    "model": "gemini-2.5-pro"
  }
  ```

### 5. Multi-Provider Architecture & Single-Endpoint Limitation

#### The Single-Endpoint Constraint
Hermes Agent initializes provider adapters and authorization tokens at boot. The `llm_request` middleware hook allows rewriting the requested `model` and `effort` parameters, but **cannot dynamically switch provider credentials or API client adapters mid-session** (e.g., jumping from native `gemini` adapter to `anthropic` adapter on the fly).

#### Operating Models:
1. **Native Single-Provider Mode (Default - Gemini):**
   - Directly connects to Google Gemini API (`generativelanguage.googleapis.com`).
   - One API key (`GEMINI_API_KEY`).
   - Routes between `gemini-2.5-flash-lite`, `gemini-flash-latest`, and `gemini-2.5-pro`.
   - Zero gateway overhead; simplest and fastest path.

2. **Unified Multi-Provider Gateway Mode (LiteLLM / OpenRouter):**
   - Configures Hermes with `provider: openai` or `provider: openrouter` pointing to the unified gateway endpoint.
   - The gateway manages credentials and routing to diverse backends:
     - `quick`: `google/gemini-2.5-flash-lite`
     - `general`: `google/gemini-flash-1.5`
     - `code`: `anthropic/claude-3.5-sonnet`
     - `deep`: `anthropic/claude-3.7-sonnet` or `openai/o3-mini`
   - Laya Router returns the mapped model name for the selected gateway, allowing Hermes to stay on a single API endpoint while leveraging different underlying model vendors.
