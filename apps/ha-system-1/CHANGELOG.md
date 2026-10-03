# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [2.0.0] - 2026-10-02

### Added
- **Fast-Path Domotica Engine (`/v1/domotica/route`):** Sub-50ms local resolution of natural language smart home commands into structured Home Assistant service calls without cloud LLM dependencies.
- **Home Assistant Area & Entity Registry Resolver (`resolver.py`):** Real-time caching and fuzzy/exact target resolution against the Home Assistant Core API (`homeassistant_api: true`).
- **OpenAI-Compatible Chat Completions Shim (`/v1/chat/completions`):** Drop-in bridge for Home Assistant OpenAI Conversation and Assist, returning official `HassTurnOn` / `HassTurnOff` tool calls with both streaming (SSE) and non-streaming support.
- **Direct HA Service Execution:** Optional `execute: true` parameter to trigger service calls directly against the Home Assistant Core REST API.
- **Model modernizations:** Default `deep` reasoning model upgraded to `gemini-3.1-pro`.

## [0.5.0] - 2026-10-01

### Added
- Fully configurable `families` section in Home Assistant add-on options (`config.yaml`).
- Configurable semantic criteria, target models, effort levels, token ceilings (`max_tokens`), temperatures, and thinking budgets per task family.
- Default configuration updated to `gemini-3.5-flash-lite` for the `quick` family with `thinking_budget: 0`.
- Unconstrained thinking budget defaults (omitted) for `code` and `deep` families to support full reasoning capabilities.
- Intelligent omission/defaulting for optional parameters (temperature, thinking budget, token caps).
- Dynamic runtime category evaluation: user-added custom families in Home Assistant options are automatically evaluated by Laya in the single forward pass.
- Ingress WebUI updated to display temperature, max tokens, and thinking budget parameters.

## [0.4.0] - 2026-10-01

### Added
- Home Assistant Ingress GUI (`ingress: true`, `ingress_panel: true`) accessible directly from the HA sidebar.
- Interactive Routing Playground with presets, confidence meters, latency timing, and raw JSON inspector.
- Ingress authentication bypass (`X-Ingress-Path`) allowing authenticated HA users to test without copying API tokens.
- Offline-first vanilla HTML/CSS/JS interface with dark mode styling matching Home Assistant Lovelace.

## [0.3.0] - 2026-10-01

### Added
- Configurable model provider selection (`gemini`, `litellm`, `openrouter`, `custom`) in HA options.
- Configurable model mapping per task family (`model_quick`, `model_general`, `model_code`, `model_deep`).
- `GET /v1/models` endpoint returning active provider, mappings, and architectural limitation note.
- Multi-provider gateway documentation for LiteLLM and OpenRouter.

## [0.2.0] - 2026-10-01

### Added
- Hardware acceleration for Intel Arc / Arrow Lake integrated GPU (`device: xpu`).
- DRM hardware passthrough (`/dev/dri`) and Level-Zero runtime compute driver stack.
- Startup GPU warmup pass to pre-compile JIT kernels, eliminating first-request latency penalty.
- Asynchronous background preloading in FastAPI lifespan to keep `/health` watchdog responsive.

## [0.1.0] - 2026-10-01

### Added
- Initial scaffolding for Laya Router Home Assistant Add-on.
- FastAPI service with `/health`, `/v1/route`, `/v1/systemone`, and `/v1/question-sets` endpoints.
- Versioned `hermes-v1` question set mapping prompts to `task_family` and `effort`.
- Constant-time Bearer token authentication gate.
- Preloading architecture for CPU-only execution with resident weights in `/data/hf`.
- OKF v0.2 Knowledge Bundle covering decision engine architecture, integration contracts, and model management.
- Complete automated test suite (`pytest`) and manifest validator (`test_config.py`).
- Security scanner and shell syntax checks.
