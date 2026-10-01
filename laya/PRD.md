# Laya Router PRD

Oct 1, 2026

## Overview

Laya Router picks a Gemini model and reasoning effort for every Hermes user turn, decided locally by a Laya decision model before the provider call. Today every turn runs on `gemini-flash-latest`: simple turns overpay and hard turns get too little reasoning.

**Goals**

- Route each user turn to Flash Lite, Flash or Pro, each with an effort level, based on the question asked.
- Never break a turn: any router failure leaves the request exactly as it is today.
- Log every decision to Langfuse so routing quality and cost per model can be reviewed.
- Collect labelled decisions so Laya can be fine-tuned later.
- Stay inside Hermes: native `gemini` provider, one API key, no gateway.

**Success measures**

- Decision latency p95 at or under 500 ms on CPU.
- Zero failed turns caused by the router.
- Mis-route rate in the observe-week review below a target you set after seeing the data.

## Scope

Version 1 is two components and three Google models, nothing more.

**In scope**

- Laya HA app on CPU, serving decisions over HTTP.
- `laya-router` Hermes plugin with `observe` and `apply` modes.
- Models: Gemini Flash Lite, Flash and Pro on the native `gemini` provider.
- Main chat turns only.
- Decision logging to Langfuse plus a local JSONL audit.

**Non-goals for v1**

- Other providers (Claude, GLM). Adding them later needs an OpenAI-compatible gateway, because the middleware cannot switch provider.
- Fine-tuning Laya (phase 4).
- iGPU / XPU acceleration (benchmark in phase 3).
- Routing auxiliary calls: titles, compression, vision.
- HA automations as callers. The same `/v1/route` endpoint can serve them later.

## Architecture

Two new pieces sit between Hermes and Gemini: a Hermes plugin that rewrites the request, and an HA app that runs Laya. Hermes, Gemini and Langfuse stay as they are.

```
Hermes (user turn) ──► laya-router plugin ──► Laya HA app (/v1/route)
                          │      ◄── family + effort ──┘
                          ├──► Gemini API (Flash Lite / Flash / Pro, rewritten effort)
                          └──► Langfuse (decision on the turn's trace)
```

The plugin is the only component that touches a live request; the app only answers questions.

**Request flow**

1. Hermes builds the provider request for the first call of a user turn.
2. The plugin sends the latest message and the two previous turns to the app's `/v1/route`.
3. The app returns task family, effort and their confidences.
4. The plugin maps the family to a model, clamps the effort, and rewrites the request (only in `apply` mode).
5. Hermes' native Gemini adapter sends the request; the decision is replayed for the rest of the turn's tool loop.
6. The plugin logs the decision to Langfuse on the turn's trace.

## Laya HA app

The app is one Python process that loads Laya at startup and answers "which task family, which effort" over HTTP. It returns decisions only; mapping a family to a Gemini model is the plugin's job, so other callers can reuse the app later.

**Build**

- Repo: `matthijsberg/HomeAssistant-Addons`, folder `laya/`.
- Base image `python:3.12-slim`, `amd64` only.
- Install CPU-only PyTorch first, then `laya[serve]==0.3.21` (pinned).
- One FastAPI service under uvicorn that imports `laya.Router` in-process.
- Weights in `/data/hf` (`HF_HOME`), pinned with `LAYA_REVISION`. Both checkpoints in fp32 are roughly 3 GB on disk, downloaded on first start.
- Preload both checkpoints at startup. `/health` reports not-ready until they are loaded.

**Endpoints**

| Endpoint | Purpose |
| --- | --- |
| `POST /v1/route` | Decision for one turn. In: `prompt`, `recent_turns[]` (max 2), `question_set` (`hermes-v1`), optional `session_id`. Out: `family`, `effort`, `confidence.family`, `confidence.effort`, `checkpoint`, `latency_ms`, `question_set` version. |
| `POST /v1/systemone` | Raw Laya passthrough on the Jev-compatible wire format, for experiments and other tools. |
| `GET /health` | Loaded checkpoints, device, Laya version, ready flag. Used as the HA watchdog. |
| `GET /v1/question-sets` | The active question sets and their criteria text, for debugging. |

**App options**

| Option | Default | Notes |
| --- | --- | --- |
| `device` | `cpu` | `xpu` reserved for phase 3. |
| `threads` | `6` | Torch intra-op threads; leave headroom for HA and other apps. |
| `checkpoints` | `english,multilingual` | Both preloaded. |
| `router_default` | `multilingual` | Short ambiguous text goes here instead of the English checkpoint. |
| `laya_revision` | pinned hash | Reproducible decisions. |
| `api_key` | required | Bearer token; the plugin sends it. |
| `log_level` | `info` |  |

**Network and security**

- Port 8000 on the internal HA network only; not exposed to the LAN by default.
- Every request needs the bearer key, `/health` excepted.
- The app stores no prompt text. Logging prompts is Langfuse's job.

## Decision design

Laya answers two `choice` questions in one forward pass: what kind of task this is, and how much thinking it needs. It never names a model, so models can change without touching the questions, and labels stay reusable for fine-tuning.

