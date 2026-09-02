"""CLI entrypoint: branch, diff, and apply data patches against Cloud SQL Postgres."""

from pathlib import Path

import click

from src.branches import BranchError, create_branch, drop_branch, get_branch, list_branches
from src.db_engine import setup_engine
from src.differ import DBConnection, apply_patch_file, link_dbs, make_diff_patch
from src.gcp_admin import GCPAdminError, get_instance, list_instances
from src.tunnel import AuthProxyError, AuthProxyTunnel, DatabaseTunnel, DirectTunnel

_POSTGRES_PORT = 5432
# DB to connect to for admin SQL (CREATE DATABASE, pg_database, ...).
# Guaranteed to exist, never itself a source/branch database.
_MAINTENANCE_DB = "template1"


@click.group()
def cli():
    """db-differ: branch, diff, and apply data patches against Cloud SQL Postgres."""


@cli.command("list-databases")
@click.option("--project", required=True, help="GCP project id")
def list_databases(project):
    """List the Cloud SQL instances in a GCP project."""
    try:
        instances = list_instances(project)
    except GCPAdminError as exc:
        raise click.ClickException(str(exc)) from exc

    if not instances:
        click.echo("No Cloud SQL instances found.")
        return

    for instance in instances:
        click.echo(
            f"{instance.name}\t{instance.region}\t{instance.database_version}\t"
            f"private={instance.private_ip}\tpublic={instance.public_ip}"
        )


def _resolve_instance(project, instance_name):
    try:
        return get_instance(project, instance_name)
    except GCPAdminError as exc:
        raise click.ClickException(str(exc)) from exc


def _resolve_tunnel(project, instance, host, port, server_host=None, server_port=None) -> DatabaseTunnel:
    """--project/--instance (GCP) or --host/--port (direct) - exactly one
    pair required. server_host/server_port apply only to the direct path."""
    gcp_given = bool(project or instance)
    direct_given = bool(host or port)

    if gcp_given and direct_given:
        raise click.ClickException("Use either --project/--instance or --host/--port, not both.")
    if direct_given:
        if not (host and port):
            raise click.ClickException("--host and --port must be given together.")
        return DirectTunnel(host, port, server_host=server_host, server_port=server_port)
    if gcp_given:
        if not (project and instance):
            raise click.ClickException("--project and --instance must be given together.")
        return AuthProxyTunnel(project, _resolve_instance(project, instance))
    raise click.ClickException("Provide either --project/--instance or --host/--port.")


_project_option = click.option("--project", default=None, help="GCP project id (with --instance)")
_instance_option = click.option("--instance", default=None, help="Cloud SQL instance the branch lives on (with --project)")
_host_option = click.option("--host", default=None, help="Direct Postgres host (with --port) - alternative to --project/--instance")
_port_option = click.option("--port", default=None, type=int, help="Direct Postgres port (with --host)")


@cli.group("branch")
def branch_group():
    """Manage branches (base + modify database pairs on one instance)."""


@branch_group.command("create")
@click.argument("name")
@_project_option
@_instance_option
@_host_option
@_port_option
@click.option("--source-database", required=True, help="Database to clone base/modify from")
@click.option("--username", required=True, help="Postgres user on that instance")
@click.option("--password", default=None, help="Postgres password on that instance")
def branch_create(name, project, instance, host, port, source_database, username, password):
    """Create a branch: base = CREATE DATABASE TEMPLATE source, modify = CREATE DATABASE TEMPLATE base."""
    tunnel = _resolve_tunnel(project, instance, host, port)
    try:
        with tunnel as t:
            engine = setup_engine(t.host, t.port, _MAINTENANCE_DB, username, password)
            try:
                info = create_branch(engine, source_database, name)
            finally:
                engine.dispose()
    except (AuthProxyError, BranchError) as exc:
        raise click.ClickException(str(exc)) from exc

    click.echo(f"Created branch '{info.name}': base={info.base_database} modify={info.modify_database}")


