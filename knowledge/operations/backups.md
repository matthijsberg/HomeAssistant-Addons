---
id: operations/backups
title: "Backup Strategy"
type: "Operational Pattern"
description: "What Home Assistant backups include, the periodic per-profile archives, and the hermes backup wrapper."
status: active
trust: unverified
tags: [backup, restore, retention]
generated:
  by: "agent:claude-opus-5.5"
  at: "2026-10-01T00:00:00Z"
sources:
  - id: manifest
    resource: "config.yaml"
    title: "backup_exclude"
  - id: backup-setup
    resource: "backup-setup.sh"
    title: "configure_backup_storage, periodic backup daemon"
---

# Backup Strategy

## Layers
1. **Home Assistant backup** of `addon_config`, excluding rebuildable data
   (`backup_exclude`: shared venv, project/dashboard `node_modules`, profile LSP
   runtimes, `.cache`, `.npm`). First start after restore rebuilds them (network needed).
2. **Hermes pre-update backups**: each `<profile>/backups` is a symlink to
   `/backup/hermes/<profile>`, so Hermes' own backups land on HA backup storage.
3. **Periodic archives** (`enable_periodic_backups`): every
   `periodic_backup_interval_hours` (default 24) a `tar.gz` per profile is written
   to `/backup/hermes/<profile>/`, keeping `periodic_backup_keep_count` (default 7).
   Excludes `backups`, `logs`, `venv`, `node_modules`, `.cache`, `lsp`, sockets/FIFOs.
4. **Manual**: `hermes backup` defaults `--output` to `/backup/hermes`.

## Database consistency (since 2.5.0)
Every `*.db`, `*.sqlite`, `*.sqlite3` in the profile is snapshotted with SQLite's
online backup API (read-only source, 30 s busy timeout) and the snapshot replaces the
live file in the archive; `-wal`, `-shm` and `-journal` companions are excluded.
Files that are not SQLite are archived raw. Build steps: uncompressed tar → append
snapshots → gzip, all inside a `.work.*` directory that is always removed.

## Caveats
- Archives are written atomically (`.tmp` then `mv`), and pruning only touches
  `hermes-backup-<profile>-*.tar.gz`.
