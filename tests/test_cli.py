from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from src.branches import BranchError, BranchInfo
from src.cli import cli


def _patched_engine():
    """Patches setup_engine for branch/diff commands. Returns the mock, since
    cli.py calls .dispose() on the returned engine."""
    return patch("src.cli.setup_engine", return_value=MagicMock(name="fake-engine"))


def test_branch_create_prints_created_branch():
    runner = CliRunner()
    info = BranchInfo(name="feature_x", base_database="feature_x_base", modify_database="feature_x_modify",
                       source_database="app")
    with _patched_engine() as mock_setup_engine, \
         patch("src.cli.create_branch", return_value=info) as mock_create:
        result = runner.invoke(cli, [
            "branch", "create", "Feature X",
            "--host", "10.0.0.1", "--port", "5432",
            "--source-database", "app",
            "--username", "app_user",
        ])

    assert result.exit_code == 0, result.output
    mock_create.assert_called_once_with(mock_setup_engine.return_value, "app", "Feature X")
    assert "feature_x_base" in result.output
    assert "feature_x_modify" in result.output


def test_branch_create_reports_branch_error():
    runner = CliRunner()
    with _patched_engine(), \
         patch("src.cli.create_branch", side_effect=BranchError("source database not found")):
        result = runner.invoke(cli, [
            "branch", "create", "Feature X",
            "--host", "10.0.0.1", "--port", "5432",
            "--source-database", "app",
            "--username", "app_user",
        ])

    assert result.exit_code != 0
    assert "source database not found" in result.output


def test_branch_list_prints_branches():
    runner = CliRunner()
    info = BranchInfo(name="feature_x", base_database="feature_x_base", modify_database="feature_x_modify",
                       source_database="app")
    with _patched_engine(), patch("src.cli.list_branches", return_value=[info]):
        result = runner.invoke(cli, [
            "branch", "list",
            "--host", "10.0.0.1", "--port", "5432",
            "--username", "app_user",
        ])

    assert result.exit_code == 0, result.output
    assert "feature_x" in result.output


def test_branch_drop_reports_error():
    runner = CliRunner()
    with _patched_engine(), \
         patch("src.cli.drop_branch", side_effect=BranchError("no branch named 'x'")):
        result = runner.invoke(cli, [
            "branch", "drop", "x",
            "--host", "10.0.0.1", "--port", "5432",
            "--username", "app_user",
        ])

    assert result.exit_code != 0
    assert "no branch named" in result.output


def test_diff_writes_patch_file(tmp_path):
    runner = CliRunner()
    branch_info = BranchInfo(name="feature_x", base_database="feature_x_base", modify_database="feature_x_modify",
                              source_database="app")
    output_path = tmp_path / "patch.sql"

    with _patched_engine(), \
         patch("src.cli.get_branch", return_value=branch_info), \
         patch("src.cli.link_dbs") as mock_link_dbs, \
         patch("src.cli.make_diff_patch", return_value="BEGIN;\nCOMMIT;"):
        result = runner.invoke(cli, [
            "diff", "feature-x",
            "--host", "10.0.0.5", "--port", "5432",
            "--username", "app_user",
            "-o", str(output_path),
        ])

    assert result.exit_code == 0, result.output
    assert output_path.read_text() == "BEGIN;\nCOMMIT;"
    mock_link_dbs.assert_called_once()
    local, remote = mock_link_dbs.call_args[0]
    assert local.dbname == "feature_x_modify"
    assert remote.dbname == "feature_x_base"
    # no --server-host/--server-port given - FDW hop defaults to --host/--port
    assert remote.host == "10.0.0.5"
    assert remote.port == 5432


def test_diff_server_host_overrides_fdw_address(tmp_path):
    runner = CliRunner()
    branch_info = BranchInfo(name="feature_x", base_database="feature_x_base", modify_database="feature_x_modify",
                              source_database="app")
    output_path = tmp_path / "patch.sql"

    with _patched_engine(), \
         patch("src.cli.get_branch", return_value=branch_info), \
         patch("src.cli.link_dbs") as mock_link_dbs, \
         patch("src.cli.make_diff_patch", return_value="BEGIN;\nCOMMIT;"):
        result = runner.invoke(cli, [
            "diff", "feature-x",
            "--host", "127.0.0.1", "--port", "54321",
            "--server-host", "10.0.0.5", "--server-port", "5432",
            "--username", "app_user",
            "-o", str(output_path),
        ])

    assert result.exit_code == 0, result.output
    _local, remote = mock_link_dbs.call_args[0]
    assert remote.host == "10.0.0.5"
    assert remote.port == 5432


def test_diff_reports_branch_not_found():
    runner = CliRunner()
    with _patched_engine(), \
         patch("src.cli.get_branch", side_effect=BranchError("No branch named 'x' found")):
        result = runner.invoke(cli, [
            "diff", "x",
            "--host", "10.0.0.1", "--port", "5432",
            "--username", "app_user",
            "-o", "patch.sql",
        ])

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
