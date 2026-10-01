# Laya Router Home Assistant Add-on

[![Quality Gate](https://img.shields.io/badge/scaffolding-verified-brightgreen.svg)]()
[![OKF v0.2](https://img.shields.io/badge/OKF-v0.2-blue.svg)](knowledge/index.md)
[![SemVer](https://img.shields.io/badge/semver-0.5.0-blue.svg)]()

Local System 1 decision engine and multi-provider model & effort router for Home Assistant and Hermes Agent.

Laya Router categorizes incoming user turns in a single forward pass on CPU or Intel Arc iGPU (down to ~55 ms), picking between calibrated model tiers (Gemini Flash Lite 3.5, Flash, Pro, or OpenRouter/LiteLLM models such as Claude 3.5/3.7 Sonnet) along with reasoning effort levels, token ceilings, temperatures, and thinking budgets.

---

## Features

- **Intel Arc iGPU Acceleration (XPU):** Hardware-accelerated inference via `/dev/dri` on Intel Meteor Lake / Arrow Lake iGPUs (Core Ultra 5 225H), delivering **55–120 ms inference latency** (up to 5.4x faster than CPU).
- **Home Assistant Ingress WebUI:** Interactive routing playground accessible from the HA sidebar, featuring live inference metrics, hardware badges, and confidence visualization.
- **Configurable Task Families & Profiles:** Fully customizable `families` in Home Assistant options: customize semantic criteria, upstream model (`gemini-3.5-flash-lite`), effort, max tokens, temperature, and thinking budget per family.
- **Multi-Provider & Model Tiering:** Configurable for native **Google Gemini**, **LiteLLM**, **OpenRouter**, or custom OpenAI-compatible proxies.
- **Fail-Open Resilience:** Zero broken turns. Timeouts, errors, or low-confidence decisions leave the client request byte-identical.
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
| `/` | `GET` | Ingress / None | Interactive dark-mode Ingress WebUI playground. |
| `/health` | `GET` | None | Container watchdog; reports readiness, device, loaded checkpoints, and pinned revision. |
| `/v1/route` | `POST` | Bearer / Ingress | Decision endpoint for one turn. Returns `family`, `effort`, `confidence`, `model`, `max_tokens`, `temperature`, `thinking_budget`. |
| `/v1/models` | `GET` | Bearer | Returns active provider, model mappings, and full configured family profiles. |
| `/v1/systemone` | `POST` | Bearer | Raw Jev-compatible wire protocol passthrough for experiments and external tools. |
| `/v1/question-sets` | `GET` | Bearer | Returns active criteria text and versioning details (e.g. `hermes-v1`). |

---

## Task Family Configuration (`families`)

Configurable via Home Assistant Add-on options:

```yaml
families:
  - name: "quick"
    criteria: "small talk, greetings, simple facts, unit conversions, one-step home control commands"
    model: "gemini-3.5-flash-lite"
    effort: "low"
    max_tokens: 1024
    temperature: 0.2
    thinking_budget: 0

  - name: "general"
    criteria: "everyday writing, explaining, summarising, translating, ordinary questions that need a few tool calls"
    model: "gemini-flash-latest"
    effort: "medium"
    max_tokens: 4096
    temperature: 0.7

  - name: "code"
    criteria: "writing or debugging code or configuration files, multi-step tool or agent work"
    model: "gemini-flash-latest"
    effort: "high"
    max_tokens: 8192
    temperature: 0.1

  - name: "deep"
    criteria: "hard reasoning where a wrong answer is costly: maths, finance, planning, comparing complex options"
    model: "gemini-2.5-pro"
    effort: "high"
    max_tokens: 8192
    temperature: 0.2
```

### Parameter Defaults & Omission
- **`temperature`:** When omitted, upstream model default temperature is used.
- **`thinking_budget`:** When omitted (default for `code` and `deep`), model reasoning is unconstrained. Set to `0` for `quick` to strictly bypass thinking latency.
- **`max_tokens`:** When omitted, upstream model context maximum applies.

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
