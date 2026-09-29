# ARCHITECTURE — Langfuse v4 Home Assistant Add-on

## 1. System Overview

`ha-addon-langfuse` wraps the complete Langfuse v4 microservices stack into a single, supervised Home Assistant Add-on container managed by **S6-overlay v3**.

```
┌────────────────────────────────────────────────────────────────────────┐
│                   Home Assistant OS Host Environment                   │
│                                                                        │
│   ┌──────────────────────────┐          ┌──────────────────────────┐   │
│   │       Hermes Agent       │          │  Home Assistant Frontend │   │
│   │  (observability/langfuse)│          │      (Sidebar Ingress)   │   │
│   └─────────────┬────────────┘          └─────────────┬────────────┘   │
│                 │ Trace Ingestion                     │ Web Access     │
│                 ▼ (Optional Host Port 3000)           ▼ (Ingress Port) │
│  ┌──────────────────────────────────────────────────────────────────┐  │
│  │             ha-addon-langfuse Container (S6-Overlay v3)          │  │
│  │                                                                  │  │
│  │   ┌────────────────────────┐         ┌─────────────────────────┐ │  │
│  │   │ Langfuse Web Server    │◄────────┤ Nginx Ingress Proxy     │ │  │
│  │   │ (Next.js / Node.js)    │         │ (Header & Path Rewrite) │ │  │
│  │   └──────────┬─────────────┘         └─────────────────────────┘ │  │
│  │              │                                                   │  │
│  │       ┌──────┴───────┬──────────────┬──────────────┐             │  │
│  │       ▼              ▼              ▼              ▼             │  │
│  │  ┌──────────┐   ┌─────────┐   ┌────────────┐  ┌──────────────┐   │  │
│  │  │Postgres16│   │ Redis 7 │   │ ClickHouse │  │Langfuse Async│   │  │
│  │  │ (OLTP)   │   │ (Queue) │   │   (OLAP)   │  │    Worker    │   │  │
│  │  └────┬─────┘   └────┬────┘   └─────┬──────┘  └──────┬───────┘   │  │
│  │       │              │              │                │           │  │
│  └───────┼──────────────┼──────────────┼────────────────┼───────────┘  │
│          ▼              ▼              ▼                ▼              │
│    /data/postgres/  /data/redis/ /data/clickhouse/  /data/secrets.env  │
└────────────────────────────────────────────────────────────────────────┘
```

## 2. Process Supervision Dependency Graph (`s6-rc.d`)

```text
init-dirs ──► init-secrets ──► init-config ──► init-basepath
                                     │
           ┌─────────────┬───────────┼─────────────┐
           ▼             ▼           ▼             ▼
       postgres        redis     clickhouse     seaweedfs       (longruns)
           │                         │             │
    init-postgres-db       init-clickhouse-user init-s3-bucket  (oneshot readiness gates)
           └─────────────┬───────────┴─────────────┘
                         ▼
                   langfuse-web    (applies migrations on start)
                         │ wait for /api/public/health
                ┌────────┴────────┐
                ▼                 ▼
          langfuse-worker       nginx
```

## 3. Network Interfaces & Binding Rules

| Service | Port | Binding | Security Policy |
|---|---|---|---|
| **Nginx Ingress** | `8099` | `0.0.0.0` | `allow 172.30.32.2; deny all;` (HA Supervisor only) |
| **Nginx LAN / API** | `3000` | `0.0.0.0` | Optional; bound to host only when user configures `ports: 3000/tcp: <port>` |
| **Langfuse Web** | `3100` | `127.0.0.1` | Loopback only |
| **Langfuse Worker** | `3030` | `127.0.0.1` | Loopback only |
| **PostgreSQL 16** | `5432` | `127.0.0.1` | Loopback only |
| **Redis 7.2** | `6379` | `127.0.0.1` | Loopback only |
| **ClickHouse HTTP** | `8123` | `127.0.0.1` | Loopback only |
| **ClickHouse Native**| `9000` | `127.0.0.1` | Loopback only |
| **SeaweedFS S3** | `8333` | `127.0.0.1` | Loopback only |

## 4. Storage & Persistence Layout

- `/data/postgres/` — PostgreSQL 16 cluster database files.
- `/data/clickhouse/` — ClickHouse analytical trace databases and mutations.
- `/data/redis/` — Redis append-only persistence file (`appendonly.aof`).
- `/data/seaweedfs/` — SeaweedFS volume files and filer metadata.
- `/data/secrets.env` — Dynamic cryptographic secrets (`NEXTAUTH_SECRET`, `ENCRYPTION_KEY`, database passwords).
