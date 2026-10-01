---
id: integrations/assist_conversation
title: "Assist Conversation Agent (Per-User Routing)"
type: "Integration Contract"
description: "How the hermes_agent Home Assistant integration maps Assist requests to Hermes profiles and sessions."
status: active
trust: unverified
tags: [assist, voice, conversation, routing, home-assistant]
generated:
  by: "agent:claude-opus-5.5"
  at: "2026-10-01T00:00:00Z"
sources:
  - id: integration
    resource: "ha_integration/custom_components/hermes_agent/conversation.py"
    title: "HermesConversationEntity"
  - id: hermes-api
    resource: "/config/.hermes/hermes-agent/gateway/platforms/api_server_openai_routes.py"
    title: "Hermes OpenAI-compatible routes (X-Hermes-Session-Id)"
---

# Assist Conversation Agent (Per-User Routing)

## Topology
```
Assist (app, browser, satellite) ──► conversation.hermes_agent (HA Core)
        │ user_id → person.<name> → profile <name>
        ▼
http://local-hermes-agent:8080[/profile/<name>]/v1/chat/completions  (Bearer access_password)
```
Traffic stays on the internal Supervisor network; it needs `enable_api` and the
direct ports enabled in the App (nginx listens on 8080 inside the container even
when no host port is mapped).

## Routing contract
1. `user_input.context.user_id` → the `person` entity whose `user_id` attribute matches.
2. Profile name = the person's object id, sanitised exactly like `profile-init.sh`.
3. The primary profile is served at the root; any other profile is used only when
   `/profile/<name>/v1/health` returns 200 (cached per name). Otherwise → primary.
4. No user (voice satellites, automations) → primary profile.

## Session contract
- HA `conversation_id` → `(profile, Hermes session id)` in memory (max 200 entries).
- The first turn omits `X-Hermes-Session-Id`; Hermes returns one, which follow-up
  turns send back so Hermes loads the history from its own `state.db`.
- A changed profile for the same conversation starts a new Hermes session.
- The integration does not store transcripts; HA restarts start fresh sessions.

## Output contract
A system message asks for short, markdown-free answers in the user's language
(TTS-safe); residual `**`, backticks and heading markers are stripped before the
speech response is set.

## Installation
Not installed by the App (the App has no `homeassistant_config` mapping by design).
Copy the folder to `/config/custom_components/` and restart Home Assistant.
