#!/usr/bin/env bash
set -e

echo "[init-dirs] Ensuring persistent storage directories exist in /data..."

mkdir -p /run/postgresql
mkdir -p /data/postgres
mkdir -p /data/redis
mkdir -p /data/clickhouse/data
mkdir -p /data/clickhouse/metadata
mkdir -p /data/seaweedfs/volumes
mkdir -p /data/seaweedfs/filer
mkdir -p /data/secrets

# Set directory permissions for non-root / service accounts
chown -R postgres:postgres /run/postgresql /data/postgres
chmod 700 /data/postgres /data/secrets
chmod 755 /data/redis /data/clickhouse /data/seaweedfs

echo "[init-dirs] Directories ready."
