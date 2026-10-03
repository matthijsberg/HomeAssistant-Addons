# Product Requirements Document (PRD): HA System 1

**Version:** 0.4.0  
**Date:** October 3, 2026  
**Status:** Draft — end-state specification, in review  
**Owner:** @matthijsberg  
**Product Name:** HA System 1  
**App Slug:** `ha_system_1` (successor to the `laya` add-on, currently installed as `local_laya`)  
**Repository:** `matthijsberg/HomeAssistant-Apps` (`apps/ha-system-1`)  

> This document describes the **target end state**. It is intentionally independent of the current implementation; delivery happens as deltas against it (see §15). Requirement IDs (e.g. `FP-03`) are stable and meant to be referenced from issues and PRs. **MUST / SHOULD / MAY** follow RFC 2119.

---

## 1. Executive Summary & Vision

Modern smart homes and autonomous AI agents rely heavily on Large Language Models (LLMs) for natural language understanding, everyday decisions, and device control. Sending simple requests (*"zet het licht in de woonkamer uit"*, *"wat is 10 + 15?"*) to cloud LLMs introduces real friction:

1. **High latency:** 1.5–4.0 s per round trip makes voice control (Home Assistant Assist) feel sluggish.
2. **Payload bloat & token cost:** injected agent memories (~1,000 tokens) and tool definitions (~3,200 tokens across 26+ tools) turn a 5-word turn into a 4,500+ token request.
3. **Overkill escalation & context bleeding:** conversation history full of code or complex reasoning pushes basic household questions to expensive top-tier models.
4. **One-size-fits-all model choice:** a fixed default model overpays on simple turns and under-thinks on hard ones.
5. **Vendor lock-in:** model tiers, reasoning controls, and sampling rules differ per vendor, so routing logic tends to get welded to one provider.

**HA System 1** is a local "System 1" reflex layer for Home Assistant, named after the fast, intuitive mode of thinking in Daniel Kahneman's *Thinking, Fast and Slow*. It sits in front of cloud LLMs and does three things:

1. **Act** — executes simple, unambiguous home commands locally in under 50 ms, without any LLM.
2. **Decide** — classifies every other turn in a single local forward pass on the Intel Arc iGPU and picks the cheapest model tier, reasoning depth, tool set, and memory context that can answer it well.
3. **Trim** — removes tool definitions and memory the turn does not need, so the upstream request is smaller, faster, and cheaper.

Decisions are **provider-neutral**. Google Gemini is the default and reference provider. Anthropic, OpenAI, Qwen, GLM, and any OpenAI-compatible endpoint (LiteLLM, OpenRouter, vLLM, Ollama) are supported through adapters that translate one neutral policy into each vendor's parameters.

Personal data is pseudonymized locally before a request leaves the house. Every decision is logged and measurable, and every failure has a defined fallback.

### 1.1 Positioning vs. Home Assistant built-ins

Home Assistant Assist already ships a local intent engine (sentence templates, aliases, areas, floors, entity exposure) and the conversation-agent option **"Prefer handling commands locally"**, which tries local intents before calling the LLM. HA System 1 does **not** replace this; it is layered behind it:

| Layer | Handles | Cost / latency |
| :--- | :--- | :--- |
| 1. HA built-in local intents | Commands that match HA's sentence templates | €0, local |
| 2. HA System 1 fast path | Free-form command phrasings HA's templates miss (*"kun je de serre spots even uitdoen?"*) and commands arriving via non-Assist channels (Hermes / Telegram) | €0, < 50 ms |
| 3. HA System 1 router | Everything else: selects model tier, reasoning, tools, and memory | One upstream call on the smallest adequate model |

All success metrics are measured against a baseline with layer 1 enabled (§11).

### 1.2 Goals

* **G1 — Instant home control:** single-intent commands complete without a cloud round trip.
* **G2 — Lower spend at equal quality:** fewer and smaller upstream calls with no measurable loss in answer quality.
* **G3 — Never break a turn:** every failure mode has a defined fallback. The one deliberate exception: a PII filter failure blocks the request instead of sending personal data unfiltered (PII-06).
* **G4 — Never act unasked:** no device action unless the user clearly commanded it, on a target that exists and is exposed.
* **G5 — Explainable:** every decision can be inspected after the fact.
* **G6 — Provider-neutral:** switching or mixing LLM vendors is a configuration change, not a code change; routing quality does not depend on the vendor.

### 1.3 Non-goals

* Running a generative LLM locally.
* Replacing HA's built-in intent engine or the Assist pipeline (wake word, STT, TTS).
* Fast-path control of security-sensitive devices (locks, alarms, garage doors, gates, valves). These always go through the LLM path and HA's own confirmation flows.
* Multi-intent fast-path commands (*"doe het licht uit en de tv aan"*). These are routed to the LLM.
* Acting as a general multi-tenant LLM gateway for other clients. The add-on calls multiple providers for its own chat bridge; other clients that need multi-vendor access use LiteLLM / OpenRouter.
* Abstracting vendor-specific extras (provider-hosted web search, code execution, batch APIs, fast modes). These remain reachable only through per-model passthrough parameters (PA-13).
* Training or fine-tuning Laya checkpoints.

### 1.4 Users

* **Household members** using Assist by voice or text (Dutch first, English supported). They care about speed and correctness.
* **The operator** (home owner / admin). They care about cost, vendor choice, configuration, and auditability.
* **Hermes Agent** (machine client serving Telegram and dashboards). It needs a stable, fail-open decision API.

---

## 2. Architecture

### 2.1 Request flow

```text
 Voice Assist / HA chat                       Hermes Agent (Telegram, dashboard)
          │                                                  │
          ▼                                                  │
 HA built-in local intents ──match──► HA executes            │
          │ no match                                         │
          ▼                                                  ▼
 ┌──────────────────────────────────── HA System 1 ───────────────────────────────────┐
 │                                                                                    │
 │  ① Fast-path gate (CPU: rules + registry mirror)                       ≤ 20 ms     │
 │     imperative? single intent? existing, exposed, non-sensitive, unique target?    │
 │          │ yes                                      │ no                           │
 │          ▼                                          ▼                              │
 │   Emit HA intent tool call               ② System 1 router (Laya on Arc XPU)       │
 │   or execute via HA API                     one forward pass:                      │
 │   + local spoken confirmation               family · effort · data need ·          │
 │                                             personal context         ≤ 150 ms p95  │
 │                                                     │                              │
 │                                                     ▼                              │
 │                                          ③ Policy resolver (provider-neutral)      │
 │                                             family → model set → catalog model     │
 │                                             reasoning · output limit · tools ·     │
 │                                             memory · sampling                      │
 │                                                     │                              │
 │                                                     ▼                              │
 │                                          ④ PII filter + provider adapter           │
 │                                             neutral policy → vendor parameters,    │
 │                                             schema/stream/usage/error translation, │
 │                                             PII pseudonymize ↔ restore             │
 └─────────────────────────────────────────────────────┼──────────────────────────────┘
                                                       │
                         ┌─────────────────────────────┴───────────────────────┐
                         ▼                                                     ▼
          Chat bridge calls the provider                       Decision (neutral + translated
          directly (Assist path)                               params) returned to Hermes,
                         │                                     which calls its own provider
                         ▼
   Gemini · Anthropic · OpenAI · Qwen · GLM · OpenAI-compatible (LiteLLM, OpenRouter, vLLM, Ollama)
```

### 2.2 Component responsibilities

Policy is **decided** once, in neutral terms, by the add-on. It is **translated** by a provider adapter and **applied** by whichever component builds the upstream request.

| Component | Owns | Lives in |
| :--- | :--- | :--- |
| Fast-path engine | Command detection, target resolution, safety gates, confirmation text, optional execution | Add-on |
| Registry mirror | Live copy of HA entity / device / area / floor registries, aliases, Assist exposure | Add-on |
| System 1 router | Laya forward pass, confidence gating, neutral policy resolution | Add-on |
| Model catalog & model sets | Which concrete model serves each family, its capabilities, and its pricing | Add-on (shipped defaults + operator overrides) |
| Provider adapters | Translating the neutral policy, tools, streams, usage, and errors per vendor dialect | Add-on |
| PII filter | Detecting and pseudonymizing personal data in upstream payloads; restoring it in responses | Add-on |
| Chat bridge | OpenAI-compatible endpoint; applies the policy and calls the provider through an adapter | Add-on |
| Hermes plugin | Calls `/v1/route`, applies the returned policy, fails open, memoizes per turn | Hermes repo (contract in §8) |
| Audit & telemetry | JSONL audit log, Langfuse metadata, metrics, ingress UI | Add-on (Hermes adds its own trace tags) |

---

## 3. Fast-Path Domotica Engine

The fast path is **deterministic** (rules + registry), not model-based. It does not use the GPU or any LLM provider, which keeps it predictable, testable, and available while model weights are loading or providers are down.

### 3.1 Detection

