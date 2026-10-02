#!/usr/bin/env bash
set -e

SECRETS_FILE="/data/secrets.env"
# shellcheck disable=SC1090
source "${SECRETS_FILE}"

echo "[init-seaweedfs] Waiting for SeaweedFS S3 gateway readiness..."
for i in $(seq 1 30); do
  if curl -sf http://127.0.0.1:8333/ >/dev/null 2>&1 || [ $? -eq 22 ] || [ $? -eq 52 ]; then
    break
  fi
  sleep 1
done

echo "[init-seaweedfs] Ensuring bucket 'langfuse' exists..."
# Create bucket directory via filer or S3 call
mkdir -p /data/seaweedfs/filer/buckets/langfuse

echo "[init-seaweedfs] S3 storage ready."
