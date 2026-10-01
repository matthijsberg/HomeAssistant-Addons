---
id: architecture/upstream_lineage
title: "Upstream Lineage and Sync Procedure"
type: "Architectural Decision"
description: "Which upstream hermes-ha-addon release this fork is based on, what has been ported since, and how to sync future releases."
status: active
trust: unverified
tags: [upstream, fork, sync, versioning]
generated:
  by: "agent:claude-opus-5.5"
  at: "2026-10-01T00:00:00Z"
sources:
  - id: upstream-repo
    resource: "https://github.com/WolframRavenwolf/hermes-ha-addon"
    title: "Upstream Hermes Agent Home Assistant add-on (Wolfram Ravenwolf)"
  - id: upstream-changelog
    resource: "https://github.com/WolframRavenwolf/hermes-ha-addon/blob/main/hermes_agent/CHANGELOG.md"
    title: "Upstream CHANGELOG"
---

# Upstream Lineage and Sync Procedure

## Context
This add-on is a fork of `WolframRavenwolf/hermes-ha-addon`. The fork uses its own
SemVer line (2.x) and does **not** share version numbers with upstream (1.x).
Upstream changes must therefore be tracked explicitly; a version comparison alone
says nothing about which upstream fixes are present.

## Lineage (normative record)

| Fork version | Upstream base / ports | Notes |
|---|---|---|
| 2.0.0 – 2.1.0 | upstream **v1.3.0** (`05b4ee7`) | Fork point. HA user sync, MCP, sensors, voice skill added. |
| 2.2.0 | partial hand-port of v1.3.1/v1.3.2 | `api-server.sh` credential validation, `backup_exclude`, `hermes-gateway` symlink, owned `.env` keys. Gateway supervisor stack **not** ported. |
| 2.4.0 | full port of **v1.3.1 → v1.3.4** (`d6de622`) | Gateway supervisor/launcher/logger, `.python-version` pin + venv rebuild, `gateway.standalone` for named profiles, credential-rule translations. |

Upstream `docs/verification/` smoke records and `tests/` are **not** vendored; they
assume upstream's `run.sh` layout. See [testing gap](#known-divergences).

## Known divergences (intentional)
- `log()` helper with level filtering and timestamps replaces upstream's bare `echo`.
- `api-server.sh` accepts `profile_env_vars` for profiles that are not in the
  `profiles` option, because HA user sync adds profiles at runtime.
- TLS generation lives in `tls-certs.sh`; backups in `backup-setup.sh`; HA user sync
  in `ha-user-sync.sh`.
- `kill_port` pre-cleans stale listeners and the WhatsApp bridge (port 3000) before
  the primary gateway starts.
- Dashboards are supervised and restarted (upstream starts them once).
- The dashboard launch imports `hermes_bootstrap` first (see
  [python-runtime](python_runtime.md)).
- Upstream helper `cleanup_gateway_descendants` is unused upstream and was not ported.

## Sync procedure
1. Clone upstream and diff from the last recorded port commit:
   `git diff d6de622 <new-tag> -- hermes_agent tests`.
2. Port per file; keep the divergences above.
3. Run upstream's suite against the fork in a scratch tree (copy upstream, replace
   `hermes_agent/` with the fork, rewrite `log "` → `echo "` in the copy, unset
   `SUPERVISOR_TOKEN` so HA user sync stays offline). Expected fork-only failures:
   version/changelog metadata, helper-lookup style, `%%ACCESS_LOG%%`, and
   `kill_port` in the extracted shutdown harness.
4. Record the new upstream commit in the table above and in [`log.md`](../log.md).
