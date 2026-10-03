# Product Requirements Document (PRD): HA System 1

**Version:** 0.1.0  
**Date:** October 3, 2026  
**Status:** In Review  
**Product Name:** HA System 1  
**App Slug:** `ha_system_1` (migrating from `local_laya`)  
**Repository:** `matthijsberg/HomeAssistant-Apps` (`apps/ha-system-1`)  

---

## 1. Executive Summary & Vision

Modern smart homes and autonomous AI agents rely heavily on Large Language Models (LLMs) for everyday decision-making, natural language understanding, and device control. However, sending simple requests (*"zet het licht in de woonkamer uit"* or *"wat is 10 + 15?"*) to cloud LLMs introduces major friction:
1. **High Latency:** 1.5 to 4.0 seconds per roundtrip, making voice control (Home Assistant Assist) feel sluggish and unnatural.
2. **Excessive Token Costs & Payload Bloat:** Injected agent memories (1,000+ tokens) and extensive tool definitions (3,000+ tokens) turn a 5-word turn into a 4,500+ token cloud API payload.
3. **Context Bleeding & Overkill Escalation:** Conversational history full of code or complex reasoning causes basic household commands to escalate to expensive Pro models (e.g. Gemini 2.5/3.1 Pro or Claude Opus).

**HA System 1** solves this by establishing a **local, sub-50ms System 1 cognitive reflex layer** for Home Assistant. Inspired by Daniel Kahneman's *Thinking, Fast and Slow*, HA System 1 operates directly on the local Intel Arc iGPU (Level-Zero / XPU) to instantly classify prompts, prune tools, gate memories, execute domotica commands via a zero-LLM fast path (<50ms), and route complex turns to calibrated upstream models (Gemini Flash Lite 3.5, Flash, and Pro 3.1).

---

## 2. Core Architectural Pillars

```text
                     User Prompt (Voice Assist, Telegram, HA Dashboard)
                                       │
                                       ▼
                       ┌───────────────────────────────┐
                       │          HA System 1          │
                       │    (FastAPI + Intel Arc XPU)  │
                       └───────────────┬───────────────┘
                                       │
         ┌─────────────────────────────┴─────────────────────────────┐
         │ Fast-Path Domotica (<50ms)                                │ System 1 Routing (<100ms)
         ▼                                                           ▼
┌──────────────────────────────┐                           ┌──────────────────────────────┐
│ HA Entity & Area Resolver    │                           │ Upstream Model & Effort Gate │
│ (3,600+ cached HA entities)  │                           │ - Dynamic Tool Pruning       │
└──────────────┬───────────────┘                           │ - Smart Memory Gating        │
               │                                           └──────────────┬───────────────┘
               ▼                                                          │
┌──────────────────────────────┐                                          ▼
│ Direct HA Core REST Call     │                           ┌──────────────────────────────┐
│ (Light, Switch, Climate, etc)│                           │ Upstream LLM Execution:      │
│ ⚡ Latency: ~50ms | Cost: €0 │                           │ • quick:     Flash Lite 3.5  │
└──────────────────────────────┘                           │ • smarthome: Flash Lite 3.5  │
                                                           │ • general:   Flash Latest    │
                                                           │ • code:      Flash Latest    │
                                                           │ • deep:      Pro 3.1         │
                                                           └──────────────────────────────┘
```

---

## 3. Product Scope & Functional Requirements

### 3.1. Sub-50ms Fast-Path Domotica Engine (`/v1/domotica/route`)
* **Objective:** Execute home automation actions instantly without cloud LLMs.
* **Universal Semantic Detection:**
  * Analyzes prompts for device domain classes (`lamp`, `licht`, `spot`, `ledstrip`, `thermostaat`, `verwarming`, `rolluik`, `gordijn`, `schakelaar`, etc.) in combination with control action verbs (`aan`, `uit`, `zet`, `doe`, `dim`, `open`, `sluit`).
  * Operates universally across any room, entity, or target without maintaining brittle hardcoded room lists.
* **Local Registry Resolution (`resolver.py`):**
  * Synchronizes 3,600+ entities and areas from Home Assistant Core API (`homeassistant_api: true`).
  * Maps natural language names (*"serre spots"*, *"eetkamer"*) to exact `entity_id` or `area_id`.
* **Execution Options:**
  * `execute: false`: Returns structured action payload + standard OpenAI tool call (`HassTurnOn`, `HassTurnOff`) for client dispatch.
  * `execute: true`: Executes the service call directly against the Home Assistant Core REST API within ~10ms.

