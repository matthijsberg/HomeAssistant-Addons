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
`npx -y @orellbuehler/homeassistant-mcp` with `HASS_URL`/`HASS_TOKEN`. The file is
re-serialised with `yaml.safe_dump`, so **comments and formatting in profile
`config.yaml` are lost on each start**.

## Status sensors
`ha_sensor_reporter.py` posts `sensor.hermes_agent` (version, profiles) and
`sensor.hermes_agent_<profile>` (online/offline from `GET /v1/health` on the
profile's API port). With `enable_api: false` there is no API listener, so profile
sensors always read `offline`. Sensors are state-only (no `unique_id`) and disappear
after a Core restart until the next report.

## Skill
A `skills/homeassistant/SKILL.md` template is created once per profile (never
overwritten), including Voice Assist TTS guidance.
