#!/usr/bin/env bash
set -e

SECRETS_FILE="/data/secrets.env"
# shellcheck disable=SC1090
source "${SECRETS_FILE}"

echo "[init-clickhouse] Waiting for ClickHouse server readiness..."
for i in $(seq 1 45); do
  if curl -sf http://127.0.0.1:8123/ping >/dev/null 2>&1; then
    break
  fi
  sleep 1
done

echo "[init-clickhouse] Configuring ClickHouse user 'langfuse' and grants..."

# Use default admin user (or clickhouse client) via HTTP to configure langfuse user
curl -sS "http://127.0.0.1:8123/?multiquery=1" -d "
CREATE USER IF NOT EXISTS langfuse IDENTIFIED WITH plaintext_password BY '${CLICKHOUSE_PASSWORD}';
ALTER USER langfuse IDENTIFIED WITH plaintext_password BY '${CLICKHOUSE_PASSWORD}';
GRANT ALL ON default.* TO langfuse;
GRANT SELECT(database, partition, table, active) ON system.parts TO langfuse;
GRANT SELECT(database, table, is_done) ON system.mutations TO langfuse;
GRANT SELECT ON system.query_log TO langfuse;
GRANT SELECT ON system.processes TO langfuse;
" >/dev/null

echo "[init-clickhouse] ClickHouse permissions configured successfully."
