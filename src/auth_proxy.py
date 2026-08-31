"""Manages the Cloud SQL Auth Proxy as a subprocess.

The proxy binary (https://github.com/GoogleCloudSQL/cloud-sql-proxy) is
expected to already be installed and on PATH - this module does not
download or manage the binary itself.
"""

import shutil
import socket
import subprocess
import tempfile
import time
from pathlib import Path

from src.gcp_admin import InstanceInfo

_BINARY = "cloud-sql-proxy"
_STARTUP_TIMEOUT = 30
_STARTUP_POLL_INTERVAL = 0.5


class AuthProxyError(Exception):
    """Raised when the proxy binary is missing or fails to come up."""


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _port_is_open(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(1)
        return sock.connect_ex((host, port)) == 0


class AuthProxyTunnel:
    """Context manager that tunnels 127.0.0.1:<port> to a Cloud SQL instance.

    Usage:
        with AuthProxyTunnel(project, instance_info) as tunnel:
            engine = setup_engine(tunnel.host, tunnel.port, ...)
    """

    def __init__(self, project: str, instance: InstanceInfo, port: int | None = None):
        self.project = project
        self.instance = instance
        self.host = "127.0.0.1"
        self.port = port or _free_port()
        self._process: subprocess.Popen | None = None
        self._log_file = None

    def __enter__(self) -> "AuthProxyTunnel":
        if shutil.which(_BINARY) is None:
            raise AuthProxyError(
                f"'{_BINARY}' was not found on PATH. Install the Cloud SQL Auth Proxy: "
                "https://cloud.google.com/sql/docs/postgres/sql-proxy"
            )

        connection_name = f"{self.project}:{self.instance.region}:{self.instance.name}"
        self._log_file = tempfile.NamedTemporaryFile(
            mode="w+", prefix="cloud-sql-proxy-", suffix=".log", delete=False
        )
        self._process = subprocess.Popen(
            [_BINARY, "--port", str(self.port), connection_name],
            stdout=self._log_file,
            stderr=subprocess.STDOUT,
        )

        deadline = time.monotonic() + _STARTUP_TIMEOUT
        while time.monotonic() < deadline:
            if self._process.poll() is not None:
                self._raise_startup_error(connection_name)
            if _port_is_open(self.host, self.port):
                return self
            time.sleep(_STARTUP_POLL_INTERVAL)

        self.__exit__(None, None, None)
        raise AuthProxyError(
            f"Timed out after {_STARTUP_TIMEOUT}s waiting for the Auth Proxy tunnel to "
            f"{connection_name} to come up."
        )

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._process is not None and self._process.poll() is None:
            self._process.terminate()
            try:
                self._process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self._process.kill()
                self._process.wait()
        if self._log_file is not None:
            self._log_file.close()

    def _raise_startup_error(self, connection_name: str) -> None:
        assert self._log_file is not None
        log_path = Path(self._log_file.name)
        output = log_path.read_text() if log_path.exists() else ""
        self.__exit__(None, None, None)
        raise AuthProxyError(
            f"Auth Proxy exited before the tunnel to {connection_name} came up:\n{output}"
        )
