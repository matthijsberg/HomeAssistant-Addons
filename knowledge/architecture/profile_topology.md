---
id: architecture/profile_topology
title: "Profile Resolution, HA User Sync and Routing"
type: "Architectural Decision"
description: "How profiles are resolved from options and Home Assistant users, where they live, which ports and URL prefixes they get, and how channels are isolated."
status: active
trust: unverified
tags: [profiles, multi-user, routing, nginx, ports]
generated:
  by: "agent:claude-opus-5.5"
  at: "2026-10-01T00:00:00Z"
sources:
  - id: profile-init
    resource: "profile-init.sh"
    title: "resolve_profiles, apply_env_vars_for_profile, configure_profile_topology"
  - id: ha-user-sync
    resource: "ha-user-sync.sh"
    title: "sync_ha_users"
  - id: nginx-render
    resource: "nginx-render.sh"
    title: "Per-profile nginx locations"
---

# Profile Resolution, HA User Sync and Routing

## Resolution order
1. `profiles` option (list) → otherwise legacy `hermes_home` (default `.hermes`).
2. Bare names resolve to `<profiles_base>/<name>` (default `.hermes/profiles`); an
   existing flat dir (e.g. `/config/amy`) is kept when the new target does not exist.
3. `auto_sync_ha_users: true` adds one profile per `person.*` entity linked to an HA
   user. When `profiles` is empty, the first HA user replaces `.hermes` as primary and
   inherits its memories, sessions, `state.db`, and `.env` (copy, never move).
4. Names are sanitised; collisions are fatal.

## Index-derived allocation (profile *i*, 0 = primary)
| Resource | Value |
|---|---|
| URL prefix | `""` for i=0, `/profile/<name>` otherwise |
| API port | 8642 + i |
| ttyd Hermes / terminal | 49269 + i / 49369 + i |
| Dashboard | 49469 + i |

Ports change if the profile **order** changes; HA sensors and external clients that
cache ports must follow.

## Gateway topology
- Named profiles (homes directly under a `profiles/` dir) get
  `gateway.standalone: true` before any gateway starts, on Hermes revisions that
  support it (feature-detected via `hermes_cli.profiles.profile_is_standalone`).
- The default/flat homes are left untouched.

## Channel isolation (fork contract)
Only the primary profile may own polling channels. For i > 0 the add-on deletes
`TELEGRAM_*`, `WHATSAPP_*`, `DISCORD_*`, `SLACK_*` from `.env`, skips them from
shared `env_vars`, and sets `platforms.<telegram|whatsapp|discord|slack|signal>.enabled:
false` in `config.yaml`. This prevents Telegram 409 long-poll conflicts and port-3000
collisions. Per-profile channels must use `profile_env_vars` **and** re-enable the
platform in that profile's config by hand.
