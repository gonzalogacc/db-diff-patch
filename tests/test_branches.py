import pytest
import sqlalchemy as sa
from pytest_postgresql import factories

from src.branches import (
    BranchError,
    _database_name,
    _slugify,
    create_branch,
    drop_branch,
    get_branch,
    list_branches,
)
from src.db_engine import setup_engine

# dbname must be a safe Postgres identifier (no hyphens) since it's used
# directly as a `source_database` value passed to create_branch.
db = factories.postgresql("postgresql_noproc", dbname="branch_source")


@pytest.fixture
def admin_engine(db):
    """Connected to template1, the way the CLI connects for branch ops -
    never itself the thing being cloned."""
    return setup_engine(db.info.host, db.info.port, "template1", db.info.user)


@pytest.fixture
def source_database(db):
    return db.info.dbname


def _existing_database_names(engine) -> set[str]:
    with engine.connect() as conn:
        return {row[0] for row in conn.execute(sa.text("SELECT datname FROM pg_database"))}


def test_slugify_normalizes_name():
    assert _slugify("My Feature Branch!") == "my_feature_branch"
    assert _slugify("already_slug") == "already_slug"
    assert _slugify("  spaced  ") == "spaced"


def test_slugify_prefixes_names_starting_with_a_digit():
    assert _slugify("123-branch") == "b_123_branch"


def test_slugify_rejects_unusable_name():
    with pytest.raises(BranchError):
        _slugify("!!!")


def test_database_name_appends_role_suffix():
    assert _database_name("my-branch", "base") == "my_branch_base"
    assert _database_name("My Branch", "modify") == "my_branch_modify"


def test_create_branch_creates_base_and_modify(admin_engine, source_database):
    try:
        info = create_branch(admin_engine, source_database, "Feature X")

        assert info.name == "feature_x"
        assert info.base_database == "feature_x_base"
        assert info.modify_database == "feature_x_modify"
        assert info.source_database == source_database

        names = _existing_database_names(admin_engine)
        assert "feature_x_base" in names
        assert "feature_x_modify" in names
    finally:
        drop_branch(admin_engine, "Feature X")


def test_create_branch_rejects_unsafe_source_database_name(admin_engine):
    with pytest.raises(BranchError, match="not a safe Postgres identifier"):
        create_branch(admin_engine, 'evil"; DROP DATABASE foo; --', "some-branch")


def test_create_branch_survives_a_lingering_connection_to_source(admin_engine, db, source_database):
    """CREATE DATABASE ... TEMPLATE requires zero other connections to the
    template - our own tooling (e.g. a stray connect_psql.sh session) is a
    likely source of exactly that, so create_branch must terminate other
    backends on the template first rather than erroring out."""
    other_engine = setup_engine(db.info.host, db.info.port, source_database, db.info.user)
    lingering_conn = other_engine.connect()
    try:
        lingering_conn.execute(sa.text("SELECT 1"))
        info = create_branch(admin_engine, source_database, "feature-lingering")

        assert info.base_database == "feature_lingering_base"
    finally:
        # create_branch is expected to have forcibly killed this connection
        # server-side (that's the point of the test) - invalidate() discards
        # it without trying a graceful rollback on a connection that's
        # already gone.
        lingering_conn.invalidate()
        other_engine.dispose()
        # unconditional, so a failure above can't leave the branch behind
        # for the next test run to trip over
        drop_branch(admin_engine, "feature-lingering")


def test_list_branches_finds_created_branch(admin_engine, source_database):
    try:
        create_branch(admin_engine, source_database, "feature-y")

        branches = {b.name: b for b in list_branches(admin_engine)}

        assert "feature_y" in branches
        info = branches["feature_y"]
        assert info.base_database == "feature_y_base"
        assert info.modify_database == "feature_y_modify"
        assert info.source_database == source_database
    finally:
        drop_branch(admin_engine, "feature-y")


def test_list_branches_ignores_databases_not_matching_the_convention(admin_engine):
    branches = list_branches(admin_engine)
    assert all(b.name not in ("template0", "template1", "postgres") for b in branches)


def test_get_branch_returns_matching_branch(admin_engine, source_database):
    try:
        create_branch(admin_engine, source_database, "feature-z")

        info = get_branch(admin_engine, "Feature Z")

        assert info.name == "feature_z"
    finally:
        drop_branch(admin_engine, "feature-z")


def test_get_branch_raises_when_not_found(admin_engine):
    with pytest.raises(BranchError, match="No branch named"):
        get_branch(admin_engine, "does-not-exist")


def test_drop_branch_removes_both_databases(admin_engine, source_database):
    create_branch(admin_engine, source_database, "feature-w")

    drop_branch(admin_engine, "feature-w")

    names = _existing_database_names(admin_engine)
    assert "feature_w_base" not in names
    assert "feature_w_modify" not in names


def test_drop_branch_is_idempotent_when_already_gone(admin_engine):
    drop_branch(admin_engine, "never-existed")  # must not raise
