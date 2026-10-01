# Laya Router Documentation

## Installation & Setup

1. Add this repository to Home Assistant Add-on Store:
   `https://github.com/matthijsberg/HomeAssistant-Addons`
2. Install the **Laya Router** add-on.
3. In the add-on configuration tab, set a secure `api_key` (Bearer token).
4. Start the add-on. On the first launch, the add-on will download ~3 GB of model weights to `/data/hf`. Subsequent starts are near-instant.
5. Watch the add-on log until you see:
   `Laya Router checkpoints successfully preloaded: ['english', 'multilingual']`

## Hermes Agent Plugin Configuration

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
          quick:   {model: gemini-2.5-flash-lite, effort: low}
          general: {model: gemini-flash-latest, effort: medium}
          code:    {model: gemini-flash-latest, effort: high}
          deep:    {model: gemini-2.5-pro, effort: high}
        downgrade_within_session: false
        audit_enabled: true
```

### Scenario B: Multi-Provider Gateway (LiteLLM or OpenRouter)
Because Hermes Agent **cannot dynamically change provider credentials mid-session**, routing across multi-vendor models (e.g. Anthropic Claude + Google Gemini + OpenAI) requires Hermes to be configured with a single unified gateway provider (`provider: openrouter` or `provider: custom` pointing to LiteLLM):

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
          quick:   {model: google/gemini-2.5-flash-lite, effort: low}
          general: {model: google/gemini-flash-1.5, effort: medium}
          code:    {model: anthropic/claude-3.5-sonnet, effort: high}
          deep:    {model: anthropic/claude-3.7-sonnet, effort: high}
```
