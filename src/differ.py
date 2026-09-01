import sys
from copy import copy
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from networkx import DiGraph, find_cycle, is_directed_acyclic_graph, topological_sort
import sqlalchemy as sa
from sqlalchemy.orm import Session
from sqlalchemy.sql.compiler import SQLCompiler

from src.db_engine import setup_engine


@dataclass
class DBConnection:
    host: str
    port: int
    username: str
    dbname: str
    password: str | None = None

@dataclass
class DBPair:
    """This is the pair of databases to be connected.
    
    local is the database where the script operates, the one with the changes.
    remote is the original database, the one the diff needs to computed upon and eventually applied. 
    """
    local: DBConnection
    remote: DBConnection


class OperationType(StrEnum):
    DELETE = "delete"
    UPDATE = "update"
    INSERT = "insert"


@dataclass
class DatabaseOperation:
    table_name: str
    operation: OperationType
    statement: SQLCompiler


def link_dbs(local: DBConnection, remote: DBConnection):
    """Link the local db to the remote one via postgres_fdw.

    Idempotent: safe to call repeatedly against the same pair (e.g. running
    `diff` more than once against the same branch) - each step is skipped if
    it was already done by a previous call.
    """
    local_engine = setup_engine(
        local.host,
        local.port,
        local.dbname,
        local.username,
        local.password
    )

    with Session(local_engine) as session:

        session.execute(sa.text("CREATE EXTENSION IF NOT EXISTS postgres_fdw"))

        server_exists = session.execute(
            sa.text("SELECT 1 FROM pg_foreign_server WHERE srvname = 'remote_server'")
        ).first()
        if not server_exists:
            session.execute(
                sa.text(
                    "CREATE SERVER remote_server FOREIGN DATA WRAPPER postgres_fdw "
                    "OPTIONS (host :host, port :port, dbname :dbname)"
                ),
                {"host": remote.host, "port": str(remote.port), "dbname": remote.dbname}
            )

        mapping_exists = session.execute(
            sa.text(
                "SELECT 1 FROM pg_user_mappings "
                "WHERE srvname = 'remote_server' AND usename = current_user"
            )
        ).first()
        if not mapping_exists:
            # Postgres requires the password in the mapping for non-superusers
            # (Cloud SQL's own "postgres" role isn't a true superuser there),
            # otherwise CREATE SERVER/IMPORT FOREIGN SCHEMA later fails with
            # "password is required".
            if remote.password:
                session.execute(
                    sa.text(
                        "CREATE USER MAPPING FOR user SERVER remote_server "
                        "OPTIONS (user :user, password :password)"
                    ),
                    {"user": remote.username, "password": remote.password}
                )
            else:
                session.execute(
                    sa.text("CREATE USER MAPPING FOR user SERVER remote_server OPTIONS (user :user)"),
                    {"user": remote.username}
                )

        schema_exists = session.execute(
            sa.text(
                "SELECT 1 FROM information_schema.schemata WHERE schema_name = 'remote_server_schema'"
            )
        ).first()
        if not schema_exists:
            session.execute(sa.text("CREATE SCHEMA remote_server_schema"))
            session.execute(
                sa.text("IMPORT FOREIGN SCHEMA public FROM SERVER remote_server INTO remote_server_schema")
            )

        session.commit()


class NoPrimaryKeyError(Exception):
    """Raised when a table has no primary key, so it can't be safely diffed."""


def get_table_keys(table: sa.Table) -> list[sa.Column]:
    """Given a table return the primary key columns from that table."""
    return [column for column in table.c if column.primary_key]


def _require_primary_key(table: sa.Table) -> list[sa.Column]:
    """Like get_table_keys, but fails loudly and clearly instead of letting
    callers build a SELECT with zero columns (a confusing SQLAlchemy error
    that also aborts the caller's whole run, not just this table)."""
    keys = get_table_keys(table)
    if not keys:
        raise NoPrimaryKeyError(f"Table '{table.name}' has no primary key - it can't be safely diffed.")
    return keys


def print_stmt(stmt) -> None:
    compiled = stmt.compile(compile_kwargs={"literal_binds": True})
    print(compiled)


