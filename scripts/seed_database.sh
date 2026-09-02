#!/usr/bin/env bash
#
# Tunnels through the Cloud SQL Auth Proxy and runs seed.sql (or another SQL
# file) against a Cloud SQL Postgres instance (e.g. one created by
# create_test_instance.sh), non-interactively. Stops the proxy when done.
#
# Usage: ./scripts/seed_database.sh [INSTANCE_NAME] [DBNAME] [DBUSER] [PASSWORD] [PORT] [SQL_FILE]
# If PASSWORD is omitted, psql prompts/.pgpass - needs a real tty. Pass it
# explicitly on "fe_sendauth: no password supplied".

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/_lib.sh"

INSTANCE_NAME=${1:-"test-pg-instance"}
DBNAME=${2:-"postgres"}
DBUSER=${3:-"postgres"}
PASSWORD=${4:-}
PORT=${5:-$(_free_port)}
SQL_FILE=${6:-"${SCRIPT_DIR}/../seed.sql"}

open_tunnel "${INSTANCE_NAME}" "${PORT}"

# set only if given - an empty PGPASSWORD would override .pgpass/trust-auth
if [[ -n "${PASSWORD}" ]]; then
    export PGPASSWORD="${PASSWORD}"
fi

echo "Seeding '${DBNAME}' on '${INSTANCE_NAME}' from ${SQL_FILE}..."
psql "host=127.0.0.1 port=${PORT} dbname=${DBNAME} user=${DBUSER}" \
    -v ON_ERROR_STOP=1 \
    -f "${SQL_FILE}"

echo "Done."
