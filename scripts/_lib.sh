# Shared helpers for scripts/*.sh. Not meant to be run directly - source it.

require_cloud_sql_proxy() {
    if ! command -v cloud-sql-proxy &>/dev/null; then
        echo "cloud-sql-proxy not found on PATH. Install it: https://cloud.google.com/sql/docs/postgres/sql-proxy" >&2
        exit 1
    fi
}

# Picks a free local port. Used as the default PORT in scripts that tunnel
# through the Auth Proxy, so a leftover proxy process from an earlier run
# squatting on a hardcoded port can't make a new run silently connect to the
# wrong (stale) tunnel instead of failing loudly.
_free_port() {
    python3 - <<'PYEOF'
import socket
s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
s.bind(("127.0.0.1", 0))
print(s.getsockname()[1])
s.close()
PYEOF
}

# Starts the Auth Proxy for INSTANCE_NAME on 127.0.0.1:PORT, blocks until the
# tunnel is accepting connections, and registers a cleanup trap that stops it
# on exit. Sets PROXY_PID.
open_tunnel() {
    local instance_name=$1
    local port=$2

    require_cloud_sql_proxy

    local connection_name
    connection_name=$(gcloud sql instances describe "${instance_name}" --format="value(connectionName)")
    echo "Tunneling to ${connection_name} on 127.0.0.1:${port}..." >&2

    # No --private-ip: instances here have both public and private IP
    # (private for the base<->modify postgres_fdw hop, public so a machine
    # outside the VPC - like the one running this script - can reach them
    # at all). --private-ip would make the proxy dial the private IP,
    # which typically isn't routable from outside the VPC in the first
    # place (a dial timeout, not an auth/config error, is the symptom).
    cloud-sql-proxy --port "${port}" "${connection_name}" &
    PROXY_PID=$!

    # shellcheck disable=SC2064
    trap "echo 'Stopping Auth Proxy (pid ${PROXY_PID})...' >&2; kill ${PROXY_PID} 2>/dev/null || true; wait ${PROXY_PID} 2>/dev/null || true" EXIT

    echo "Waiting for the tunnel to come up..." >&2
    for _ in $(seq 1 30); do
        if ! kill -0 "${PROXY_PID}" 2>/dev/null; then
            echo "Auth Proxy exited before the tunnel came up." >&2
            exit 1
        fi
        if (exec 3<>"/dev/tcp/127.0.0.1/${port}") 2>/dev/null; then
            exec 3<&- 3>&-
            return 0
        fi
        sleep 1
    done

    echo "Timed out waiting for the Auth Proxy tunnel to come up." >&2
    exit 1
}
