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
Preferred transport: **MQTT discovery** (`services: mqtt:want`; credentials from
`GET http://supervisor/services/mqtt`), published by `ha_sensor_reporter.py` running
on the image's system Python with `python3-paho-mqtt` (independent of the Hermes venv).
- Devices: `Hermes Agent` (app) and `Hermes <profile>` (`via_device`).
- Entities: `binary_sensor.hermes_agent_<profile>` (connectivity), `…_gateway`
  (running, diagnostic), `…_api` (only with `enable_api`), `sensor.hermes_agent_app_version`.
- Availability topic `hermes_agent/status` with a retained `offline` last-will: every
  entity turns unavailable when the App stops.
- `online` = `/v1/health` answers (API enabled) or the gateway process runs (API off).
- On MQTT start the legacy REST entities (`sensor.hermes_agent[_<profile>]`) are deleted.

Fallback without a broker: REST-posted `sensor.hermes_agent[_<profile>]` states,
re-posted every 5 minutes (they vanish on Core restart and have no `unique_id`).

## Assist
See [`assist-conversation`](assist_conversation.md).

## Skill
A `skills/homeassistant/SKILL.md` template is created once per profile (never
overwritten), including Voice Assist TTS guidance.