def check_deletions(engine: sa.Engine, local_table: sa.Table, remote_table: sa.Table) -> list[DatabaseOperation]:
    """Find the deletion between tables."""
    table_keys = _require_primary_key(local_table)
    local_keys = [getattr(local_table.c, table_key.name) for table_key in table_keys]
    remote_keys = [getattr(remote_table.c, table_key.name) for table_key in table_keys]
    
    # Deleted are fields in the remote but not in the local db
    with Session(engine) as session:
        # Returns all ids in the remote that are not in the local. They were deleted.
        stmt = sa.select(*remote_keys).except_(sa.select(*local_keys))
        deleted_rows = session.execute(stmt).all()

        deletion_list = []
        for deleted_ids in deleted_rows:
            stmt = sa.delete(local_table).where(
                *[table_key == key_value for table_key, key_value in zip(local_keys, deleted_ids, strict=True)]
            )
            deletion_list.append(
                DatabaseOperation(
                    table_name=local_table.name,
                    operation=OperationType.DELETE,
                    statement=stmt.compile(engine, compile_kwargs={"literal_binds": True})
                )
            )

    return deletion_list


def check_insertions(engine, local_table, remote_table) -> list[DatabaseOperation]:
    """Find the insertions between tables."""
    table_keys = _require_primary_key(local_table)
    local_keys = [getattr(local_table.c, table_key.name) for table_key in table_keys]
    remote_keys = [getattr(remote_table.c, table_key.name) for table_key in table_keys]
    
    with Session(engine) as session:
        stmt = sa.select(*local_keys).except_(sa.select(*remote_keys))
        inserted_rows = session.execute(stmt).all() 
        
        insert_list = [] 
        for inserted_ids in inserted_rows:
            stmt = sa.select(local_table).where(
                *[table_key == key_value for table_key, key_value in zip(local_keys, inserted_ids, strict=True)]
            )
            response = session.execute(stmt).one()

            # remap columns
            data = {}
            for i, c in enumerate(local_table.c):
                data[c.name] = response[i]
            
            stmt = sa.insert(local_table).values(**data)
            insert_list.append(
                DatabaseOperation(
                    table_name=local_table.name,
                    operation=OperationType.INSERT,
                    statement=stmt.compile(engine, compile_kwargs={"literal_binds": True})
                )
            )

    return insert_list


def check_updates(engine, local_table: sa.Table, remote_table: sa.Table) -> list[DatabaseOperation]:
    key_columns = _require_primary_key(local_table)
    key_names = {c.name for c in key_columns}
    key_indices = [index for index, column in enumerate(local_table.c) if column.name in key_names]

    def row_key(row):
        return tuple(row[i] for i in key_indices)

    with Session(engine) as session:
        stmt = sa.select(remote_table).except_(sa.select(local_table))
        remote_exclusive = session.execute(stmt).all()

        stmt = sa.select(local_table).except_(sa.select(remote_table))
        local_exclusive = session.execute(stmt).all()

        # If a row's key is shared by both sides then it's modified, not inserted/deleted
        shared_keys = {row_key(r) for r in remote_exclusive} & {row_key(r) for r in local_exclusive}

        update_list = []
        for key_values in shared_keys:
            local_row = next(r for r in local_exclusive if row_key(r) == key_values)
            remote_row = next(r for r in remote_exclusive if row_key(r) == key_values)
            ## Compare field by field to get the differences and construct the sql update
            changed = {}
            for column, remote_field, local_field in zip(local_table.c, remote_row, local_row):
                if column.name in key_names:
                    continue
                if remote_field != local_field:
                    changed[column.name] = local_field
            where_clauses = [column == value for column, value in zip(key_columns, key_values)]
            stmt = sa.update(local_table).where(*where_clauses).values(**changed)
            update_list.append(
                DatabaseOperation(
                    table_name=local_table.name,
                    operation=OperationType.UPDATE,
                    statement=stmt.compile(engine, compile_kwargs={"literal_binds": True})
                )
            )

    return update_list


@dataclass
class TablePair:
    prod: sa.Table
    clone: sa.Table


def get_tables(engine):
    """Get all tables and return the pairs to run the comparisons."""

    metadata = sa.MetaData()
    metadata.reflect(bind=engine)

    pairs = []
    tables = copy(metadata.tables)
    for table in tables:
        tabl_prod = sa.Table(table, metadata, autoload_with=engine, schema='remote_server_schema')
        tabl_clone = sa.Table(table, metadata, autoload_with=engine, schema='public')
        table_pair = TablePair(prod=tabl_prod, clone=tabl_clone) 
        pairs.append(table_pair) 
    return pairs


def make_all_checks(engine, local_table, remote_table) -> list[DatabaseOperation]:
    return [
        *check_deletions(engine, local_table, remote_table),
        *check_insertions(engine, local_table, remote_table),
        *check_updates(engine, local_table, remote_table)
    ]