**Question set `hermes-v1`**

| Question | Option key | Criteria text sent to Laya |
| --- | --- | --- |
| `task_family` | `quick` | small talk, greetings, simple facts, unit conversions, one-step home control commands |
| `task_family` | `general` | everyday writing, explaining, summarising, translating, ordinary questions that need a few tool calls |
| `task_family` | `code` | writing or debugging code or configuration files, multi-step tool or agent work |
| `task_family` | `deep` | hard reasoning where a wrong answer is costly: maths, finance, planning, comparing complex options |
| `effort` | `light` | the answer is short and obvious |
| `effort` | `normal` | needs some thought or a few steps |
| `effort` | `deep` | needs careful multi-step reasoning or double-checking |

Wording rules:

- Every criterion names a kind of task. A task-free superlative ("best all-round choice") reads as safe on every prompt and absorbs decisions.
- No `yes`/`no` or `true`/`false` keys in `choice` questions; Laya can follow those words instead of the criteria.
- No `score` questions: they are Laya's weakest type, and the multilingual checkpoint rarely picks the first level.
- Criteria text is versioned with the question set; changing it means bumping `hermes-v2`.

**State sent to Laya**

- `request`: the latest user message.
- `context`: the previous two turns, truncated to fit the 1,024-token default.
- Context matters for short follow-ups such as "ja, doe maar", which carry no task signal alone.

**Language**

Prompts are mixed Dutch and English. The Laya `Router` picks the English or multilingual checkpoint per request; `router_default: multilingual` catches short text with no clear language. After the spike, consider pinning everything to the multilingual checkpoint: one checkpoint means one calibration and one fine-tune.

**Confidence and fallback**

- Gate on `answer_confidence`, the probability of the chosen answer.
- `task_family` below `min_confidence` (start at 0.6): keep the default model and effort.
- `effort` below `min_confidence`: use the family's default effort.
- The multilingual checkpoint ships without fitted temperatures and is over-confident, so the threshold is tuned from observe-week data, not trusted as-is.

## Hermes plugin `laya-router`

The plugin hooks Hermes' `llm_request` middleware, asks the Laya app once per user turn, and rewrites the model and effort in the provider request. It is modelled on the community `jev-effort-router` plugin (MIT), minus the OpenRouter call and the Ollama Cloud scope.

**Policy: family to model**

