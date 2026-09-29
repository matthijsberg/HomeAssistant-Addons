#!/usr/bin/env bash
set -e

SECRETS_FILE="/data/secrets.env"
# shellcheck disable=SC1090
source "${SECRETS_FILE}"

MARKER_FILE="/run/langfuse_basepath_token"
INGRESS_ENTRY=""
if [ -f "${MARKER_FILE}" ]; then
  INGRESS_ENTRY="$(cat "${MARKER_FILE}")"
fi

# Extract options from Home Assistant options.json
ADMIN_EMAIL="admin@homeassistant.local"
ADMIN_PASSWORD=""
CLICKHOUSE_MEM_MB=4096
NODE_MAX_OLD_SPACE_MB=2048
TELEMETRY_ENABLED="false"
EXTERNAL_URL="https://hass.b3rg.nl:8123"

if [ -f "/data/options.json" ]; then
  # Simple robust JSON extraction
  OPT_EMAIL=$(grep -o '"admin_email": *"[^"]*"' /data/options.json | cut -d'"' -f4 || true)
  OPT_PASS=$(grep -o '"admin_password": *"[^"]*"' /data/options.json | cut -d'"' -f4 || true)
  OPT_CH_MEM=$(grep -o '"clickhouse_memory_limit_mb": *[0-9]*' /data/options.json | awk '{print $2}' || true)
  OPT_NODE_MEM=$(grep -o '"node_max_old_space_mb": *[0-9]*' /data/options.json | awk '{print $2}' || true)
  OPT_TELEM=$(grep -o '"telemetry_enabled": *true' /data/options.json || true)
  OPT_EXT_URL=$(grep -o '"external_url": *"[^"]*"' /data/options.json | cut -d'"' -f4 || true)

  [ -n "${OPT_EMAIL}" ] && ADMIN_EMAIL="${OPT_EMAIL}"
  [ -n "${OPT_PASS}" ] && ADMIN_PASSWORD="${OPT_PASS}"
  [ -n "${OPT_CH_MEM}" ] && CLICKHOUSE_MEM_MB="${OPT_CH_MEM}"
  [ -n "${OPT_NODE_MEM}" ] && NODE_MAX_OLD_SPACE_MB="${OPT_NODE_MEM}"
  [ -n "${OPT_TELEM}" ] && TELEMETRY_ENABLED="true"
  [ -n "${OPT_EXT_URL}" ] && EXTERNAL_URL="${OPT_EXT_URL}"
fi

# Write environment file for Langfuse Web and Worker processes
cat <<EOF > /run/langfuse.env
DATABASE_URL=postgresql://langfuse:${POSTGRES_PASSWORD}@127.0.0.1:5432/langfuse
CLICKHOUSE_URL=http://127.0.0.1:8123
CLICKHOUSE_MIGRATION_URL=clickhouse://127.0.0.1:9000
CLICKHOUSE_USER=langfuse
CLICKHOUSE_PASSWORD=${CLICKHOUSE_PASSWORD}
CLICKHOUSE_CLUSTER_ENABLED=false
REDIS_CONNECTION_STRING=redis://:${REDIS_PASSWORD}@127.0.0.1:6379
LANGFUSE_S3_EVENT_UPLOAD_BUCKET=langfuse
LANGFUSE_S3_EVENT_UPLOAD_ENDPOINT=http://127.0.0.1:8333
LANGFUSE_S3_EVENT_UPLOAD_ACCESS_KEY_ID=${S3_ACCESS_KEY_ID}
LANGFUSE_S3_EVENT_UPLOAD_SECRET_ACCESS_KEY=${S3_SECRET_ACCESS_KEY}
LANGFUSE_S3_EVENT_UPLOAD_REGION=auto
LANGFUSE_S3_EVENT_UPLOAD_FORCE_PATH_STYLE=true
LANGFUSE_S3_EVENT_UPLOAD_PREFIX=events/
NEXTAUTH_SECRET=${NEXTAUTH_SECRET}
SALT=${SALT}
ENCRYPTION_KEY=${ENCRYPTION_KEY}
NEXTAUTH_URL=${EXTERNAL_URL}${INGRESS_ENTRY}
HOSTNAME=127.0.0.1
PORT=3100
TELEMETRY_ENABLED=${TELEMETRY_ENABLED}
AUTH_DISABLE_SIGNUP=true
NODE_OPTIONS=--max-old-space-size=${NODE_MAX_OLD_SPACE_MB}
LANGFUSE_INIT_ORG_ID=default
LANGFUSE_INIT_ORG_NAME=Default
LANGFUSE_INIT_PROJECT_ID=${HERMES_PROJECT_ID}
LANGFUSE_INIT_PROJECT_NAME=Hermes
LANGFUSE_INIT_PROJECT_PUBLIC_KEY=${HERMES_PUBLIC_KEY}
LANGFUSE_INIT_PROJECT_SECRET_KEY=${HERMES_SECRET_KEY}
LANGFUSE_INIT_USER_EMAIL=${ADMIN_EMAIL}
LANGFUSE_INIT_USER_NAME=Admin
LANGFUSE_INIT_USER_PASSWORD=${ADMIN_PASSWORD}
EOF

chmod 600 /run/langfuse.env
echo "[init-config] Runtime environment rendered to /run/langfuse.env."

# Configure port 3000 SSL automatically if certificates exist in /ssl
if [ -f "/ssl/fullchain.pem" ] && [ -f "/ssl/privkey.pem" ]; then
  echo "[init-config] Detected SSL certificates in /ssl. Enabling HTTPS for port 3000."
  cat <<EOF > /run/nginx_port_3000.conf
listen 3000 ssl;
ssl_certificate /ssl/fullchain.pem;
ssl_certificate_key /ssl/privkey.pem;
ssl_protocols TLSv1.2 TLSv1.3;
ssl_ciphers HIGH:!aNULL:!MD5;
error_page 497 https://\$host:3000\$request_uri;
EOF
else
  echo "[init-config] No SSL certificates in /ssl. Using plain HTTP for port 3000."
  cat <<EOF > /run/nginx_port_3000.conf
listen 3000;
EOF
fi