@branch_group.command("list")
@_project_option
@_instance_option
@_host_option
@_port_option
@click.option("--username", required=True, help="Postgres user on that instance")
@click.option("--password", default=None, help="Postgres password on that instance")
def branch_list(project, instance, host, port, username, password):
    """List existing branches on an instance."""
    tunnel = _resolve_tunnel(project, instance, host, port)
    try:
        with tunnel as t:
            engine = setup_engine(t.host, t.port, _MAINTENANCE_DB, username, password)
            try:
                branches = list_branches(engine)
            finally:
                engine.dispose()
    except AuthProxyError as exc:
        raise click.ClickException(str(exc)) from exc

    if not branches:
        click.echo("No branches found.")
        return

    for info in branches:
        click.echo(
            f"{info.name}\tsource={info.source_database}\tbase={info.base_database}\tmodify={info.modify_database}"
        )


@branch_group.command("drop")
@click.argument("name")
@_project_option
@_instance_option
@_host_option
@_port_option
@click.option("--username", required=True, help="Postgres user on that instance")
@click.option("--password", default=None, help="Postgres password on that instance")
def branch_drop(name, project, instance, host, port, username, password):
    """Delete a branch's base and modify databases."""
    tunnel = _resolve_tunnel(project, instance, host, port)
    try:
        with tunnel as t:
            engine = setup_engine(t.host, t.port, _MAINTENANCE_DB, username, password)
            try:
                drop_branch(engine, name)
            finally:
                engine.dispose()
    except (AuthProxyError, BranchError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"Dropped branch '{name}'.")


@cli.command("diff")
@click.argument("name")
@_project_option
@_instance_option
@_host_option
@_port_option
@click.option("--server-host", default=None, help="FDW hop address, if different from --host (e.g. docker). Defaults to --host")
@click.option("--server-port", default=None, type=int, help="FDW hop port, if different from --port. Defaults to --port")
@click.option("--username", required=True, help="Postgres user on that instance")
@click.option("--password", default=None, help="Postgres password on that instance")
@click.option("-o", "--output", "output_path", required=True, type=click.Path(), help="Where to write the patch file")
def diff(name, project, instance, host, port, server_host, server_port, username, password, output_path):
    """Compute the diff between a branch's base and modify databases and write it as a patch file."""
    tunnel = _resolve_tunnel(project, instance, host, port, server_host, server_port)
    try:
        # Read before entering the tunnel, so a missing private IP fails
        # fast without starting a proxy process.
        resolved_server_host, resolved_server_port = tunnel.server_host, tunnel.server_port
    except AuthProxyError as exc:
        raise click.ClickException(str(exc)) from exc

    try:
        with tunnel as t:
            admin_engine = setup_engine(t.host, t.port, _MAINTENANCE_DB, username, password)
            try:
                branch_info = get_branch(admin_engine, name)
            except BranchError as exc:
                raise click.ClickException(str(exc)) from exc
            finally:
                admin_engine.dispose()

            local = DBConnection(
                host=t.host, port=t.port, username=username,
                dbname=branch_info.modify_database, password=password,
            )
            # FDW hop uses the server's own view of itself, not the
            # client-facing host/port - see DatabaseTunnel in src/tunnel.py.
            remote = DBConnection(
                host=resolved_server_host, port=resolved_server_port, username=username,
                dbname=branch_info.base_database, password=password,
            )
            link_dbs(local, remote)

            local_engine = setup_engine(t.host, t.port, branch_info.modify_database, username, password)
            try:
                patch_sql = make_diff_patch(local_engine)
            finally:
                local_engine.dispose()
    except AuthProxyError as exc:
        raise click.ClickException(str(exc)) from exc

    Path(output_path).write_text(patch_sql)
    click.echo(f"Wrote patch to {output_path}")


@cli.command("apply")
@click.argument("patch_file", type=click.Path(exists=True))
@click.option("--host", required=True)
@click.option("--port", default=_POSTGRES_PORT, type=int)
@click.option("--username", required=True)
@click.option("--password", default=None)
@click.option("--dbname", required=True)
def apply(patch_file, host, port, username, password, dbname):
    """Apply a patch file (from `diff`) to a database - typically prod."""
    connection = DBConnection(host=host, port=port, username=username, dbname=dbname, password=password)
    apply_patch_file(patch_file, connection)
    click.echo(f"Applied {patch_file} to {dbname}@{host}:{port}")


if __name__ == "__main__":
    cli()
