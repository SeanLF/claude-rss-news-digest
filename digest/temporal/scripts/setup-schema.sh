#!/bin/sh
# Schema setup for a Postgres-backed Temporal server, replacing what the deprecated
# temporalio/auto-setup image (stalled at 1.29.7) did implicitly. Adapted from
# temporalio/samples-server compose/scripts/setup-postgres.sh: create and setup-schema are
# tolerated on a volume that already holds a schema; update-schema is the idempotent step.
set -eu
: "${POSTGRES_SEEDS:?}"
: "${POSTGRES_USER:?}"
PORT="${DB_PORT:-5432}"
SCHEMA_ROOT=/etc/temporal/schema/postgresql/v12
echo "Waiting for Postgres at ${POSTGRES_SEEDS}:${PORT}..."
nc -z -w 30 "${POSTGRES_SEEDS}" "${PORT}"
for db in temporal temporal_visibility; do
  case "${db}" in
    temporal) versioned="${SCHEMA_ROOT}/temporal/versioned" ;;
    *)        versioned="${SCHEMA_ROOT}/visibility/versioned" ;;
  esac
  sql() { temporal-sql-tool --plugin postgres12 --ep "${POSTGRES_SEEDS}" -u "${POSTGRES_USER}" -p "${PORT}" --db "${db}" "$@"; }
  sql create || echo "  ${db}: already exists"
  sql setup-schema -v 0.0 || echo "  ${db}: already initialised"
  sql update-schema -d "${versioned}"
done
echo "Temporal schema setup complete"
