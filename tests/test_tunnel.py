from unittest.mock import MagicMock, patch

import pytest

from src.gcp_admin import InstanceInfo
from src.tunnel import AuthProxyError, AuthProxyTunnel, DirectTunnel

_INSTANCE = InstanceInfo(
    name="branch-a-modify",
    region="us-central1",
    database_version="POSTGRES_15",
    private_ip="10.0.0.2",
    public_ip=None,
)


def _mock_process(exited=False):
    process = MagicMock()
    process.poll.return_value = 0 if exited else None
    return process


def test_missing_binary_raises_before_spawning():
    with patch("shutil.which", return_value=None):
        with patch("subprocess.Popen") as mock_popen:
            with pytest.raises(AuthProxyError, match="not found on PATH"):
                with AuthProxyTunnel("test-project", _INSTANCE, port=15432):
                    pass
            mock_popen.assert_not_called()


def test_successful_tunnel_starts_and_tears_down_proxy():
    process = _mock_process()
    with patch("shutil.which", return_value="/usr/local/bin/cloud-sql-proxy"):
        with patch("subprocess.Popen", return_value=process) as mock_popen:
            with patch("src.tunnel._port_is_open", return_value=True):
                with AuthProxyTunnel("test-project", _INSTANCE, port=15432) as tunnel:
                    assert tunnel.host == "127.0.0.1"
                    assert tunnel.port == 15432

    args = mock_popen.call_args[0][0]
    assert args[0] == "cloud-sql-proxy"
    assert "test-project:us-central1:branch-a-modify" in args
    process.terminate.assert_called_once()
    process.wait.assert_called()


def test_process_exiting_early_raises():
    process = _mock_process(exited=True)
    with patch("shutil.which", return_value="/usr/local/bin/cloud-sql-proxy"):
        with patch("subprocess.Popen", return_value=process):
            with pytest.raises(AuthProxyError, match="exited before"):
                with AuthProxyTunnel("test-project", _INSTANCE, port=15432):
                    pass


def test_startup_timeout_raises_and_cleans_up():
    process = _mock_process()
    with patch("shutil.which", return_value="/usr/local/bin/cloud-sql-proxy"):
        with patch("subprocess.Popen", return_value=process):
            with patch("src.tunnel._port_is_open", return_value=False):
                with patch("src.tunnel._STARTUP_TIMEOUT", 0.2):
                    with patch("src.tunnel._STARTUP_POLL_INTERVAL", 0.05):
                        with pytest.raises(AuthProxyError, match="Timed out"):
                            with AuthProxyTunnel("test-project", _INSTANCE, port=15432):
                                pass
    process.terminate.assert_called_once()


def test_auth_proxy_tunnel_server_host_raises_without_private_ip():
    instance = InstanceInfo(
        name="no-private-ip", region="us-central1", database_version="POSTGRES_15",
        private_ip=None, public_ip="34.0.0.1",
    )
    tunnel = AuthProxyTunnel("test-project", instance, port=15432)
    with pytest.raises(AuthProxyError, match="no private IP"):
        _ = tunnel.server_host


def test_auth_proxy_tunnel_server_port_is_fixed_postgres_port():
    tunnel = AuthProxyTunnel("test-project", _INSTANCE, port=15432)
    assert tunnel.server_port == 5432


def test_direct_tunnel_defaults_server_host_port_to_host_port():
    tunnel = DirectTunnel("10.0.0.9", 5432)
    assert tunnel.server_host == "10.0.0.9"
    assert tunnel.server_port == 5432


def test_direct_tunnel_accepts_server_host_port_override():
    tunnel = DirectTunnel("127.0.0.1", 54321, server_host="localhost", server_port=5432)
    assert tunnel.host == "127.0.0.1"
    assert tunnel.port == 54321
    assert tunnel.server_host == "localhost"
    assert tunnel.server_port == 5432


def test_direct_tunnel_is_a_noop_context_manager():
    tunnel = DirectTunnel("127.0.0.1", 5432)
    with tunnel as t:
        assert t is tunnel
