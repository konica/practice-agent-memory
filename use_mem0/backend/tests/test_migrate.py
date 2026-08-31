import os
import pytest
from psycopg import connect
from app.db.migrate import run_migrations

DB_URL = os.environ.get("TEST_DATABASE_URL", "postgresql://app:app@localhost:5432/app")


@pytest.fixture
def clean_db():
    with connect(DB_URL, autocommit=True) as conn:
        conn.execute("DROP TABLE IF EXISTS conversations, auth_sessions, users CASCADE")
    yield DB_URL


def test_migrations_create_application_tables(clean_db):
    run_migrations(clean_db)
    with connect(clean_db) as conn:
        rows = conn.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema='public'"
        ).fetchall()
    names = {r[0] for r in rows}
    assert {"users", "auth_sessions", "conversations"} <= names


def test_migrations_create_checkpointer_tables(clean_db):
    run_migrations(clean_db)
    with connect(clean_db) as conn:
        rows = conn.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema='public'"
        ).fetchall()
    names = {r[0] for r in rows}
    assert "checkpoints" in names, "PostgresSaver.setup() must run during migrations"


def test_migrations_are_idempotent(clean_db):
    run_migrations(clean_db)
    run_migrations(clean_db)
