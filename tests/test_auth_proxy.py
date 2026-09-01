from unittest.mock import MagicMock, patch

import pytest

from src.auth_proxy import AuthProxyError, AuthProxyTunnel
from src.gcp_admin import InstanceInfo

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
            with patch("src.auth_proxy._port_is_open", return_value=True):
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
            with patch("src.auth_proxy._port_is_open", return_value=False):
                with patch("src.auth_proxy._STARTUP_TIMEOUT", 0.2):
                    with patch("src.auth_proxy._STARTUP_POLL_INTERVAL", 0.05):
                        with pytest.raises(AuthProxyError, match="Timed out"):
                            with AuthProxyTunnel("test-project", _INSTANCE, port=15432):
                                pass
    process.terminate.assert_called_once()
