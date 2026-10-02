# Laya Router Documentation

## Installation & Setup

1. Add this repository to Home Assistant Add-on Store:
   `https://github.com/matthijsberg/HomeAssistant-Addons`
2. Install the **Laya Router** add-on.
3. In the add-on configuration tab, set a secure `api_key` (Bearer token) or leave blank if testing exclusively via Ingress.
4. Start the add-on. On the first launch, the add-on will download ~3 GB of model weights to `/data/hf`. Subsequent starts are near-instant.
5. Watch the add-on log until you see:
   `Laya Router checkpoints successfully preloaded and ready: ['english', 'multilingual']`
6. Access the built-in **Ingress WebUI** via the Home Assistant sidebar to test prompts interactively.

---

## Task Families & Parameter Configuration

In version 0.5.0, task families, semantic matching criteria, and upstream LLM execution parameters are fully configurable directly in the Home Assistant add-on options (`families:`).

### Default Configuration

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

### Parameter Defaults & Omission Rules

When configuring family profiles in Home Assistant:
- **`temperature` (optional):** When omitted or unset, the upstream provider's default model temperature is used without modification.
- **`thinking_budget` (optional):** When omitted or unset (as for `code` and `deep`), reasoning tokens are unconstrained, allowing models like Gemini Flash Thinking or Gemini 2.5 Pro to think naturally. Setting `thinking_budget: 0` (as in `quick`) strictly disables thinking tokens for instant sub-second replies.
- **`max_tokens` (optional):** When omitted or unset, the model's standard context ceiling applies.
- **`effort` (optional):** Defaults to `low` (quick), `medium` (general), `high` (code), or `high` (deep).

### Adding Custom Families (Dynamic Expansion)

You can define any custom category (e.g. `smarthome`, `creative`, `finance`) by simply appending it to the `families` list in Home Assistant. Laya dynamically evaluates all configured families in a single sub-100ms forward pass:

```yaml
  - name: "smarthome"
    criteria: "domotica status, lampen, scènes, verwarming, Home Assistant entiteiten controleren of schakelen"
    model: "gemini-3.5-flash-lite"
    effort: "low"
    max_tokens: 512
    temperature: 0.0
    thinking_budget: 0
```

---

## Client Integration Examples (Hermes Agent)

### Scenario A: Native Single Provider (Google Gemini) - Default
Hermes Agent directly calls Google's API with a single `GEMINI_API_KEY`:

```yaml
plugins:
  entries:
    laya-router:
      settings:
        mode: observe
        laya_url: http://local-laya:8000
        api_key_env: LAYA_API_KEY
        question_set: hermes-v1
        timeout_s: 1.0
        min_confidence: 0.6
        context_turns: 2
        default: {model: gemini-flash-latest, effort: medium}
        families:
          quick:   {model: gemini-3.5-flash-lite, effort: low}
          general: {model: gemini-flash-latest, effort: medium}
          code:    {model: gemini-flash-latest, effort: high}
          deep:    {model: gemini-2.5-pro, effort: high}
        downgrade_within_session: false
        audit_enabled: true
```

### Scenario B: Multi-Provider Gateway (LiteLLM or OpenRouter)
Because LLM clients **cannot dynamically change provider credentials mid-session**, routing across multi-vendor models (e.g. Anthropic Claude + Google Gemini + OpenAI) requires Hermes to be configured with a single unified gateway provider (`provider: openrouter` or `provider: custom` pointing to LiteLLM):

```yaml
# Under Hermes config.yaml
model: google/gemini-flash-1.5

plugins:
  entries:
    laya-router:
      settings:
        mode: observe
        laya_url: http://local-laya:8000
        api_key_env: LAYA_API_KEY
        question_set: hermes-v1
        default: {model: google/gemini-flash-1.5, effort: medium}
        families:
          quick:   {model: google/gemini-3.5-flash-lite, effort: low}
          general: {model: google/gemini-flash-1.5, effort: medium}
          code:    {model: anthropic/claude-3.5-sonnet, effort: high}
          deep:    {model: anthropic/claude-3.7-sonnet, effort: high}
```
