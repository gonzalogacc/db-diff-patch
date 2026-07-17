from copy import copy
from dataclasses import dataclass

from sqlalchemy import MetaData, Table, create_engine
from sqlalchemy.orm import Session
# from table_models import local_users, remote_users
import sqlalchemy as sa

from db_engine import setup_engine
from differ import get_tables, make_all_checks


def main():
    engine = setup_engine(
        host="localhost",
        port=5432,
        db_name="test",
        username="postgres",
    )
    table_pairs = get_tables(engine)

    print("BEGIN")
    for tp in table_pairs:
        if tp.prod.name in ['orders']:
            continue
        local_users = tp.clone
        remote_users = tp.prod
        for x in make_all_checks(engine, local_users, remote_users):
            print(x)
    print("COMMIT")

    # print()
    # print("local")
    # print("------------------------------")
    # with Session(engine) as session:
    #     stmt = sa.select(local_users)
    #     response = session.execute(stmt).all()
    #     for r in response:
    #         print(r)
    
    # print()
    # print("remote")
    # print("------------------------------")
    # with Session(engine) as session:
    #     stmt = sa.select(remote_users)
    #     response = session.execute(stmt).all()
    #     for r in response:
    #         print(r)


    
    
            
if __name__ == "__main__":
    main()
