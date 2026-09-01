#!/usr/bin/env bash
#
# Tunnels through the Cloud SQL Auth Proxy and opens an interactive psql
# session against a Cloud SQL Postgres instance (e.g. one created by
# create_test_instance.sh). Stops the proxy when psql exits.
#
# Usage: ./scripts/connect_psql.sh [INSTANCE_NAME] [DBNAME] [DBUSER] [PASSWORD] [PORT]
# PORT defaults to a freshly picked free port, not a fixed one, so a
# leftover proxy from an earlier run can't make this one silently connect
# to the wrong (stale) tunnel. If PASSWORD is omitted, psql falls back to
# its own prompt/.pgpass - which needs a real interactive tty to work; pass
# PASSWORD explicitly if that fails with "fe_sendauth: no password supplied".

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/_lib.sh"

INSTANCE_NAME=${1:-"test-pg-instance"}
DBNAME=${2:-"postgres"}
DBUSER=${3:-"postgres"}
PASSWORD=${4:-}
PORT=${5:-$(_free_port)}

open_tunnel "${INSTANCE_NAME}" "${PORT}"

# only set PGPASSWORD if PASSWORD was actually given - an empty PGPASSWORD
# would override a working .pgpass/trust-auth fallback with an explicit
# empty password instead of leaving it unset.
if [[ -n "${PASSWORD}" ]]; then
    export PGPASSWORD="${PASSWORD}"
fi

psql "host=127.0.0.1 port=${PORT} dbname=${DBNAME} user=${DBUSER}"
