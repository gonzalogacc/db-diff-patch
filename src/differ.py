from copy import copy
from dataclasses import dataclass

import sqlalchemy as sa
from sqlalchemy.orm import Session

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



def link_dbs(local: DBConnection, remote: DBConnection):
    """Function to connect the pairs."""
    # Here i need to log into the local engine and link the remote 
    local_engine = setup_engine(
        local.host,
        local.port,
        local.dbname,
        local.username
    )

    with Session(local_engine) as session:

        session.execute(sa.text("CREATE EXTENSION IF NOT EXISTS postgres_fdw"))
        session.execute(
            sa.text(f"CREATE SERVER remote_server foreign data wrapper postgres_fdw options (host '{remote.host}', port '{remote.port}', dbname '{remote.dbname}')"),
            {"host": remote.host, "port": remote.port, "dbname": remote.dbname}
        ) 
        session.execute(
            sa.text("CREATE USER MAPPING FOR user SERVER remote_server OPTIONS (user :user)"),
            {"user": remote.username}
        )
        session.execute(sa.text("CREATE SCHEMA remote_server_schema"))
        session.execute(sa.text("IMPORT FOREIGN SCHEMA public from server remote_server into remote_server_schema"))

        session.commit()


def check_deletions(engine, local_table, remote_table) -> list:
    """Find the deletion between tables."""
    table_key = [c for c in local_table.columns][0].name

    # Deleted are fields in the remote but not in the local db
    with Session(engine) as session:
        # Returns all ids in the remote that are not in the local. They were deleted.
        stmt = sa.select(getattr(remote_table.c, table_key)).except_(sa.select(getattr(local_table.c, table_key)))
        deleted_rows = session.execute(stmt).all()

        deletion_list = []
        for d in deleted_rows:
            deleted_id = d[0]
            stmt = sa.delete(local_table).where(getattr(local_table.c, table_key) == deleted_id)
            deletion_list.append(stmt.compile(engine, compile_kwargs={"literal_binds": True}))

    return deletion_list


def check_insertions(engine, local_table, remote_table) -> list:
    """Find the insertions between tables."""
    table_key = [c for c in local_table.columns][0].name
    with Session(engine) as session:
        stmt = sa.select(getattr(local_table.c, table_key)).except_(sa.select(getattr(remote_table.c, table_key)))
        inserted_rows = session.execute(stmt).all() 
        
        insert_list = [] 
        for ir in inserted_rows:
            inserted_id = ir[0]
            stmt = sa.select(local_table).where(getattr(local_table.c, table_key) == inserted_id)
            response = session.execute(stmt).one()

            # remap columns
            data = {}
            for i, c in enumerate(local_table.c):
                data[c.name] = response[i]
            
            stmt = sa.insert(local_table).values(**data)
            insert_list.append(stmt.compile(engine, compile_kwargs={"literal_binds": True}))

    return insert_list


def check_updates(engine, local_table, remote_table) -> list:
    table_key = [c for c in local_table.columns][0].name
    # Returns all ids in the remote that are not in the local. They were deleted.
    with Session(engine) as session:
        stmt = sa.select(remote_table).except_(sa.select(local_table))
        remote_exclusive = session.execute(stmt).all() 

        stmt = sa.select(local_table).except_(sa.select(remote_table))
        local_exclusive = session.execute(stmt).all() 
        
        # If a row is shared by the id then it's modified in both
        shared_result = set([x[0] for x in remote_exclusive]) & set([x[0] for x in local_exclusive])
        
        update_list = []
        for sr in shared_result:
            local_version = [obj for obj in local_exclusive if obj[0] == sr][0]
            remote_version = [obj for obj in remote_exclusive if obj[0] == sr][0]
            ## Compare field by field to get the differences and construct the sql update
            changed = {}
            for name, remote_field, local_field in zip(remote_table.c, remote_version, local_version):
                if name.name == table_key:
                    id_value = local_field
                    continue
                if remote_field != local_field:
                    changed[name.name] = local_field
            stmt = sa.update(local_table).where(getattr(local_table.c, table_key) == id_value).values(**changed)
            update_list.append(stmt.compile(engine, compile_kwargs={"literal_binds": True}))

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


def make_all_checks(engine, prod_table, clone_table) -> list[str]:
    return [
        *check_deletions(engine, clone_table, prod_table),
        *check_insertions(engine, clone_table, prod_table),
        *check_updates(engine, clone_table, prod_table)
    ]
