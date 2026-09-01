import json
import os
import uuid

import pytest
from fastapi.testclient import TestClient
from psycopg import connect

from app.agui.agent import RUN_FAILED_MESSAGE
from app.agui.routes import AGENT_PATH
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


def _login(client, user_sub: str) -> None:
    from app.auth.session import SESSION_COOKIE, create_session

    cookie = create_session(client.app.state.pool, user_sub, ENV["SESSION_SECRET"])
    client.cookies.set(SESSION_COOKIE, cookie)


def test_agent_endpoint_requires_authentication(client):
    response = client.post(AGENT_PATH, json={"threadId": str(uuid.uuid4()), "messages": []})
    assert response.status_code == 401


def test_agent_endpoint_rejects_another_users_thread(client):
    _login(client, "user-a")
    owned = client.post("/conversations").json()
    client.cookies.clear()
    _login(client, "user-b")
    response = client.post(AGENT_PATH, json={"threadId": owned["id"], "messages": []})
    assert response.status_code == 404


def test_agent_endpoint_rejects_unknown_thread(client):
    _login(client, "user-a")
    response = client.post(AGENT_PATH, json={"threadId": str(uuid.uuid4()), "messages": []})
    assert response.status_code == 404


def test_agent_endpoint_requires_a_thread_id(client):
    _login(client, "user-a")
    assert client.post(AGENT_PATH, json={"messages": []}).status_code == 400


# --- identity on the run ----------------------------------------------------
# The graph scopes every mem0 read and write by `user_id`, so the gate has to
# set it from the session. A separate app puts an echo handler where the AG-UI
# adapter normally sits, which is the only way to see what the adapter receives.


@pytest.fixture
def echo_client(client):
    from fastapi import FastAPI, Request

    from app.agui.routes import add_agent_gate

    echo = FastAPI()
    add_agent_gate(echo)

    @echo.post(AGENT_PATH)
    async def _echo(request: Request) -> dict:
        return await request.json()

    echo.state.pool = client.app.state.pool
    echo.state.settings = client.app.state.settings
    with TestClient(echo) as c:
        yield c


def test_the_run_carries_the_session_user(echo_client, client):
    _login(client, "user-a")
    echo_client.cookies = client.cookies
    convo = client.post("/conversations").json()

    body = echo_client.post(AGENT_PATH, json={"threadId": convo["id"], "messages": []}).json()

    assert body["state"]["user_id"] == "user-a"
    assert body["threadId"] == convo["id"]


def test_a_client_cannot_name_its_own_user_id(echo_client, client):
    """Otherwise the endpoint would hand out another user's memories."""
    _login(client, "user-a")
    echo_client.cookies = client.cookies
    convo = client.post("/conversations").json()

    body = echo_client.post(
        AGENT_PATH,
        json={"threadId": convo["id"], "messages": [], "state": {"user_id": "user-b"}},
    ).json()

    assert body["state"]["user_id"] == "user-a"


# --- a failed run is reported, not swallowed --------------------------------
# `call_model` retries once and then lets the exception propagate. The adapter
# emits RUN_ERROR only for in-band "error" events, so a raise ends the SSE
# stream with no terminal event at all and the UI shows a reply that never
# arrives. `ReportingLangGraphAgent` is what makes that a reported failure the
# error state and its Retry action can key off.


@pytest.fixture
def failing_client(client):
    """An app whose model always raises, mounted behind the real adapter."""
    from fastapi import FastAPI
    from langgraph.checkpoint.memory import InMemorySaver

    from app.agent.graph import build_graph
    from app.agent.memory import MemoryStore
    from app.agui.routes import add_agent_gate, mount_agent_endpoint

    class ExplodingModel:
        def invoke(self, messages):
            raise RuntimeError("upstream is down")

    class SilentClient:
        def search(self, *args, **kwargs):
            return []

        def add(self, *args, **kwargs):
            return None

    app = FastAPI()
    add_agent_gate(app)
    app.state.pool = client.app.state.pool
    app.state.settings = client.app.state.settings
    mount_agent_endpoint(
        app, build_graph(MemoryStore(SilentClient()), ExplodingModel(), InMemorySaver())
    )
    with TestClient(app) as c:
        yield c


def test_a_failed_run_ends_with_run_error(failing_client, client):
    _login(client, "user-a")
    failing_client.cookies = client.cookies
    convo = client.post("/conversations").json()

    response = failing_client.post(
        AGENT_PATH,
        json={
            "threadId": convo["id"],
            "runId": str(uuid.uuid4()),
            "messages": [{"id": "m1", "role": "user", "content": "hello"}],
            "tools": [],
            "context": [],
            "state": {},
            "forwardedProps": {},
        },
    )

    assert response.status_code == 200
    events = [
        json.loads(line.removeprefix("data: "))
        for line in response.text.splitlines()
        if line.startswith("data: ")
    ]
    assert events, "the adapter streamed nothing at all"
    assert events[-1]["type"] == "RUN_ERROR"
    # The exception text reaches a browser; the detail stays in the server log.
    assert events[-1]["message"] == RUN_FAILED_MESSAGE
    assert "upstream is down" not in response.text
