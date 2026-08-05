# AGENTS.md — Instructions for AI Agents & Developers

This document defines context, architectural constraints, and test commands for AI agents working on this project.

## Project Summary
- **App Name:** Matrix Synapse Home Assistant Add-on
- **Version:** `1.0.0-dev`
- **Location:** `/config/addons/matrix-synapse/`
- **Purpose:** Full-stack Matrix Synapse + Postgres + Sliding Sync Home Assistant Add-on.

## Directory Structure
```
matrix-synapse/
├── PRD.md
├── ARCHITECTURE.md
├── README.md
├── AGENTS.md
├── config.yaml          # Home Assistant Add-on configuration manifest
├── build.yaml           # HA Add-on multi-arch build configuration
├── Dockerfile           # Docker container image spec
├── rootfs/              # Container startup & service orchestration scripts
│   └── etc/
│       └── s6-overlay/  # Service definitions (postgres, synapse, sliding-sync)
└── scripts/
    ├── setup.sh         # Local verification script
    ├── demo.sh          # Local test run script
    └── backup.sh        # Backup target export script (/backup mount)
```

## Critical Rules for AI Agents
1. **Never Hardcode Secrets:** Always generate dynamic passwords/tokens in `/data/secrets.json` on first launch.
2. **Mount Paths:**
   - `/ssl` -> Home Assistant SSL certificates (Let's Encrypt / custom certs)
   - `/backup` -> Target folder for HA Supervisor backup snapshots
   - `/config` -> Shared Home Assistant configuration directory
   - `/data` -> Add-on persistent internal data (Postgres DB, media, keys)
3. **Chat-Only Output Isolation (Phase 4):**
   - During dev & testing, outputs must stay in the active chat. Do NOT send test webhooks or messages to external WhatsApp/Telegram groups.
4. **Test Verification:**
   - Always run `bash scripts/setup.sh` to validate `config.yaml` schema, Dockerfile syntax, and script permissions before committing.

## Quick Test Commands
- Validate Add-on config schema: `python3 -c "import yaml; yaml.safe_load(open('config.yaml'))"`
- Run lint / syntax check: `bash scripts/setup.sh`
