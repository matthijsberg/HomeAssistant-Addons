# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
