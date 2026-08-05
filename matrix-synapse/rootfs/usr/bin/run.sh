#!/usr/bin/env bash
set -e

echo "[Matrix Add-on] Starting Home Assistant Matrix Synapse Add-on..."

# 1. Read HA Add-on configuration options from /data/options.json
OPTIONS_FILE="/data/options.json"
SERVER_NAME="matrix.local"
ENABLE_SLIDING_SYNC="true"
ENABLE_SSL="true"
SSL_CERT="fullchain.pem"
SSL_KEY="privkey.pem"

if [ -f "$OPTIONS_FILE" ]; then
    SERVER_NAME=$(jq -r '.server_name // "matrix.local"' "$OPTIONS_FILE")
    ENABLE_SLIDING_SYNC=$(jq -r '.enable_sliding_sync // true' "$OPTIONS_FILE")
    ENABLE_SSL=$(jq -r '.enable_ssl // true' "$OPTIONS_FILE")
    SSL_CERT=$(jq -r '.ssl_cert // "fullchain.pem"' "$OPTIONS_FILE")
    SSL_KEY=$(jq -r '.ssl_key // "privkey.pem"' "$OPTIONS_FILE")
fi

echo "[Matrix Add-on] Server Name: ${SERVER_NAME}"
echo "[Matrix Add-on] Checking Home Assistant Directory Mappings:"
echo "  - /ssl: $( [ -d /ssl ] && echo 'Mounted ✓' || echo 'Missing' )"
echo "  - /backup: $( [ -d /backup ] && echo 'Mounted ✓' || echo 'Missing' )"
echo "  - /config: $( [ -d /config ] && echo 'Mounted ✓' || echo 'Missing' )"
echo "  - /data: $( [ -d /data ] && echo 'Mounted ✓' || echo 'Missing' )"

# 2. Check SSL certificates in /ssl mount
if [ "$ENABLE_SSL" = "true" ]; then
    if [ -f "/ssl/${SSL_CERT}" ] && [ -f "/ssl/${SSL_KEY}" ]; then
        echo "[Matrix Add-on] Found SSL Certificates at /ssl/${SSL_CERT} and /ssl/${SSL_KEY}"
    else
        echo "[Matrix Add-on] Warning: SSL enabled but certificates not found in /ssl/. Falling back to HTTP."
    fi
fi

# 3. Initialize Postgres Database if not initialized
PG_DATA_DIR="/data/postgres"
if [ ! -d "$PG_DATA_DIR" ]; then
    echo "[Matrix Add-on] Initializing PostgreSQL Database in ${PG_DATA_DIR}..."
    mkdir -p "$PG_DATA_DIR"
    chown -R postgres:postgres "$PG_DATA_DIR"
    su - postgres -c "/usr/lib/postgresql/16/bin/initdb -D ${PG_DATA_DIR} --locale=C --encoding=UTF8"
fi

# Start PostgreSQL
echo "[Matrix Add-on] Starting PostgreSQL..."
su - postgres -c "/usr/lib/postgresql/16/bin/pg_ctl -D ${PG_DATA_DIR} -l /data/postgres.log start" || true

# 4. Generate initial Synapse config if missing
SYNAPSE_CONFIG="/data/homeserver.yaml"
if [ ! -f "$SYNAPSE_CONFIG" ]; then
    echo "[Matrix Add-on] Generating Synapse Homeserver Configuration..."
    python3 -m synapse.app.homeserver \
        --config-path "$SYNAPSE_CONFIG" \
        --generate-config \
        --server-name "$SERVER_NAME" \
        --report-stats=no
fi

echo "[Matrix Add-on] Synapse & Postgres initialization complete."
