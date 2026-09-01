from types import SimpleNamespace
from unittest.mock import patch

from click.testing import CliRunner

from src.branches import BranchError, BranchInfo
from src.cli import cli
from src.gcp_admin import GCPAdminError, InstanceInfo


def _instance(name, private_ip="10.0.0.1", public_ip="34.0.0.1"):
    return InstanceInfo(
        name=name, region="us-central1", database_version="POSTGRES_15",
        private_ip=private_ip, public_ip=public_ip, labels={},
    )


def _patched_tunnel():
    """Context manager for patching src.cli.get_instance/AuthProxyTunnel/
    setup_engine the way every branch/diff command needs - yields the mock
    tunnel class so callers can assert on it if needed."""
    fake_tunnel = SimpleNamespace(host="127.0.0.1", port=6543)
    patches = [
        patch("src.cli.get_instance", return_value=_instance("my-instance")),
        patch("src.cli.AuthProxyTunnel"),
        patch("src.cli.setup_engine", return_value="fake-engine"),
    ]
    _, mock_tunnel_cls, _ = (p.start() for p in patches)
    mock_tunnel_cls.return_value.__enter__.return_value = fake_tunnel
    mock_tunnel_cls.return_value.__exit__.return_value = False
    return patches


def _stop(patches):
    for p in reversed(patches):
        p.stop()


def test_list_databases_prints_instances():
    runner = CliRunner()
    with patch("src.cli.list_instances", return_value=[_instance("prod")]):
        result = runner.invoke(cli, ["list-databases", "--project", "test-project"])

    assert result.exit_code == 0
    assert "prod" in result.output


def test_list_databases_empty():
    runner = CliRunner()
    with patch("src.cli.list_instances", return_value=[]):
        result = runner.invoke(cli, ["list-databases", "--project", "test-project"])

    assert result.exit_code == 0
    assert "No Cloud SQL instances found" in result.output


def test_list_databases_reports_gcp_admin_error():
    runner = CliRunner()
    with patch("src.cli.list_instances", side_effect=GCPAdminError("no credentials")):
        result = runner.invoke(cli, ["list-databases", "--project", "test-project"])

    assert result.exit_code != 0
    assert "no credentials" in result.output


def test_branch_create_prints_created_branch():
    runner = CliRunner()
    info = BranchInfo(name="feature_x", base_database="feature_x_base", modify_database="feature_x_modify",
                       source_database="app")
    patches = _patched_tunnel()
    try:
        with patch("src.cli.create_branch", return_value=info) as mock_create:
            result = runner.invoke(cli, [
                "branch", "create", "Feature X",
                "--project", "test-project",
                "--instance", "my-instance",
                "--source-database", "app",
                "--username", "app_user",
            ])
    finally:
        _stop(patches)

    assert result.exit_code == 0, result.output
    mock_create.assert_called_once_with("fake-engine", "app", "Feature X")
    assert "feature_x_base" in result.output
    assert "feature_x_modify" in result.output


def test_branch_create_reports_gcp_admin_error():
    runner = CliRunner()
    with patch("src.cli.get_instance", side_effect=GCPAdminError("instance not found")):
        result = runner.invoke(cli, [
            "branch", "create", "Feature X",
            "--project", "test-project",
            "--instance", "my-instance",
            "--source-database", "app",
            "--username", "app_user",
        ])

    assert result.exit_code != 0
    assert "instance not found" in result.output


def test_branch_list_prints_branches():
    runner = CliRunner()
    info = BranchInfo(name="feature_x", base_database="feature_x_base", modify_database="feature_x_modify",
                       source_database="app")
    patches = _patched_tunnel()
    try:
        with patch("src.cli.list_branches", return_value=[info]):
            result = runner.invoke(cli, [
                "branch", "list",
                "--project", "test-project",
                "--instance", "my-instance",
                "--username", "app_user",
            ])
    finally:
        _stop(patches)

    assert result.exit_code == 0, result.output
    assert "feature_x" in result.output


def test_branch_drop_reports_error():
    runner = CliRunner()
    patches = _patched_tunnel()
    try:
        with patch("src.cli.drop_branch", side_effect=BranchError("no branch named 'x'")):
            result = runner.invoke(cli, [
                "branch", "drop", "x",
                "--project", "test-project",
                "--instance", "my-instance",
                "--username", "app_user",
            ])
    finally:
        _stop(patches)

    assert result.exit_code != 0
    assert "no branch named" in result.output


def test_diff_writes_patch_file(tmp_path):
    runner = CliRunner()
    branch_info = BranchInfo(name="feature_x", base_database="feature_x_base", modify_database="feature_x_modify",
                              source_database="app")
    output_path = tmp_path / "patch.sql"

    patches = _patched_tunnel()
    try:
        with patch("src.cli.get_branch", return_value=branch_info), \
             patch("src.cli.link_dbs") as mock_link_dbs, \
             patch("src.cli.make_diff_patch", return_value="BEGIN;\nCOMMIT;"):
            result = runner.invoke(cli, [
                "diff", "feature-x",
                "--project", "test-project",
                "--instance", "my-instance",
                "--username", "app_user",
                "-o", str(output_path),
            ])
    finally:
        _stop(patches)

    assert result.exit_code == 0, result.output
    assert output_path.read_text() == "BEGIN;\nCOMMIT;"
    mock_link_dbs.assert_called_once()
    local, remote = mock_link_dbs.call_args[0]
    assert local.dbname == "feature_x_modify"
    assert remote.dbname == "feature_x_base"
    # Cloud SQL rejects postgres_fdw connecting to the loopback address,
    # even for an instance connecting to itself - the FDW hop must target
    # the instance's real private IP, not localhost.
    assert remote.host == "10.0.0.1"


def test_diff_fails_without_instance_private_ip():
    runner = CliRunner()
    with patch("src.cli.get_instance", return_value=_instance("my-instance", private_ip=None)):
        result = runner.invoke(cli, [
            "diff", "feature-x",
            "--project", "test-project",
            "--instance", "my-instance",
            "--username", "app_user",
            "-o", "patch.sql",
        ])

    assert result.exit_code != 0
    assert "no private IP" in result.output


def test_diff_reports_branch_not_found():
    runner = CliRunner()
    patches = _patched_tunnel()
    try:
        with patch("src.cli.get_branch", side_effect=BranchError("No branch named 'x' found")):
            result = runner.invoke(cli, [
                "diff", "x",
                "--project", "test-project",
                "--instance", "my-instance",
                "--username", "app_user",
                "-o", "patch.sql",
            ])
    finally:
        _stop(patches)

    assert result.exit_code != 0
    assert "No branch named" in result.output


def test_apply_calls_apply_patch_file(tmp_path):
    runner = CliRunner()
    patch_file = tmp_path / "patch.sql"
    patch_file.write_text("BEGIN;\nCOMMIT;")

    with patch("src.cli.apply_patch_file") as mock_apply:
        result = runner.invoke(cli, [
            "apply", str(patch_file),
            "--host", "10.0.0.5",
            "--username", "app_user",
            "--dbname", "app",
        ])

    assert result.exit_code == 0, result.output
    mock_apply.assert_called_once()
    args = mock_apply.call_args[0]
    assert str(args[0]) == str(patch_file)
    assert args[1].host == "10.0.0.5"
