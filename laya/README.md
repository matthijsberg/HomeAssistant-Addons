# Laya Router Home Assistant Add-on

[![Quality Gate](https://img.shields.io/badge/scaffolding-verified-brightgreen.svg)]()
[![OKF v0.2](https://img.shields.io/badge/OKF-v0.2-blue.svg)](knowledge/index.md)
[![SemVer](https://img.shields.io/badge/semver-0.3.0-blue.svg)]()

Local System 1 decision engine and multi-provider model & effort router for Home Assistant and Hermes Agent.

Laya Router categorizes incoming user turns in a single forward pass on CPU or Intel Arc iGPU (down to ~55 ms), picking between calibrated model tiers (Gemini Flash Lite, Flash, Pro, or OpenRouter/LiteLLM models such as Claude 3.5/3.7 Sonnet) along with calibrated reasoning effort levels (`light`, `normal`, `deep`).

---

## Features

- **Intel Arc iGPU Acceleration (XPU):** Hardware-accelerated inference via `/dev/dri` on Intel Meteor Lake / Arrow Lake iGPUs (Core Ultra 5 225H), delivering **55–120 ms inference latency** (up to 5.4x faster than CPU).
- **Multi-Provider & Model Tiering:** Configurable in Home Assistant settings for native **Google Gemini**, **LiteLLM**, **OpenRouter**, or custom OpenAI-compatible proxies.
- **Fail-Open Resilience:** Zero broken turns. Timeouts, errors, or low-confidence decisions leave the Hermes request byte-identical.
- **Language Aware:** Automatically routes between English and Multilingual checkpoints; short ambiguous Dutch follow-ups (e.g. *"ja, doe maar"*) are routed to the multilingual checkpoint using the preceding 2 turns of context.
- **Startup GPU Warmup:** Asynchronous preloading and warmup pass pre-compiles Level-Zero SPIR-V JIT kernels at boot, ensuring the first live user request is served instantly without compilation delay.
- **Open Knowledge Format (OKF v0.2):** Full architecture and operational specifications documented as agent-consumable knowledge concepts in `knowledge/`.

---

## Benchmark Results (Measured on Intel Core Ultra 5 225H)

Empirical latency benchmark measured on the live add-on comparing CPU execution against Intel Arc iGPU hardware acceleration:

| Task / Prompt | Category | CPU Latency | Intel Arc XPU Latency | Speedup |
|---|---|---|---|---|
| *Bereken de annuïtaire hypotheeklasten voor 450k* | NL Deep Reasoning | 218 ms | **55.3 ms** | **4.0x** |
| *Compare discounted cash flow vs net present value* | EN Deep Finance | 280 ms | **55.6 ms** | **5.0x** |
| *Schrijf een python script om een CSV bestand te plotten* | NL Code Generation | 481 ms | **89.3 ms** | **5.4x** |
| *Refactor this async class to implement circuit breaker* | EN Code Refactoring | 450 ms | **120.0 ms** | **3.8x** |
| *Hello, what is the weather like today?* | EN Quick Greeting | 449 ms | **116.7 ms** | **3.8x** |
| *Zet de lampen in de woonkamer uit* | NL HA Control | 310 ms | **117.8 ms** | **2.6x** |
| **Overall Average Latency** | *All Workloads* | **364.6 ms** | **120.2 ms** | **3.0x** |

*Note: Code and deep reasoning queries achieve sub-100ms latency on the Intel Arc iGPU.*

---

## API Surface

| Endpoint | Method | Auth | Purpose |
|---|---|---|---|
| `/health` | `GET` | None | Container watchdog; reports readiness, device, loaded checkpoints, and pinned revision. |
| `/v1/route` | `POST` | Bearer | Decision endpoint for one user turn. Returns `family`, `effort`, `confidence`, `checkpoint`, `provider`, and `model`. |
| `/v1/models` | `GET` | Bearer | Returns active provider, model mappings, and gateway topology details. |
| `/v1/systemone` | `POST` | Bearer | Raw Jev-compatible wire protocol passthrough for experiments and external tools. |
| `/v1/question-sets` | `GET` | Bearer | Returns active criteria text and versioning details (e.g. `hermes-v1`). |

---

## Add-on Options

| Option | Type | Default | Description |
|---|---|---|---|
| `device` | select | `xpu` | Hardware compute device (`xpu` for Intel Arc iGPU acceleration; `cpu` for standard CPU). |
| `threads` | int | `6` | PyTorch CPU intra-op threads. Cap to physical CPU cores. |
| `checkpoints` | string | `english,multilingual` | Comma-separated checkpoints preloaded into memory at startup. |
| `router_default` | select | `multilingual` | Fallback checkpoint for short prompts without clear language signal. |
| `laya_revision` | string | `main` | Hugging Face weights revision for deterministic execution. |
| `api_key` | password | *(required)* | Shared secret Bearer token used by the Hermes `laya-router` plugin. |
| `log_level` | select | `info` | Logging verbosity (`debug`, `info`, `warn`, `error`). |
| `provider` | select | `gemini` | Upstream model provider (`gemini`, `litellm`, `openrouter`, or `custom`). |
| `model_quick` | string | `gemini-2.5-flash-lite` | Model identifier mapped to `quick` task family. |
| `model_general` | string | `gemini-flash-latest` | Model identifier mapped to `general` task family. |
| `model_code` | string | `gemini-flash-latest` | Model identifier mapped to `code` task family. |
| `model_deep` | string | `gemini-2.5-pro` | Model identifier mapped to `deep` task family. |
| `gateway_url` | string | `""` | Optional gateway base URL when using LiteLLM or OpenRouter. |

---

## Architectural Limitation: Single-Endpoint vs Multi-Provider Gateways

Hermes Agent connects to upstream LLMs via a configured provider adapter. The `llm_request` middleware hook can rewrite model identifiers and reasoning effort on the fly, but **cannot switch provider authentication tokens or API adapters mid-session**:

* **Native Single Provider (Gemini):** Routes seamlessly between Flash Lite, Flash, and Pro using a single Google API key and direct connection without extra gateway latency.
* **Multi-Vendor Providers (e.g. Claude + Gemini + OpenAI):** Must use an OpenAI-compatible unified proxy such as **LiteLLM** or **OpenRouter** as the single configured provider in Hermes. The gateway receives the routed model name and multiplexes downstream.

---

## Development & Scaffolding Verification

Run the full verification suite in one command:

```bash
./scripts/setup.sh
```

This runs:
1. Manifest validation (`config.yaml`, `build.yaml`, translations)
2. OKF v0.2 Knowledge Bundle conformance checks
3. Security & credential scanner
4. Shell syntax validation (`bash -n`)
5. Code style & linting (`ruff check .`)
6. Automated Pytest suite (`pytest tests/ -v`)
