
from pytest_postgresql import factories
import sqlalchemy as sa
from pytest import fixture
from sqlalchemy import URL, UUID, Column, DateTime, Integer, MetaData, String, Table, create_engine, text
from sqlalchemy.orm import Session

from src.db_engine import setup_engine
from src.differ import DBConnection, DBPair, check_deletions, get_tables, link_dbs


def _populate_db(engine):
    metadata = MetaData()
    users = Table(
        "users",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("username", String),
        Column("email", String),
        extend_existing=True
    )

    movies = Table(
        "movies",
        metadata,
        Column('id', Integer, primary_key=True),
        Column("title", String),
        Column("release_year", Integer),
        extend_existing=True
    )

    books = Table(
        'books',
        metadata,
        Column("book_uuid", UUID, primary_key=True),
        Column("title", String),
        Column("author", String),
        extend_existing=True
    )

    orders = Table(
        "orders",
        metadata,
        Column("user_id", Integer, primary_key=True),
        Column("book_uuid", UUID, primary_key=True),
        Column("order_date", DateTime),
        Column("quantity", Integer),
        extend_existing=True
    )
    metadata.create_all(engine)

    with Session(engine) as session:
        users_data = [
            ('alice', 'alice@example.com'),
            ('bob', 'bob@example.com'),
            ('charlie', 'charlie@example.com'),
            ('david', 'david@example.com'),
            ('eve', 'eve@example.com'),
            ('frank', 'frank@example.com'),
            ('grace', 'grace@example.com'),
            ('heidi', 'heidi@example.com'),
            ('ivan', 'ivan@example.com'),
            ('judy', 'judy@example.com')
        ]
        user_insert = [dict(username=username, email=email) for username, email in users_data]
        session.execute(sa.insert(users), user_insert)
        session.commit()

# db = factories.postgresql('postgresql_noproc')
db_local = factories.postgresql('postgresql_noproc', dbname="db1-uno")
db_remote = factories.postgresql('postgresql_noproc', dbname="db2-dos")

@fixture
def setup_database_local(db_local):
    engine = setup_engine(
        host=db_local.info.host,
        port=db_local.info.port,
        db_name=db_local.info.dbname,
        username=db_local.info.user,
    )
    _populate_db(engine)
    yield engine
    # delete database here

@fixture
def setup_database_remote(db_remote):
    engine = setup_engine(
        host=db_remote.info.host,
        port=db_remote.info.port,
        db_name=db_remote.info.dbname,
        username=db_remote.info.user,
    )
    _populate_db(engine)
    yield engine
    # delete database here 
 
def test_tables(setup_database_local, setup_database_remote):
    """"""
    engine1 = setup_database_local
    engine2 = setup_database_remote

    with Session(engine1) as session:
        stmt = text("delete from users;")
        session.execute(stmt)
        session.commit()
        stmt = text("select count(1) from users;")
        result = session.execute(stmt).scalar()
    assert not result

    
    with Session(engine2) as session:
        stmt = text("select count(1) from users;")
        result = session.execute(stmt).scalar()
    assert result == 10


def test_link_dbs(setup_database_local, setup_database_remote, db_local, db_remote):
    engine = setup_database_local
    local=DBConnection(
            host=db_local.info.host,
            port=db_local.info.port,
            username=db_local.info.user,
            dbname=db_local.info.dbname
            )

    remote=DBConnection(
            host=db_remote.info.host,
            port=db_remote.info.port,
            username=db_remote.info.user,
            dbname=db_remote.info.dbname
        )
    
    link_dbs(local, remote)

    with Session(engine) as session:
        session.execute(sa.text("delete from users where id>6"))
        session.commit()

    table_pair = get_tables(engine)[0]
    deletions = check_deletions(engine, table_pair.clone, table_pair.prod)
    for d in deletions:
        print(d)
    assert len(deletions) == 4
