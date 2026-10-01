# Laya Router Home Assistant Add-on

[![Quality Gate](https://img.shields.io/badge/scaffolding-verified-brightgreen.svg)]()
[![OKF v0.2](https://img.shields.io/badge/OKF-v0.2-blue.svg)](knowledge/index.md)
[![SemVer](https://img.shields.io/badge/semver-0.1.0-blue.svg)]()

Local System 1 decision engine and Gemini model & effort router for Home Assistant and Hermes Agent.

Laya Router categorizes incoming user turns in a single sub-50ms forward pass on CPU, picking between Gemini Flash Lite, Flash, and Pro, along with calibrated reasoning effort levels (`light`, `normal`, `deep`).

---

## Features

- **Sub-50ms CPU Inference:** Evaluates two orthogonal questions (`task_family` and `effort`) in one single forward pass using resident ModernBERT / mmBERT weights.
- **Fail-Open Resilience:** Zero broken turns. Timeouts, errors, or low-confidence decisions leave the Hermes request byte-identical.
- **Language Aware:** Automatically routes between English and Multilingual checkpoints; short ambiguous Dutch follow-ups (e.g. *"ja, doe maar"*) are routed to the multilingual checkpoint using the preceding 2 turns of context.
- **Open Knowledge Format (OKF v0.2):** Full architecture and operational specifications documented as agent-consumable knowledge concepts in `knowledge/`.

---

## API Surface

| Endpoint | Method | Auth | Purpose |
|---|---|---|---|
| `/health` | `GET` | None | Container watchdog; reports readiness, device, loaded checkpoints, and pinned revision. |
| `/v1/route` | `POST` | Bearer | Decision endpoint for one user turn. Returns `family`, `effort`, `confidence`, and `checkpoint`. |
| `/v1/systemone` | `POST` | Bearer | Raw Jev-compatible wire protocol passthrough for experiments and external tools. |
| `/v1/question-sets` | `GET` | Bearer | Returns active criteria text and versioning details (e.g. `hermes-v1`). |

---

## Add-on Options

| Option | Type | Default | Description |
|---|---|---|---|
| `device` | string | `cpu` | Hardware compute device (`cpu`; `xpu` reserved for future Intel iGPU acceleration). |
| `threads` | int | `6` | PyTorch CPU intra-op threads. Cap to physical CPU cores. |
| `checkpoints` | string | `english,multilingual` | Comma-separated checkpoints preloaded into memory at startup. |
| `router_default` | string | `multilingual` | Fallback checkpoint for short prompts without clear language signal. |
| `laya_revision` | string | `main` | Hugging Face weights revision for deterministic execution. |
| `api_key` | password | *(required)* | Shared secret Bearer token used by the Hermes `laya-router` plugin. |
| `log_level` | list | `info` | Logging verbosity (`debug`, `info`, `warn`, `error`). |

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
