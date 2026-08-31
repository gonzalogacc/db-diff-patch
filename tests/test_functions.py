
import uuid
from datetime import datetime

from pytest_postgresql import factories
import sqlalchemy as sa
from pytest import fixture
from sqlalchemy import UUID, Column, DateTime, ForeignKey, Integer, MetaData, String, Table, text
from sqlalchemy.orm import Session

from src.db_engine import setup_engine
from src.differ import (
    DBConnection,
    DatabaseOperation,
    OperationType,
    _build_table_graph,
    _topological_table_sort,
    apply_patch_file,
    check_deletions,
    check_insertions,
    check_updates,
    get_table_keys,
    get_tables,
    link_dbs,
    make_all_checks,
    make_diff_patch,
    sort_ops,
)
from sqlalchemy_utils import drop_database


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

    Table(
        "movies",
        metadata,
        Column('id', Integer, primary_key=True),
        Column("title", String),
        Column("release_year", Integer),
        extend_existing=True
    )

    Table(
        'books',
        metadata,
        Column("book_uuid", UUID, primary_key=True),
        Column("title", String),
        Column("author", String),
        extend_existing=True
    )

    Table(
        "orders",
        metadata,
        Column("user_id", Integer, ForeignKey("users.id"), primary_key=True),
        Column("book_uuid", UUID, ForeignKey("books.book_uuid"), primary_key=True),
        Column("order_date", DateTime),
        Column("quantity", Integer),
        extend_existing=True
    )

    Table(
        "publishers",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("name", String),
        extend_existing=True
    )

    Table(
        "authors",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("name", String),
        Column("publisher_id", Integer, ForeignKey("publishers.id")),
        extend_existing=True
    )

    Table(
        "editions",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("title", String),
        Column("author_id", Integer, ForeignKey("authors.id")),
        extend_existing=True
    )

    # 5-deep FK chain: countries -> regions -> cities -> warehouses -> shipments,
    # with shipments also depending on carriers (a second, independent root),
    # so shipments has in-degree 2 from two branches of different depth.
    Table(
        "countries",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("name", String),
        extend_existing=True
    )

    Table(
        "regions",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("name", String),
        Column("country_id", Integer, ForeignKey("countries.id")),
        extend_existing=True
    )

    Table(
        "cities",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("name", String),
        Column("region_id", Integer, ForeignKey("regions.id")),
        extend_existing=True
    )

    Table(
        "warehouses",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("name", String),
        Column("city_id", Integer, ForeignKey("cities.id")),
        extend_existing=True
    )

    Table(
        "carriers",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("name", String),
        extend_existing=True
    )

    Table(
        "shipments",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("tracking_number", String),
        Column("warehouse_id", Integer, ForeignKey("warehouses.id")),
        Column("carrier_id", Integer, ForeignKey("carriers.id")),
        extend_existing=True
    )

    table_with_multiple_pks = Table(
        "table_with_multiple_pks",
        metadata,
        Column("pk1", Integer, primary_key=True),
        Column("pk2", Integer, primary_key=True),
        Column("c1", Integer),
        Column("c2", Integer),
        Column("c3", Integer),
        # Column(not a pk),
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

        pk_test_data = [
            (1,11,111,1111,11111),
            (2,22,222,2222,22222),
            (3,33,333,3333,33333),
        ]
        pk_insert = [dict(pk1=c1, pk2=c2, c1=c3, c2=c4, c3=c5) for c1, c2, c3, c4, c5 in pk_test_data]
        session.execute(sa.insert(table_with_multiple_pks), pk_insert)
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
    user_table_paris = _get_table_pair(table_pairs, 'users')

    deletions = check_deletions(engine, user_table_paris.clone, user_table_paris.prod)
    assert len(deletions) == deleted_count


# def test_delete_detecting_pks():
#     # test delete a composite key table
#     orders_table = metadata.tables['orders']
#     with Session(engine) as session:
#         stmt = orders_table.delete().where(orders_table.c.user_id==1)
#         response = session.execute(stmt)
#         deleted_count = response.rowcount
#         session.commit()

#     table_pairs = get_tables(engine)
#     orders_table_paris = table_pairs[3]

#     deletions = check_deletions(engine, orders_table_paris.clone, orders_table_paris.prod)
#     assert len(deletions) == deleted_count 


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
    user_table_paris = _get_table_pair(table_pairs, 'users')

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
    user_table_paris = _get_table_pair(table_pairs, 'users')

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
    user_table_paris = _get_table_pair(table_pairs, 'users')

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

        # --- orders --- (book_uuids[2] was deleted above, so it can't be referenced here)
        for i, book_uuid in enumerate(book_uuids[:2], start=1):
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


def test_multiple_keys(
    setup_database_local,
    linked_databases
):
    """Identify keys."""
    engine = setup_database_local
    metadata = MetaData()
    metadata.reflect(bind=engine)
    multikey_table = metadata.tables['table_with_multiple_pks']

    table_keys = get_table_keys(multikey_table)
    assert len(table_keys) == 2
    table_key_names = [table.name for table in table_keys]
    assert 'pk1' in table_key_names
    assert 'pk2' in table_key_names


def _get_table_pair(table_pairs, name):
    """Look up a TablePair by table name instead of relying on list position."""
    return next(pair for pair in table_pairs if pair.prod.name == name)


def test_deletions_detect_multiple_keys(
    setup_database_local,
    linked_databases,
):
    """Check deletions are correctly detected on a table with a composite primary key."""
    engine = setup_database_local

    metadata = MetaData()
    metadata.reflect(bind=engine)
    multikey_table = metadata.tables['table_with_multiple_pks']

    with Session(engine) as session:
        stmt = multikey_table.delete().where(multikey_table.c.pk1.in_([2, 3]))
        response = session.execute(stmt)
        deleted_count = response.rowcount
        session.commit()

    table_pairs = get_tables(engine)
    multikey_pair = _get_table_pair(table_pairs, 'table_with_multiple_pks')

    deletions = check_deletions(engine, multikey_pair.clone, multikey_pair.prod)
    assert len(deletions) == deleted_count

    deletion_sql = [str(d.statement) for d in deletions]
    # Both key columns must appear in each generated statement, ANDed together,
    # otherwise a delete could match more rows than the one that was actually removed.
    assert any('pk1 = 2' in sql and 'pk2 = 22' in sql for sql in deletion_sql)
    assert any('pk1 = 3' in sql and 'pk2 = 33' in sql for sql in deletion_sql)


def test_deletions_detect_partial_composite_key(
    setup_database_local,
    setup_database_remote,
    linked_databases,
):
    """A pk1-only key match misses a deletion when a sibling row shares pk1.

    Two rows share pk1=1 (pk2=11 and pk2=999) in both local and remote.
    Only the pk2=999 row is deleted locally; pk2=11 survives untouched.
    An implementation keyed on the first PK column alone sees pk1=1 still
    present in local (via the surviving pk2=11 row) and reports zero
    deletions, silently missing the row that needs to be removed from prod.
    """
    local_engine = setup_database_local
    remote_engine = setup_database_remote

    local_metadata = MetaData()
    local_metadata.reflect(bind=local_engine)
    local_multikey_table = local_metadata.tables['table_with_multiple_pks']

    remote_metadata = MetaData()
    remote_metadata.reflect(bind=remote_engine)
    remote_multikey_table = remote_metadata.tables['table_with_multiple_pks']

    extra_row = dict(pk1=1, pk2=999, c1=1, c2=2, c3=3)
    with Session(local_engine) as session:
        session.execute(local_multikey_table.insert().values(**extra_row))
        session.commit()
    with Session(remote_engine) as session:
        session.execute(remote_multikey_table.insert().values(**extra_row))
        session.commit()

    with Session(local_engine) as session:
        stmt = local_multikey_table.delete().where(
            local_multikey_table.c.pk1 == 1,
            local_multikey_table.c.pk2 == 999,
        )
        response = session.execute(stmt)
        deleted_count = response.rowcount
        session.commit()

    table_pairs = get_tables(local_engine)
    multikey_pair = _get_table_pair(table_pairs, 'table_with_multiple_pks')

    deletions = check_deletions(local_engine, multikey_pair.clone, multikey_pair.prod)

    assert len(deletions) == deleted_count == 1

    deletion_sql = str(deletions[0].statement)
    assert 'pk1 = 1' in deletion_sql
    assert 'pk2 = 999' in deletion_sql
    # must not target the surviving sibling row that shares pk1
    assert 'pk2 = 11' not in deletion_sql


def test_insertions_detect_multiple_keys(
    setup_database_local,
    linked_databases,
):
    """check_insertions must key off every PK column, not just the first one.

    This row shares pk1=1 with an existing remote row (pk1=1, pk2=11) and is
    only distinguished by pk2=999. An implementation that diffs on pk1 alone
    will see pk1=1 already present remotely and silently drop this insert.
    """
    engine = setup_database_local

    metadata = MetaData()
    metadata.reflect(bind=engine)
    multikey_table = metadata.tables['table_with_multiple_pks']

    with Session(engine) as session:
        stmt = multikey_table.insert().values(pk1=1, pk2=999, c1=1, c2=2, c3=3)
        session.execute(stmt)
        session.commit()

    table_pairs = get_tables(engine)
    multikey_pair = _get_table_pair(table_pairs, 'table_with_multiple_pks')

    insertions = check_insertions(engine, multikey_pair.clone, multikey_pair.prod)
    assert len(insertions) == 1

    insertion_sql = str(insertions[0].statement)
    assert 'pk1' in insertion_sql and '999' in insertion_sql


def test_check_updates_multiple_keys(
    setup_database_local,
    setup_database_remote,
    linked_databases,
):
    """check_updates must key off every PK column, not just the first one.

    Two rows share pk1=1 (pk2=11 and pk2=999) in both local and remote, and
    both get modified locally with different values. An implementation that
    matches modified rows by pk1 alone collapses both rows onto a single id
    (1) and only keeps one of the two updates, silently dropping the other.
    """
    local_engine = setup_database_local
    remote_engine = setup_database_remote

    local_metadata = MetaData()
    local_metadata.reflect(bind=local_engine)
    local_multikey_table = local_metadata.tables['table_with_multiple_pks']

    remote_metadata = MetaData()
    remote_metadata.reflect(bind=remote_engine)
    remote_multikey_table = remote_metadata.tables['table_with_multiple_pks']

    extra_row = dict(pk1=1, pk2=999, c1=1, c2=2, c3=3)
    with Session(local_engine) as session:
        session.execute(local_multikey_table.insert().values(**extra_row))
        session.commit()
    with Session(remote_engine) as session:
        session.execute(remote_multikey_table.insert().values(**extra_row))
        session.commit()

    with Session(local_engine) as session:
        session.execute(
            sa.update(local_multikey_table)
            .where(local_multikey_table.c.pk1 == 1, local_multikey_table.c.pk2 == 11)
            .values(c1=500)
        )
        session.execute(
            sa.update(local_multikey_table)
            .where(local_multikey_table.c.pk1 == 1, local_multikey_table.c.pk2 == 999)
            .values(c1=600)
        )
        session.commit()

    table_pairs = get_tables(local_engine)
    multikey_pair = _get_table_pair(table_pairs, 'table_with_multiple_pks')

    updates = check_updates(local_engine, multikey_pair.clone, multikey_pair.prod)

    assert len(updates) == 2

    update_sql = [str(u.statement) for u in updates]
    assert any('pk2 = 11' in sql and 'c1=500' in sql for sql in update_sql)
    assert any('pk2 = 999' in sql and 'c1=600' in sql for sql in update_sql)


def test_fk_table_operations(
    setup_database_local,
    setup_database_remote,
    linked_databases,
):
    """Insert, update and delete detection on a table with foreign keys.

    orders has FKs to users.id and books.book_uuid. Seed an identical
    baseline of books + orders in both local and remote, then diverge
    local with one insert, one update and one delete, and confirm all
    three checks handle the FK-constrained table without violating
    referential integrity (the new row's parent book is inserted first;
    the deleted order's parent rows are left untouched).
    """
    local_engine = setup_database_local
    remote_engine = setup_database_remote

    local_metadata = MetaData()
    local_metadata.reflect(bind=local_engine)
    local_books = local_metadata.tables['books']
    local_orders = local_metadata.tables['orders']

    remote_metadata = MetaData()
    remote_metadata.reflect(bind=remote_engine)
    remote_books = remote_metadata.tables['books']
    remote_orders = remote_metadata.tables['orders']

    book_uuids = [uuid.uuid4() for _ in range(3)]
    books_data = [
        dict(book_uuid=book_uuid, title=f"Book {i}", author=f"Author {i}")
        for i, book_uuid in enumerate(book_uuids, start=1)
    ]
    orders_data = [
        dict(user_id=i, book_uuid=book_uuid, order_date=datetime(2020, 1, i), quantity=i)
        for i, book_uuid in enumerate(book_uuids, start=1)
    ]

    for engine, books_table, orders_table in (
        (local_engine, local_books, local_orders),
        (remote_engine, remote_books, remote_orders),
    ):
        with Session(engine) as session:
            session.execute(sa.insert(books_table), books_data)
            session.execute(sa.insert(orders_table), orders_data)
            session.commit()

    new_book_uuid = uuid.uuid4()
    with Session(local_engine) as session:
        # insert: new order, parent book inserted first to satisfy the FK
        session.execute(
            local_books.insert().values(book_uuid=new_book_uuid, title="Book 4", author="Author 4")
        )
        session.execute(
            local_orders.insert().values(
                user_id=4, book_uuid=new_book_uuid, order_date=datetime(2020, 1, 4), quantity=4
            )
        )
        # update
        session.execute(
            sa.update(local_orders)
            .where(local_orders.c.user_id == 1, local_orders.c.book_uuid == book_uuids[0])
            .values(quantity=99)
        )
        # delete
        session.execute(
            local_orders.delete().where(
                local_orders.c.user_id == 3, local_orders.c.book_uuid == book_uuids[2]
            )
        )
        session.commit()

    table_pairs = get_tables(local_engine)
    orders_pair = _get_table_pair(table_pairs, 'orders')

    insertions = check_insertions(local_engine, orders_pair.clone, orders_pair.prod)
    deletions = check_deletions(local_engine, orders_pair.clone, orders_pair.prod)
    updates = check_updates(local_engine, orders_pair.clone, orders_pair.prod)

    assert len(insertions) == 1
    assert len(deletions) == 1
    assert len(updates) == 1

    assert 'VALUES (4,' in str(insertions[0].statement) and str(new_book_uuid) in str(insertions[0].statement)
    assert 'user_id = 3' in str(deletions[0].statement) and str(book_uuids[2]) in str(deletions[0].statement)
    assert 'user_id = 1' in str(updates[0].statement) and 'quantity=99' in str(updates[0].statement)

    diff_patch = make_diff_patch(local_engine)
    assert 3 == len([sql for sql in diff_patch.split('\n') if 'public.orders' in sql])

    all_checks = [*insertions, *deletions, *updates]
    print(all_checks)


def test_table_graph_edges(setup_database_local):
    """The table graph must have one edge per FK, pointing child -> parent."""
    engine = setup_database_local
    graph = _build_table_graph(engine)

    assert ('orders', 'users') in graph.edges
    assert ('orders', 'books') in graph.edges
    assert ('authors', 'publishers') in graph.edges
    assert ('editions', 'authors') in graph.edges
    assert ('regions', 'countries') in graph.edges
    assert ('cities', 'regions') in graph.edges
    assert ('warehouses', 'cities') in graph.edges
    assert ('shipments', 'warehouses') in graph.edges
    assert ('shipments', 'carriers') in graph.edges

    # tables without FKs are still present, as isolated nodes
    assert 'movies' in graph.nodes
    assert 'table_with_multiple_pks' in graph.nodes


def test_topological_table_sort_is_parent_first(setup_database_local):
    """_topological_table_sort must place every parent before its children."""
    engine = setup_database_local
    graph = _build_table_graph(engine)
    order = _topological_table_sort(graph)
    rank = {name: i for i, name in enumerate(order)}

    assert rank['users'] < rank['orders']
    assert rank['books'] < rank['orders']
    assert rank['publishers'] < rank['authors'] < rank['editions']
    assert rank['countries'] < rank['regions'] < rank['cities'] < rank['warehouses'] < rank['shipments']
    assert rank['carriers'] < rank['shipments']


def test_sort_ops_inserts_before_updates_before_deletes():
    """Regardless of table, every insert must precede every update, which
    must precede every delete - a changed/removed row can only safely
    reference rows that already exist at that point in the patch."""
    order = ['publishers', 'authors', 'editions']
    ops = [
        DatabaseOperation('editions', OperationType.DELETE, 'delete editions'),
        DatabaseOperation('authors', OperationType.UPDATE, 'update authors'),
        DatabaseOperation('publishers', OperationType.INSERT, 'insert publishers'),
        DatabaseOperation('authors', OperationType.INSERT, 'insert authors'),
        DatabaseOperation('publishers', OperationType.DELETE, 'delete publishers'),
    ]

    sorted_ops = sort_ops(ops, order)
    phase_rank = {OperationType.INSERT: 0, OperationType.UPDATE: 1, OperationType.DELETE: 2}
    phases = [phase_rank[op.operation] for op in sorted_ops]
    assert phases == sorted(phases)


def test_sort_ops_inserts_parent_first():
    order = ['publishers', 'authors', 'editions']
    ops = [
        DatabaseOperation('editions', OperationType.INSERT, 'insert editions'),
        DatabaseOperation('publishers', OperationType.INSERT, 'insert publishers'),
        DatabaseOperation('authors', OperationType.INSERT, 'insert authors'),
    ]

    sorted_ops = sort_ops(ops, order)
    assert [op.table_name for op in sorted_ops] == ['publishers', 'authors', 'editions']


def test_sort_ops_deletes_child_first():
    order = ['publishers', 'authors', 'editions']
    ops = [
        DatabaseOperation('publishers', OperationType.DELETE, 'delete publishers'),
        DatabaseOperation('editions', OperationType.DELETE, 'delete editions'),
        DatabaseOperation('authors', OperationType.DELETE, 'delete authors'),
    ]

    sorted_ops = sort_ops(ops, order)
    assert [op.table_name for op in sorted_ops] == ['editions', 'authors', 'publishers']


def test_make_diff_patch_applies_without_fk_violation(
    setup_database_local,
    setup_database_remote,
    linked_databases,
):
    """The generated diff patch must apply to the remote DB without tripping
    a FK constraint, exercising both insert (parent-first) and delete
    (child-first) ordering across the publishers -> authors -> editions
    chain: a brand new 3-deep chain is inserted while an existing one is
    torn down in the same patch.
    """
    local_engine = setup_database_local
    remote_engine = setup_database_remote

    local_metadata = MetaData()
    local_metadata.reflect(bind=local_engine)
    local_publishers = local_metadata.tables['publishers']
    local_authors = local_metadata.tables['authors']
    local_editions = local_metadata.tables['editions']

    remote_metadata = MetaData()
    remote_metadata.reflect(bind=remote_engine)
    remote_publishers = remote_metadata.tables['publishers']
    remote_authors = remote_metadata.tables['authors']
    remote_editions = remote_metadata.tables['editions']

    # seed an identical baseline chain in both dbs: publisher 1 -> author 1 -> edition 1
    for engine, publishers, authors, editions in (
        (local_engine, local_publishers, local_authors, local_editions),
        (remote_engine, remote_publishers, remote_authors, remote_editions),
    ):
        with Session(engine) as session:
            session.execute(publishers.insert().values(id=1, name="Publisher 1"))
            session.execute(authors.insert().values(id=1, name="Author 1", publisher_id=1))
            session.execute(editions.insert().values(id=1, title="Edition 1", author_id=1))
            session.commit()

    with Session(local_engine) as session:
        # a new 3-deep chain inserted in one patch: needs parent-first insert order
        session.execute(local_publishers.insert().values(id=2, name="Publisher 2"))
        session.execute(local_authors.insert().values(id=2, name="Author 2", publisher_id=2))
        session.execute(local_editions.insert().values(id=2, title="Edition 2", author_id=2))

        # the whole baseline chain removed: needs child-first delete order
        session.execute(local_editions.delete().where(local_editions.c.id == 1))
        session.execute(local_authors.delete().where(local_authors.c.id == 1))
        session.execute(local_publishers.delete().where(local_publishers.c.id == 1))
        session.commit()

    diff_patch = make_diff_patch(local_engine)

    # applied as a single multi-statement script (its BEGIN/COMMIT manage the
    # transaction), the way a real consumer would run it via psql or a raw
    # autocommit connection - not through an ORM session's own transaction.
    with remote_engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        conn.execute(text(diff_patch))

    with Session(remote_engine) as session:
        remaining_publishers = session.execute(sa.select(remote_publishers.c.id)).scalars().all()
        remaining_authors = session.execute(sa.select(remote_authors.c.id)).scalars().all()
        remaining_editions = session.execute(sa.select(remote_editions.c.id)).scalars().all()

    assert sorted(remaining_publishers) == [2]
    assert sorted(remaining_authors) == [2]
    assert sorted(remaining_editions) == [2]


def test_link_dbs_is_idempotent(
    setup_database_local,
    setup_database_remote,
    db_local,
    db_remote,
):
    """link_dbs must not error when called twice against the same pair, since
    a real `diff` command needs to be re-runnable against the same branch as
    someone edits the modify database."""
    local = DBConnection(
        host=db_local.info.host,
        port=db_local.info.port,
        username=db_local.info.user,
        dbname=db_local.info.dbname,
    )
    remote = DBConnection(
        host=db_remote.info.host,
        port=db_remote.info.port,
        username=db_remote.info.user,
        dbname=db_remote.info.dbname,
    )

    link_dbs(local, remote)
    link_dbs(local, remote)


def test_link_dbs_includes_password_in_user_mapping_when_given(
    setup_database_local,
    setup_database_remote,
    db_local,
    db_remote,
):
    """CREATE USER MAPPING must include the password option when the remote
    connection has one - Postgres requires it for non-superusers (e.g. Cloud
    SQL's own "postgres" role isn't a true superuser there), otherwise
    CREATE SERVER/IMPORT FOREIGN SCHEMA fails with "password is required"."""
    local = DBConnection(
        host=db_local.info.host,
        port=db_local.info.port,
        username=db_local.info.user,
        dbname=db_local.info.dbname,
    )
    remote = DBConnection(
        host=db_remote.info.host,
        port=db_remote.info.port,
        username=db_remote.info.user,
        dbname=db_remote.info.dbname,
        password="irrelevant-for-trust-auth-but-must-not-error",
    )

    link_dbs(local, remote)

    with Session(setup_database_local) as session:
        mapping_options = session.execute(
            sa.text(
                "SELECT umoptions FROM pg_user_mappings "
                "WHERE srvname = 'remote_server' AND usename = current_user"
            )
        ).scalar()

    assert any(opt.startswith("password=") for opt in mapping_options)


def test_apply_patch_file(
    setup_database_local,
    setup_database_remote,
    linked_databases,
    tmp_path,
    db_remote,
):
    """apply_patch_file must read a patch saved to disk and apply it to the
    target database, exercising the file-based generate -> apply handoff."""
    local_engine = setup_database_local
    remote_engine = setup_database_remote

    local_metadata = MetaData()
    local_metadata.reflect(bind=local_engine)
    users_table = local_metadata.tables['users']

    with Session(local_engine) as session:
        session.execute(users_table.delete().where(users_table.c.id > 7))
        session.commit()

    diff_patch = make_diff_patch(local_engine)
    patch_path = tmp_path / "patch.sql"
    patch_path.write_text(diff_patch)

    remote_connection = DBConnection(
        host=db_remote.info.host,
        port=db_remote.info.port,
        username=db_remote.info.user,
        dbname=db_remote.info.dbname,
    )
    apply_patch_file(patch_path, remote_connection)

    with Session(remote_engine) as session:
        remaining = session.execute(sa.select(users_table.c.id)).scalars().all()

    assert sorted(remaining) == [1, 2, 3, 4, 5, 6, 7]