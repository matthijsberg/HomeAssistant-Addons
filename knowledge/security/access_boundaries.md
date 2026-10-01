---
id: security/access_boundaries
title: "Access Boundaries and Secret Handling"
type: "Security Invariant"
description: "Authentication per entry point, credential rules, secret file modes and container confinement."
status: active
trust: unverified
tags: [security, auth, nginx, secrets, apparmor]
generated:
  by: "agent:claude-opus-5.5"
  at: "2026-10-01T00:00:00Z"
sources:
  - id: api-server
    resource: "api-server.sh"
    title: "Credential validation and serialization"
  - id: nginx
    resource: "nginx.conf.tpl, nginx-ports.conf.tpl, nginx-render.sh"
    title: "Ingress and direct-port routing"
  - id: manifest
    resource: "config.yaml, apparmor.txt"
    title: "Add-on manifest and AppArmor profile"
---

# Access Boundaries and Secret Handling

## Entry points
| Entry | Auth | Notes |
|---|---|---|
| HA Ingress (49169) | Home Assistant session | Dashboard token injected server-side by nginx. |
| Direct HTTP/HTTPS (8080/8443) | Basic auth `hermes:<access_password>` | `/hermes/`, `/terminal/` return 403 when no password is set. |
| `/v1/*` OpenAI API | Bearer `access_password` | `/v1/health` is public liveness; `/v1/models` verifies auth. |
| Desktop backend (9119) | `access_password` | Opt-in; full agent control; trusted LAN/VPN only. |

## Credential rules (enforced before nginx or Hermes start)
With `enable_api`, `access_password` must be ≥ 16 printable ASCII characters after
trimming, must not be a known placeholder, and must not contain `'`, `\`, dotenv
interpolation, or line breaks. `env_vars` / `profile_env_vars` must be single-line with
shell-identifier names. Rejected values are never echoed.

## Secret-bearing files
| File | Mode | Content |
|---|---|---|
| `<profile>/.env` | 0600 | Provider keys, `API_SERVER_KEY` |
| `/config/.hermes_profile` | 0600 (since 2.4.0) | `HASS_TOKEN`, `GITHUB_TOKEN` |
| `/etc/nginx/.htpasswd` | 0644 | apr1 hash only; unprivileged nginx workers must read it (2.3.2 notes claimed 0600) |
| `<profile>/config.yaml` | 0600 (since 2.5.0) | `HASS_TOKEN` in the HA MCP server block |
| `$CERTS_DIR/*.key` | 0600 | Self-signed TLS keys |

## TLS verification
No component disables certificate checks (removed in 2.5.0: `NODE_TLS_REJECT_UNAUTHORIZED=0`
in the MCP block and `curl -k` in HA user sync). Use the default internal
`http://supervisor/core`, or a custom `https://` URL with a valid certificate.

## Supply chain
The HA MCP server is pinned (`@orellbuehler/homeassistant-mcp@<version>` in
`ha_mcp_config.py`); bump it deliberately after reviewing the release.

## AppArmor confinement
`apparmor.txt` mirrors Docker's `docker-default` (capability set, signal/ptrace peers,
`/proc` and `/sys` write denials, no `mount`) and additionally denies writes to
`/run.sh`, `/usr/local/{lib,bin}/hermes-*` and nginx templates.
- **Rollout invariant:** the profile ships with `complain`. Allow-rule gaps are logged
  as `apparmor="ALLOWED"` (`ha host logs -t audit`) and never block. `deny` rules are
  enforced regardless of mode.
- Switch to enforce (drop `complain`) only after an observation window with no
  `ALLOWED` entries for `local_hermes_agent`, covering a restart, a `hermes update`,
  dashboard use, a terminal session and a periodic backup.
