
from sqlalchemy import URL, create_engine


def setup_engine(host, port, db_name, username):
    url_object = URL.create(
        "postgresql+psycopg2",
        username=username,
        host=host,
        database=db_name,
        port=port
    )
    engine = create_engine(url_object) 
    return engine

