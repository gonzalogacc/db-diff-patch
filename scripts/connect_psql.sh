#!/usr/bin/env bash
#
# Opens an interactive psql session against a Postgres server. The server
# must already be reachable at 127.0.0.1:PORT, for example through a
# cloud-sql-proxy tunnel you started yourself. This script does not start
# or manage a tunnel.
#
# Usage: ./scripts/connect_psql.sh [DBNAME=postgres] [DBUSER=postgres] [PASSWORD] [PORT=5433]
#
# These are positional arguments, not flags. To set PASSWORD, you must
# also pass DBNAME and DBUSER before it, even the default values. Example:
# "connect_psql.sh mydb postgres mypassword" is correct.
# "connect_psql.sh mydb mypassword" puts "mypassword" into DBUSER, not
# PASSWORD.
#
# If you omit PASSWORD, psql prompts for it, or uses .pgpass. This needs a
# real terminal. Pass PASSWORD directly if you see this error:
# "fe_sendauth: no password supplied".

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
