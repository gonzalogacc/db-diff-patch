"""CLI entrypoint: branch, diff, and apply data patches against Cloud SQL Postgres."""

from pathlib import Path

import click

from src.auth_proxy import AuthProxyError, AuthProxyTunnel
from src.branches import BranchError, create_branch, drop_branch, get_branch, list_branches
from src.db_engine import setup_engine
from src.differ import DBConnection, apply_patch_file, link_dbs, make_diff_patch
from src.gcp_admin import GCPAdminError, get_instance, list_instances

_POSTGRES_PORT = 5432
# Every branch operation needs to connect to *some* database on the target
# instance to run admin SQL (CREATE DATABASE, pg_database, ...). template1
# is guaranteed to exist and is never itself a source/branch database.
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


def _open_tunnel(project, instance_name):
    """Resolve `instance_name` and return an AuthProxyTunnel context manager
    for it, so the caller can open a connection through it."""
    return AuthProxyTunnel(project, _resolve_instance(project, instance_name))


@cli.group("branch")
def branch_group():
    """Manage branches (base + modify database pairs on one instance)."""


@branch_group.command("create")
@click.argument("name")
@click.option("--project", required=True, help="GCP project id")
@click.option("--instance", required=True, help="Cloud SQL instance the branch lives on")
@click.option("--source-database", required=True, help="Database to clone base/modify from")
@click.option("--username", required=True, help="Postgres user on that instance")
@click.option("--password", default=None, help="Postgres password on that instance")
def branch_create(name, project, instance, source_database, username, password):
    """Create a branch: base = CREATE DATABASE TEMPLATE source, modify = CREATE DATABASE TEMPLATE base."""
    tunnel = _open_tunnel(project, instance)
    try:
        with tunnel as t:
            engine = setup_engine(t.host, t.port, _MAINTENANCE_DB, username, password)
            info = create_branch(engine, source_database, name)
    except (AuthProxyError, BranchError) as exc:
        raise click.ClickException(str(exc)) from exc

    click.echo(f"Created branch '{info.name}': base={info.base_database} modify={info.modify_database}")


@branch_group.command("list")
@click.option("--project", required=True, help="GCP project id")
@click.option("--instance", required=True, help="Cloud SQL instance to look for branches on")
@click.option("--username", required=True, help="Postgres user on that instance")
@click.option("--password", default=None, help="Postgres password on that instance")
def branch_list(project, instance, username, password):
    """List existing branches on an instance."""
    tunnel = _open_tunnel(project, instance)
    try:
        with tunnel as t:
            engine = setup_engine(t.host, t.port, _MAINTENANCE_DB, username, password)
            branches = list_branches(engine)
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
@click.option("--project", required=True, help="GCP project id")
@click.option("--instance", required=True, help="Cloud SQL instance the branch lives on")
@click.option("--username", required=True, help="Postgres user on that instance")
@click.option("--password", default=None, help="Postgres password on that instance")
def branch_drop(name, project, instance, username, password):
    """Delete a branch's base and modify databases."""
    tunnel = _open_tunnel(project, instance)
    try:
        with tunnel as t:
            engine = setup_engine(t.host, t.port, _MAINTENANCE_DB, username, password)
            drop_branch(engine, name)
    except (AuthProxyError, BranchError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"Dropped branch '{name}'.")


@cli.command("diff")
@click.argument("name")
@click.option("--project", required=True, help="GCP project id")
@click.option("--instance", required=True, help="Cloud SQL instance the branch lives on")
@click.option("--username", required=True, help="Postgres user on that instance")
@click.option("--password", default=None, help="Postgres password on that instance")
@click.option("-o", "--output", "output_path", required=True, type=click.Path(), help="Where to write the patch file")
def diff(name, project, instance, username, password, output_path):
    """Compute the diff between a branch's base and modify databases and write it as a patch file."""
    instance_info = _resolve_instance(project, instance)
    if not instance_info.private_ip:
        raise click.ClickException(
            f"Instance '{instance}' has no private IP. base/modify are databases on this "
            "same instance, but postgres_fdw still needs a real address for the hop between "
            "them - Cloud SQL rejects a loopback/localhost connection outright "
            "('connection to the loopback address is not allowed'). Recreate SOURCE with "
            "a private IP (enable_private_services_access.sh, then create_test_instance.sh "
            "--network)."
        )

    tunnel = AuthProxyTunnel(project, instance_info)
    try:
        with tunnel as t:
            admin_engine = setup_engine(t.host, t.port, _MAINTENANCE_DB, username, password)
            try:
                branch_info = get_branch(admin_engine, name)
            except BranchError as exc:
                raise click.ClickException(str(exc)) from exc

            local = DBConnection(
                host=t.host, port=t.port, username=username,
                dbname=branch_info.modify_database, password=password,
            )
            # base and modify are databases on the same server, but the FDW
            # hop still needs the instance's own private IP, not localhost -
            # Cloud SQL specifically rejects loopback connections here.
            remote = DBConnection(
                host=instance_info.private_ip, port=_POSTGRES_PORT, username=username,
                dbname=branch_info.base_database, password=password,
            )
            link_dbs(local, remote)

            local_engine = setup_engine(t.host, t.port, branch_info.modify_database, username, password)
            patch_sql = make_diff_patch(local_engine)
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
