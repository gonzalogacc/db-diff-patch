#!/usr/bin/env bash
#
# Opens an interactive psql session against a Postgres server already
# reachable at 127.0.0.1:PORT - e.g. a cloud-sql-proxy tunnel you started
# yourself. Doesn't manage any tunnel itself.
#
# Usage: ./scripts/connect_psql.sh [DBNAME=postgres] [DBUSER=postgres] [PASSWORD] [PORT=5433]
# If PASSWORD is omitted, psql prompts/.pgpass - needs a real tty. Pass it
# explicitly on "fe_sendauth: no password supplied".

set -euo pipefail

DBNAME=${1:-"postgres"}
DBUSER=${2:-"postgres"}
PASSWORD=${3:-}
PORT=${4:-5433}

# set only if given - an empty PGPASSWORD would override .pgpass/trust-auth
if [[ -n "${PASSWORD}" ]]; then
    export PGPASSWORD="${PASSWORD}"
fi

psql "host=127.0.0.1 port=${PORT} dbname=${DBNAME} user=${DBUSER}"
