# System Architecture Document

## Matrix Synapse Home Assistant Add-on (`v1.0.0-dev`)

```
                          ┌────────────────────────────────────────────────────────┐
                          │            Home Assistant OS / Supervised Host         │
                          │                                                        │
                          │   ┌─────────────┐   ┌─────────────┐   ┌─────────────┐  │
                          │   │   /ssl/     │   │  /backup/   │   │   /config/  │  │
                          │   └──────┬──────┘   └──────┬──────┘   └──────┬──────┘  │
                          └──────────┼─────────────────┼─────────────────┼────────┘
                                     │ (ssl:rw)        │ (backup:rw)     │ (config:rw)
                                     ▼                 ▼                 ▼
┌──────────────────────────────────────────────────────────────────────────────────┐
│ Matrix Synapse Add-on Container                                                 │
│                                                                                  │
│   ┌──────────────────────────────────────────────────────────────────────────┐   │
│   │ S6-Overlay Supervisor / Entrypoint Orchestrator                          │   │
│   └──────┬─────────────────────────────┬──────────────────────────────┬──────┘   │
│          │                             │                              │          │
│          ▼                             ▼                              ▼          │
│   ┌──────────────┐             ┌──────────────┐               ┌──────────────┐   │
│   │ PostgreSQL 16│ ──(Unix)──► │ Synapse 1.x  │ ──(HTTP:8008)►│ Sliding Sync │   │
│   │  Database    │             │  Homeserver  │               │    Proxy     │   │
│   └──────┬───────┘             └──────┬───────┘               └──────┬───────┘   │
│          │                            │                              │           │
│          ▼                            ▼                              ▼           │
│   /data/postgres/              /data/media/                   /data/sync.db      │
└──────────────────────────────────────────────────────────────────────────────────┘
                                        │
                         ┌──────────────┴──────────────┐
                         ▼                             ▼
                 Port 8008 / 8448                 Port 8009
               (Clients & Federation)           (Element X)
```

---

## Component Specifications

### 1. PostgreSQL Database Service
- Embedded PostgreSQL database daemon.
- Configured specifically for Synapse (UTF-8 encoding, `C` collation).
- Automatic daily/backup snapshot hooks dumping to `/backup/matrix-synapse/postgres_backup.sql`.

### 2. Matrix Synapse Homeserver
- Runs as dedicated `matrix` user.
- Configuration generated dynamically at `/data/homeserver.yaml`.
- Reads SSL certificates directly from `/ssl/fullchain.pem` and `/ssl/privkey.pem` when enabled.
- Stores user uploads and media in `/data/media`.

### 3. Sliding Sync Proxy (`sliding-sync`)
- Connects locally to Synapse (`http://localhost:8008`).
- Uses PostgreSQL database for sync state management.
- Exposes port `8009` for Element X mobile and desktop clients.

### 4. Backup & Recovery Orchestration
- On HA Supervisor backup invocation or manual trigger:
  1. Executes `pg_dump` of matrix database to `/backup/matrix-synapse/latest_db.sql.gz`.
  2. Copies `/data/homeserver.yaml`, signing keys, and media metadata to `/backup/matrix-synapse/config_backup.tar.gz`.
