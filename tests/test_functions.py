
import uuid
from datetime import datetime

from pytest_postgresql import factories
import sqlalchemy as sa
from pytest import fixture
from sqlalchemy import UUID, Column, DateTime, Integer, MetaData, String, Table, text
from sqlalchemy.orm import Session

from src.db_engine import setup_engine
from src.differ import DBConnection, check_deletions, check_insertions, check_updates, get_tables, link_dbs, make_all_checks, make_diff_patch


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


@fixture
def linked_databases(
    setup_database_local, 
    setup_database_remote, 
    db_local, 
    db_remote
):
    
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

    
def test_deletions_detect(
    setup_database_local, 
    linked_databases,
    ):
    """Check the function correctly pickup the deletes in a single table."""
    engine = setup_database_local
    
    metadata = MetaData() 
    metadata.reflect(bind=engine)
    users_table = metadata.tables['users']

    with Session(engine) as session:
        stmt = users_table.delete().where(users_table.c.id > 1)
        response = session.execute(stmt)
        deleted_count = response.rowcount
        session.commit()

    table_pairs = get_tables(engine)
    user_table_paris = table_pairs[0]

    deletions = check_deletions(engine, user_table_paris.clone, user_table_paris.prod)
    assert len(deletions) == deleted_count


def test_insertions_detect(
    setup_database_local, 
    linked_databases,
    ):
    """Check function correctly picksup the inserts in a table."""
    engine = setup_database_local

    metadata = MetaData() 
    metadata.reflect(bind=engine)
    users_table = metadata.tables['users']

    users_to_insert = [
        dict(username="inserted-user-1", email="email1@user.com"),
        dict(username="inserted-user-2", email="email2@user.com"),
        dict(username="inserted-user-3", email="email3@user.com"),
    ]
    with Session(engine) as session:
        for user in users_to_insert:
            stmt = users_table.insert().values(**user)
            session.execute(stmt)
        session.commit()
        
    table_pairs = get_tables(engine)
    user_table_paris = table_pairs[0]

    insertions = check_insertions(engine, user_table_paris.clone, user_table_paris.prod)
    assert len(insertions) == len(users_to_insert)


def test_check_updates(
    setup_database_local,
    linked_databases
):
    """Check function correctly pickups update tables."""
    engine = setup_database_local

    metadata = MetaData() 
    metadata.reflect(bind=engine)
    users_table = metadata.tables['users']
    
    with Session(engine) as session:
        stmt = sa.update(users_table).where(users_table.c.id == 5).values(username="updated-user")
        session.execute(stmt)
        session.commit()
    
    table_pairs = get_tables(engine)
    user_table_paris = table_pairs[0]

    updates = check_updates(engine, user_table_paris.clone, user_table_paris.prod)
    assert len(updates) == 1


def test_make_all_checks(
    setup_database_local,
    linked_databases
):
    """Check update, delete and insert are all detected at the same time for the users table."""
    engine = setup_database_local

    metadata = MetaData()
    metadata.reflect(bind=engine)
    users_table = metadata.tables['users']

    with Session(engine) as session:
        # updates
        update_stmt = sa.update(users_table).where(users_table.c.id == 5).values(username="updated-user")
        session.execute(update_stmt)

        # deletions
        delete_stmt = users_table.delete().where(users_table.c.id > 7)
        delete_response = session.execute(delete_stmt)
        deleted_count = delete_response.rowcount

        # insertions
        users_to_insert = [
            dict(username="inserted-user-1", email="email1@user.com"),
            dict(username="inserted-user-2", email="email2@user.com"),
            dict(username="inserted-user-3", email="email3@user.com"),
        ]
        for user in users_to_insert:
            session.execute(users_table.insert().values(**user))

        session.commit()

    table_pairs = get_tables(engine)
    user_table_paris = table_pairs[0]

    changes = make_all_checks(engine, user_table_paris.clone, user_table_paris.prod)
    assert len(changes) == 1 + deleted_count + len(users_to_insert)
    assert 1 == len([change for change in changes if "UPDATE" in str(change)])
    assert deleted_count == len([change for change in changes if "DELETE" in str(change)])
    assert len(users_to_insert) == len([change for change in changes if "INSERT" in str(change)])


def test_make_diff_patch(
    setup_database_local,
    linked_databases
):
    """Apply update, delete and insert modifications to every table."""
    engine = setup_database_local

    metadata = MetaData()
    metadata.reflect(bind=engine)
    users_table = metadata.tables['users']
    movies_table = metadata.tables['movies']
    books_table = metadata.tables['books']
    orders_table = metadata.tables['orders']

    # uuids reused for books (primary key) and orders (foreign key)
    book_uuids = [uuid.uuid4() for _ in range(3)]

    with Session(engine) as session:
        # --- users ---
        session.execute(
            sa.update(users_table).where(users_table.c.id == 5).values(username="updated-user")
        )
        session.execute(users_table.delete().where(users_table.c.id > 7))
        for i in range(1, 4):
            session.execute(
                users_table.insert().values(username=f"inserted-user-{i}", email=f"email{i}@user.com")
            )

        # --- movies ---
        for i in range(1, 4):
            session.execute(
                movies_table.insert().values(title=f"Movie {i}", release_year=2000 + i)
            )
        session.execute(
            sa.update(movies_table).where(movies_table.c.title == "Movie 1").values(release_year=1999)
        )
        session.execute(movies_table.delete().where(movies_table.c.title == "Movie 3"))

        # --- books ---
        for i, book_uuid in enumerate(book_uuids, start=1):
            session.execute(
                books_table.insert().values(book_uuid=book_uuid, title=f"Book {i}", author=f"Author {i}")
            )
        session.execute(
            sa.update(books_table).where(books_table.c.book_uuid == book_uuids[0]).values(author="updated-author")
        )
        session.execute(books_table.delete().where(books_table.c.book_uuid == book_uuids[2]))

        # --- orders ---
        for i, book_uuid in enumerate(book_uuids, start=1):
            session.execute(
                orders_table.insert().values(
                    user_id=i,
                    book_uuid=book_uuid,
                    order_date=datetime(2020, 1, i),
                    quantity=i,
                )
            )
        session.execute(
            sa.update(orders_table).where(orders_table.c.user_id == 1).values(quantity=99)
        )
        session.execute(orders_table.delete().where(orders_table.c.user_id == 3))
        session.commit()

    diff_patch = make_diff_patch(engine)

    assert 7 == len([sql for sql in diff_patch.split('\n') if 'public.users' in sql])
    assert 1 == len([sql for sql in diff_patch.split('\n') if 'public.users' in sql and 'UPDATE' in sql])
    assert 3 == len([sql for sql in diff_patch.split('\n') if 'public.users' in sql and 'DELETE' in sql])
    assert 3 == len([sql for sql in diff_patch.split('\n') if 'public.users' in sql and 'INSERT' in sql])