* **FP-01 (MUST)** Vocabulary matching uses whole tokens (word boundaries), never substrings. *"spotify"* must not match *"spot"*; *"beeldscherm"* must not match *"scherm"*.
* **FP-02 (MUST)** Vocabulary (device nouns, action verbs, filler words, question markers, follow-up markers) lives in per-language data files (`nl`, `en` at launch), not in code. Room and device names come exclusively from the registry mirror (§4), never from vocabulary lists.
* **FP-03 (MUST)** Only imperatives qualify. Questions — a question mark, or a leading interrogative/inversion such as *staat, is, zijn, wat, hoe, welke, wanneer, waarom* / *is, are, what, how, when* — never take the fast path. They are routed (typically family `smarthome`).
* **FP-04 (MUST)** Single intent only. Prompts with more than one action or target (*"en", "and", "daarna", "then"*) are routed.
* **FP-05 (MUST)** Negations, conditions, and schedules (*"niet", "als", "tenzij", "over 10 minuten", "om 22:00"*) are routed.
* **FP-06 (MUST)** Numeric parameters are bound to the unit that qualifies them (*"40%"*, *"20,5 graden"*), never "the first number in the sentence". Out-of-range values (brightness/position outside 0–100, temperature outside the entity's `min_temp`/`max_temp`) abort the fast path.

### 3.2 Supported actions

| Action | Example | HA service | HA intent tool |
| :--- | :--- | :--- | :--- |
| On / off | *"doe de serre spots uit"* | `<domain>.turn_on` / `turn_off` | `HassTurnOn` / `HassTurnOff` |
| Brightness | *"zet de keukenlamp op 40%"* | `light.turn_on` + `brightness_pct` | `HassLightSet` (`brightness`) |
| Open / close cover | *"doe de rolluiken in de slaapkamer omlaag"* | `cover.open_cover` / `close_cover` | `HassTurnOn` / `HassTurnOff` (domain `cover`) |
| Cover position | *"zet het rolluik op 30%"* | `cover.set_cover_position` | `HassSetPosition` |
| Set temperature | *"zet de thermostaat in de woonkamer op 20,5 graden"* | `climate.set_temperature` | `HassClimateSetTemperature` |
| Activate scene | *"activeer scene film"* | `scene.turn_on` | `HassTurnOn` |
| Media pause / resume | *"pauzeer de muziek in de woonkamer"* | `media_player.media_pause` / `media_play` | `HassMediaPause` / `HassMediaUnpause` |
| Media next / previous | *"volgend nummer"* | `media_player.media_next_track` / `media_previous_track` | `HassMediaNext` / `HassMediaPrevious` |
| Media volume | *"zet de speaker in de keuken op 30%"*, *"zachter"* | `media_player.volume_set` | `HassSetVolume` |
| Media mute / unmute | *"zet de tv op stil"* | `media_player.volume_mute` | HA mute intent, where offered |

Eligible domains: `light`, `switch`, `fan`, `input_boolean`, `cover` (non-sensitive device classes), `climate`, `scene`, `media_player`. In tool-call mode, only intents that HA actually offers in the request's `tools` are emitted (CB-03); otherwise the turn is routed.

### 3.3 Target resolution

* **FP-07 (MUST)** Resolve targets against the registry mirror: entity names and aliases, device names, area names and aliases, floor names and aliases.
* **FP-08 (MUST)** Precedence: exact entity name/alias → exact area/floor name/alias (combined with the detected domain) → fuzzy entity match. A fuzzy match qualifies only if its score ≥ `fuzzy_min_score` (default 0.85) **and** it beats the runner-up by ≥ `fuzzy_min_margin` (default 0.15). Otherwise the target is ambiguous and the turn is routed.
* **FP-09 (MUST)** Targets are never invented. Text that does not resolve to an existing registry object is never slugified into an `area_id` or `entity_id`; the turn is routed.
* **FP-10 (MUST)** Only entities exposed to Assist are eligible. Area and floor targets expand to the exposed entities of the requested domain in that area/floor; if that set is empty, the turn is routed.
* **FP-11 (MUST)** Sensitive targets never take the fast path: domains `lock`, `alarm_control_panel`, `siren`, `valve`; `cover` entities with device class `garage`, `gate`, or `door`; plus an operator-configurable denylist of entity IDs, areas, and domains.
* **FP-18 (MUST)** *Media players.*
  * An area target or an unnamed target (*"pauzeer de muziek"*) resolves only to exposed players whose current state makes the action meaningful (e.g. `playing` for pause, `paused` for resume). If none or more than one qualify, the turn is routed.
  * Relative volume (*"harder", "zachter"*) is converted to an absolute level: the player's current volume ± `fast_path.volume_step` (default 10 percentage points), clamped to 0–100.
  * Starting specific content (*"speel Radio 1 in de keuken"*) needs a content search and is routed.

### 3.4 Output, execution & confirmation

* **FP-12 (MUST)** Every fast-path evaluation returns either a match or an explicit `rejected_reason` (`question`, `multi_intent`, `conditional`, `no_action`, `unknown_target`, `ambiguous_target`, `not_exposed`, `sensitive_target`, `out_of_range`, `mirror_stale`, `disabled`).
* **FP-13 (MUST)** Intent tool calls are valid OpenAI tool calls: `function.arguments` is a JSON-encoded **string**. Intent slots use names HA understands — `name` is the entity's name or alias, `area`/`floor` are area/floor names, `domain` is a list — never raw `entity_id`/`area_id` values.
* **FP-14 (MUST)** Direct execution (`mode: execute`) calls the HA service over the Core WebSocket API on the resolved entity IDs. It requires `fast_path.execute_enabled: true`.
* **FP-15 (MUST)** Execution status is truthful:
  * `confirmed` — target states reached (or already were in) the requested state within `confirm_timeout_ms` (default 1500).
  * `accepted` — HA accepted the call but the state change was not observed within the timeout.
  * `failed` — HA returned an error or the call timed out.

  A no-op is never reported as success.
* **FP-16 (MUST)** Every fast-path result carries a short, localized confirmation (`speech`) generated from templates in the prompt's language, e.g. *"Oké, de serre spots zijn uit."* Failures state what failed.
* **FP-17 (MUST)** Fast-path exceptions and timeouts fall through to the routed path and never surface as errors to the user.

---

## 4. Home Assistant Registry Mirror

* **RG-01 (MUST)** On start, load the entity, device, area, and floor registries, Assist exposure settings, and current states via the HA Core WebSocket API (`ws://supervisor/core/websocket`, using the Supervisor token).
* **RG-02 (MUST)** Subscribe to registry update events and apply them incrementally. New, renamed, re-aliased, or re-exposed entities are usable by the fast path within 60 s.
* **RG-03 (MUST)** Run a full resync every `registry.full_resync_minutes` (default 15) and on `POST /v1/domotica/sync`. Reconnect with exponential backoff on disconnect. If the mirror is older than `registry.max_staleness_minutes` (default 60), the fast path is disabled (`rejected_reason: mirror_stale`); routing continues unaffected.
* **RG-04 (MUST)** Normalize names for matching: lowercase, diacritics folded (*jaloezieën → jaloezieen*), punctuation stripped. Colloquial or compound names are handled through HA aliases, which the operator manages in HA, not in the add-on.
* **RG-05 (MUST)** Scale to ≥ 10,000 entities (3,600+ today) with fast-path resolution p95 ≤ 20 ms and full sync ≤ 3 s, without blocking request handling.

---

## 5. System 1 Router (`/v1/route`)

The router classifies **tasks**, not vendors. Its output is a provider-neutral policy; mapping that policy to a concrete model and vendor parameters is the job of model sets (§5.4) and provider adapters (§6). Routing accuracy is therefore measured once, independent of the provider.

### 5.1 Questions (single forward pass)

| Question | Options | Drives |
| :--- | :--- | :--- |
| `task_family` | Configured families (§5.3) | Model tier and family defaults |
| `effort` | `light` · `normal` · `deep` | Reasoning depth for families with `reasoning: auto` |
| `data_need` | `general_knowledge`: answerable from general knowledge alone · `live_or_external`: needs current information, device states, files, web, or actions | Tool pruning |
| `personal_context` | `impersonal`: answerable without knowing anything about the user · `about_the_user`: refers to the user's own life, people, preferences, plans, or earlier conversations | Memory gating |

* **RT-01 (MUST)** All questions are answered in one Laya forward pass. Criteria wording follows the decision-engine rules: describe task types, no superlatives, no yes/no option keys, no score questions.
* **RT-02 (MUST)** Question sets are versioned (`ha-system-1-v1`, …). `GET /v1/question-sets` returns the **effective** runtime criteria, including family criteria injected from add-on options.
* **RT-03 (MUST)** The latency budget (§10.1) covers the full pass with all questions. If `data_need`/`personal_context` cannot fit the budget (decided in M0), tool and memory policy fall back to family defaults only.

### 5.2 Context handling (context-bleed prevention)

* **RT-04 (MUST)** Laya receives `context` (at most the 2 previous turns, truncated to fit its ~1,024-token window) **only** when the request is a follow-up. Follow-up detection is a deterministic, per-language rule: the request has ≤ `followup_max_words` words (default 6) **or** contains a reference marker (*hem, haar, die, dat, deze, ook, nog, hetzelfde, ja, nee, doe maar, ga door, waarom* / *it, that, this, same, also, yes, no, go on, why*). Markers live in data files.
* **RT-05 (MUST)** Standalone requests are classified on the request text alone.
* **RT-06 (SHOULD)** The follow-up rule is evaluated with the context-bleed and follow-up-miss metrics (§11.2). It MAY be replaced by a Laya question if that measures better.

### 5.3 Task families (defaults, provider-neutral)

| Family | Criteria | Reasoning | `max_output_tokens` | Tools | Memory |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `quick` | small talk, greetings, simple facts, unit conversions, general knowledge that needs no live data or tools | off | 1024 | none | gated |
| `smarthome` | controlling or checking Home Assistant devices, scenes, sensors, and automations | off | 1024 | `ha_*`, `homeassistant*`, `Hass*` | gated |
| `general` | everyday writing, explaining, summarising, translating, ordinary questions that need a few tool calls | auto (Laya effort) | 4096 | `*` | full |
| `code` | writing or debugging code or configuration files, multi-step tool or agent work | high | 8192 | `*` | full |
| `deep` | hard reasoning where a wrong answer is costly: maths, finance, planning, comparing complex options | high | 8192 | `*` | full |

* The default (fallback) family is `general`.
* `temperature` is unset (provider default) for every family. A family MAY set it; adapters drop it where the model rejects or discourages non-default sampling (PA-04).
* `reasoning_budget_tokens` is unset (unconstrained) unless the operator sets a cap.

* **RT-07 (MUST)** Families are operator-configurable (add / remove / edit) in add-on options. Custom families are injected as `task_family` options at runtime.
* **RT-08 (MUST)** Families never contain vendor-specific parameters or model IDs by default. A family MAY set a `model` override (a catalog alias, §6.2) that takes precedence over the active model set.

### 5.4 Model sets

A **model set** maps each family to a catalog model (§6.2), with an optional ordered fallback list. One model set is active for the chat bridge (`active_model_set`). Callers of `/v1/route` MAY select another set per request. Shipped defaults:

| Family | `gemini` (default) | `anthropic` | `openai` | `qwen` | `glm` |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `quick` | `gemini-3.5-flash-lite` | `claude-haiku-4-5` | *pin at calibration* | *pin at calibration* | *pin at calibration* |
| `smarthome` | `gemini-3.5-flash-lite` | `claude-haiku-4-5` | *pin at calibration* | *pin at calibration* | *pin at calibration* |
| `general` | `gemini-flash-latest` (alias, D-12) | `claude-sonnet-5-5` | *pin at calibration* | *pin at calibration* | *pin at calibration* |
| `code` | `gemini-flash-latest` (alias, D-12) | `claude-sonnet-5-5` | *pin at calibration* | *pin at calibration* | *pin at calibration* |
| `deep` | `gemini-3.1-pro` | `claude-opus-5-5` | *pin at calibration* | *pin at calibration* | *pin at calibration* |

Operators MAY define mixed sets (e.g. Gemini for `quick`/`smarthome`/`general`, Anthropic for `code`/`deep`).

* **RT-09 (MUST)** Catalog models MAY use a pinned version or a floating alias (e.g. `gemini-flash-latest`). For aliases, the resolved version is tracked per response (PA-18). When it changes, every model set using that alias is flagged `drifted` until it is re-evaluated. Routing continues unchanged.
* **RT-10 (MUST)** A model set is marked `calibrated` only after it passes the quality and cost targets of §11 on the eval set. `/v1/models` and `/health` report calibration status per set (`uncalibrated` · `calibrated` · `drifted`).
* **RT-11 (MUST)** At startup and on config change, every family in every set must resolve to a catalog model on a configured, reachable provider. Families that don't resolve fall back to the set's default-family model, and `/health` reports `degraded` with the reason.

### 5.5 Policy resolution

* **RT-12 (MUST)** *Confidence gating.* Family confidence < `min_confidence` (default 0.60) → default family with `fallback_reason: low_confidence`. Effort confidence < `min_confidence` → `medium` for families with `reasoning: auto`.
* **RT-13 (MUST)** *Reasoning.* Neutral levels are `off · low · medium · high · max`. If the family pins a level, that level is used; `auto` maps Laya effort `light → low`, `normal → medium`, `deep → high`. `max` is only reachable through a family pin. The adapter translates the level per model (PA-02).
* **RT-14 (MUST)** *Tool policy.* Tools are filtered to the family allowlist (glob patterns). A turn classified into a family with an **empty** allowlist but `data_need = live_or_external` (confidence ≥ `prune_confidence`, default 0.75) is promoted to the default family, so "what's the weather?" never reaches a tool-less model.
* **RT-15 (MUST)** *Memory policy.* Family `memory` is `full`, `gated`, or `none`. `gated` strips the `<memory-context>…</memory-context>` block unless `personal_context = about_the_user`. If the `impersonal` confidence is below `prune_confidence`, memory is kept. Uncertainty always keeps memory. Stripping only ever touches the newest user message (see PA-08).
* **RT-16 (MUST)** *Session policy* (decision D-1):
  * Upgrades apply immediately.
  * Downgrades within a session are allowed only for standalone requests (RT-05) with family confidence ≥ `downgrade_min_confidence` (default 0.80).
  * Routing happens once per user turn and is memoized for that turn's tool loop; there is no mid-turn model switching.
* **RT-17 (MUST)** *Reachability.* The router never returns a model on a provider the caller cannot reach. Callers declare `reachable_providers` (or select a `model_set`). If a family's model is unreachable, the family's fallback list is tried, then the default family.
* **RT-18 (MUST)** Once the engine is loaded, `/v1/route` always returns 200 with a decision, including `fallback: true` and a `fallback_reason` when defaults were applied. It returns 503 with `Retry-After` only while weights are loading.

---

## 6. Provider Abstraction Layer

### 6.1 Concepts

| Concept | Definition |
| :--- | :--- |
| **Provider connection** | A named endpoint: dialect, base URL, credentials, data policy. Several can be active at once. |
| **Dialect** | The wire protocol an adapter speaks: `gemini` (native `generateContent`), `anthropic` (Messages API), `openai_chat` (Chat Completions), `openai_responses` (Responses API). One vendor may offer several dialects: GLM serves OpenAI Chat, OpenAI Responses, and Anthropic Messages; Gemini serves native and OpenAI-compatible; Qwen (DashScope) serves OpenAI-compatible. |
| **Catalog model** | An alias → provider connection + upstream model ID + capability profile + pricing. |
| **Model set** | A family → catalog model mapping with fallbacks (§5.4). |
| **Neutral policy** | What the router decides (§6.3); never vendor-specific. |

### 6.2 Capability profile (per catalog model)

The capability profile, not code branches, tells an adapter how to translate a neutral policy:

| Field | Values / meaning |
| :--- | :--- |
| `reasoning.control` | `none` · `toggle` (on/off only) · `levels` · `budget` · `levels_and_budget` · `always_on` (cannot be disabled) |
| `reasoning.map` | Neutral level → provider value, e.g. `{off: "between_tools", low: "low", …}` |
| `reasoning.counts_toward_output_limit` | Whether reasoning tokens consume the provider's output limit |
| `sampling` | `free` · `default_only` (non-default temperature rejected or discouraged) · `default_when_reasoning` |
| `recommended_sampling` | Optional per-mode values (e.g. separate values for thinking and non-thinking mode) |
| `tools` | Supported; parallel calls; forced `tool_choice` supported; schema dialect (`json_schema` · `json_schema_subset`); name constraints |
| `reasoning_state` | `none` · `replay_within_turn` (artifacts must be returned during the tool loop) · `bound_to_history` (artifacts are tied to the exact model and conversation; history must be append-only) |
| `caching` | `implicit` · `explicit_breakpoints` · `none`; minimum cacheable prefix |
| `limits` | Context window, maximum output tokens |
| `version_tracking` | `pinned` · `alias` (resolved version read from each response where the provider reports it) |
| `pricing` | Input, cached input, cache write, and output per 1M tokens, with `as_of` date |
| `calibration` | `uncalibrated` · `calibrated` · `drifted`, per model set (§11, RT-10) |

### 6.3 Neutral policy (router output)

| Field | Values | Meaning |
| :--- | :--- | :--- |
| `reasoning` | `off · low · medium · high · max` | Requested thinking depth |
| `reasoning_budget_tokens` | int or null | Optional hard cap on thinking tokens |
| `max_output_tokens` | int | Budget for the visible answer, excluding reasoning |
| `temperature` | float or null | null = provider default |
| `tools` | `none` or `allowlist` + glob patterns | Tool pruning |
| `tool_choice` | `auto · none · required` | Passed through from the client |
| `memory` | `keep · strip` | Memory gating |

### 6.4 Adapter requirements

* **PA-01 (MUST)** The router, families, and model sets never emit vendor-specific parameters. Adapters translate the neutral policy using the catalog model's capability profile.
* **PA-02 (MUST)** *Reasoning translation.* Map each neutral level to the model's nearest supported value via `reasoning.map`. If `off` is unsupported (`always_on`), use the lowest available level. A `reasoning_budget_tokens` cap is applied only where the model supports budgets; otherwise it is converted to the nearest level. Both the requested and the applied value are recorded in the decision and the audit log.
* **PA-03 (MUST)** *Output limit.* If reasoning counts toward the provider's output limit, the adapter sets that limit to `max_output_tokens` plus a reasoning allowance (the budget, or a per-level default from the catalog), capped at the model maximum and satisfying any provider ordering rule (e.g. a thinking budget must be below `max_tokens`).
* **PA-04 (MUST)** *Sampling.* The adapter never sends a parameter the model is known to reject. Temperature and other sampling parameters are dropped for `default_only` models and for `default_when_reasoning` models while reasoning is on. Dropped parameters are recorded (`dropped_params`). If a provider rejects a parameter as unsupported before the first token — for example after an alias moved to a new model generation — the adapter retries once without it and flags the catalog entry for review.
* **PA-05 (MUST)** *Tools.* Translate tool definitions between dialects (OpenAI `function` ↔ Anthropic `input_schema` ↔ Gemini function declarations). Strip JSON-Schema keywords a dialect does not support, normalize tool names to provider constraints with a reversible mapping, and preserve or map tool-call IDs so clients see their original names and IDs.
* **PA-06 (MUST)** *Tool choice.* Map `required` to the provider equivalent. Where forced tool choice is unsupported, degrade to `auto` and record it.
* **PA-07 (MUST)** *Reasoning state within a turn.* Clients such as HA's conversation integrations speak Chat Completions and drop vendor fields like thought signatures or thinking blocks. For `replay_within_turn` and `bound_to_history` models, the bridge therefore stores reasoning artifacts server-side, keyed by turn and tool-call ID (TTL 15 min). It re-attaches them unchanged on the next request of the same turn.
* **PA-08 (MUST)** *Append-only history.* Transformations never modify earlier turns:
  * Memory gating touches only the newest user message.
  * Tool-set and model changes happen only at turn boundaries.
  * When the model changes between turns, reasoning artifacts produced by a different model are omitted from the history sent upstream.
* **PA-09 (MUST)** *Usage.* Normalize usage into `input`, `cached_input`, `cache_write`, `output`, and `reasoning` tokens, and compute cost from catalog pricing.
* **PA-10 (MUST)** *Errors.* Normalize errors to `rate_limited` (with retry-after), `overloaded`, `context_length_exceeded`, `invalid_request`, `auth`, `refused` (provider safety/refusal stop), and `timeout`. These drive the fallback rules in §10.2.
* **PA-11 (MUST)** *Streaming.* Translate each dialect's stream into OpenAI chunks: text deltas, tool-call deltas with `index`, mapped `finish_reason`, and usage in the final chunk. Reasoning content is not forwarded to clients unless `expose_reasoning: true`.
* **PA-12 (MUST)** *Caching.*
  * Tool lists and system prompts are serialized deterministically (stable order, stable JSON) so identical policies produce identical prefixes.
  * For `explicit_breakpoints` models, the adapter places cache breakpoints after the stable prefix (tools + system).
  * Cached-token counts are reported in usage.
* **PA-13 (MAY)** *Passthrough.* A catalog model MAY carry `extra_params` (vendor-specific flags), merged last. The router never sets them.
* **PA-14 (MUST)** *Conformance.* Every adapter passes a shared conformance suite against recorded fixtures: plain text, streaming, a tool-call round trip including reasoning replay, each reasoning level, sampling drops, refusal and error mapping, and usage normalization. A live smoke test per provider runs on demand.
* **PA-15 (MUST)** *Unknown models.* Operator-added catalog entries without a capability profile get a conservative default: no reasoning control, temperature allowed, tools supported, no caching assumptions, calibration status `uncalibrated`.
* **PA-16 (MUST)** *Native dialects first.* Use the vendor's native dialect when the OpenAI-compatible variant loses reasoning, caching, or reasoning-state control (Gemini, Anthropic). `openai_chat` is the universal dialect for OpenAI, Qwen, GLM, gateways, and self-hosted servers.
* **PA-17 (SHOULD)** *Data policy per connection.* Each provider connection declares `send_memory` (default `true`), `pii_filter` (default `pseudonymize`, §6.5), and optionally `allowed_families`. The policy resolver never sends memory, or families outside the allowlist, to a connection that disallows them (e.g. for data-jurisdiction reasons); it uses fallbacks instead.
* **PA-18 (MUST)** *Version tracking.*
  * Record the resolved model version of every response where the provider reports it.
  * A change behind an alias flags the affected model sets `drifted` (RT-09) and shows in `/health` and the UI.
  * For pinned versions, warn at least 30 days before a provider-announced retirement, where the provider publishes one.

### 6.5 PII Filter

Memory and conversation history are sent upstream by default (`send_memory: true`). The PII filter limits which personal identifiers travel with them, and runs locally before a request leaves the house.

* **PII-01 (MUST)** Every upstream payload the add-on sends passes the filter: all messages (system, user, assistant, tool results, memory), not just the newest prompt. The mode is set per provider connection: `off` · `redact` · `pseudonymize` (default `pseudonymize`).
* **PII-02 (MUST)** Deterministic detectors, always available:
  * validated patterns: email addresses, phone numbers (Dutch and E.164), IBAN (mod-97), BSN (11-proef), payment cards (Luhn), Dutch postcode + house number and street addresses, IP addresses, Dutch licence plates, precise GPS coordinates;
  * dictionaries from Home Assistant: `person` names, HA user names, zone names;
  * an operator-maintained list of extra terms (e.g. family names, the home street).
* **PII-03 (MAY)** Model-based named-entity recognition (NER) on the iGPU, opt-in via `pii.ner: true`, for free-text names and places that HA doesn't know (multilingual). It runs in addition to the deterministic detectors and is skipped, not blocking, if it exceeds its latency budget.
* **PII-04 (MUST)** *Pseudonymization.* Each value is replaced with a typed placeholder (`[PERSON_1]`, `[EMAIL_1]`, …).
  * Placeholders are numbered in order of first appearance across the request's full message history. They are therefore stable across turns of an append-only conversation without server-side state, and keep prompt caching intact (PA-12).
  * The mapping lives in memory for the duration of the request (bridge), or per `session_id` with a 24 h TTL (Hermes endpoints, PII-07).
  * The mapping is never logged or persisted.
* **PII-05 (MUST)** *Restoration.* Placeholders in upstream responses are restored before they reach the client. This covers text (buffered across stream-chunk boundaries) and tool-call arguments, so HA executes tool calls with the real values and users see real names. `redact` mode replaces values irreversibly (`[EMAIL]`) and restores nothing.
* **PII-06 (MUST)** *Fail closed.* If the filter fails on a connection that requires it, the turn is not sent to that connection. The bridge tries fallbacks on connections whose policy allows them, otherwise it returns an error. This is the one deliberate exception to G3.
* **PII-07 (MUST)** *Hermes path.* Hermes builds its own upstream payload, so the add-on offers `POST /v1/pii/redact` and `POST /v1/pii/restore` (session-scoped). `/v1/route` returns the target connection's `pii_filter` mode, and the Hermes plugin applies it.
* **PII-08 (MUST)** Audit-log excerpts and Langfuse exports always pass the filter in `redact` mode, regardless of provider settings.
* **PII-09 (MUST)** The filter reduces exposure; it does not guarantee anonymity. Free-text names unknown to HA are only caught with NER, and tasks that depend on the literal value (spelling a name, judging an email domain) may degrade. Both effects are measured (§11.2).

### 6.6 Initial adapter scope

| Dialect | Vendors covered | Milestone |
| :--- | :--- | :--- |
| `gemini` | Google Gemini (reference) | M4 |
| `openai_chat` | OpenAI, Qwen (DashScope compatible mode), GLM (Z.ai), LiteLLM, OpenRouter, vLLM, Ollama | M4 |
| `anthropic` | Anthropic; GLM via its Anthropic-compatible endpoint | M6 |
| `openai_responses` | OpenAI (reasoning-item replay), GLM | Backlog (B-1) |

Appendix A gives the initial parameter mapping per vendor.

---

## 7. OpenAI-Compatible Chat Bridge (`/v1/chat/completions`)

* **CB-01 (MUST)** Implement OpenAI Chat Completions — non-streaming and SSE streaming — well enough for HA conversation integrations and the official `openai` SDK:
  * Request fields: `messages`, `tools`, `tool_choice`, `stream`, `max_tokens` / `max_completion_tokens`, `temperature`, `user`.
  * Tool-call `arguments` are JSON strings, and streamed tool-call deltas carry `index`.
  * `usage` reports real, normalized token counts (PA-09): upstream usage for routed turns, zero for local turns.
* **CB-02 (MUST)** Model selection:
  * `model: "ha-system-1"` (or `"auto"`) routes the turn using the active model set.
  * `"ha-system-1/<family>"` forces a family.
  * `"ha-system-1@<model_set>"` selects a model set.
  * A catalog alias is sent to that model unrouted (routing bypass).
* **CB-03 (MUST)** *Fast path in the bridge.* Applies only when:
  * the last message is a new user message, **and**
  * the required HA intent tool is present in the request's `tools`, **and**
  * the arguments validate against that tool's JSON schema.

  The bridge emits the tool call; HA executes it, which also enforces HA's own exposure and permissions. The bridge never executes directly on this path (D-4).
* **CB-04 (MUST)** *Tool-result turn.* When the trailing messages are `tool` results for a fast-path call (tool-call ID prefix `call_hs1_`), the bridge replies with a final assistant message — a local confirmation built from the result, `finish_reason: "stop"` — and never re-emits the tool call. If the result reports failure, the conversation is forwarded upstream as family `smarthome` to recover.
* **CB-05 (MUST)** *Routed turn.* For a routed turn, the bridge:
  * classifies the latest user message (§5);
  * applies the neutral policy;
  * calls the selected catalog model through its adapter (§6);
  * returns the response in OpenAI format, streamed if requested.
* **CB-06 (MUST)** Tool-result turns for routed (non-fast-path) tool calls reuse the memoized turn decision (same model, same tool set, re-attached reasoning state per PA-07) and are not reclassified.
* **CB-07 (MUST)** *Fail-open.*
  * If routing fails or exceeds `route_timeout_ms` (default 300), forward the request with the default family's policy on the active model set.
  * On the **first** upstream request of a turn, a `rate_limited`, `overloaded`, or `timeout` error before the first token moves to the next model in the family's fallback list, which may be on another provider.
  * Mid-turn failures retry the same model with backoff (no mid-turn switching).
  * Errors that cannot be recovered are returned as OpenAI-format errors, never as truncated or malformed streams.
* **CB-08 (MUST)** Credentials for all provider connections are configured in add-on options as password fields.
* **CB-09 (MUST)** Validated end-to-end against at least one HA conversation integration that supports a custom OpenAI-compatible base URL (OQ-1), against the official `openai` Python SDK, and against each shipped model set.

---

## 8. Hermes Integration Contract

* **HM-01 (MUST)** Hermes calls `POST /v1/route` once per user turn with:
  * the prompt, up to 2 recent turns, and `session_id`;
  * `fast_path: off | detect | execute`;
  * the providers it can reach (`reachable_providers`) or a `model_set`.
* **HM-02 (MUST)** Client timeout is 1.0 s. Any timeout, non-200 response, or network error leaves the upstream request completely unmodified.
* **HM-03 (MUST)** Hermes applies the decision in one of two ways:
  * the neutral policy (if Hermes has its own adapter), or
  * the pre-translated `provider_params` for its provider connection.

  In both cases it applies the tool allowlist and memory policy, and enforces RT-16 memoization within the turn.
* **HM-04 (MUST)** *Single-endpoint constraint.* Hermes initializes its provider client at boot and cannot switch vendor credentials mid-session. It therefore only receives models on its declared reachable providers (RT-17). Multi-vendor routing for Hermes uses a gateway (LiteLLM / OpenRouter): a provider connection with dialect `openai_chat`, whose catalog entries use gateway model names.
* **HM-05 (MAY)** With `fast_path: execute` and status `confirmed` or `accepted`, Hermes replies with the returned `speech` and skips the LLM call (zero-LLM turn).
* **HM-06 (MUST)** Hermes tags its Langfuse generation with the `routing_id` and decision metadata (§11.3).

---

## 9. Token Budget Model

The prunable portion of a typical Hermes request is the tool definitions (~3,200 tokens, 26+ tools) and the memory block (~1,000 tokens). Expected effect per family:

| Family | Tool definitions | Memory block | Expected effect |
| :--- | :--- | :--- | :--- |
| `quick` | removed | stripped unless personal | Request ≈ system prompt + prompt |
| `smarthome` | HA tools only (~400 tokens) | stripped unless personal | Request ≈ system prompt + HA tools + prompt |
| `general` / `code` / `deep` | full (or family allowlist) | kept | Unchanged; benefit comes from model choice |

These figures are illustrative; real numbers are measured in M0 (§15).

Pruning changes the request prefix and therefore reduces prompt-cache hits. Caches are also model-scoped, so a model switch starts a fresh cache. Cost is therefore measured on **billed** tokens, including cached-token discounts, per model set, not on raw token counts.

---

## 10. Non-Functional Requirements

### 10.1 Latency (p95, measured in-add-on, warm, Intel Core Ultra 5 225H)

| Path | Target |
| :--- | :--- |
| Fast-path decision (detect only) | ≤ 20 ms |
| `/v1/domotica/route` and bridge fast path end-to-end, excluding HA service execution | ≤ 50 ms |
| `/v1/route` on Arc iGPU (XPU) | ≤ 150 ms (stretch: ≤ 100 ms) |
| `/v1/route` on CPU fallback | ≤ 500 ms |
| PII filter, deterministic detectors, typical bridge payload | ≤ 15 ms |
| Bridge overhead on routed turns (time to first token vs. calling the provider directly, including the PII filter), any provider | ≤ 200 ms |
| `GET /health` | ≤ 5 ms |
| `GET /v1/models` | ≤ 10 ms |

HA service execution time and provider latency are recorded and reported separately. They are not targets, because they depend on the device, integration, or vendor.

### 10.2 Reliability

* **RL-01 (MUST)** Zero turns broken by HA System 1. Every failure mode has a defined fallback:

| Failure | Behaviour |
| :--- | :--- |
| Weights still loading | `/v1/route` returns 503 + `Retry-After`; Hermes fails open; bridge uses the default family policy; fast path remains available |
| Laya inference error or timeout | Default decision with `fallback_reason` |
| Registry mirror unavailable or stale | Fast path disabled; routed path continues |
| HA service call fails | Status `failed`; bridge forwards upstream; Hermes lets the LLM handle the turn |
| Provider rate-limited / overloaded / timed out before first token | Next model in the family's fallback list (any provider), then the default family |
| Provider error mid-turn | Retry the same model with backoff; then an OpenAI-format error |
| Provider refusal | Returned to the client as a normal assistant refusal with `finish_reason` mapped; no silent retry on another vendor unless the catalog model enables provider-native fallback |
| Catalog model unresolvable or misconfigured | Family falls back per RT-11; `/health` reports `degraded` |
| Alias resolves to a new model version | Routing continues; model set flagged `drifted` (RT-09); unsupported parameters retried without (PA-04) |
| PII filter fails on a connection that requires it | Turn is not sent to that connection; fallbacks on permitted connections, otherwise an OpenAI-format error (PII-06) |
| XPU unavailable | CPU fallback; `/health` reports `degraded: xpu_unavailable` |
| `laya` package or weights unavailable | `/health` reports `degraded`; router runs in pass-through mode (default decision). The mock router is only ever used with an explicit `LAYA_MOCK_MODE`, never implicitly |

* **RL-02 (MUST)** `GET /health` reports `ok | loading | degraded` with reasons, active device, loaded checkpoints, weight revision, registry mirror age and entity count, provider connection status, and model-set validation and calibration. The add-on manifest configures the Supervisor `watchdog` against `/health`.
* **RL-03 (MUST)** Ready within 60 s of start with cached weights. First install downloads ~3 GB to the weights path (PK-03).
* **RL-04 (MUST)** At boot, warmup forward passes over representative input lengths precompile Level-Zero / SPIR-V kernels, so the first user turn meets the latency target.

### 10.3 Resources & packaging

* **PK-01 (MUST)** Base image: Ubuntu 24.04 (Noble) with the official Intel GPU repository, Level-Zero runtime, and PyTorch XPU. `laya`, `torch`, and driver packages are version-pinned for reproducible builds. Provider adapters use plain HTTP clients or the vendors' official SDKs, also pinned.
* **PK-02 (MUST)** Platform: Home Assistant OS, amd64, Intel Arc iGPU via `/dev/dri`. CPU-only operation is supported as a degraded mode.
* **PK-03 (MUST)** Laya weights:
  * The weight revision is pinned (`laya_revision` = commit SHA) and actually applied at load time.
  * Weights are stored in `/share/ha_system_1/hf` (option `weights_path`), so they survive reinstalls and slug changes.
  * Other add-ons with `/share` access could modify them, so files are verified against the pinned revision's checksums at load; a mismatch triggers a re-download.
  * `/share` is part of HA's full backups, so the docs state the ~3 GB size and how to exclude the folder.
* **PK-04 (SHOULD)** Document resident RAM with both checkpoints loaded, and allow loading a single checkpoint to reduce footprint.

### 10.4 Security

* **SC-01 (MUST)** All `/v1/*` endpoints require a bearer token, except requests that genuinely arrive via HA Ingress. Ingress is verified by source address (the Supervisor ingress proxy, `172.30.32.2`), never by the presence of a header such as `X-Ingress-Path` alone.
* **SC-02 (MUST)** The add-on never serves non-ingress requests without authentication. If no `api_key` is configured, one is generated at first start, stored in `/data`, and shown in the ingress UI.
* **SC-03 (MUST)** Token comparison is constant-time. Secrets (add-on API key, all provider credentials, Langfuse keys) are `password` schema fields and never logged, echoed in `/v1/models`, or written to the model catalog file.
* **SC-04 (MUST)** Fast-path execution is limited to exposed, non-sensitive entities (FP-10, FP-11), regardless of the Supervisor token's broader permissions.
* **SC-05 (MUST)** No `privileged`, `full_access`, `host_network`, `docker_api`, or `host_pid`; a custom AppArmor profile; port 8000 not published to the host by default. Target: the highest Supervisor security rating achievable with `homeassistant_api` and `/dev/dri` access (HA's scale tops out at 6).

### 10.5 Privacy

* **PV-01 (MUST)** The audit log stores prompt text according to `audit.prompt_mode` (`none | truncated | full`, default `truncated` at 200 characters). `<memory-context>` content is never logged, and stored excerpts are PII-redacted (PII-08).
* **PV-02 (MUST)** The audit log rotates daily and is retained for `audit.retention_days` (default 30).
* **PV-03 (MUST)** Add-on-side Langfuse export is disabled by default. Both self-hosted Langfuse and Langfuse Cloud are supported. Only decision metadata is sent unless prompt export is explicitly enabled, and exported text is always PII-redacted (PII-08).
* **PV-04 (MUST)** Per-connection data policies (PA-17, §6.5) are enforced before any request leaves the add-on, and every decision records which provider received the turn.

---

## 11. Success Metrics & Evaluation

### 11.1 Evaluation set & baseline

* **EV-01 (MUST)** A labelled evaluation set of ≥ 500 prompts (≥ 60% Dutch), drawn from anonymised audit logs plus synthetic edge cases. It covers:
  * single-intent commands and questions about device state;
  * follow-ups, and switches from code turns to household turns;
  * personal-memory questions and live-data questions;
  * commands for sensitive devices, ambiguous names, and non-existent targets;
  * media commands with zero, one, or several active players;
  * prompts and memory containing personal data (names, addresses, emails, IBANs), labelled with the identifiers that must not leave the house in clear.

  Each prompt is labelled with family, effort, `data_need`, `personal_context`, follow-up status, and expected fast-path outcome (target, or "must not fast-path").
* **EV-02 (MUST)** Routing and fast-path accuracy run in CI on CPU; they are provider-independent and measured once. Latency benchmarks run on the target hardware for every release.
* **EV-03 (MUST)** Baseline per model set: the same traffic with HA local intents enabled, no router, and every turn sent to that set's `general` model with full tools and memory.
* **EV-04 (MUST)** Quality and cost targets are evaluated **per model set**. A set ships as `calibrated` only when it meets them (RT-10).

### 11.2 Targets

| Area | Metric | Target |
| :--- | :--- | :--- |
| Safety | Unintended or wrong-target fast-path actions (eval set + production audit) | **0** |
| Safety | Fast path taken on questions or sensitive devices | **0** |
| Fast path | Coverage of single-intent commands that HA local intents miss | ≥ 70% |
| Routing | Family accuracy | ≥ 90% |
| Routing | Under-routing: true `deep`/`code` turns sent to `quick`/`smarthome` | ≤ 2% |
| Routing | Context bleed: standalone household/quick prompts after code turns routed to `code`/`deep` | ≤ 3% |
| Routing | Follow-up miss: follow-ups classified without needed context into the wrong family | ≤ 5% |
| Gating | Tools removed although needed | ≤ 2% |
| Gating | Memory stripped although needed | ≤ 2% |
| Privacy | Identifiers of deterministic types (PII-02) and HA-known names sent upstream in clear on filtered connections | **0** |
| Privacy | Free-text names unknown to HA sent in clear (with NER on) | Measured; target set after M7 |
| Quality (per set) | Routed answers, with the PII filter on, judged worse than that set's baseline (blind pairwise review) | ≤ 5% of eval turns |
| Cost (per set) | Billed input tokens on `quick` / `smarthome` turns vs. baseline | ≥ 80% reduction (confirm after M0) |
| Cost (per set) | Monthly upstream spend vs. baseline at equal traffic | ≥ 40% reduction (confirm after M0) |
| Adapters | Conformance suite pass rate per dialect | 100% |
| Latency | All targets in §10.1 | met |
| Reliability | Turns broken by HA System 1 | **0** |
| Auditability | Decisions logged | 100% |

### 11.3 Observability

* **OB-01 (MUST)** Every decision (route, bridge, fast path) is appended to `/data/audit.jsonl` with these fields:
  * identity: `ts`, `routing_id`, `session_id`, `surface`, `language`;
  * the prompt, according to PV-01;
  * the decision: `family`, confidences, `laya_effort`, `context_used`;
  * routing target: `model_set`, `provider`, catalog `model`, `upstream_model`, resolved model version (PA-18);
  * privacy: filter mode and placeholder counts per type (never values);
  * reasoning: requested vs. applied level, `dropped_params`;
  * gating: tool counts before/after pruning, `memory_stripped`;
  * normalized usage and cost;
  * fast path: outcome and `rejected_reason`, targets, execution status;
  * `fallback_reason` and latency breakdown.
* **OB-02 (MUST)** Langfuse metadata key `ha_system_1_routing`. The legacy key `laya_routing` is also emitted during migration.
* **OB-03 (MUST)** The ingress UI provides:
  * a routing playground with a model-set selector;
  * the last N decisions with filters;
  * latency p50/p95, family distribution, and fast-path hit/miss reasons;
  * cost per model set and provider;
  * the ability to flag a decision as wrong, which exports it as an eval-set candidate.
* **OB-04 (MAY)** Prometheus-format metrics at `GET /metrics`.

---

## 12. API Specification

| Endpoint | Purpose | Auth | Latency target (p95) |
| :--- | :--- | :--- | :--- |
| `GET /` | Ingress UI | Ingress only | — |
| `GET /health` | Watchdog, readiness, degradation reasons | None | ≤ 5 ms |
| `GET /v1/models` | Provider connections (no secrets), catalog, model sets, calibration, validation errors | Bearer / ingress | ≤ 10 ms |
| `GET /v1/question-sets` | Effective runtime criteria (debug) | Bearer / ingress | ≤ 10 ms |
| `POST /v1/route` | System 1 decision (+ optional fast path) | Bearer / ingress | ≤ 150 ms |
| `POST /v1/domotica/route` | Fast-path detect / execute | Bearer / ingress | ≤ 50 ms excl. HA execution |
| `POST /v1/domotica/sync` | Force a registry mirror resync | Bearer / ingress | ≤ 3 s |
| `POST /v1/chat/completions` | OpenAI-compatible bridge | Bearer / ingress | §10.1 |
| `POST /v1/systemone` | Raw Laya passthrough (debug) | Bearer / ingress | — |
| `GET /v1/decisions` | Recent audit entries for the UI | Ingress only | ≤ 50 ms |
| `POST /v1/pii/redact` | Pseudonymize or redact messages (Hermes path, PII-07) | Bearer / ingress | ≤ 20 ms |
| `POST /v1/pii/restore` | Restore placeholders in a response (Hermes path) | Bearer / ingress | ≤ 10 ms |

### 12.1 `POST /v1/route`

Request:

```json
{
  "prompt": "Bereken de maandlasten van een annuïteitenhypotheek van 400k over 30 jaar tegen 3,9%",
  "recent_turns": [
    {"role": "user", "content": "Wat kost een huis in Utrecht?"},
    {"role": "assistant", "content": "Dat hangt af van..."}
  ],
  "session_id": "tg_thread_4842",
  "question_set": "ha-system-1-v1",
  "model_set": "gemini",
  "reachable_providers": ["gemini"],
  "fast_path": "detect",
  "language": "nl"
}
```

Response:

```json
{
  "routing_id": "r_01JB7Q8X2M",
  "family": "deep",
  "confidence": {"family": 0.88, "effort": 0.82, "data_need": 0.91, "personal_context": 0.94},
  "laya_effort": "deep",
  "policy": {
    "reasoning": "high",
    "reasoning_budget_tokens": null,
    "max_output_tokens": 8192,
    "temperature": null,
    "tools": {"mode": "allowlist", "patterns": ["*"]},
    "tool_choice": "auto",
    "memory": "keep"
  },
  "target": {
    "model_set": "gemini",
    "provider": "gemini",
    "dialect": "gemini",
    "model": "gemini-pro",
    "upstream_model": "gemini-3.1-pro",
    "calibration": "calibrated",
    "pii_filter": "pseudonymize"
  },
  "provider_params": {
    "generationConfig": {"thinkingConfig": {"thinkingLevel": "high"}, "maxOutputTokens": 16384}
  },
  "applied": {"reasoning": "high", "dropped_params": []},
  "context_used": false,
  "fallback": false,
  "fallback_reason": null,
  "fast_path": {"matched": false, "rejected_reason": "no_action"},
  "checkpoint": "multilingual",
  "question_set": "ha-system-1-v1",
  "latency_ms": {"fast_path": 2.8, "laya": 96.4, "total": 101.2}
}
```

* `policy` is the neutral decision (§6.3), and `target` is the resolved catalog model.
* `provider_params` is the adapter's translation for the target's dialect, for clients that merge parameters instead of running their own adapter.
* `maxOutputTokens` exceeds `max_output_tokens` because this catalog model counts reasoning toward the output limit (PA-03).
* During migration, the legacy fields `effort` (Laya vocabulary), `model`, `needs_memory`, and `allowed_tools` are also returned at top level (MG-01).

### 12.2 `POST /v1/domotica/route`

Request:

```json
{"prompt": "doe de serre spots maar uit", "mode": "execute", "language": "nl"}
```

Response:

```json
{
  "matched": true,
  "status": "confirmed",
  "language": "nl",
  "action": {"domain": "light", "service": "turn_off", "data": {}},
  "targets": [
    {"type": "entity", "entity_id": "light.serre_spots", "name": "Serre spots", "match": "alias_exact", "score": 1.0}
  ],
  "speech": "Oké, de serre spots zijn uit.",
  "tool_call": {
    "id": "call_hs1_7f3a",
    "type": "function",
    "function": {"name": "HassTurnOff", "arguments": "{\"name\": \"Serre spots\", \"domain\": [\"light\"]}"}
  },
  "rejected_reason": null,
  "latency_ms": {"decision": 3.4, "ha_execution": 182.0}
}
```

* `mode` is `detect` (default) or `execute`.
* `status` is `not_executed`, `confirmed`, `accepted`, or `failed`.
* A non-match returns `matched: false` with a `rejected_reason` (FP-12).

---

## 13. Configuration

Configuration is split in two:
* **Add-on options (HA UI):** device, credentials, the active model set, thresholds, and families. Secrets live here, as password fields.
* **Model catalog (`/addon_configs/<slug>/models.yaml`):** catalog models, capability profiles, pricing, and model sets. The shipped catalog is versioned with the add-on; the operator's file overrides or extends it per key. It never contains secrets.

### 13.1 Add-on options

```yaml
device: xpu                     # xpu | cpu
threads: 6
checkpoints: "english,multilingual"
router_default: multilingual
laya_revision: "<pinned commit sha>"
weights_path: /share/ha_system_1/hf
api_key: ""                     # generated on first start if empty
log_level: info
language: nl                    # default language for local confirmations

providers:                      # connections; several may be active
  - name: gemini
    dialect: gemini             # gemini | anthropic | openai_chat | openai_responses
    base_url: ""                # empty = vendor default
    api_key: ""
    send_memory: true
    pii_filter: pseudonymize    # off | redact | pseudonymize
  - name: anthropic
    dialect: anthropic
    base_url: ""
    api_key: ""
    send_memory: true
    pii_filter: pseudonymize
active_model_set: gemini

pii:
  detectors: [email, phone, iban, bsn, payment_card, address, ip, licence_plate, gps, ha_persons, ha_users, ha_zones]
  extra_terms: []               # operator-maintained names / terms
  ner: false                    # optional model-based detection (PII-03)

routing:
  question_set: ha-system-1-v1
  default_family: general
  min_confidence: 0.60
  prune_confidence: 0.75
  downgrade_min_confidence: 0.80
  followup_max_words: 6
  route_timeout_ms: 300
  expose_reasoning: false

fast_path:
  enabled: true
  execute_enabled: true
  fuzzy_min_score: 0.85
  fuzzy_min_margin: 0.15
  confirm_timeout_ms: 1500
  volume_step: 10               # percentage points for "harder" / "zachter"
  denylist: []                  # entity_ids, area names, or domains

registry:
  full_resync_minutes: 15
  max_staleness_minutes: 60

audit:
  prompt_mode: truncated        # none | truncated | full
  retention_days: 30

langfuse:
  enabled: false
  host: ""
  public_key: ""
  secret_key: ""

families:
  - name: quick
    criteria: "small talk, greetings, simple facts, unit conversions, general knowledge that needs no live data or tools"
    reasoning: "off"            # auto | off | low | medium | high | max
    max_output_tokens: 1024
    memory: gated               # full | gated | none
    allowed_tools: ""           # comma-separated globs; empty = no tools
  - name: smarthome
    criteria: "controlling or checking Home Assistant devices, scenes, sensors, and automations"
    reasoning: "off"
    max_output_tokens: 1024
    memory: gated
    allowed_tools: "ha_*, homeassistant*, Hass*"
  - name: general
    criteria: "everyday writing, explaining, summarising, translating, ordinary questions that need a few tool calls"
    reasoning: auto
    max_output_tokens: 4096
    memory: full
    allowed_tools: "*"
  - name: code
    criteria: "writing or debugging code or configuration files, multi-step tool or agent work"
    reasoning: high
    max_output_tokens: 8192
    memory: full
    allowed_tools: "*"
  - name: deep
    criteria: "hard reasoning where a wrong answer is costly: maths, finance, planning, comparing complex options"
    reasoning: high
    max_output_tokens: 8192
    memory: full
    allowed_tools: "*"
    # optional: temperature, reasoning_budget_tokens, model (catalog alias override)
```

### 13.2 Model catalog (excerpt)

```yaml
catalog:
  - alias: gemini-flash-lite
    provider: gemini
    model: gemini-3.5-flash-lite
    reasoning: {control: levels, map: {off: low, low: low, medium: low, high: high, max: high}, counts_toward_output_limit: true}
    sampling: default_only
    reasoning_state: replay_within_turn
    caching: implicit
    pricing: {input: "<usd/1M>", cached_input: "<usd/1M>", output: "<usd/1M>", as_of: "<date>"}

  - alias: gemini-flash
    provider: gemini
    model: gemini-flash-latest     # floating alias (D-12)
    version_tracking: alias        # resolved version recorded per response (PA-18)
    reasoning: {control: levels, map: {off: low, low: low, medium: low, high: high, max: high}, counts_toward_output_limit: true}
    sampling: default_only
    reasoning_state: replay_within_turn
    caching: implicit
    pricing: {input: "<usd/1M>", cached_input: "<usd/1M>", output: "<usd/1M>", as_of: "<date>"}

  - alias: claude-sonnet
    provider: anthropic
    model: claude-sonnet-5-5
    reasoning: {control: levels, map: {off: between_tools, low: low, medium: medium, high: high, max: max}, counts_toward_output_limit: true}
    sampling: default_only
    tools: {forced_tool_choice: false}
    reasoning_state: bound_to_history
    caching: explicit_breakpoints
    pricing: {input: 2.00, cached_input: 0.20, output: 10.00, as_of: "2026-09-25"}

model_sets:
  - name: gemini
    families: {quick: gemini-flash-lite, smarthome: gemini-flash-lite, general: gemini-flash, code: gemini-flash, deep: gemini-pro}
    fallbacks: {deep: [gemini-flash]}
  - name: anthropic
    families: {quick: claude-haiku, smarthome: claude-haiku, general: claude-sonnet, code: claude-sonnet, deep: claude-opus}
    fallbacks: {deep: [claude-sonnet]}
  - name: mixed
    families: {quick: gemini-flash-lite, smarthome: gemini-flash-lite, general: gemini-flash, code: claude-sonnet, deep: claude-opus}
    fallbacks: {code: [gemini-flash], deep: [gemini-pro]}
```

Manifest requirements:
* `watchdog` pointing at `/health`;
* `map: [addon_config:rw, share:rw]` for `models.yaml` and the Laya weights (PK-03);
* `ingress: true`, `homeassistant_api: true`, `devices: [/dev/dri]`, `ports: {8000/tcp: null}`.

---

## 14. Migration from `laya`

* **MG-01 (MUST)** The new slug `ha_system_1` installs as a new add-on; HA does not migrate options or `/data` between slugs. The migration path:
  1. Install HA System 1 alongside Laya and convert the options:
     * `provider` / `gateway_url` → a `providers` entry;
     * per-family `model` → a model set (`laya-migrated`);
     * `effort` / `thinking_budget` → `reasoning` / `reasoning_budget_tokens`;
     * `max_tokens` → `max_output_tokens`;
     * `needs_memory: false → memory: gated`, `true → full`.

     The ingress UI offers this conversion as a one-click import.
  2. Let the weights download once (~3 GB) into `/share/ha_system_1/hf`. Later reinstalls and slug changes reuse them.
  3. Update clients to the new hostname (`http://local-ha-system-1:8000` for a local install): the Hermes `laya_url` and the HA conversation integration's base URL.
  4. Keep `/v1/route` backward compatible for one minor version: the legacy top-level fields (`effort`, `model`, `needs_memory`, `allowed_tools`) are still returned alongside the new ones.
  5. Emit both Langfuse metadata keys (`laya_routing`, `ha_system_1_routing`) during migration.
  6. Uninstall Laya after validation. The final Laya release logs a deprecation notice pointing to HA System 1.
* **MG-02 (MUST)** The repository path becomes `apps/ha-system-1`, and `apps/laya` is removed after the deprecation release. All docs reference `matthijsberg/HomeAssistant-Apps`.
* **MG-03 (MUST)** Update the knowledge bundle:
  * `knowledge/architecture/decision_engine.md`: questions, five families, neutral policy;
  * `knowledge/architecture/hermes_integration.md`: session policy D-1, reachability, new response schema, fast-path contract;
  * `knowledge/operations/model_management.md`: provider connections, catalog, model sets, calibration.

---

## 15. Delivery Milestones

Suggested order for delta work; each milestone is independently shippable.

| Milestone | Scope | Requirements |
| :--- | :--- | :--- |
| **M0 — Baseline, eval & research** | Build the eval set; measure the Gemini baseline (tokens, cost, quality); measure the latency of the four-question pass (decides RT-03); research spike on the HA conversation integration (OQ-1, blocks M5) | EV-01..04, OQ-1 |
| **M1 — Safety first** | Auth hardening; fast-path gates; no invented targets; truthful execution status; media players | SC-01..04, FP-01..18 |
| **M2 — Registry mirror** | WebSocket registries, aliases, exposure, live updates | RG-01..05 |
| **M3 — Router contract** | Questions, context policy, neutral policy resolution, fallbacks, health | RT-01..18, RL-01..04 |
| **M4 — Provider layer + Gemini** | Catalog, model sets, capability profiles, version tracking, `gemini` and `openai_chat` adapters, conformance suite | PA-01..18 |
| **M5 — Chat bridge** | Full proxy, tool-result handling, reasoning-state store, deterministic PII filter with restoration, HA client validation | CB-01..09, PII-01..02, PII-04..06 |
| **M6 — Additional providers** | `anthropic` adapter; calibrated `anthropic`, `openai`, `qwen`, `glm` model sets | PA-14, EV-04, RT-10 |
| **M7 — Observability & privacy** | Audit schema, Langfuse (self-hosted and cloud), UI, retention, PII endpoints for Hermes, optional NER, privacy metrics | OB-01..04, PV-01..04, PII-03, PII-07..09 |
| **M8 — Rename & migration** | New slug, option import, docs, deprecation | MG-01..03, PK-01..04 |

---

## 16. Decisions

* **D-1 — Session downgrades allowed for confident standalone turns.** This supersedes "no downgrades within a session" in `hermes_integration.md`. Rationale: a cheaper model on a standalone household question costs less, even after losing cache hits. Mid-turn switching remains prohibited, because Gemini thought signatures and Anthropic thinking blocks are only valid for the model and turn that produced them.
* **D-2 — Layer behind HA's built-in local intents rather than replacing them.** HA already handles template-matched commands locally; HA System 1 covers what they miss plus non-Assist channels.
* **D-3 — Conservative gating.** Uncertainty always keeps tools and memory; savings never come at the cost of a broken answer.
* **D-4 — HA executes on the Assist path.** In the chat bridge the fast path emits HA intent tool calls and HA executes them, so HA's exposure and permission model stays authoritative. Direct execution happens only through `/v1/domotica/route` and `/v1/route` with `fast_path: execute`.
* **D-5 — Sensitive devices never take the fast path.**
* **D-6 — The fast path is deterministic.** Rules plus the registry mirror; no model inference.
* **D-7 — The router decides in neutral terms; adapters translate.** Task families and Laya criteria never mention vendors or vendor parameters. Vendor differences live in capability profiles, not in routing code.
* **D-8 — Native dialects where compatibility shims lose control.** Gemini and Anthropic get native adapters; `openai_chat` is the universal dialect for everything else.
* **D-9 — The bridge owns reasoning state.** Because HA's clients drop vendor reasoning artifacts, the bridge stores and replays them within a turn.
* **D-10 — Fast-path execution is on by default** (`fast_path.execute_enabled: true`). Safety rests on the gates FP-03..FP-11 and FP-18 and on the zero-tolerance safety metrics (§11.2).
* **D-11 — Memory is sent by default; personal data is pseudonymized.** Every connection defaults to `send_memory: true` and `pii_filter: pseudonymize`. A filter failure fails closed (PII-06).
* **D-12 — Floating aliases are allowed.** `gemini-flash-latest` serves `general` and `code`, which gives automatic upgrades and no retirement maintenance. Drift detection (RT-09, PA-18) replaces pinning as the safeguard: the resolved version is recorded per response, and a change marks the set `drifted` until it is re-evaluated.
* **D-13 — Laya weights live in `/share`.** They survive reinstalls and slug changes, and are integrity-checked at load (PK-03).

---

## 17. Assumptions (to validate)

Provider facts in this section and in Appendix A were checked against vendor documentation on 2026-10-03. Adapters re-verify them during implementation, and the catalog's capability profiles are authoritative at runtime.

* **A-1** HA's "Prefer handling commands locally" handles template-matched commands before calling the LLM conversation agent.
* **A-2** Google recommends keeping Gemini 3-series temperature at the default (1.0), with `thinking_level` as the thinking control. Thought signatures are strictly validated for function calling within the current turn.
* **A-3** An add-on with `homeassistant_api: true` can read the entity, device, area, and floor registries and Assist exposure settings over the Core WebSocket API.
* **A-4** Laya answers four choice questions in one forward pass within the §10.1 budget on the Arc iGPU.
* **A-5** On current Anthropic models (Claude Opus 5.5 / Sonnet 5.5), thinking blocks are bound to the model and conversation, and edits to earlier turns invalidate them. To validate in M6:
  * that rebuilding history from Chat Completions without earlier-turn thinking blocks is accepted;
  * that changing the tool set between turns does not invalidate the current turn's thinking.

  If either fails, per-turn tool pruning is disabled for `bound_to_history` models (the tool set becomes fixed per session).
* **A-6** Qwen hybrid models accept `enable_thinking` and `thinking_budget` through DashScope's OpenAI-compatible mode. GLM accepts `thinking.type: enabled | disabled`, has thinking on by default on GLM-5-series models, and counts thinking toward output tokens.
* **A-7** Gemini responses report the resolved model version (`modelVersion`), so alias drift can be detected per response. Other providers report it in the response's `model` field where they resolve aliases.

---

## 18. Open Questions

* **OQ-1 — Research needed.** Which HA conversation integration hosts the bridge? As far as known, the core OpenAI Conversation integration does not support a custom base URL and uses the Responses API. Options to research:
  * (a) a HACS integration that supports a custom OpenAI-compatible base URL;
  * (b) a small companion custom integration that registers HA System 1 as a native conversation agent;
  * (c) also implementing the Responses API on the bridge.

  A research spike in M0 decides this before M5.
* **OQ-2** Which models make up the `openai`, `qwen`, and `glm` sets? The `gemini` set is settled (D-12).

### 18.1 Resolved

| Question | Resolution |
| :--- | :--- |
| OQ-3 — `media_player` in the fast path | Yes → §3.2, FP-18 |
| OQ-4 — Weights in `/share` | Yes → PK-03, D-13 |
| OQ-5 — Langfuse self-hosted or cloud | Both supported → PV-03 |
| OQ-6 — `fast_path.execute_enabled` default | `true` → D-10 |
| OQ-7 — Default data policy per provider | Memory on by default, plus a PII filter → §6.5, D-11 |
| OQ-8 — Upstream `openai_responses` adapter | Backlog → B-1 |

---

## 19. Backlog

Not in scope for the end state described here; candidates for a later version.

* **B-1** Upstream `openai_responses` adapter, for OpenAI reasoning-item replay and GLM's Responses endpoint (was OQ-8).
* **B-2** Multi-intent fast-path commands (*"doe het licht uit en de tv aan"*).
* **B-3** Replace the rule-based follow-up detector with a Laya question, if it measures better (RT-06).

---

## 20. Revision History

**0.4.0 — 2026-10-03.** Decisions on the open questions:

* `media_player` joins the fast path: pause/resume, next/previous, volume (including relative), mute (FP-18).
* Floating aliases are allowed. `gemini-flash-latest` serves `general`/`code`, with per-response version tracking and `drifted` status replacing pinning (RT-09, RT-10, PA-04, PA-18, D-12).
* New §6.5 PII filter: deterministic detectors plus HA-known names, reversible pseudonymization, fail-closed, endpoints for Hermes, optional NER, and privacy metrics. Memory stays on by default (D-11).
* Laya weights move to `/share` with checksum verification (PK-03, D-13).
* Langfuse self-hosted and cloud are both supported (PV-03); fast-path execution is on by default (D-10).
* OQ-1 stays open as an M0 research spike; OQ-8 moved to the new backlog (§19).

**0.3.0 — 2026-10-03.** Provider-neutral architecture:

* New §6 Provider Abstraction Layer: provider connections, dialects, model catalog with capability profiles, neutral policy, adapter requirements PA-01..17.
* Families are now provider-neutral (`reasoning`, `max_output_tokens`); models move into model sets (`gemini` default, plus `anthropic`, `openai`, `qwen`, `glm`, and mixed). Model sets are calibrated individually.
* `general` now uses `reasoning: auto` so Laya's effort question drives it.
* `/v1/route` now returns `policy`, `target`, and `provider_params`, and accepts `model_set` and `reachable_providers`.
* Chat bridge: cross-provider fallback on the first request of a turn; server-side reasoning-state store.
* Configuration split into add-on options and a model catalog file; per-connection data policy; milestones re-sequenced (M4–M6).
* Added Appendix A: vendor parameter mapping.

**0.2.0 — 2026-10-03.** Rewritten as an end-state specification:

* Added positioning against HA's built-in local intents, plus goals, non-goals, and users.
* Fast-path safety rules and the registry mirror.
* Router: data-need and personal-context questions, follow-up detection, and the session policy.
* Chat bridge as a full proxy.
* Consistent latency targets; safety, quality, and accuracy metrics; security, privacy, and migration.

**0.1.0 — 2026-10-03.** Initial draft.

---

## Appendix A — Vendor Parameter Mapping (initial)

Starting point for capability profiles; the catalog is authoritative at runtime (A-2, A-5, A-6).

| Neutral | Gemini 3 (native) | Anthropic (Claude 4.6+ / 5.x) | OpenAI (reasoning models) | Qwen (DashScope, hybrid) | GLM (Z.ai) |
| :--- | :--- | :--- | :--- | :--- | :--- |
| Thinking control | `thinkingConfig.thinkingLevel` (model-specific set, e.g. `low` / `high`) | `thinking: {type: "adaptive"}` + `output_config.effort` (`low`…`max`); Haiku 4.5: `thinking: {type: "enabled", budget_tokens}` (≥ 1024, < `max_tokens`) | `reasoning_effort` (`none` / `minimal` / `low` / `medium` / `high` / `xhigh`, model-dependent) | `enable_thinking` + `thinking_budget` (via `extra_body`) | `thinking: {type: "enabled" \| "disabled"}` |
| `off` | Lowest level where thinking cannot be disabled | Opus 5.5: cannot disable → `effort: low`; Sonnet 5.5: `thinking: {type: "between_tools"}`; Haiku 4.5: omit `thinking` | `none` where supported, else lowest level | `enable_thinking: false` | `thinking.type: "disabled"` |
| `low` / `medium` / `high` | Nearest supported level | `effort` of the same name | Same name | `enable_thinking: true` + per-level `thinking_budget` from catalog | `enabled` (no levels) |
| `max` | Highest level | `effort: max` | `xhigh` | Largest catalog budget | `enabled` |
| Temperature | Omit (1.0 recommended) | Omit on Opus 5.5 / Sonnet 5.5 (non-default rejected) | Omit (rejected on reasoning models) | Catalog `recommended_sampling` per mode | Allowed (catalog) |
| Output limit | `maxOutputTokens` | `max_tokens` (includes thinking) | `max_completion_tokens` (includes reasoning) | `max_tokens` + separate `thinking_budget` | `max_tokens` (thinking counts toward output) |
| Forced `tool_choice` | Supported | Rejected on Opus 5.5 / Sonnet 5.5 → `auto` | Supported | Supported | Verify per model |
| Reasoning state | Thought signatures, required within the current turn | Thinking blocks bound to model + conversation; append-only history | Responses API reasoning items (none on Chat Completions) | Not replayed | Thinking blocks on the Anthropic endpoint; verify on OpenAI endpoint |
| Caching | Implicit (+ explicit cached content) | Explicit `cache_control` breakpoints; model-scoped; prefix order tools → system → messages | Automatic prefix caching | Provider-dependent (verify) | Provider-dependent (verify) |
| Refusal signal | Safety finish reason | `stop_reason: "refusal"` + `stop_details` | Refusal field / content filter | Content filter | Content filter |
| Preferred dialect | `gemini` | `anthropic` | `openai_chat` | `openai_chat` | `openai_chat` or `anthropic` |
