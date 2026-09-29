#!/usr/bin/env bash
set -e

SECRETS_FILE="/data/secrets.env"
# shellcheck disable=SC1090
source "${SECRETS_FILE}"

PG_DATA="/data/postgres"

if [ ! -f "${PG_DATA}/PG_VERSION" ]; then
  echo "[init-postgres] Initializing fresh PostgreSQL cluster in ${PG_DATA}..."
  su-exec postgres initdb -D "${PG_DATA}" --encoding=UTF8 --locale=C
  
  echo "listen_addresses = '127.0.0.1'" >> "${PG_DATA}/postgresql.conf"
  echo "shared_buffers = 256MB" >> "${PG_DATA}/postgresql.conf"
  echo "work_mem = 16MB" >> "${PG_DATA}/postgresql.conf"
  
  echo "[init-postgres] PostgreSQL initialized."
fi

# Wait for PostgreSQL to be ready
echo "[init-postgres] Waiting for PostgreSQL readiness..."
for i in $(seq 1 30); do
  if su-exec postgres pg_isready -h 127.0.0.1 -p 5432 >/dev/null 2>&1; then
    break
  fi
  sleep 1
done

# Ensure langfuse user and database exist with correct password
su-exec postgres psql -h 127.0.0.1 -p 5432 -c "
DO \$\$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'langfuse') THEN
    CREATE ROLE langfuse WITH LOGIN SUPERUSER PASSWORD '${POSTGRES_PASSWORD}';
  ELSE
    ALTER ROLE langfuse WITH PASSWORD '${POSTGRES_PASSWORD}';
  END IF;
END
\$\$;
"

su-exec postgres psql -h 127.0.0.1 -p 5432 -c "
SELECT 'CREATE DATABASE langfuse OWNER langfuse'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'langfuse')\gexec
"

echo "[init-postgres] PostgreSQL database and user 'langfuse' confirmed ready."
