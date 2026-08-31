import os
import uuid

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, HumanMessage
from psycopg import connect

from app.agent.graph import build_graph
from app.agent.memory import MemoryStore
from app.auth.session import SESSION_COOKIE, create_session
from app.config import load_settings
from app.main import create_app

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
def client():
    with TestClient(create_app(load_settings(ENV))) as c:
        with connect(DB_URL, autocommit=True) as conn:
            conn.execute("DELETE FROM conversations")
            conn.execute("DELETE FROM auth_sessions")
            conn.execute("DELETE FROM users")
            conn.execute("INSERT INTO users (sub, email) VALUES ('user-a','a@x.com')")
            conn.execute("INSERT INTO users (sub, email) VALUES ('user-b','b@x.com')")
        yield c


class FakeMemoryClient:
    """mem0 stand-in: the transcript comes from the checkpointer, not memory."""

    def search(self, query, filters=None, **kwargs):
        return []

    def add(self, messages, user_id=None, **kwargs):
        return None


class ScriptedModel:
    def invoke(self, messages):
        return AIMessage("hi there")


def _run_a_turn(client, thread_id: str, text: str) -> None:
    """Drive a real turn through the app's own checkpointer.

    The model and mem0 are faked because neither is reachable from a test, but
    the checkpointer is the app's: the transcript the endpoint reads back is the
    one a real run would have written, in the same tables.

    `invoke` from the test thread rather than `ainvoke`: the async saver only
    permits its synchronous interface from off the event loop, which is exactly
    where both this call and the endpoint's worker thread sit.
    """
    graph = build_graph(
        MemoryStore(FakeMemoryClient()),
        ScriptedModel(),
        client.app.state.graph.checkpointer,
    )
    graph.invoke(
        {"messages": [HumanMessage(text)], "user_id": "user-a", "memory_enabled": False},
        {"configurable": {"thread_id": thread_id}},
    )


def _login(client, user_sub):
    client.cookies.set(
        SESSION_COOKIE, create_session(client.app.state.pool, user_sub, ENV["SESSION_SECRET"])
    )


def test_empty_conversation_returns_no_messages(client):
    _login(client, "user-a")
    convo = client.post("/conversations").json()
    response = client.get(f"/conversations/{convo['id']}/messages")
    assert response.status_code == 200
    assert response.json() == {"messages": []}


def test_history_returns_the_transcript(client):
    _login(client, "user-a")
    convo = client.post("/conversations").json()
    _run_a_turn(client, convo["id"], "hello")
    messages = client.get(f"/conversations/{convo['id']}/messages").json()["messages"]
    assert messages[0] == {"role": "user", "content": "hello"}
    assert messages[1]["role"] == "assistant"


def test_history_is_scoped_to_its_own_thread(client):
    """A second conversation must not inherit the first one's transcript."""
    _login(client, "user-a")
    first = client.post("/conversations").json()
    second = client.post("/conversations").json()
    _run_a_turn(client, first["id"], "hello")
    assert client.get(f"/conversations/{second['id']}/messages").json() == {"messages": []}


def test_a_malformed_conversation_id_is_404(client):
    _login(client, "user-a")
    assert client.get("/conversations/not-a-uuid/messages").status_code == 404


def test_history_requires_authentication(client):
    response = client.get(f"/conversations/{uuid.uuid4()}/messages")
    assert response.status_code == 401


def test_history_rejects_another_users_conversation(client):
    _login(client, "user-a")
    convo = client.post("/conversations").json()
    client.cookies.clear()
    _login(client, "user-b")
    assert client.get(f"/conversations/{convo['id']}/messages").status_code == 404


# --- updated_at and the title, set after each run ---------------------------
# The run itself goes through the AG-UI endpoint, so the bump lives in that
# middleware. An echo handler stands in for the adapter, which is the only way
# to complete a run without a reachable model.


@pytest.fixture
def echo_client(client):
    from fastapi import FastAPI, Request

    from app.agui.routes import AGENT_PATH, add_agent_gate

    echo = FastAPI()
    add_agent_gate(echo)

    @echo.post(AGENT_PATH)
    async def _echo(request: Request) -> dict:
        return await request.json()

    echo.state.pool = client.app.state.pool
    echo.state.settings = client.app.state.settings
    with TestClient(echo) as c:
        yield c


def _run(echo_client, thread_id: str, *contents: str):
    from app.agui.routes import AGENT_PATH

    return echo_client.post(
        AGENT_PATH,
        json={
            "threadId": thread_id,
            "messages": [{"role": "user", "content": c} for c in contents],
        },
    )


def test_a_run_titles_the_conversation_from_the_first_message(echo_client, client):
    _login(client, "user-a")
    echo_client.cookies = client.cookies
    convo = client.post("/conversations").json()
    assert convo["title"] is None

    _run(echo_client, convo["id"], "I am vegetarian")

    assert client.get("/conversations").json()[0]["title"] == "I am vegetarian"


def test_later_runs_leave_the_title_alone(echo_client, client):
    _login(client, "user-a")
    echo_client.cookies = client.cookies
    convo = client.post("/conversations").json()

    _run(echo_client, convo["id"], "I am vegetarian")
    _run(echo_client, convo["id"], "something else entirely")

    assert client.get("/conversations").json()[0]["title"] == "I am vegetarian"


def test_a_run_bumps_updated_at(echo_client, client):
    """The sidebar is ordered by `updated_at`, so a run has to move its thread."""
    _login(client, "user-a")
    echo_client.cookies = client.cookies
    older = client.post("/conversations").json()
    newer = client.post("/conversations").json()
    assert [row["id"] for row in client.get("/conversations").json()] == [
        newer["id"],
        older["id"],
    ]

    _run(echo_client, older["id"], "hello")

    assert [row["id"] for row in client.get("/conversations").json()] == [
        older["id"],
        newer["id"],
    ]


def test_a_rejected_run_does_not_touch_the_conversation(echo_client, client):
    """The bump sits behind the gate: another user's run must leave no trace."""
    _login(client, "user-a")
    convo = client.post("/conversations").json()
    client.cookies.clear()
    _login(client, "user-b")
    echo_client.cookies = client.cookies

    assert _run(echo_client, convo["id"], "hijacked").status_code == 404

    client.cookies.clear()
    _login(client, "user-a")
    assert client.get("/conversations").json()[0]["title"] is None
