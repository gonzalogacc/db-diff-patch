"""Branch orchestration at the database level.

A branch is a pair of databases on the *same* Postgres server: `base` (a
frozen clone of a source database, the comparison point) and `modify` (a
clone of `base`, the working copy someone edits). This module knows nothing
about GCP or the Auth Proxy - it just needs an Engine connected to some
database on the target server (conventionally `template1`; see
create_branch's docstring for why) and works purely in Postgres SQL.
"""

import re
from dataclasses import dataclass

import sqlalchemy as sa
from sqlalchemy import Engine

_MAX_IDENTIFIER_LEN = 63  # Postgres NAMEDATALEN - 1
_INVALID_CHARS = re.compile(r"[^a-z0-9_]+")
_SAFE_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_COMMENT_PREFIX = "db-differ:source="


class BranchError(Exception):
    """Raised for invalid branch/database names or an incomplete/missing branch."""


@dataclass
class BranchInfo:
    name: str
    base_database: str
    modify_database: str
    source_database: str | None


def _slugify(value: str) -> str:
    slug = _INVALID_CHARS.sub("_", value.lower()).strip("_")
    slug = re.sub(r"_{2,}", "_", slug)
    if not slug:
        raise BranchError(f"'{value}' does not contain any usable characters for a branch/database name")
    if not slug[0].isalpha():
        slug = f"b_{slug}"
    return slug


def _database_name(branch_name: str, role: str) -> str:
    suffix = f"_{role}"
    slug = _slugify(branch_name)
    return f"{slug[: _MAX_IDENTIFIER_LEN - len(suffix)]}{suffix}"


def _validate_identifier(name: str, what: str) -> str:
    if not _SAFE_IDENTIFIER.match(name):
        raise BranchError(f"{what} '{name}' is not a safe Postgres identifier (letters, digits, underscore only)")
    return name


def _terminate_backends(conn, database_name: str) -> None:
    """Disconnect other sessions from database_name - CREATE DATABASE ...
    TEMPLATE requires the template to have no other active connections."""
    conn.execute(
        sa.text(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            "WHERE datname = :name AND pid <> pg_backend_pid()"
        ),
        {"name": database_name},
    )


def create_branch(engine: Engine, source_database: str, branch_name: str) -> BranchInfo:
    """Create branch_name's base/modify databases on the server `engine`
    connects to, by templating base off source_database and modify off base.

    `engine` must be connected to a database other than source_database -
    Postgres can't template a database off the one the current session is
    connected to. Connecting to `template1` (guaranteed to exist, and never
    itself the thing being cloned) is the intended usage - it works even
    when source_database is the only other database on the server.
    """
    _validate_identifier(source_database, "source database")
    slug = _slugify(branch_name)
    base_name = _database_name(slug, "base")
    modify_name = _database_name(slug, "modify")

    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        _terminate_backends(conn, source_database)
        conn.execute(sa.text(f'CREATE DATABASE "{base_name}" TEMPLATE "{source_database}"'))
        conn.execute(
            sa.text(f'COMMENT ON DATABASE "{base_name}" IS :comment'),
            {"comment": f"{_COMMENT_PREFIX}{source_database}"},
        )

        _terminate_backends(conn, base_name)
        conn.execute(sa.text(f'CREATE DATABASE "{modify_name}" TEMPLATE "{base_name}"'))

    return BranchInfo(name=slug, base_database=base_name, modify_database=modify_name, source_database=source_database)


def list_branches(engine: Engine) -> list[BranchInfo]:
    with engine.connect() as conn:
        rows = conn.execute(
            sa.text(
                "SELECT d.datname, sd.description "
                "FROM pg_database d "
                "LEFT JOIN pg_shdescription sd ON sd.objoid = d.oid"
            )
        ).all()

    by_branch: dict[str, dict[str, str]] = {}
    sources: dict[str, str] = {}
    for datname, description in rows:
        if datname.endswith("_base"):
            slug, role = datname[: -len("_base")], "base"
        elif datname.endswith("_modify"):
            slug, role = datname[: -len("_modify")], "modify"
        else:
            continue
        by_branch.setdefault(slug, {})[role] = datname
        if role == "base" and description and description.startswith(_COMMENT_PREFIX):
            sources[slug] = description[len(_COMMENT_PREFIX):]

    branches = []
    for slug, roles in by_branch.items():
        base = roles.get("base")
        modify = roles.get("modify")
        if base is None or modify is None:
            # a branch mid create/drop, or one that was only partially cleaned up
            continue
        branches.append(
            BranchInfo(name=slug, base_database=base, modify_database=modify, source_database=sources.get(slug))
        )
    return branches


def get_branch(engine: Engine, branch_name: str) -> BranchInfo:
    slug = _slugify(branch_name)
    for info in list_branches(engine):
        if info.name == slug:
            return info
    raise BranchError(f"No branch named '{branch_name}' found")


def drop_branch(engine: Engine, branch_name: str) -> None:
    slug = _slugify(branch_name)
    modify_name = _database_name(slug, "modify")
    base_name = _database_name(slug, "base")

    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        conn.execute(sa.text(f'DROP DATABASE IF EXISTS "{modify_name}" WITH (FORCE)'))
        conn.execute(sa.text(f'DROP DATABASE IF EXISTS "{base_name}" WITH (FORCE)'))
