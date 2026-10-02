# PRD — Home Assistant Add-on: Langfuse v4 Observability Server

**Project:** `langfuse` (Home Assistant Add-on)
**Target Repository:** [`matthijsberg/HomeAssistant-Addons`](https://github.com/matthijsberg/HomeAssistant-Addons)
**Target Platform:** Home Assistant OS (Supervisor Add-on)
**Stack Version:** Langfuse v4 (Web + Worker, PostgreSQL, ClickHouse, Redis, S3-compatible blob store)
**Author:** Matthijs van den Berg & Hermes Agent
**Version:** 2.1 (Revised Specification)
**Date:** 2026-09-29
**Status:** Ready for Phase 0 spikes

---

## 0. Changes since v2.0

| # | Change | Reason |
|---|---|---|
| 1 | Target **Langfuse v4** instead of v3 | v4 is GA since 2026-07-29; v3 only gets security patches until end of January 2027. Same components, newer data model, no migration needed later. |
| 2 | Added **SeaweedFS** as S3-compatible blob store | Blob storage is mandatory in v3/v4. MinIO Community Edition is archived and no longer ships binaries or images. |
| 3 | **Ingress redesigned** (custom web build + runtime base-path substitution + dual nginx listeners) | Langfuse bakes its base path in at build time; HA's ingress path is only known at runtime. Ingress is a hard requirement. |
| 4 | **Hermes connectivity corrected** | `127.0.0.1` only works for host-network add-ons; the `local-` hostname only applies to locally installed add-ons. Internal add-on traffic does not need a host port. |
| 5 | Added **retention (30 days)**, **cold backups**, **headless init**, **security model**, **resource budget**, **acceptance criteria**, **risks** | Missing from v2.0. |
| 6 | Fixed `ingress_port`, added `timeout`, `backup`, `image`, `watchdog`, required ClickHouse/Redis settings | Correctness and clean shutdowns. |
| 7 | Base image and runtimes aligned with Langfuse's own images; Node 20 removed | Native modules (Prisma engines) must match the libc they were built for; Node 20 is EOL. |

---

## 1. Executive Summary & Objectives

### 1.1 Purpose
Build and deploy a native Home Assistant add-on for **Langfuse v4**, providing a private, self-hosted LLM observability and prompt-management platform inside Home Assistant OS. It is the tracing backend for **Hermes Agent** (bundled `observability/langfuse` plugin) and any other LLM application on the home network.

### 1.2 Key Deliverables
1. **Complete Langfuse v4 stack** in one add-on: PostgreSQL, ClickHouse, Redis, SeaweedFS (S3), Langfuse Web, Langfuse Worker, nginx.
2. **S6-overlay v3 supervision** with an explicit dependency graph, readiness gates, and clean shutdowns within the add-on stop timeout.
3. **Full Home Assistant Ingress** (required): the complete Langfuse UI works from the HA sidebar, locally and via the external URL.
4. **Optional LAN port** (`3000/tcp`, default disabled) for direct UI and SDK/OTLP ingestion.
5. **Zero-touch first start**: org, `Hermes` project, admin user and API keys provisioned automatically.
6. **30-day retention** of trace data and **cold, consistent HA backups**.
7. **Prebuilt images on GHCR** and publication in `matthijsberg/HomeAssistant-Addons` under `langfuse/`.

### 1.3 Non-goals (v1)
- Multi-node / clustered ClickHouse.
- Multimodal media uploads and batch exports to S3 (would require exposing the blob store to clients).
- SMTP / email (password reset, invitations).
- SSO / OIDC login.
- Migrating data from an existing Langfuse v2/v3 instance.

---

## 2. System Architecture

```
┌──────────────────────────────────────────────────────────────────────────┐
│                   Home Assistant OS host (hassio network)                │
│                                                                          │
│   Hermes add-on                         HA frontend / Supervisor         │
│   (observability/langfuse, SDK ≥4.7)    (ingress proxy 172.30.32.2)      │
│        │  http://<hostname>:3000             │  /api/hassio_ingress/<t>/  │
│        │  (or 127.0.0.1:<port> if host_net)  │                           │
│        ▼                                     ▼                           │
│  ┌────────────────────────────────────────────────────────────────────┐  │
│  │              langfuse add-on container (s6-overlay v3)             │  │
│  │                                                                    │  │
│  │   nginx :3000  (API/OTLP + LAN UI)     nginx :8099  (ingress only)  │  │
│  │        └───────────────┬───────────────────────┘                   │  │
│  │                        ▼  prefixes base path, rewrites auth origin │  │
│  │              Langfuse Web :3100 (127.0.0.1)                        │  │
│  │                        │                                           │  │
│  │     ┌──────────────┬───┴──────────┬──────────────┬───────────────┐ │  │
│  │     ▼              ▼              ▼              ▼               ▼ │  │
│  │  Postgres 16   Redis 7.2     ClickHouse      SeaweedFS     Langfuse │  │
│  │  :5432         :6379         :8123/:9000     S3 :8333      Worker   │  │
│  │  (OLTP)        (queue/cache) (OLAP)          (blobs)       :3030    │  │
│  │     all bound to 127.0.0.1 — unreachable from other add-ons        │  │
│  └─────┼──────────────┼──────────────┼──────────────┼─────────────────┘  │
│        ▼              ▼              ▼              ▼                    │
│   /data/postgres  /data/redis  /data/clickhouse  /data/seaweedfs         │
│                          /data/secrets.env                               │
└──────────────────────────────────────────────────────────────────────────┘
```

### 2.1 Component versions (pinned; bumped via Renovate)

| Component | Version | Notes |
|---|---|---|
| Langfuse Web | latest stable `4.x` at Phase 1, pinned exact | **Custom build from source** (see §3) |
| Langfuse Worker | same `4.x` tag as Web | Official `langfuse/langfuse-worker` image |
| ClickHouse | **26.4** (v4 minimum 25.12) | Single node, `CLICKHOUSE_CLUSTER_ENABLED=false` |
| PostgreSQL | 16 (v4 minimum 15) | |
| Redis | 7.2 (v4 minimum 7.0); Valkey 8 acceptable | `maxmemory-policy noeviction` |
| SeaweedFS | latest stable, pinned | S3 gateway, path-style addressing |
| nginx | distro package | |
| Node.js | whatever Langfuse's own images ship | Not installed separately |
| Base image | HA base image matching Langfuse's libc (Alpine expected; verify in Phase 0) | Includes s6-overlay v3 + bashio |

### 2.2 Internal ports

| Port | Service | Bound to |
|---|---|---|
| 3000 | nginx — API/OTLP ingestion + LAN UI | `0.0.0.0` (container) |
| 8099 | nginx — HA ingress (`ingress_port`) | `0.0.0.0`, allow `172.30.32.2` only |
| 3100 | Langfuse Web | `127.0.0.1` |
| 3030 | Langfuse Worker (health) | `127.0.0.1` |
| 5432 | PostgreSQL | `127.0.0.1` |
| 6379 | Redis | `127.0.0.1` |
| 8123 / 9000 | ClickHouse HTTP / native | `127.0.0.1` (interserver port disabled) |
| 8333 (+ internal SeaweedFS ports) | SeaweedFS S3 | `127.0.0.1` |

### 2.3 Process supervision (s6-rc dependency graph)

```
init-dirs ─► init-secrets ─► init-config ─► init-basepath
                                  │
          ┌────────────┬──────────┼────────────┐
          ▼            ▼          ▼            ▼
      postgres      redis    clickhouse    seaweedfs        (longruns)
          │                       │            │
   init-postgres-db     init-clickhouse-user   init-s3-bucket   (oneshots, wait for readiness)
          └──────────────┬────────┴────────────┘
                         ▼
                   langfuse-web   (runs Prisma + ClickHouse migrations itself on start)
                         │  wait for /api/public/health
               ┌─────────┴─────────┐
               ▼                   ▼
         langfuse-worker         nginx
                                  
   retention (longrun scheduler; depends on clickhouse + seaweedfs)
```

- **No separate migration stage**: the Langfuse web container applies Prisma and ClickHouse migrations on startup. The worker starts only after the web health check passes, so it never runs against an unmigrated schema.
- Readiness gates are bounded wait loops (e.g. `pg_isready`, ClickHouse `SELECT 1`, S3 `ListBuckets`, web health) with timeouts; a failed gate stops the add-on with a clear log line.
- Shutdown runs in reverse order. `timeout: 120` in `config.yaml` and matching s6 grace times give Postgres and ClickHouse time to stop cleanly (HA's default of 10 s would kill them).

---

## 3. Ingress Design (required)

### 3.1 The problem
- HA serves the add-on under `/api/hassio_ingress/<token>/` and strips that prefix before forwarding to `ingress_port`.
- Langfuse (Next.js) inlines its base path (`NEXT_PUBLIC_BASE_PATH`) into static assets at **build time**, so the prebuilt web image cannot run under a sub-path. nginx rewriting of an unprefixed build is known to be unreliable.
- `NEXTAUTH_URL` must be an absolute URL including base path, but users reach HA via several origins (LAN IP, hostname, external URL).

### 3.2 Solution
1. **Custom web build (CI):** build the Langfuse web image from the pinned source tag with `NEXT_PUBLIC_BASE_PATH=/__LF_BASEPATH_PLACEHOLDER__`.
2. **Runtime substitution (`init-basepath`):** on start, read the ingress entry with `bashio::addon.ingress_entry` (e.g. `/api/hassio_ingress/abc123`). If it differs from the marker of the last run, copy the pristine build and replace the placeholder in all text assets (`.js`, `.json`, `.html`, `.css`, `.rsc`, `.txt`). The runtime copy lives in the container filesystem, not in `/data`, so it stays out of backups. A reinstall (new token) is handled automatically.
3. **nginx ingress listener (`:8099`):** `allow 172.30.32.2; deny all;`, re-adds the ingress prefix to incoming paths, forwards `X-Forwarded-*` headers, and removes any `X-Frame-Options: DENY` / restrictive `frame-ancestors` so the HA iframe renders.
4. **nginx API/LAN listener (`:3000`):** paths that already start with the ingress entry pass through unchanged; all others get the prefix. SDK clients therefore use a plain base URL (`http://host:3000`), and the LAN UI also works because its links point at prefixed paths.
5. **Origin-agnostic auth:** set `NEXTAUTH_URL=http://langfuse.invalid<ingress_entry>/api/auth`. nginx rewrites that fake origin to relative URLs in `Location` headers (`proxy_redirect`) and in auth JSON/HTML responses (`sub_filter`, with upstream compression disabled). Login then works from any origin. **Fallback** if this proves brittle: an `external_url` option used for `NEXTAUTH_URL`, supporting one primary origin.
6. **Two logins are accepted:** HA authenticates the ingress session; Langfuse keeps its own login (long-lived session cookie).

### 3.3 Phase 0 go/no-go criteria
All must pass on the pinned Langfuse version:
- Sign-up is disabled; sign-in and sign-out work via ingress on the LAN URL **and** the external URL.
- Deep links (trace detail, prompt detail, settings) load directly and after a hard refresh.
- No asset 404s in the browser console; client-side navigation stays inside the prefix.
- The UI renders inside the HA iframe (no frame-blocking headers).
- SDK/OTLP ingestion works on `http://<host>:3000` without a path prefix.
- The same checks pass after an add-on reinstall (new ingress token).

If any criterion fails and cannot be fixed within the time box, stop and decide with Matthijs before Phase 1.

---

## 4. Storage & Persistence

| Path | Content |
|---|---|
| `/data/postgres/` | PostgreSQL 16 cluster |
| `/data/clickhouse/` | ClickHouse data and metadata |
| `/data/redis/` | Redis AOF (`appendonly.aof`) |
| `/data/seaweedfs/` | SeaweedFS volumes and filer metadata (bucket `langfuse`) |
| `/data/secrets.env` | Generated secrets, mode `600` |
| `/share/langfuse/hermes.env` | Optional export of Hermes credentials (`export_hermes_env`) |

### 4.1 Generated secrets (first start only, never regenerated)
- `NEXTAUTH_SECRET`, `SALT`: `openssl rand -base64 32`
- `ENCRYPTION_KEY`: **64 hex characters** (`openssl rand -hex 32`); losing it makes encrypted data (e.g. LLM API keys stored in Langfuse) unreadable.
- Random passwords for PostgreSQL, ClickHouse, Redis, SeaweedFS S3 access/secret key.
- Hermes project keys: `pk-lf-<random>` / `sk-lf-<random>`.

### 4.2 Required Langfuse settings (rendered by `init-config`)
- `DATABASE_URL=postgresql://langfuse:<pw>@127.0.0.1:5432/langfuse`
- `CLICKHOUSE_URL=http://127.0.0.1:8123`, `CLICKHOUSE_MIGRATION_URL=clickhouse://127.0.0.1:9000`, `CLICKHOUSE_USER`, `CLICKHOUSE_PASSWORD`, `CLICKHOUSE_CLUSTER_ENABLED=false`
- Redis host/port/password (`127.0.0.1:6379`)
- `LANGFUSE_S3_EVENT_UPLOAD_BUCKET=langfuse`, `_ENDPOINT=http://127.0.0.1:8333`, `_ACCESS_KEY_ID`, `_SECRET_ACCESS_KEY`, `_REGION=auto`, `_FORCE_PATH_STYLE=true`, `_PREFIX=events/`
- Media upload and batch export: not configured (non-goal)
- `NEXTAUTH_URL` (see §3.2), `NEXTAUTH_SECRET`, `SALT`, `ENCRYPTION_KEY`
- `HOSTNAME=127.0.0.1`, `PORT=3100` (web)
- `TELEMETRY_ENABLED` from option (default `false`), `AUTH_DISABLE_SIGNUP=true`
- `NODE_OPTIONS=--max-old-space-size=<node_max_old_space_mb>` for web and worker
- `LANGFUSE_INIT_ORG_ID/NAME`, `LANGFUSE_INIT_PROJECT_ID/NAME=Hermes`, `LANGFUSE_INIT_PROJECT_PUBLIC_KEY/SECRET_KEY`, `LANGFUSE_INIT_USER_EMAIL/NAME/PASSWORD`

### 4.3 ClickHouse configuration
- Dedicated `langfuse` user with full rights on the `default` database plus the system-table grants Langfuse v4 requires (`system.parts`, `system.mutations`, `system.tables`, `system.processes`, `system.query_log*`).
- `max_server_memory_usage` from `clickhouse_memory_limit_mb`.
- System logs: `query_log` kept (Langfuse reads it) with a **3-day TTL**; `trace_log`, `metric_log`, `asynchronous_metric_log`, `text_log`, `processors_profile_log`, `part_log` disabled or TTL-limited. These tables otherwise grow unbounded on small hosts.

---

## 5. Add-on Configuration

### 5.1 `config.yaml`

```yaml
name: "Langfuse"
description: "Self-hosted LLM observability & prompt management (Langfuse v4)"
version: "4.x.y-1"            # <langfuse version>-<add-on revision>
slug: "langfuse"
url: "https://github.com/matthijsberg/HomeAssistant-Addons/tree/main/langfuse"
image: "ghcr.io/matthijsberg/{arch}-addon-langfuse"
init: false                   # s6-overlay v3 is PID 1
arch:
  - amd64                     # required (target host)
  - aarch64                   # best effort
startup: services
boot: auto
timeout: 120                  # clean shutdown of Postgres/ClickHouse
backup: cold                  # add-on is stopped during HA backups
ingress: true
ingress_port: 8099            # nginx ingress listener, not Langfuse
panel_icon: "mdi:chart-timeline-variant"
panel_title: "Langfuse"
panel_admin: true
map:
  - share:rw                  # only used when export_hermes_env is true
watchdog: "tcp://[HOST]:[PORT:3000]"   # verify behaviour with an unmapped port (Phase 0)

ports:
  3000/tcp: null
ports_description:
  3000/tcp: "Direct LAN UI and SDK/OTLP ingestion (optional; not needed for add-on-to-add-on traffic)"

options:
  admin_email: null           # required before first start
  admin_password: null        # required before first start
  retention_days: 30
  clickhouse_memory_limit_mb: 4096
  node_max_old_space_mb: 2048
  export_hermes_env: false
  telemetry_enabled: false
  log_level: "info"

schema:
  admin_email: email
  admin_password: password
  retention_days: int(0,3650)            # 0 = keep forever
  clickhouse_memory_limit_mb: int(1024,16384)
  node_max_old_space_mb: int(512,8192)
  export_hermes_env: bool
  telemetry_enabled: bool
  log_level: list(debug|info|warn|error)
```

`admin_email` / `admin_password` are only applied on the very first start (Langfuse headless init). Changing them later does not change the existing account.

### 5.2 Access matrix

| Host port setting | HA sidebar (Ingress) | LAN `http://<ha-ip>:<port>` | Other add-ons (internal DNS) |
|---|---|---|---|
| **Empty (default)** | Works | Disabled | Works: `http://<hostname>:3000` |
| **`3000` or custom** | Works | UI + API/OTLP | Works: `http://<hostname>:3000` |

**Setting a port does not put Langfuse on the host network.** It only publishes the container's port 3000 on the host's IP. Add-ons on the normal add-on network reach Langfuse via its internal hostname regardless of this setting.

---

## 6. Security Model
- Postgres, Redis, ClickHouse, SeaweedFS, Web and Worker bind to `127.0.0.1` only; other add-ons on the hassio network cannot reach them.
- The ingress listener accepts connections from the Supervisor (`172.30.32.2`) only.
- The `:3000` listener is protected by Langfuse auth (UI) and project API keys (ingestion). Sign-up is disabled after headless init.
- `secrets.env` has mode `600`. Secrets are never written to the log. The Hermes key export to `/share` is opt-in, because any add-on that maps `/share` can read it.
- HA backups contain all secrets: keep HA backup encryption enabled.
- No SMTP: password reset is a documented manual procedure (README).

---

## 7. Data Retention (30 days)
Langfuse's built-in data retention is an Enterprise-only feature for self-hosted instances, so the add-on implements its own:
- **`retention` service:** runs nightly (03:30 local time). When `retention_days > 0`, it deletes ClickHouse rows older than the window from the v4 event tables and scores, using lightweight deletes. Exact tables and timestamp columns are determined in Phase 0 from the pinned v4 schema. The community project `langfuse-open-retention` is evaluated as a reference or alternative.
- **SeaweedFS:** bucket-level TTL/expiry for the `events/` prefix matching `retention_days`.
- **PostgreSQL** (users, projects, prompts, datasets) is not trimmed.
- **ClickHouse system logs:** TTLs per §4.3.
- Each run logs rows deleted, bytes reclaimed and duration.

---

## 8. Backup & Restore
- `backup: cold`: HA stops the add-on, archives `/data` (all databases, blobs, secrets) and restarts it. The backup is consistent by construction.
- During a backup, ingestion is unavailable. The Hermes plugin fails open, so traces from that window are dropped. This is accepted.
- The 30-day retention bounds backup size. Measure backup size and duration in Phase 4.
- Restore = standard HA add-on restore; no manual steps. Test the restore in Phase 4.
- HA's "back up before update" covers Langfuse upgrades, since Langfuse migrations are forward-only.

---

## 9. Resource Budget (target host: Intel 225H, 64 GB RAM)

| Component | Budget |
|---|---|
| ClickHouse | 4 GB (option, default) |
| Langfuse Web | ~2 GB (Node heap option) |
| Langfuse Worker | ~2 GB (Node heap option) |
| PostgreSQL | ~0.5 GB |
| Redis | 256 MB `maxmemory` |
| SeaweedFS | ~0.5 GB |
| **Total peak** | **~9–10 GB** |

Disk: measure at Hermes' real volume during the Phase 4 soak and record the 30-day projection in the README.

---

## 10. Hermes Agent Integration

### 10.1 Credentials
The `Hermes` project and its API keys are created automatically on first start and stored in `/data/secrets.env`. To hand them to Hermes, either enable `export_hermes_env` (writes `/share/langfuse/hermes.env`, which the Hermes add-on can read if it maps `/share`), or create a fresh key pair in the Langfuse UI under Settings → API Keys. Keys are never printed to the add-on log.

### 10.2 Networking

| Hermes add-on network mode | `HERMES_LANGFUSE_BASE_URL` | Host port needed? |
|---|---|---|
| Normal add-on network (default) | `http://<langfuse-hostname>:3000` | No |
| `host_network: true` | `http://127.0.0.1:<mapped port>` | Yes |

- `<langfuse-hostname>` is `local-langfuse` while installed from `/addons` (Phase 4) and `<repo-hash>-langfuse` once installed from the GitHub repository (Phase 5). Read it from `ha addons info <slug>` (field `hostname`). The Hermes config must be updated after switching install source.
- Document both modes in the README; check the Hermes add-on's `host_network` setting during Phase 4.

### 10.3 Configuration (`~/.hermes/.env`)
```bash
HERMES_LANGFUSE_PUBLIC_KEY=pk-lf-...
HERMES_LANGFUSE_SECRET_KEY=sk-lf-...
HERMES_LANGFUSE_BASE_URL=http://<langfuse-hostname>:3000
# Optional: HERMES_LANGFUSE_MAX_CHARS, HERMES_LANGFUSE_ENV, HERMES_LANGFUSE_RELEASE, HERMES_LANGFUSE_DEBUG
# HERMES_LANGFUSE_CAPTURE=sanitized  -> not found in current plugin docs; verify before relying on it
```

### 10.4 SDK
- Langfuse v4 defaults to the new ingestion path; Python SDK **≥ 4.7.0** writes in real time. Older SDKs are rejected (v2) or delayed.
- Bake `langfuse>=4.7.0` into the Hermes add-on image. A runtime `pip install` inside an add-on container is lost when the container is rebuilt.
- Enable with `hermes plugins enable observability/langfuse`, restart Hermes, verify a "Hermes turn" trace appears.

---

## 11. Build & Release Pipeline
- **GitHub Actions job 1:** build Langfuse web from the pinned tag with the placeholder base path → `ghcr.io/matthijsberg/langfuse-web-ingress:<version>`.
- **GitHub Actions job 2:** build the add-on image from the HA base image. Multi-stage `COPY --from` the custom web image, the official worker image (same tag), pinned ClickHouse and SeaweedFS binaries; distro packages for PostgreSQL, Redis and nginx → `ghcr.io/matthijsberg/{arch}-addon-langfuse:<version>`.
- **Native runners** per architecture (no QEMU emulation for the Next.js build).
- **libc check:** confirm Langfuse's image base and that the ClickHouse binary runs on it; choose the HA base variant accordingly.
- **Renovate** keeps Langfuse, ClickHouse, SeaweedFS and base image pins current. Each bump is a new add-on version with a CHANGELOG entry.
- The add-on is never built on the HAOS host.

---

## 12. Implementation Roadmap

**Phase 0 — Spikes (time-boxed, go/no-go)**
- 0a Ingress: custom build + substitution + nginx + origin-agnostic auth against §3.3.
- 0b SeaweedFS: Langfuse event upload/read with path-style addressing; S3 checksum compatibility; bucket TTL.
- 0c Retention: tables/columns in the pinned v4 schema; delete performance; evaluate `langfuse-open-retention`.
- 0d Runtime: libc/base choice, ClickHouse on that base, watchdog with an unmapped port.

**Phase 1 — Repo scaffolding**
`langfuse/` in `matthijsberg/HomeAssistant-Addons`: `config.yaml`, `Dockerfile`, `rootfs/` (s6-rc.d, nginx templates, init scripts), `DOCS.md`, `README.md`, `CHANGELOG.md`, `translations/en.yaml`, CI workflows.

**Phase 2 — Images & CI**
Both GitHub Actions jobs, GHCR publishing, Renovate config.

**Phase 3 — S6 services & init scripts**
Dependency graph from §2.3, secrets generator, config renderer, readiness gates, retention service, headless init, optional Hermes env export.

**Phase 4 — Local testing on HAOS**
Install from `/addons` (`local-langfuse`) and run the acceptance tests in §13, including a 24-hour Hermes soak, reboot, backup/restore and update tests.

**Phase 5 — Release**
Push to the repository, install from GitHub, switch Hermes to the repo hostname, tag the release.

---

## 13. Acceptance Criteria
1. Fresh install with only `admin_email`/`admin_password` set reaches a working UI via the sidebar in under 3 minutes (after image pull).
2. All §3.3 ingress criteria pass.
3. Hermes traces appear in the `Hermes` project within seconds, with host port **empty**.
4. With port `3000` set, the LAN UI and SDK ingestion work; with it empty, `<ha-ip>:3000` is closed.
5. Backing services are unreachable from another add-on (`nc` to 5432/6379/8123/8333 fails).
6. Add-on stop completes within 120 s with clean Postgres and ClickHouse shutdown logs.
7. Survives host reboot and add-on update with all data intact.
8. HA backup → uninstall → restore returns the full instance, including working API keys.
9. Retention job removes data older than `retention_days` (tested with a short window) from ClickHouse and SeaweedFS.
10. Peak RAM stays within the §9 budget during the soak test.

---

## 14. Risks & Open Items

| Risk / item | Impact | Mitigation |
|---|---|---|
| Runtime base-path substitution breaks on a Langfuse update | Ingress UI fails | Phase 0 test suite rerun in CI on every version bump |
| Origin-agnostic auth rewrite proves brittle | Login fails on some origins | Fallback: `external_url` option for `NEXTAUTH_URL` |
| SeaweedFS S3 incompatibility (checksums, path style) | Ingestion fails | Phase 0b; RustFS as second choice |
| Custom retention conflicts with future Langfuse schema changes | Data not deleted or errors | Schema-driven table list, run logs, CI test |
| Langfuse `4.x` minor upgrades with long background migrations | Slow first start after update | `timeout` and health gates tolerant; note in CHANGELOG |
| Large image size (several GB) | Slow first install | Accepted; prebuilt images only |
| `HERMES_LANGFUSE_CAPTURE` not in current plugin docs | Setting silently ignored | Verify against Hermes plugin source |
| Watchdog behaviour with unmapped port | Watchdog ineffective | Phase 0d; fall back to s6 supervision only |
