#!/usr/bin/env bash
#
# Starts a disposable local Postgres container. Then seeds it from
# seed.sql. Use this to try db-differ by hand, without a GCP project. The
# container has no named volume. "docker stop" removes the container and
# all its data.
#
# Usage: ./scripts/start_local_postgres.sh [DBNAME=postgres] [PORT=5433] [SQL_FILE=seed.sql]
#
# These are positional arguments, not flags. To set SQL_FILE, you must
# also pass DBNAME and PORT before it, even the default values.
#
# This script needs docker and psql on PATH.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

DBNAME=${1:-"postgres"}
PORT=${2:-5433}
SQL_FILE=${3:-"${SCRIPT_DIR}/../seed.sql"}

IMAGE="postgres:16"
PASSWORD="postgres"
CONTAINER_NAME="db-differ-local-$(date +%s)-$$"

command -v docker &>/dev/null || { echo "docker not found on PATH." >&2; exit 1; }
command -v psql &>/dev/null || { echo "psql not found on PATH." >&2; exit 1; }

echo "Starting disposable Postgres container '${CONTAINER_NAME}' on 127.0.0.1:${PORT}..."
docker run --rm -d --name "${CONTAINER_NAME}" \
    -e POSTGRES_PASSWORD="${PASSWORD}" \
    -p "${PORT}:5432" \
    "${IMAGE}" >/dev/null

# Cleanup on failure so a half-started container doesn't linger.
trap 'docker rm -f "${CONTAINER_NAME}" &>/dev/null || true' ERR

echo "Waiting for Postgres to accept connections..."
ready=false
for _ in $(seq 1 30); do
    if docker exec "${CONTAINER_NAME}" pg_isready -U postgres &>/dev/null; then
        ready=true
        break
    fi
    sleep 1
done
if [[ "${ready}" != true ]]; then
    echo "Postgres did not become ready in time." >&2
    exit 1
fi

if [[ "${DBNAME}" != "postgres" ]]; then
    PGPASSWORD="${PASSWORD}" psql "host=127.0.0.1 port=${PORT} dbname=postgres user=postgres" \
        -v ON_ERROR_STOP=1 -c "CREATE DATABASE \"${DBNAME}\"" >/dev/null
fi

echo "Seeding '${DBNAME}' from ${SQL_FILE}..."
PGPASSWORD="${PASSWORD}" psql "host=127.0.0.1 port=${PORT} dbname=${DBNAME} user=postgres" \
    -v ON_ERROR_STOP=1 -f "${SQL_FILE}" >/dev/null

trap - ERR

echo
echo "Ready. Connection details:"
echo "  host=127.0.0.1 port=${PORT} dbname=${DBNAME} user=postgres password=${PASSWORD}"
echo
echo "Try it with db-differ, e.g.:"
echo "  uv run db-differ branch create test-branch --host 127.0.0.1 --port ${PORT} \\"
echo "      --source-database ${DBNAME} --username postgres --password ${PASSWORD}"
echo
echo "Stop and remove everything with: docker stop ${CONTAINER_NAME}"
