#!/usr/bin/env bash
#
# Creates DBNAME (if it doesn't already exist) and runs seed.sql (or
# another SQL file) against it - a Postgres server already reachable at
# 127.0.0.1:PORT, e.g. a cloud-sql-proxy tunnel you started yourself.
# Doesn't manage any tunnel itself.
#
# Usage: ./scripts/seed_database.sh [DBNAME=production_database] [DBUSER=postgres] [PASSWORD] [PORT=5433] [SQL_FILE=seed.sql]
# If PASSWORD is omitted, psql prompts/.pgpass - needs a real tty. Pass it
# explicitly on "fe_sendauth: no password supplied".

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

DBNAME=${1:-"production_database"}
DBUSER=${2:-"postgres"}
PASSWORD=${3:-}
PORT=${4:-5433}
SQL_FILE=${5:-"${SCRIPT_DIR}/../seed.sql"}

# set only if given - an empty PGPASSWORD would override .pgpass/trust-auth
if [[ -n "${PASSWORD}" ]]; then
    export PGPASSWORD="${PASSWORD}"
fi

EXISTS=$(psql "host=127.0.0.1 port=${PORT} dbname=postgres user=${DBUSER}" \
    -tAc "SELECT 1 FROM pg_database WHERE datname = '${DBNAME}'")
if [[ "${EXISTS}" != "1" ]]; then
    echo "Creating database '${DBNAME}'..."
    psql "host=127.0.0.1 port=${PORT} dbname=postgres user=${DBUSER}" \
        -v ON_ERROR_STOP=1 -c "CREATE DATABASE \"${DBNAME}\""
fi

echo "Seeding '${DBNAME}' on 127.0.0.1:${PORT} from ${SQL_FILE}..."
psql "host=127.0.0.1 port=${PORT} dbname=${DBNAME} user=${DBUSER}" \
    -v ON_ERROR_STOP=1 \
    -f "${SQL_FILE}"

echo "Done."
