"""Disposable Postgres container for e2e CLI tests.
docker run on __enter__, docker rm -f on __exit__.
"""

import socket
import subprocess
import time
import uuid

import psycopg2

_IMAGE = "postgres:16"
_PASSWORD = "postgres"
_STARTUP_TIMEOUT = 30
_STARTUP_POLL_INTERVAL = 0.5


class DockerPostgresError(Exception):
    """Raised when the container fails to start or never becomes ready."""


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class DockerPostgresTunnel:
    """host/port: container's published port, for client connections.
    server_host/server_port: "localhost"/5432, the container-internal
    address, for postgres_fdw's self-hop."""

    server_host = "localhost"
    server_port = 5432
    username = "postgres"
    password = _PASSWORD

    def __init__(self, image: str = _IMAGE):
        self.image = image
        self.container_name = f"db-differ-e2e-{uuid.uuid4().hex[:8]}"
        self.host = "127.0.0.1"
        self.port = _free_port()

    def __enter__(self) -> "DockerPostgresTunnel":
        subprocess.run(
            [
                "docker", "run", "-d", "--name", self.container_name,
                "-e", f"POSTGRES_PASSWORD={self.password}",
                "-p", f"{self.port}:5432",
                self.image,
            ],
            check=True, capture_output=True, text=True,
        )
        self._wait_ready()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        subprocess.run(["docker", "rm", "-f", self.container_name], capture_output=True)

    def _wait_ready(self) -> None:
        deadline = time.monotonic() + _STARTUP_TIMEOUT
        last_error: Exception | None = None
        while time.monotonic() < deadline:
            try:
                conn = psycopg2.connect(
                    host=self.host, port=self.port, user=self.username,
                    password=self.password, dbname="postgres", connect_timeout=1,
                )
                conn.close()
                return
            except Exception as exc:  # noqa: BLE001 - any connect failure means "not ready yet"
                last_error = exc
                time.sleep(_STARTUP_POLL_INTERVAL)

        self.__exit__(None, None, None)
        raise DockerPostgresError(
            f"Postgres container {self.container_name} did not become ready within "
            f"{_STARTUP_TIMEOUT}s: {last_error}"
        )
