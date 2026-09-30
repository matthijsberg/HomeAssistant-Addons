#!/usr/bin/env bash
set -e

SECRETS_FILE="/data/secrets.env"

if [ ! -f "${SECRETS_FILE}" ]; then
  echo "[init-secrets] Generating initial cryptographic secrets and credentials..."
  
  NEXTAUTH_SECRET="$(openssl rand -base64 32)"
  SALT="$(openssl rand -base64 32)"
  # Langfuse requires exactly 64 hex characters (32 bytes) for ENCRYPTION_KEY
  ENCRYPTION_KEY="$(openssl rand -hex 32)"
  
  POSTGRES_PASSWORD="$(openssl rand -hex 16)"
  CLICKHOUSE_PASSWORD="$(openssl rand -hex 16)"
  REDIS_PASSWORD="$(openssl rand -hex 16)"
  
  S3_ACCESS_KEY_ID="lf_$(openssl rand -hex 8)"
  S3_SECRET_ACCESS_KEY="$(openssl rand -hex 24)"
  
  HERMES_PROJECT_ID="hermes"
  HERMES_PUBLIC_KEY="pk-lf-$(openssl rand -hex 16)"
  HERMES_SECRET_KEY="sk-lf-$(openssl rand -hex 24)"

  cat <<EOF > "${SECRETS_FILE}"
NEXTAUTH_SECRET="${NEXTAUTH_SECRET}"
SALT="${SALT}"
ENCRYPTION_KEY="${ENCRYPTION_KEY}"
POSTGRES_PASSWORD="${POSTGRES_PASSWORD}"
CLICKHOUSE_PASSWORD="${CLICKHOUSE_PASSWORD}"
REDIS_PASSWORD="${REDIS_PASSWORD}"
S3_ACCESS_KEY_ID="${S3_ACCESS_KEY_ID}"
S3_SECRET_ACCESS_KEY="${S3_SECRET_ACCESS_KEY}"
HERMES_PROJECT_ID="${HERMES_PROJECT_ID}"
HERMES_PUBLIC_KEY="${HERMES_PUBLIC_KEY}"
HERMES_SECRET_KEY="${HERMES_SECRET_KEY}"
EOF

  chmod 600 "${SECRETS_FILE}"
  echo "[init-secrets] Secrets generated and saved to ${SECRETS_FILE} (mode 600)."
else
  echo "[init-secrets] Existing secrets loaded from ${SECRETS_FILE}."
fi

# Always ensure ClickHouse user configuration matches current secret
# shellcheck disable=SC1090
source "${SECRETS_FILE}"
mkdir -p /etc/clickhouse-server/users.d
cat <<EOF > /etc/clickhouse-server/users.d/langfuse.xml
<clickhouse>
    <users>
        <langfuse>
            <password>${CLICKHOUSE_PASSWORD}</password>
            <networks>
                <ip>::/0</ip>
            </networks>
            <profile>default</profile>
            <quota>default</quota>
            <access_management>1</access_management>
            <named_collection_control>1</named_collection_control>
            <show_named_collections>1</show_named_collections>
            <show_named_collections_secrets>1</show_named_collections_secrets>
        </langfuse>
    </users>
</clickhouse>
EOF
chmod 644 /etc/clickhouse-server/users.d/langfuse.xml
echo "[init-secrets] Configured ClickHouse user 'langfuse' in users.d/langfuse.xml."

# Ensure ClickHouse persistent data path in /data/clickhouse
mkdir -p /etc/clickhouse-server/config.d
cat <<EOF > /etc/clickhouse-server/config.d/storage.xml
<clickhouse>
    <path>/data/clickhouse/</path>
    <tmp_path>/data/clickhouse/tmp/</tmp_path>
    <user_files_path>/data/clickhouse/user_files/</user_files_path>
    <format_schema_path>/data/clickhouse/format_schemas/</format_schema_path>
</clickhouse>
EOF
chmod 644 /etc/clickhouse-server/config.d/storage.xml
echo "[init-secrets] Configured ClickHouse persistent data path in /data/clickhouse."

# Configure SeaweedFS S3 credentials matching secrets.env
mkdir -p /etc/seaweedfs
cat <<EOF > /etc/seaweedfs/s3.json
{
  "identities": [
    {
      "name": "langfuse",
      "credentials": [
        {
          "accessKey": "${S3_ACCESS_KEY_ID}",
          "secretKey": "${S3_SECRET_ACCESS_KEY}"
        }
      ],
      "actions": [
        "Read",
        "Write",
        "List",
        "Tagging",
        "Admin"
      ]
    }
  ]
}
EOF
chmod 600 /etc/seaweedfs/s3.json
echo "[init-secrets] Configured SeaweedFS S3 credentials in /etc/seaweedfs/s3.json."

# Check if Hermes env export is requested via bashio (or option)
if [ -f "/data/options.json" ]; then
  EXPORT_HERMES="$(grep -o '"export_hermes_env": *true' /data/options.json || true)"
  if [ -n "${EXPORT_HERMES}" ]; then
    mkdir -p /share/langfuse
    # shellcheck disable=SC1090
    source "${SECRETS_FILE}"
    cat <<EOF > /share/langfuse/hermes.env
HERMES_LANGFUSE_PUBLIC_KEY=${HERMES_PUBLIC_KEY}
HERMES_LANGFUSE_SECRET_KEY=${HERMES_SECRET_KEY}
HERMES_LANGFUSE_BASE_URL=http://local-langfuse:3000
HERMES_LANGFUSE_CAPTURE=sanitized
EOF
    chmod 600 /share/langfuse/hermes.env
    echo "[init-secrets] Exported Hermes environment configuration to /share/langfuse/hermes.env."
  fi
fi
