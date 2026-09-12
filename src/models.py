from dataclasses import dataclass
from enum import StrEnum
import sqlalchemy as sa
from sqlalchemy.sql.compiler import SQLCompiler


class NoPrimaryKeyError(Exception):
    """Raised when a table has no primary key, so it can't be safely diffed."""


@dataclass
class DBConnection:
    host: str
    port: int
    username: str
    dbname: str
    password: str | None = None


class OperationType(StrEnum):
    DELETE = "delete"
    UPDATE = "update"
    INSERT = "insert"


@dataclass
class DatabaseOperation:
    table_name: str
    operation: OperationType
    statement: SQLCompiler
    


@dataclass
class TablePair:
    prod: sa.Table
    clone: sa.Table