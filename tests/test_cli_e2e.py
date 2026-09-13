"""Real, unmocked CLI e2e tests against a disposable docker Postgres.
Unlike test_cli.py, nothing below the command layer is mocked."""

import uuid

import sqlalchemy as sa
from click.testing import CliRunner
from sqlalchemy.orm import Session

from src.cli import cli
from src.db_engine import setup_engine


def _invoke(*args):
    return CliRunner().invoke(cli, [str(a) for a in args])


def _host_port_args(docker_postgres):
    return ["--host", docker_postgres.host, "--port", docker_postgres.port]


def test_full_branch_diff_apply_cycle_via_cli(docker_postgres, source_database, tmp_path):
    branch_name = "e2e-branch"

    create_result = _invoke(
        "branch", "create", branch_name,
        *_host_port_args(docker_postgres),
        "--source-database", source_database,
        "--username", docker_postgres.username,
        "--password", docker_postgres.password,
    )
    assert create_result.exit_code == 0, create_result.output
    assert "e2e_branch_base" in create_result.output
    assert "e2e_branch_modify" in create_result.output

    modify_engine = setup_engine(
        docker_postgres.host, docker_postgres.port, "e2e_branch_modify",
        docker_postgres.username, docker_postgres.password,
    )
    with Session(modify_engine) as session:
        session.execute(sa.text("DELETE FROM users WHERE id > 7"))
        session.commit()

    patch_path = tmp_path / "patch.sql"
    diff_result = _invoke(
        "diff", branch_name,
        *_host_port_args(docker_postgres),
        "--server-host", docker_postgres.server_host,
        "--server-port", docker_postgres.server_port,
        "--username", docker_postgres.username,
        "--password", docker_postgres.password,
        "-o", patch_path,
    )
    assert diff_result.exit_code == 0, diff_result.output
    assert patch_path.exists()
    patch_sql = patch_path.read_text()
    assert "DELETE" in patch_sql
    assert "public.users" in patch_sql

    # "prod" target: fresh clone of source_database's pre-edit state
    target_db = f"e2e_target_{uuid.uuid4().hex[:8]}"
    admin_engine = setup_engine(
        docker_postgres.host, docker_postgres.port, "postgres",
        docker_postgres.username, docker_postgres.password,
    )
    try:
        with admin_engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
            conn.execute(sa.text(f'CREATE DATABASE "{target_db}" TEMPLATE "{source_database}"'))

        apply_result = _invoke(
            "apply", patch_path,
            "--host", docker_postgres.host, "--port", docker_postgres.port,
            "--dbname", target_db,
            "--username", docker_postgres.username,
            "--password", docker_postgres.password,
        )
        assert apply_result.exit_code == 0, apply_result.output

        target_engine = setup_engine(
            docker_postgres.host, docker_postgres.port, target_db,
            docker_postgres.username, docker_postgres.password,
        )
        with Session(target_engine) as session:
            remaining = session.execute(sa.text("SELECT id FROM users")).scalars().all()
        assert sorted(remaining) == [1, 2, 3, 4, 5, 6, 7]
    finally:
        with admin_engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
            conn.execute(sa.text(f'DROP DATABASE IF EXISTS "{target_db}" WITH (FORCE)'))

    drop_result = _invoke(
        "branch", "drop", branch_name,
        *_host_port_args(docker_postgres),
        "--username", docker_postgres.username,
        "--password", docker_postgres.password,
    )
    assert drop_result.exit_code == 0, drop_result.output

    admin_engine2 = setup_engine(
        docker_postgres.host, docker_postgres.port, "postgres",
        docker_postgres.username, docker_postgres.password,
    )
    with admin_engine2.connect() as conn:
        names = {row[0] for row in conn.execute(sa.text("SELECT datname FROM pg_database"))}
    assert "e2e_branch_base" not in names
    assert "e2e_branch_modify" not in names


def test_branch_list_shows_created_branch_via_cli(docker_postgres, source_database):
    branch_name = "e2e-list-branch"
    try:
        create_result = _invoke(
            "branch", "create", branch_name,
            *_host_port_args(docker_postgres),
            "--source-database", source_database,
            "--username", docker_postgres.username,
            "--password", docker_postgres.password,
        )
        assert create_result.exit_code == 0, create_result.output

        list_result = _invoke(
            "branch", "list",
            *_host_port_args(docker_postgres),
            "--username", docker_postgres.username,
            "--password", docker_postgres.password,
        )
        assert list_result.exit_code == 0, list_result.output
        assert "e2e_list_branch" in list_result.output
        assert source_database in list_result.output
    finally:
        _invoke(
            "branch", "drop", branch_name,
            *_host_port_args(docker_postgres),
            "--username", docker_postgres.username,
            "--password", docker_postgres.password,
        )


def test_diff_reports_error_for_unknown_branch_via_cli(docker_postgres):
    result = _invoke(
        "diff", "no-such-branch",
        *_host_port_args(docker_postgres),
        "--username", docker_postgres.username,
        "--password", docker_postgres.password,
        "-o", "patch.sql",
    )
    assert result.exit_code != 0
    assert "No branch named" in result.output