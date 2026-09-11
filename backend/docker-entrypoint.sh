#!/usr/bin/env bash
# Brings the database to a usable state before the API starts serving.
#
# Everything here is idempotent, because compose restarts a container for
# reasons that have nothing to do with the database being fresh.
set -euo pipefail

: "${POSTGRES_HOST:=postgres18}"
: "${POSTGRES_PORT:=5432}"
: "${POSTGRES_USER:=postgres}"
: "${POSTGRES_PASSWORD:=123}"
: "${APP_DATABASE_NAME:=ticketing}"
: "${RUN_MIGRATIONS:=true}"
: "${RUN_SEED:=true}"
: "${RUN_INGEST:=false}"

export PGPASSWORD="$POSTGRES_PASSWORD"

echo "==> waiting for postgres at ${POSTGRES_HOST}:${POSTGRES_PORT}"
for _ in $(seq 1 60); do
  if pg_isready -h "$POSTGRES_HOST" -p "$POSTGRES_PORT" -U "$POSTGRES_USER" >/dev/null 2>&1; then
    break
  fi
  sleep 1
done
if ! pg_isready -h "$POSTGRES_HOST" -p "$POSTGRES_PORT" -U "$POSTGRES_USER" >/dev/null 2>&1; then
  echo "!! postgres never became ready" >&2
  exit 1
fi

# The compose Postgres comes up with POSTGRES_DB=mydb, not the database the
# app connects to, so creating it is part of starting up rather than a
# manual step someone has to remember (`make db-create` on a laptop).
if psql -h "$POSTGRES_HOST" -p "$POSTGRES_PORT" -U "$POSTGRES_USER" -d postgres -tAc \
     "SELECT 1 FROM pg_database WHERE datname = '${APP_DATABASE_NAME}'" | grep -q 1; then
  echo "==> database '${APP_DATABASE_NAME}' already exists"
else
  echo "==> creating database '${APP_DATABASE_NAME}'"
  psql -h "$POSTGRES_HOST" -p "$POSTGRES_PORT" -U "$POSTGRES_USER" -d postgres \
    -c "CREATE DATABASE ${APP_DATABASE_NAME};"
fi

if [ "$RUN_MIGRATIONS" = "true" ]; then
  echo "==> alembic upgrade head"
  alembic upgrade head
fi

if [ "$RUN_SEED" = "true" ]; then
  # Idempotent by design (backend/tests/test_seed.py pins that), so this is
  # safe on every boot, not only the first.
  echo "==> seeding"
  python -m app.db.seed
fi

if [ "$RUN_INGEST" = "true" ]; then
  # Off by default: it downloads an embedding model on first run and takes
  # minutes. Without it the agent still answers, it just has no corporate
  # documents to retrieve from -- set RUN_INGEST=true once to load them.
  echo "==> ingesting corporate_rag_dataset into Chroma"
  python /srv/scripts/ingest_dataset.py || echo "!! ingest failed; continuing without it" >&2
fi

echo "==> starting: $*"
exec "$@"
