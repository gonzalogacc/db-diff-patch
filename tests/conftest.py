import shutil
import uuid

import pytest
import sqlalchemy as sa

from src.db_engine import setup_engine
from tests.docker_postgres import DockerPostgresTunnel
from tests.test_functions import _populate_db

_docker_available = shutil.which("docker") is not None


@pytest.fixture(scope="session")
def docker_postgres():
    """One container per session; tests isolate via their own database (see source_database)."""
    if not _docker_available:
        pytest.skip("docker not available")
    with DockerPostgresTunnel() as tunnel:
        yield tunnel


@pytest.fixture
def source_database(docker_postgres):
    """Fresh, uniquely-named database in docker_postgres, seeded via
    _populate_db. Dropped on teardown."""
    db_name = f"e2e_source_{uuid.uuid4().hex[:8]}"

    admin_engine = setup_engine(
        docker_postgres.host, docker_postgres.port, "postgres",
        docker_postgres.username, docker_postgres.password,
    )
    with admin_engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        conn.execute(sa.text(f'CREATE DATABASE "{db_name}"'))

    source_engine = setup_engine(
        docker_postgres.host, docker_postgres.port, db_name,
        docker_postgres.username, docker_postgres.password,
    )
    _populate_db(source_engine)

    yield db_name

    with admin_engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        conn.execute(sa.text(f'DROP DATABASE IF EXISTS "{db_name}" WITH (FORCE)'))
