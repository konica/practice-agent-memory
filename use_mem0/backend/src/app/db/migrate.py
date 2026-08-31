from pathlib import Path

from langgraph.checkpoint.postgres import PostgresSaver
from psycopg import connect

SCHEMA_PATH = Path(__file__).with_name("schema.sql")


def run_migrations(database_url: str) -> None:
    """Apply the application schema, then LangGraph's checkpointer schema.

    PostgresSaver.setup() must be called explicitly by the application; the
    checkpointer does not create its own tables lazily, and the graph fails on
    missing tables without it.
    """
    with connect(database_url, autocommit=True) as conn:
        conn.execute(SCHEMA_PATH.read_text())

    with PostgresSaver.from_conn_string(database_url) as checkpointer:
        checkpointer.setup()