### 3.2. OpenAI-Compatible Chat Completions Bridge (`/v1/chat/completions`)
* **Objective:** Enable Home Assistant Voice (Assist) and OpenAI Conversation to use HA System 1 as an ultra-fast local LLM backend.
* **Full Wire-Protocol Compliance:**
  * Supports streaming (Server-Sent Events `text/event-stream`) and standard JSON responses.
  * Emits native Home Assistant tool calls (`choices[0].message.tool_calls`) when domotica intents are recognized.
  * Forwards general queries smoothly to upstream models.

### 3.3. Upstream Model & Reasoning Effort Routing (`/v1/route`)
* **Objective:** Match user intent to the optimal model tier, token budget, reasoning effort, and temperature in a single local forward pass.
* **Task Families & Calibrated Upstream Models:**
  1. **`quick`:** Small talk, simple facts, general knowledge without tools.  
     * Model: `gemini-3.5-flash-lite`, `effort: low`, `thinking_budget: 0`, `needs_memory: false`, `allowed_tools: []`.
  2. **`smarthome`:** Home automation queries requiring multi-step verification.  
     * Model: `gemini-3.5-flash-lite`, `effort: low`, `thinking_budget: 0`, `needs_memory: false`, `allowed_tools: ["ha_*", "homeassistant*"]`.
  3. **`general`:** Writing, summarising, translating, standard tool workflows.  
     * Model: `gemini-flash-latest`, `effort: medium`, `temperature: 0.7`, `needs_memory: true`, `allowed_tools: ["*"]`.
  4. **`code`:** Software engineering, debugging, configuration, multi-step agent actions.  
     * Model: `gemini-flash-latest`, `effort: high`, `temperature: 0.1`, `thinking_budget: null` (unconstrained), `needs_memory: true`, `allowed_tools: ["*"]`.
  5. **`deep`:** Complex trade-offs, financial calculations, multi-step math, architectural evaluations.  
     * Model: `gemini-3.1-pro`, `effort: high`, `temperature: 0.2`, `thinking_budget: null` (unconstrained), `needs_memory: true`, `allowed_tools: ["*"]`.

### 3.4. Token-Reduction Optimizations (Agent Middleware)
* **Dynamic Tool Pruning:**
  * `quick`: Completely removes all 26+ tool definitions (-3,200 tokens).
  * `smarthome`: Filters down to Home Assistant API and MCP tools only (-2,800 tokens).
* **Smart Memory Gating:**
  * Strips `<memory-context>...</memory-context>` from user prompts for `quick` and `smarthome` tasks (-1,000 tokens).
* **Context Bleeding Prevention:**
  * Contextual history is only forwarded for short ambiguous follow-ups (<= 3 words, e.g. *"ja"*, *"waarom?"*).
  * Standalone sentences (>3 words) are classified purely on their own prompt signal, preventing prior coding discussions from escalating simple queries to Pro models.

---

## 4. Hardware & Infrastructure Requirements

* **Platform:** Home Assistant OS (HAOS) amd64.
* **Acceleration:** Intel Arc iGPU (Level-Zero compute via `/dev/dri`).
* **Base OS:** Ubuntu 24.04 (Noble) with official Intel GPU repository & Level-Zero runtime.
* **Weights Storage:** Persistent volume mounted at `/data/hf` (weights resident in fp32/bf16).
* **Preloading & Warmup:** Background JIT SPIR-V kernel precompilation at boot to guarantee instant first-turn response.
* **Security & Sandboxing:** Security Rating 7 in Home Assistant Supervisor (no unneeded root/kernel capabilities).

---

## 5. API Interface Specification

| Endpoint | Method | Purpose | Latency Target |
| :--- | :--- | :--- | :--- |
| `GET /health` | GET | Watchdog, readiness, and device reporting | <5 ms |
| `GET /v1/models` | GET | Returns active model mappings and family profiles | <10 ms |
| `POST /v1/domotica/route` | POST | Fast-Path smart home resolution & execution | **<50 ms** |
| `POST /v1/chat/completions`| POST | OpenAI wire protocol bridge for HA Assist | **<60 ms** |
| `POST /v1/route` | POST | Full System 1 task family and effort classifier | **<100 ms** |
| `POST /v1/domotica/sync` | POST | Forces immediate sync of HA entities/areas | ~300 ms |

---

## 6. Observability, Metrics & Success Criteria

1. **Latency:**
   * Domotica Fast-Path p95 latency: **<60 ms**.
   * Model Routing p95 latency: **<150 ms** on Intel Arc iGPU.
2. **Token Efficiency:**
   * >= 90% input token reduction on `quick` and `smarthome` tasks.
3. **Reliability:**
   * **Zero broken turns (100% fail-open):** Any local engine error or timeout leaves requests untouched and falls back to default models.
4. **Auditability:**
   * 100% of routing decisions logged to local JSONL (`audit.jsonl`) and tagged on Langfuse traces (`laya_routing` generation metadata).
