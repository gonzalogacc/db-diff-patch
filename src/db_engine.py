
from sqlalchemy import URL, Engine, create_engine


def setup_engine(host, port, db_name, username, password=None) -> Engine:
    url_object = URL.create(
        "postgresql+psycopg2",
        username=username,
        host=host,
        database=db_name,
        port=port,
        password=password
    )
    engine = create_engine(url_object)
    return engine

