---
id: trace-ingestion
title: "Hermes Agent Trace Ingestion Contract"
type: "Integration Contract"
status: "active"
trust: "human_reviewed"
tags: [hermes-agent, telemetry, langfuse, observability, tokens]
---

# Hermes Agent Trace Ingestion Contract

## Context & Rationale
Hermes Agent natively bundles the `observability/langfuse` plugin. When active, it instruments conversations, turns, generations, system prompts, tool calls, and model costs directly into Langfuse.

## Normative Contracts & Invariants

1. **Zero External Network Leaks (Local Only):**
   - Telemetry must not egress outside the local host or home network unless explicitly configured by the user.
   - Traces route via the internal add-on bridge network: `http://<langfuse_hostname>:3000`.
2. **Headless Provisioning:**
   - On initial container launch, an organization (`Default`), a project (`Hermes`), and a dedicated API key pair (`pk-lf-...`, `sk-lf-...`) are provisioned automatically without user interaction.
   - When `export_hermes_env: true` is set, credentials write to `/share/langfuse/hermes.env` with restricted permissions.
3. **Capture Mode Compliance:**
   - Default capture mode is `sanitized` (automatic pattern-based redaction of passwords, private keys, and session tokens before ingestion).
4. **SDK Parity:**
   - Requires Langfuse Python SDK `>= 4.7.0` inside the caller environment to ensure real-time asynchronous batch flushing without blocking agent execution.