def _no_primary_key_warning(table_names: list[str]) -> str:
    """A warning loud enough to notice both on the terminal (stderr) and in
    the patch file itself, since whoever applies the patch later may not be
    the person who generated it."""
    border = "-- " + "!" * 70
    lines = [
        border,
        "-- WARNING: the following tables have NO PRIMARY KEY and were SKIPPED.",
        "-- They are NOT reflected in this patch at all - review them manually.",
        *(f"--   - {name}" for name in table_names),
        border,
    ]
    warning = "\n".join(lines)
    print(warning, file=sys.stderr)
    return warning


def make_diff_patch(engine) -> str:

    commands = []
    commands.append("BEGIN;")

    table_pairs = get_tables(engine)

    diffable_pairs = []
    skipped_tables = []
    for table_pair in table_pairs:
        if get_table_keys(table_pair.clone):
            diffable_pairs.append(table_pair)
        else:
            skipped_tables.append(table_pair.clone.name)

    if skipped_tables:
        commands.append(_no_primary_key_warning(skipped_tables))

    all_ops = []
    for table_pair in diffable_pairs:
        all_ops.extend(make_all_checks(engine, table_pair.clone, table_pair.prod))

    graph = _build_table_graph(engine)
    table_order = _topological_table_sort(graph)
    for op in sort_ops(all_ops, table_order):
        commands.append(f"{op.statement};")

    commands.append("COMMIT;")
    return "\n".join(commands)


def apply_patch_file(patch_path: str | Path, connection: DBConnection) -> None:
    """Apply a patch file (as produced by make_diff_patch) to a database.

    The file is a self-contained BEGIN;...COMMIT; script, so it's run as one
    statement on an autocommit connection rather than through a session that
    would try to manage its own transaction around it.
    """
    patch_sql = Path(patch_path).read_text()

    engine = setup_engine(
        connection.host,
        connection.port,
        connection.dbname,
        connection.username,
        connection.password
    )
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        conn.execute(sa.text(patch_sql))


class CyclicForeignKeysError(Exception):
    """Raised when foreign keys form a cycle across multiple distinct
    tables, so no single table-level insert order is possible."""


def _build_table_graph(engine) -> DiGraph:
    """Create a Directed graph of the table relations in the database.

    Each edge points from a table to the table its foreign key references,
    e.g. orders -> users. This means a plain topological sort of the graph
    yields a child-before-parent order.

    Self-referencing FKs (e.g. employees.manager_id -> employees.id) are not
    represented as edges: "employees before employees" isn't a real
    inter-table ordering constraint, and networkx treats any self-loop as a
    cycle, which would otherwise block sorting the *entire* graph over a
    single self-referencing table. Row-level ordering within such a table's
    own inserts/deletes is a separate concern sort_ops doesn't handle yet.
    """
    metadata = sa.MetaData()
    metadata.reflect(bind=engine)

    graph = DiGraph()
    for table_name, table_obj in metadata.tables.items():
        graph.add_node(table_name)
        for fk_constraint in table_obj.foreign_keys:
            referenced_table = fk_constraint.column.table.name
            if referenced_table == table_name:
                continue
            graph.add_edge(table_name, referenced_table)

    return graph


def _topological_table_sort(graph) -> list[str]:
    """Return tables in parent-before-child order, safe for inserts."""
    if not is_directed_acyclic_graph(graph):
        cycle_edges = find_cycle(graph)
        cycle_tables = [u for u, v in cycle_edges] + [cycle_edges[0][0]]
        raise CyclicForeignKeysError(
            "Foreign keys form a cycle across multiple tables, so no single insert "
            "order is possible: " + " -> ".join(cycle_tables) + ". This needs deferred "
            "constraints or a two-phase insert, which isn't supported."
        )
    tps = topological_sort(graph)
    return list(reversed(list(tps)))


_OPERATION_PHASE = {
    OperationType.INSERT: 0,
    OperationType.UPDATE: 1,
    OperationType.DELETE: 2,
}


def sort_ops(ops: list[DatabaseOperation], table_order: list[str]) -> list[DatabaseOperation]:
    """Order operations so applying them never violates a FK constraint.

    table_order must be a parent-before-child table ordering (as returned by
    _topological_table_sort). Operations are grouped into three phases:
    inserts (parent-first, so referenced rows exist before their children),
    updates (order doesn't matter - a changed FK value can only point at a
    row that already exists or was just inserted in this same patch), and
    deletes (child-first, so a row is removed before the parent it
    references).
    """
    table_rank = {table_name: rank for rank, table_name in enumerate(table_order)}

    def sort_key(op: DatabaseOperation):
        phase = _OPERATION_PHASE[op.operation]
        rank = table_rank.get(op.table_name, len(table_rank))
        if op.operation == OperationType.DELETE:
            rank = -rank
        return (phase, rank)

    return sorted(ops, key=sort_key)


