# Shared helpers for scripts/*.sh. Not meant to be run directly - source it.

require_cloud_sql_proxy() {
    if ! command -v cloud-sql-proxy &>/dev/null; then
        echo "cloud-sql-proxy not found on PATH. Install it: https://cloud.google.com/sql/docs/postgres/sql-proxy" >&2
        exit 1
    fi
}

# Picks a free local port. Avoids colliding with a leftover proxy from an
# earlier run.
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

    # No --private-ip: dial the public IP, which is reachable from outside
    # the VPC (the private IP typically isn't).
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
