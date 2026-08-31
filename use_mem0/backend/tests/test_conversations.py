import os
import uuid

import pytest
from psycopg import connect

from app.conversations.ownership import owns_conversation
from app.conversations.store import (
    create_conversation,
    delete_conversation,
    list_conversations,
    rename_conversation,
    touch_conversation,
)
from app.db.engine import open_pool
from app.db.migrate import run_migrations

DB_URL = os.environ.get("TEST_DATABASE_URL", "postgresql://app:app@localhost:5432/app")

ENV = {
    "OPENAI_API_KEY": "sk-test",
    "MEM0_API_KEY": "m0-test",
    "LANGSMITH_API_KEY": "ls-test",
    "LANGSMITH_PROJECT": "test",
    "GOOGLE_CLIENT_ID": "gid",
    "GOOGLE_CLIENT_SECRET": "gsecret",
    "DATABASE_URL": DB_URL,
    "SESSION_SECRET": "t" * 32,
}


@pytest.fixture
def pool():
    run_migrations(DB_URL)
    with connect(DB_URL, autocommit=True) as conn:
        conn.execute("DELETE FROM conversations")
        conn.execute("DELETE FROM auth_sessions")
        conn.execute("DELETE FROM users")
        conn.execute("INSERT INTO users (sub, email) VALUES ('user-a', 'a@example.com')")
        conn.execute("INSERT INTO users (sub, email) VALUES ('user-b', 'b@example.com')")
    p = open_pool(DB_URL)
    yield p
    p.close()


def test_create_then_list(pool):
    created = create_conversation(pool, "user-a")
    rows = list_conversations(pool, "user-a")
    assert [r["id"] for r in rows] == [created["id"]]


def test_list_is_scoped_to_the_owner(pool):
    create_conversation(pool, "user-a")
    assert list_conversations(pool, "user-b") == []


def test_list_is_newest_first(pool):
    first = create_conversation(pool, "user-a")
    second = create_conversation(pool, "user-a")
    touch_conversation(pool, first["id"], None)
    assert [r["id"] for r in list_conversations(pool, "user-a")] == [
        first["id"],
        second["id"],
    ]


def test_title_is_set_once_from_the_first_message(pool):
    convo = create_conversation(pool, "user-a")
    touch_conversation(pool, convo["id"], "I am vegetarian")
    touch_conversation(pool, convo["id"], "something else entirely")
    assert list_conversations(pool, "user-a")[0]["title"] == "I am vegetarian"


def test_long_titles_are_truncated_to_50_chars(pool):
    convo = create_conversation(pool, "user-a")
    touch_conversation(pool, convo["id"], "x" * 200)
    assert len(list_conversations(pool, "user-a")[0]["title"]) == 50


def test_ownership_rejects_another_users_conversation(pool):
    convo = create_conversation(pool, "user-a")
    assert owns_conversation(pool, convo["id"], "user-a") is True
    assert owns_conversation(pool, convo["id"], "user-b") is False


def test_ownership_rejects_unknown_conversation(pool):
    assert owns_conversation(pool, str(uuid.uuid4()), "user-a") is False


def test_rename_is_scoped_to_the_owner(pool):
    convo = create_conversation(pool, "user-a")
    rename_conversation(pool, convo["id"], "user-b", "hijacked")
    assert list_conversations(pool, "user-a")[0]["title"] != "hijacked"


def test_delete_removes_the_conversation(pool):
    convo = create_conversation(pool, "user-a")
    delete_conversation(pool, convo["id"], "user-a")
    assert list_conversations(pool, "user-a") == []


def test_delete_is_scoped_to_the_owner(pool):
    convo = create_conversation(pool, "user-a")
    delete_conversation(pool, convo["id"], "user-b")
    assert len(list_conversations(pool, "user-a")) == 1


def test_delete_removes_the_checkpointer_rows(pool):
    """Orphaned checkpoint rows would still hold the deleted message content."""
    convo = create_conversation(pool, "user-a")
    thread_id = convo["id"]
    with pool.connection() as conn:
        conn.execute(
            "INSERT INTO checkpoints (thread_id, checkpoint_id, checkpoint) "
            "VALUES (%s, 'c1', '{}'::jsonb)",
            (thread_id,),
        )
        conn.execute(
            "INSERT INTO checkpoint_blobs (thread_id, channel, version, type, blob) "
            "VALUES (%s, 'messages', '1', 'msgpack', 'x'::bytea)",
            (thread_id,),
        )
        conn.execute(
            "INSERT INTO checkpoint_writes "
            "(thread_id, checkpoint_id, task_id, idx, channel, blob) "
            "VALUES (%s, 'c1', 't1', 0, 'messages', 'x'::bytea)",
            (thread_id,),
        )

    delete_conversation(pool, thread_id, "user-a")

    with pool.connection() as conn:
        for table in ("checkpoints", "checkpoint_blobs", "checkpoint_writes"):
            remaining = conn.execute(
                f"SELECT count(*) AS n FROM {table} WHERE thread_id = %s", (thread_id,)
            ).fetchone()["n"]
            assert remaining == 0, f"{table} still holds rows for the deleted thread"


# --- HTTP layer -------------------------------------------------------------
# The 404-never-403 guarantee is a status code, so it can only be observed here.


@pytest.fixture
def client(pool):
    from fastapi.testclient import TestClient

    from app.config import load_settings
    from app.main import create_app

    with TestClient(create_app(load_settings(ENV))) as c:
        yield c


def sign_in(client, pool, user_sub: str) -> None:
    """Give the client the cookie a completed OAuth callback would have set."""
    from app.auth.session import SESSION_COOKIE, create_session

    client.cookies.set(SESSION_COOKIE, create_session(pool, user_sub, ENV["SESSION_SECRET"]))


def test_routes_require_authentication(client):
    assert client.get("/conversations").status_code == 401


def test_create_and_list_over_http(client, pool):
    sign_in(client, pool, "user-a")
    created = client.post("/conversations").json()
    listed = client.get("/conversations").json()
    assert [row["id"] for row in listed] == [created["id"]]


def test_another_users_conversation_is_404_not_403(client, pool):
    convo = create_conversation(pool, "user-a")
    sign_in(client, pool, "user-b")

    assert client.get("/conversations").json() == []
    assert (
        client.patch(
            f"/conversations/{convo['id']}", json={"title": "hijacked"}
        ).status_code
        == 404
    )
    assert client.delete(f"/conversations/{convo['id']}").status_code == 404
    assert len(list_conversations(pool, "user-a")) == 1


def test_a_malformed_conversation_id_is_404(client, pool):
    """The id comes from a URL, so a guess must not reach Postgres as a bad UUID."""
    sign_in(client, pool, "user-a")
    assert client.delete("/conversations/not-a-uuid").status_code == 404
