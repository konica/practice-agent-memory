import os

import pytest
from fastapi.testclient import TestClient
from psycopg import OperationalError

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


def test_health_endpoint():
    with TestClient(create_app(load_settings(ENV))) as client:
        assert client.get("/health").json() == {"status": "ok"}


def test_me_requires_authentication():
    with TestClient(create_app(load_settings(ENV))) as client:
        assert client.get("/auth/me").status_code == 401


def test_cors_allows_the_frontend_origin_with_credentials():
    with TestClient(create_app(load_settings(ENV))) as client:
        response = client.options(
            "/auth/me",
            headers={
                "Origin": "http://localhost:5173",
                "Access-Control-Request-Method": "GET",
            },
        )
    assert response.headers["access-control-allow-origin"] == "http://localhost:5173"
    assert response.headers["access-control-allow-credentials"] == "true"


def test_startup_fails_loudly_when_postgres_is_unavailable():
    """A chat that cannot persist is broken, not degraded: refuse to serve."""
    settings = load_settings({**ENV, "DATABASE_URL": "postgresql://app:app@localhost:1/app"})
    with pytest.raises(OperationalError):
        with TestClient(create_app(settings)):
            pass
