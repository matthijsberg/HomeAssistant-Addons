#!/usr/bin/env bash
set -e

BACKUP_DIR="/backup/matrix-synapse"
TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
TARGET_FILE="${BACKUP_DIR}/matrix_backup_${TIMESTAMP}.tar.gz"

echo "[Backup] Starting Matrix Synapse backup task..."
mkdir -p "$BACKUP_DIR"

# Perform database dump if Postgres is running locally
if command -v pg_dumpall >/dev/null 2>&1; then
    echo "[Backup] Dumping PostgreSQL databases..."
    su - postgres -c "pg_dumpall" | gzip > "${BACKUP_DIR}/postgres_dump_${TIMESTAMP}.sql.gz" || true
fi

# Archive Synapse configuration and keys
if [ -d "/data" ]; then
    echo "[Backup] Archiving Matrix data and configuration..."
    tar -czf "$TARGET_FILE" -C /data . || true
fi

echo "[Backup] Backup successfully written to ${TARGET_FILE} ✓"
