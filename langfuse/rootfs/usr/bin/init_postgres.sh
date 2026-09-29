#!/usr/bin/env bash
set -e

SECRETS_FILE="/data/secrets.env"
# shellcheck disable=SC1090
source "${SECRETS_FILE}"

PG_DATA="/data/postgres"

if [ -f "${PG_DATA}/PG_VERSION" ]; then
  CURRENT_PG_VER=$(cat "${PG_DATA}/PG_VERSION" 2>/dev/null || echo "")
  if [ "${CURRENT_PG_VER}" != "16" ]; then
    echo "[init-postgres] Incompatible PostgreSQL data directory version ${CURRENT_PG_VER} (expected 16). Re-initializing..."
    rm -rf "${PG_DATA:?}"/*
  fi
fi

if [ ! -f "${PG_DATA}/PG_VERSION" ]; then
  echo "[init-postgres] Initializing fresh PostgreSQL cluster in ${PG_DATA}..."
  chown -R postgres:postgres "${PG_DATA}"
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

if ! su-exec postgres psql -h 127.0.0.1 -p 5432 -tAc "SELECT 1 FROM pg_database WHERE datname = 'langfuse'" | grep -q 1; then
  echo "[init-postgres] Creating database 'langfuse'..."
  su-exec postgres createdb -h 127.0.0.1 -p 5432 -O langfuse langfuse
fi

echo "[init-postgres] PostgreSQL database and user 'langfuse' confirmed ready."