| Family | Model | Default effort |
| --- | --- | --- |
| `quick` | Gemini Flash Lite | low |
| `general` | Gemini Flash (today's default) | medium |
| `code` | Gemini Flash | high |
| `deep` | Gemini Pro | high |

The `effort` answer adjusts the default within the levels each model accepts: `light` lowers it one step, `deep` raises it one step, `normal` keeps it.

**Behaviour**

1. Scope gate: act only on provider `gemini`, main chat turns, and a request whose model is in the grid or is the default. Anything else passes through untouched.
2. Decide once per user turn, at the first provider request. Memoise the decision per session and turn, and replay it through the tool loop, so the model never changes mid-turn.
3. Call `POST /v1/route` with `timeout_s` (1.0). Timeout, error, or confidence under the threshold: leave the request byte-identical.
4. Rewrite the model id and the effort field. Exact field names in the request kwargs are confirmed in the spike, because the native Gemini adapter translates the request afterwards.
5. Clamp effort to the levels the chosen model accepts. Never invent a level; omit the field rather than risk a 400.
6. Stickiness: an upgrade (to a stronger model) applies at once; a downgrade waits for a new session. This protects the prompt cache and the tool-call history.
7. Respect manual control: after `/model` or `/reasoning` in a session, stop routing that session.

**Modes**

- `off`: registered, does nothing.
- `observe`: decides and logs, never rewrites. Default for the first week.
- `apply`: decides, logs and rewrites.

**Settings** (under `plugins.entries.laya-router.settings` in `config.yaml`)

```yaml
plugins:
  entries:
    laya-router:
      settings:
        mode: observe
        laya_url: http://<laya-app-host>:8000
        api_key_env: LAYA_API_KEY
        question_set: hermes-v1
        timeout_s: 1.0
        min_confidence: 0.6
        context_turns: 2
        default: {model: gemini-flash-latest, effort: medium}
        families:
          quick:   {model: <flash-lite-id>, effort: low}
          general: {model: gemini-flash-latest, effort: medium}
          code:    {model: gemini-flash-latest, effort: high}
          deep:    {model: <pro-id>, effort: high}
        allowed_effort:          # per model, confirmed in the spike
          <flash-lite-id>: [...]
          gemini-flash-latest: [...]
          <pro-id>: [...]
        downgrade_within_session: false
        audit_enabled: true
        include_prompt_in_audit: false
```

**Interfaces**

- Tools: `laya_router_status`, `laya_router_route` (dry-run a prompt).
- CLI: `hermes laya-router status`, `route "<prompt>"`, `tail N`, `grid`.
- Install from a local path or your own repo; expect the community-plugin scanner to flag the network call, and review it before `--force`.

## Observability

Every routing attempt lands in Langfuse on the Hermes trace it belongs to, so cost and quality can be compared per model and mis-routes can be labelled.

**Per decision, attached to the turn's trace**

| Field | Example |
| --- | --- |
| `mode` | `observe` / `apply` |
| `family`, `confidence.family` | `deep`, 0.82 |
| `effort`, `confidence.effort` | `normal`, 0.71 |
| `model_chosen`, `effort_sent` | Pro, high |
| `model_default` | `gemini-flash-latest` |
| `fallback_reason` | `timeout`, `low_confidence`, `manual_override`, empty |
| `checkpoint`, `question_set` | `multilingual`, `hermes-v1` |
| `latency_ms` | 310 |

**Labels for fine-tuning**

- In review, mark each sampled turn with a Langfuse score `route_ok` (true/false) and, when false, the family it should have been.
- These labels plus the logged prompts become the Laya fine-tuning set in phase 4.

**Local audit**

- One JSONL line per attempt in the plugin's data directory, same fields as above.
- Prompt text stays out of the audit unless `include_prompt_in_audit` is on; Langfuse already holds it.

## Rollout

Five phases, each closed by a gate; nothing rewrites a live request until phase 2.

1. **Phase 0, spike.** Laya in a throwaway container plus a logging-only middleware in Hermes.
   - Log the Gemini request kwargs once and record the exact model and effort fields.
   - Switch Flash to Pro between two turns of a session with tool calls, and check the thought signatures survive.
   - Run 30 to 50 real prompts through `/v1/route`; record p95 latency and eyeball family accuracy.
   - Gate: fields known, switch works, p95 at or under 500 ms.
2. **Phase 1, build and observe.** Ship the HA app and the plugin in `observe` mode for one week.
   - Gate: at least 100 routed turns reviewed in Langfuse with `route_ok` labels, and a mis-route rate you accept.
3. **Phase 2, apply.** Switch to `apply`.
   - Gate: no turn failures caused by the router over a week; cost per turn compared against the Flash-only baseline.
4. **Phase 3, tune.** Fit confidence temperatures on the labelled data, refine criteria wording (`hermes-v2`), and benchmark `device: xpu` on the iGPU.
   - Gate: threshold set from data; XPU kept only if it clearly beats CPU.
5. **Phase 4, fine-tune.** Fine-tune the multilingual checkpoint on the labelled turns with Convai's Kaggle notebook (about 4 to 5 hours on free 2x T4 GPUs), then evaluate on held-out turns before switching.
   - Gate: the fine-tuned checkpoint beats zero-shot on the held-out set.

Later, outside this PRD: Claude and GLM behind a gateway, and HA automations calling `/v1/route`.

## Risks and open questions

The biggest risk is zero-shot accuracy, not hardware: the base checkpoints sit near chance on typed decisions until fine-tuned.

| Risk | Effect | Mitigation |
| --- | --- | --- |
| Weak zero-shot accuracy | Wrong model for many turns | Observe week first; fallback to default; fine-tune in phase 4 |
| Over-confident multilingual checkpoint | Threshold lets bad answers through | Tune threshold from labels; fit temperatures in phase 3 |
| Model switch breaks Gemini thought signatures | Errors on the next tool turn | Test in spike; switch only between turns; upgrade-only within a session |
| Prompt cache lost on a switch | Higher cost on long sessions | Stickiness rule; measure in phase 2 |
| Effort field differs on the native Gemini adapter | Effort silently ignored or 400 | Log kwargs in spike; per-model clamp; omit rather than guess |
| Added latency on every turn | Slower replies | Preload checkpoints; 1 s timeout, fail open |
| `-latest` aliases move | Routing quality shifts silently | Pin explicit model ids; upgrade deliberately |
| Short Dutch follow-ups misread | Wrong checkpoint or family | `router_default: multilingual`; send two turns of context |

**Open questions**

- [ ] Explicit model ids or `-latest` aliases for Flash Lite, Flash and Pro?
- [ ] Which effort levels does each model accept on the native Gemini API?
- [ ] Does Flash Lite's context window match Flash's? Hermes compresses based on the configured default.
- [ ] How does the plugin detect a manual `/model` or `/reasoning` to pause routing?
- [ ] What mis-route rate is good enough to move from observe to apply?

## Sources

- [Laya repository and README](https://github.com/NandhaKishorM/laya): install, `laya-serve`, Router, known limits, fine-tuning notebook
- [Laya documentation](https://nandhakishorm.github.io/laya/)
- [Hermes middleware](https://hermes-agent.nousresearch.com/docs/developer-guide/middleware): `llm_request` contract
- [Hermes Google Gemini guide](https://hermes-agent.nousresearch.com/docs/guides/google-gemini): native adapter, model ids, thought signatures
- [Hermes providers](https://hermes-agent.nousresearch.com/docs/integrations/providers): context length resolution, reasoning effort
- [jev-effort-router plugin](https://hermes-agent.nousresearch.com/docs/plugins/jev-effort-router): reference design for per-turn routing
- [local-system-one-hermes plugin](https://hermes-agent.nousresearch.com/docs/plugins/local-system-one-hermes): observe-only Laya plugin for Hermes
