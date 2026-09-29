#!/usr/bin/env bash
set -e

SECRETS_FILE="/data/secrets.env"
# shellcheck disable=SC1090
source "${SECRETS_FILE}"

RETENTION_DAYS=30
if [ -f "/data/options.json" ]; then
  OPT_DAYS=$(grep -o '"retention_days": *[0-9]*' /data/options.json | awk '{print $2}' || true)
  [ -n "${OPT_DAYS}" ] && RETENTION_DAYS="${OPT_DAYS}"
fi

if [ "${RETENTION_DAYS}" -le 0 ]; then
  echo "[retention] Retention disabled (retention_days=0). Keeping all historical traces."
  exit 0
fi

echo "[retention] Executing nightly retention cycle (${RETENTION_DAYS} days window)..."
START_TS=$(date +%s)

# Execute ClickHouse lightweight deletes
CH_URL="http://127.0.0.1:8123/?user=langfuse&password=${CLICKHOUSE_PASSWORD}"

for TABLE in events_full events_core; do
  echo "[retention] Pruning ${TABLE} older than ${RETENTION_DAYS} days..."
  curl -sS "${CH_URL}" -d "ALTER TABLE default.${TABLE} DELETE WHERE start_time < now() - INTERVAL ${RETENTION_DAYS} DAY" || true
done

echo "[retention] Pruning observations_batch_staging older than ${RETENTION_DAYS} days..."
curl -sS "${CH_URL}" -d "ALTER TABLE default.observations_batch_staging DELETE WHERE s3_first_seen_timestamp < now() - INTERVAL ${RETENTION_DAYS} DAY" || true

# Prune ClickHouse system query logs older than 3 days
curl -sS "http://127.0.0.1:8123/" -d "ALTER TABLE system.query_log DELETE WHERE event_date < today() - 3" || true

END_TS=$(date +%s)
DURATION=$((END_TS - START_TS))
echo "[retention] Retention cycle completed in ${DURATION}s."
