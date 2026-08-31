from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool


def open_pool(database_url: str) -> ConnectionPool:
    pool = ConnectionPool(
        conninfo=database_url,
        kwargs={"row_factory": dict_row, "autocommit": True},
        open=True,
    )
    pool.wait()
    return pool
