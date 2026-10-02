# AGENTS.md — Development Instructions for AI Agents

## Project Overview
This repository contains the **Home Assistant Matrix Server Add-on** powered by **Conduit** (Rust Matrix Homeserver).

## Architecture & Conventions
- **Version:** Single source of truth in `config.yaml` (`version: "0.1.0"`).
- **Base image:** Alpine Linux with official pre-compiled `conduit` binary or Rust build.
- **Entrypoint:** `run.sh` parses Home Assistant options from `/data/options.json` and starts `conduit`.
- **Database:** SQLite DB located at `/data/conduit.db`.

## Testing & Smoke Verification
- Run local container test: `bash scripts/demo.sh`
- Verify Matrix API response: `curl -s http://localhost:6167/_matrix/client/versions`
- Pre-release security check: `bash scripts/security_audit.sh`

## Output Isolation Rule
All development test runs, logs, and artifacts MUST remain strictly in local terminal/chat. No multicast to external channels during development.
