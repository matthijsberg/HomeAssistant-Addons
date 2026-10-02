---
id: architecture/gateway_supervision
title: "Per-Profile Gateway Slot Supervision"
type: "Architectural Decision"
description: "Process model, handoff contract, readiness, containment and restart rules for one Hermes gateway per profile."
status: active
trust: unverified
tags: [gateway, supervisor, lifecycle, s6, logging]
generated:
  by: "agent:claude-opus-5.5"
  at: "2026-10-01T00:00:00Z"
sources:
  - id: run-sh
    resource: "run.sh"
    title: "Add-on entrypoint (Sections 10–12)"
  - id: gateway-helpers
    resource: "gateway-supervisor.py, gateway-launcher.py, gateway-logger.py, gateway-child.sh"
    title: "Upstream v1.3.1/v1.3.2 gateway helpers"
---

# Per-Profile Gateway Slot Supervision

## Process tree (one slot per profile)
```
run.sh (s6 foreground, RUN_SH_PID)
├── gateway-logger.py  ── reads /run/hermes-gateway-<i>.fifo → stdout + <home>/logs/gateway.log
└── gateway-child.sh → gateway-supervisor.py  (process-group leader, subreaper)
                         └── venv/bin/hermes-gateway gateway-launcher.py gateway run --external-supervisor
                               └── platform children (e.g. WhatsApp bridge)
```

## Normative contracts
1. **One gateway per profile.** `GATEWAY_MULTIPLEX_PROFILES=false` and the
   `HERMES_HOME` pin are enforced by the launcher after every env/config load, so a
   sticky `active_profile`, profile `.env`, or managed YAML cannot redirect a slot.
2. **API settings are add-on owned.** `HERMES_ADDON_API_{HOST,PORT,ENABLED,KEY}` are
   handed to the launcher and applied to the final `GatewayConfig`. Disabled API ⇒ the
   `API_SERVER` platform is removed, whatever the profile config says.
3. **Readiness.** A slot is started only when the supervisor writes its PID to
   `/run/hermes-gateway-<i>.ready` within 5 s; otherwise startup is fatal (exit 70).
4. **Containment before restart.** A slot is restarted only after the supervisor exits
   0, which it does only when every descendant is gone. A nonzero supervisor exit is
   **container-fatal** so Supervisor restarts the add-on instead of overlapping slots.
5. **Logger is part of the slot.** If the logger dies, the slot tree is stopped and
   restarted; a log-sink write failure is a profile failure, never silent.
6. **Signals.** SIGTERM/SIGINT during a slot start is deferred until readiness or
   cleanup completes (`start_gateway_signal_safe`).

## Hermes `update` handback
Modern Hermes recognises `--external-supervisor` and hands restarts back to the
add-on: the gateway exits cleanly and the slot is respawned on new code. Before
2.4.0 the fork ran `hermes.real gateway run --replace`, so `hermes update` classed the
gateways as unmanaged and stopped them ("Stopped 3 manual gateway process(es)").
Older Hermes revisions without the flag have it stripped by the launcher.

## Not supported
Native `hermes gateway stop|restart` for add-on slots (upstream issue #35). Restart
the add-on from Home Assistant instead.

## Dashboards
Dashboards are separate processes (not slots). `supervise_dashboards` restarts a dead
dashboard at most once per 5 minutes, re-reads its new session token, re-renders
nginx, and reloads it.
