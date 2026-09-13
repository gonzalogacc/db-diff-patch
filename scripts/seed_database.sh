#!/usr/bin/env bash
#
# Creates DBNAME if it does not exist yet. Then runs a SQL file against it.
# The target server must already be reachable at 127.0.0.1:PORT, for
# example through a cloud-sql-proxy tunnel you started yourself. This
# script does not start or manage a tunnel.
#
# Usage: ./scripts/seed_database.sh [DBNAME=production_database] [DBUSER=postgres] [PASSWORD] [PORT=5433] [SQL_FILE=seed.sql]
#
# These are positional arguments, not flags. To set PASSWORD, you must
# also pass DBNAME and DBUSER before it, even the default values. Example:
# "seed_database.sh production_database postgres mypassword" is correct.
# "seed_database.sh production_database mypassword" puts "mypassword" into
# DBUSER, not PASSWORD.
#
# If you omit PASSWORD, psql prompts for it, or uses .pgpass. This needs a
# real terminal. Pass PASSWORD directly if you see this error:
# "fe_sendauth: no password supplied".

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
