# Product Requirements Document (PRD)

## Project Name: Home Assistant Matrix Server Add-on (`ha-addon-matrix`)
**Version:** 0.1.0  
**Status:** In Development  
**Backend Engine:** Conduit (Rust-based Matrix Homeserver)

---

## 1. Overview & Goals
Provide a lightweight, self-contained, stable, and low-maintenance **Matrix Homeserver Add-on for Home Assistant OS**.

### Key Objectives:
- **Ultra-Low Footprint:** Use Conduit (Rust) to keep RAM usage under 50MB and CPU impact minimal.
- **Zero-External-Dependencies:** Embedded SQLite/RocksDB database — no external PostgreSQL required.
- **Native HAOS Integration:** Direct installation via Home Assistant Local Add-on repository (`/config/addons/matrix`).
- **Flexible Configuration:** Simple HA Add-on configuration UI for server name, registration, ports, and admin token.
- **Hermes Readiness:** Ready for integration with Hermes Agent & HA Matrix bot notification integrations.

---

## 2. Architecture & Components
- **Container Base:** `alpine:3.20` / `matrixconduit/matrix-conduit` binary.
- **Supervisor Integration:** HAOS Add-on schema (`config.yaml`), `build.yaml`, and `DOCS.md`.
- **Startup Supervisor:** Shell entrypoint / `run.sh` with automatic configuration rendering (`conduit.toml`).
- **Data Persistence:** Persistent volume mounted at `/data/conduit` preserving matrix database, media, and keys across container updates.

---

## 3. Configuration Parameters (HA Add-on Options)
- `server_name` (string, required): Domain name for Matrix (e.g. `matrix.local` or `hass.b3rg.nl`).
- `allow_registration` (boolean, default: `false`): Enable public account registration.
- `database_backend` (string, default: `sqlite`): `sqlite` or `rocksdb`.
- `port` (int, default: `6167` / `8008`): Internal HTTP port for Matrix client/server API.
- `log_level` (string, default: `warn`): `trace`, `debug`, `info`, `warn`, `error`.
- `max_request_size` (int, default: `20971520`): Max upload size in bytes (20MB).

---

## 4. Developer Empowerment & Quality Standard
- **1-Click Setup (`scripts/setup.sh`):** Installs and verifies local HA add-on structure.
- **1-Click Demo (`scripts/demo.sh`):** Runs a container smoke-test to verify Matrix server startup and API readiness (`/_matrix/client/versions`).
- **Automated QA:** Automated health check script verifying container startup and Matrix endpoint response.
- **Pre-Release Security Audit:** Strict credential isolation, PII check, and input sanitization before release.
