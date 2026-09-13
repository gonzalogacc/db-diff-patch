"""CLI entrypoint: branch, diff, and apply data patches against a Postgres server."""

from pathlib import Path

import click

from src.branches import BranchError, create_branch, drop_branch, get_branch, list_branches
from src.db_engine import setup_engine
from src.differ import DBConnection, apply_patch_file, link_dbs, make_diff_patch

_POSTGRES_PORT = 5432
# DB to connect to for admin SQL (CREATE DATABASE, pg_database, ...).
# Guaranteed to exist, never itself a source/branch database.
_MAINTENANCE_DB = "template1"


@click.group()
def cli():
    """db-differ: branch, diff, and apply data patches against a Postgres server."""


_host_option = click.option("--host", required=True, help="Postgres host")
_port_option = click.option("--port", default=_POSTGRES_PORT, type=int, help="Postgres port")


@cli.group("branch")
def branch_group():
    """Manage branches (base + modify database pairs on one instance)."""


@branch_group.command("create")
@click.argument("name")
@_host_option
@_port_option
@click.option("--source-database", required=True, help="Database to clone base/modify from")
@click.option("--username", required=True, help="Postgres user on that instance")
@click.option("--password", default=None, help="Postgres password on that instance")
def branch_create(name, host, port, source_database, username, password):
    """Create a branch: base = CREATE DATABASE TEMPLATE source, modify = CREATE DATABASE TEMPLATE base."""
    engine = setup_engine(host, port, _MAINTENANCE_DB, username, password)
    try:
        info = create_branch(engine, source_database, name)
    except BranchError as exc:
        raise click.ClickException(str(exc)) from exc
    finally:
        engine.dispose()

    click.echo(f"Created branch '{info.name}': base={info.base_database} modify={info.modify_database}")


@branch_group.command("list")
@_host_option
@_port_option
@click.option("--username", required=True, help="Postgres user on that instance")
@click.option("--password", default=None, help="Postgres password on that instance")
def branch_list(host, port, username, password):
    """List existing branches on an instance."""
    engine = setup_engine(host, port, _MAINTENANCE_DB, username, password)
    try:
        branches = list_branches(engine)
    finally:
        engine.dispose()

    if not branches:
        click.echo("No branches found.")
        return

    for info in branches:
        click.echo(
            f"{info.name}\tsource={info.source_database}\tbase={info.base_database}\tmodify={info.modify_database}"
        )


@branch_group.command("drop")
@click.argument("name")
@_host_option
@_port_option
@click.option("--username", required=True, help="Postgres user on that instance")
@click.option("--password", default=None, help="Postgres password on that instance")
def branch_drop(name, host, port, username, password):
    """Delete a branch's base and modify databases."""
    engine = setup_engine(host, port, _MAINTENANCE_DB, username, password)
    try:
        drop_branch(engine, name)
    except BranchError as exc:
        raise click.ClickException(str(exc)) from exc
    finally:
        engine.dispose()

    click.echo(f"Dropped branch '{name}'.")


@cli.command("diff")
@click.argument("name")
@_host_option
@_port_option
@click.option("--server-host", default=None, help="FDW hop address, if different from --host (e.g. docker). Defaults to --host")
@click.option("--server-port", default=None, type=int, help="FDW hop port, if different from --port. Defaults to --port")
@click.option("--username", required=True, help="Postgres user on that instance")
@click.option("--password", default=None, help="Postgres password on that instance")
@click.option("-o", "--output", "output_path", required=True, type=click.Path(), help="Where to write the patch file")
def diff(name, host, port, server_host, server_port, username, password, output_path):
    """Compute the diff between a branch's base and modify databases and write it as a patch file."""
    fdw_host, fdw_port = server_host or host, server_port or port

    admin_engine = setup_engine(host, port, _MAINTENANCE_DB, username, password)
    try:
        branch_info = get_branch(admin_engine, name)
    except BranchError as exc:
        raise click.ClickException(str(exc)) from exc
    finally:
        admin_engine.dispose()

    local = DBConnection(
        host=host, port=port, username=username,
        dbname=branch_info.modify_database, password=password,
    )
    # FDW hop uses the server's own view of itself, not the client-facing
    # host/port - see --server-host/--server-port above.
    remote = DBConnection(
        host=fdw_host, port=fdw_port, username=username,
        dbname=branch_info.base_database, password=password,
    )
    link_dbs(local, remote)

    local_engine = setup_engine(host, port, branch_info.modify_database, username, password)
    try:
        patch_sql = make_diff_patch(local_engine)
    finally:
        local_engine.dispose()

    Path(output_path).write_text(patch_sql)
    click.echo(f"Wrote patch to {output_path}")


@cli.command("apply")
@click.argument("patch_file", type=click.Path(exists=True))
@_host_option
@_port_option
@click.option("--username", required=True, help="Postgres user on that instance")
@click.option("--password", default=None, help="Postgres password on that instance")
@click.option("--dbname", required=True, help="Database to apply the patch to")
def apply(patch_file, host, port, username, password, dbname):
    """Apply a patch file (from `diff`) to a database - typically prod."""
    connection = DBConnection(host=host, port=port, username=username, dbname=dbname, password=password)
    apply_patch_file(patch_file, connection)
    click.echo(f"Applied {patch_file} to {dbname}@{host}:{port}")


if __name__ == "__main__":
    cli()
