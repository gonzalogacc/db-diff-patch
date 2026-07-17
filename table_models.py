
from sqlalchemy import Integer, String, Table, MetaData, Column

local_metadata = MetaData(schema='public')

local_users = Table(
    "users",
    local_metadata,
    Column('id', Integer, primary_key=True),
    Column('username', String),
    Column('email', String),
    extend_existing=True,
)

remote_metadata = MetaData(schema='remote_server_schema')
remote_users = Table(
    "users",
    remote_metadata,
    Column('id', Integer, primary_key=True),
    Column('username', String),
    Column('email', String),
    extend_existing=True,
)
