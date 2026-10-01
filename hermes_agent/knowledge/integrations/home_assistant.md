---
id: integrations/home_assistant
title: "Home Assistant Integration Contract"
type: "Integration Contract"
description: "Token and URL resolution, MCP tool injection, status sensors, and the bundled Home Assistant skill."
status: active
trust: unverified
tags: [home-assistant, mcp, sensors, supervisor]
generated:
  by: "agent:claude-opus-5.5"
  at: "2026-10-01T00:00:00Z"
sources:
  - id: run-sh
    resource: "run.sh"
    title: "HASS_URL / HASS_TOKEN resolution, scaffold_profile_files"
  - id: mcp
    resource: "ha_mcp_config.py"
    title: "HA MCP server injection"
  - id: sensors
    resource: "ha_sensor_reporter.py"
    title: "Status sensor reporter"
---

# Home Assistant Integration Contract

## Credentials
- `HASS_TOKEN` = `homeassistant_token` option, else `SUPERVISOR_TOKEN`
  (`homeassistant_api: true`).
- `HASS_URL` = `hass_url` option, else `http://supervisor/core`.
- **Invariant (enforced since 2.4.0):** `SUPERVISOR_TOKEN` is only valid against
  `http://supervisor/core`. Without `homeassistant_token`, `run.sh` forces
  `HASS_URL=http://supervisor/core`; a custom `hass_url` requires a long-lived token.

## MCP tools
On every start each profile's `config.yaml` gets `mcp_servers.homeassistant` running
a **pinned** `npx -y @orellbuehler/homeassistant-mcp@<version>` with
`HASS_URL`/`HASS_TOKEN`.
- Only that block (and, for secondary profiles, `platforms.*.enabled`) is managed.
- Files are round-tripped with `ruamel.yaml` (PyYAML fallback), so user comments and
  formatting survive; nothing is written when the managed values already match.
- Writes are atomic and leave the file `0600`. An unparseable `config.yaml` is never
  overwritten.

## Status sensors
`ha_sensor_reporter.py` posts `sensor.hermes_agent` (version, profiles) and
`sensor.hermes_agent_<profile>`.
- `enable_api: true`: `online` ⇔ `GET /v1/health` on the profile's API port answers 200.
- `enable_api: false`: `online` ⇔ a process running `gateway-launcher.py` has the
  profile home as its working directory (`gateway_running` attribute).
- Changed states are posted immediately (15 s poll); unchanged ones every 5 min, which
  restores the REST-created entities after a Core restart. They have no `unique_id`
  (a REST API limitation), so they cannot be edited in the UI.

## Skill
A `skills/homeassistant/SKILL.md` template is created once per profile (never
overwritten), including Voice Assist TTS guidance.
