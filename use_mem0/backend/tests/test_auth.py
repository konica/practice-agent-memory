import os
import uuid

import pytest
from psycopg import connect

from app.auth.session import create_session, resolve_session
from app.db.engine import open_pool
from app.db.migrate import run_migrations

DB_URL = os.environ.get("TEST_DATABASE_URL", "postgresql://app:app@localhost:5432/app")
SECRET = "t" * 32


@pytest.fixture
def pool():
    run_migrations(DB_URL)
    with connect(DB_URL, autocommit=True) as conn:
        conn.execute("DELETE FROM auth_sessions")
        conn.execute("DELETE FROM users")
        conn.execute(
            "INSERT INTO users (sub, email) VALUES (%s, %s)",
            ("google-sub-1", "a@example.com"),
        )
    p = open_pool(DB_URL)
    yield p
    p.close()


def test_session_roundtrip(pool):
    cookie = create_session(pool, "google-sub-1", SECRET)
    assert resolve_session(pool, cookie, SECRET) == "google-sub-1"


def test_tampered_cookie_is_rejected(pool):
    cookie = create_session(pool, "google-sub-1", SECRET)
    assert resolve_session(pool, cookie + "x", SECRET) is None


def test_revoked_session_is_rejected(pool):
    cookie = create_session(pool, "google-sub-1", SECRET)
    with pool.connection() as conn:
        conn.execute("UPDATE auth_sessions SET revoked_at = now()")
    assert resolve_session(pool, cookie, SECRET) is None


def test_expired_session_is_rejected(pool):
    cookie = create_session(pool, "google-sub-1", SECRET)
    with pool.connection() as conn:
        conn.execute("UPDATE auth_sessions SET expires_at = now() - interval '1 hour'")
    assert resolve_session(pool, cookie, SECRET) is None


def test_unknown_session_id_is_rejected(pool):
    from itsdangerous import URLSafeSerializer

    forged = URLSafeSerializer(SECRET, salt="session").dumps(str(uuid.uuid4()))
    assert resolve_session(pool, forged, SECRET) is None
