#!/usr/bin/env bash
set -e

echo "[init-dirs] Ensuring persistent storage directories exist in /data..."

mkdir -p /run/postgresql
mkdir -p /data/postgres
mkdir -p /data/redis
mkdir -p /data/clickhouse/tmp
mkdir -p /data/clickhouse/user_files
mkdir -p /data/clickhouse/format_schemas
mkdir -p /data/seaweedfs/volumes
mkdir -p /data/seaweedfs/filer
mkdir -p /data/secrets

# Set directory permissions for non-root / service accounts
chown -R postgres:postgres /run/postgresql /data/postgres
chmod 700 /data/postgres /data/secrets
chmod -R 777 /data/redis /data/clickhouse /data/seaweedfs

echo "[init-dirs] Directories ready."
