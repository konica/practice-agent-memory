# mem0 Chatbot Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a personal-assistant chatbot in `use_mem0/` that recalls user facts across
conversations, integrating LangGraph, mem0, LangSmith, and AG-UI end to end.

**Architecture:** A FastAPI backend serves Google OAuth routes, a conversation registry, and
an AG-UI endpoint wrapping a three-node LangGraph graph
(`retrieve_memories → call_model → write_memories`). Conversation state persists in Postgres
via LangGraph's checkpointer; memories persist in mem0 Platform scoped by user. A React +
Vite frontend uses assistant-ui to render the chat and rehydrates transcripts through a
history adapter that calls our own backend.

**Tech Stack:** Python 3.11+, FastAPI, LangGraph, `langgraph-checkpoint-postgres`, `mem0ai`,
`langsmith`, OpenAI, Postgres 16 (Docker), React 18, Vite, TypeScript,
`@assistant-ui/react`, `@assistant-ui/react-ag-ui`, `ag-ui-protocol`, `ag-ui-langgraph`,
**Tailwind CSS + shadcn/ui**.

**Spec:** `docs/superpowers/specs/2026-08-30-mem0-chatbot-design.md`

**Mockup:** https://claude.ai/code/artifact/a1405627-733c-434c-ab82-3ccb4a3b329e

## Global Constraints

Every task's requirements implicitly include this section.

- **All application code lives under `use_mem0/`.** Do not modify `use_graphiti/`.
- **Package versions** (verified; pin these exact floors):
  `ag-ui-protocol==0.1.21`, `ag-ui-langgraph==0.0.44`, `mem0ai==2.0.19`,
  `langsmith==0.11.2`, `langgraph-checkpoint-postgres==3.1.2`,
  `@assistant-ui/react@0.15.17`, `@assistant-ui/react-ag-ui@0.0.57`, `@ag-ui/client@0.0.58`.
- **`PostgresSaver.setup()` MUST be called once** before first graph use, or the graph fails
  on missing tables.
- **mem0 and LangSmith failures must never break the chat.** Memory search/write failures
  are logged and swallowed. Only OpenAI failures surface to the user.
- **The mem0 `user_id` is the Google `sub` claim**, never the email. Email is stored for
  display only.
- **Ownership checks return 404, never 403** — a 403 confirms another user's conversation
  exists.
- **mem0 client calls need `@traceable`** to appear in LangSmith traces; they are plain SDK
  calls, not LangChain runnables, so they are not auto-traced.
- **Secrets only in `.env`.** Never commit real keys. `.env` must be gitignored.
- **Design tokens from the mockup** — the canonical list is in Task 11 Step 3, supplied by
  the designer from the mockup source. Use it verbatim; do not re-derive values by inspecting
  the rendered mockup, which produced three wrong values on a previous attempt.
  Two traps in particular: **radius is a scale, not one value**, and **bubble radius is
  directional** (`14px 14px 2px 14px` user / `14px 14px 14px 2px` assistant — the sharp
  corner is the tail and mirrors the alignment side).
- **Font is Plus Jakarta Sans** (Google Fonts, weights 400/500/600/700/800), fallback
  `system-ui, sans-serif`. A deliberate choice — not Inter, Roboto, or Arial.
- **Styling is Tailwind utility classes over shadcn/ui components.** The mockup's palette is
  applied by overriding shadcn's CSS variable *values* while keeping its variable *names*, so
  every generated component inherits the design automatically. Never inline `style` objects,
  and never hand-roll a primitive (dialog, dropdown, scroll area) that shadcn already
  provides — those bring focus management and ARIA behaviour with them.
- **shadcn component source in `src/components/ui/` is ours to edit.** It is copied in, not a
  vendored dependency; editing it directly is the intended workflow.
- **Delete is always an explicit confirmation step.** Never a one-click destructive action,
  and never offer an undo affordance — the confirmation copy promises permanence.

---

## File Structure

```
use_mem0/
  docker-compose.yml            # app-postgres only
  .env.example
  README.md
  backend/
    pyproject.toml
    src/app/
      main.py                   # FastAPI app; mounts routers; startup wiring
      config.py                 # env loading + fail-fast validation
      db/
        engine.py               # connection pool
        schema.sql              # users, auth_sessions, conversations
        migrate.py              # applies schema.sql + PostgresSaver.setup()
      auth/
        google.py               # OAuth code exchange + id-token verification
        session.py              # cookie issue/verify; get_current_user dependency
        routes.py               # /auth/login|callback|me|logout
      conversations/
        store.py                # CRUD queries
        ownership.py            # shared resolve-or-404 dependency
        routes.py               # /conversations endpoints
      agent/
        memory.py               # mem0 wrapper: search/add, degradation, @traceable
        nodes.py                # retrieve_memories, call_model, write_memories
        graph.py                # graph assembly + checkpointer
        state.py                # ChatState TypedDict
      agui/
        routes.py               # /agent endpoint, session + ownership gated
    tests/
  frontend/
    package.json
    vite.config.ts
    components.json             # shadcn config
    src/
      main.tsx
      App.tsx                   # auth gate
      api.ts                    # typed fetch helpers
      index.css                 # tailwind base + mockup tokens over shadcn variables
      components/ui/            # shadcn source, copied in and ours to edit
      Login.tsx
      Workspace.tsx             # sidebar + chat layout
      ConversationList.tsx      # list, new, inline rename, delete dialog
      DeleteDialog.tsx
      Chat.tsx                  # AssistantRuntimeProvider + composed thread
      chat/                     # styled assistant-ui primitives (Task 14)
        ThreadView.tsx
        UserMessage.tsx
        AssistantMessage.tsx
        Composer.tsx
        LoadingSkeleton.tsx
      historyAdapter.ts         # adapters.history -> GET /conversations/{id}/messages
```

---

## Task 1: Project scaffold, config, and Postgres

**Files:**
- Create: `use_mem0/docker-compose.yml`
- Create: `use_mem0/.env.example`
- Create: `use_mem0/backend/pyproject.toml`
- Create: `use_mem0/backend/src/app/config.py`
- Create: `use_mem0/backend/src/app/__init__.py`
- Test: `use_mem0/backend/tests/test_config.py`

**Interfaces:**
- Consumes: nothing (first task)
- Produces: `Settings` dataclass with fields `openai_api_key: str`,
  `mem0_api_key: str`, `langsmith_api_key: str`, `langsmith_project: str`,
  `google_client_id: str`, `google_client_secret: str`, `database_url: str`,
  `session_secret: str`, `memory_retrieval_enabled: bool`, `frontend_origin: str`;
  and `load_settings(env: Mapping[str, str]) -> Settings` which raises
  `MissingConfigError` listing every missing key.

- [ ] **Step 1: Write the failing test**

```python
# use_mem0/backend/tests/test_config.py
import pytest
from app.config import load_settings, MissingConfigError

COMPLETE_ENV = {
    "OPENAI_API_KEY": "sk-test",
    "MEM0_API_KEY": "m0-test",
    "LANGSMITH_API_KEY": "ls-test",
    "LANGSMITH_PROJECT": "mem0-chatbot",
    "GOOGLE_CLIENT_ID": "gid",
    "GOOGLE_CLIENT_SECRET": "gsecret",
    "DATABASE_URL": "postgresql://u:p@localhost:5432/app",
    "SESSION_SECRET": "s" * 32,
}


def test_loads_complete_env():
    settings = load_settings(COMPLETE_ENV)
    assert settings.openai_api_key == "sk-test"
    assert settings.database_url == "postgresql://u:p@localhost:5432/app"


def test_memory_toggle_defaults_true():
    assert load_settings(COMPLETE_ENV).memory_retrieval_enabled is True


def test_memory_toggle_reads_false():
    env = {**COMPLETE_ENV, "MEMORY_RETRIEVAL_ENABLED": "false"}
    assert load_settings(env).memory_retrieval_enabled is False


def test_missing_keys_raise_listing_all_of_them():
    env = {k: v for k, v in COMPLETE_ENV.items() if k not in ("MEM0_API_KEY", "DATABASE_URL")}
    with pytest.raises(MissingConfigError) as exc:
        load_settings(env)
    message = str(exc.value)
    assert "MEM0_API_KEY" in message
    assert "DATABASE_URL" in message
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd use_mem0/backend && pytest tests/test_config.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.config'`

- [ ] **Step 3: Write the implementation**

```python
# use_mem0/backend/src/app/config.py
from dataclasses import dataclass
from typing import Mapping

REQUIRED_KEYS = (
    "OPENAI_API_KEY",
    "MEM0_API_KEY",
    "LANGSMITH_API_KEY",
    "LANGSMITH_PROJECT",
    "GOOGLE_CLIENT_ID",
    "GOOGLE_CLIENT_SECRET",
    "DATABASE_URL",
    "SESSION_SECRET",
)


class MissingConfigError(RuntimeError):
    """Raised at startup when required environment variables are absent."""


@dataclass(frozen=True)
class Settings:
    openai_api_key: str
    mem0_api_key: str
    langsmith_api_key: str
    langsmith_project: str
    google_client_id: str
    google_client_secret: str
    database_url: str
    session_secret: str
    memory_retrieval_enabled: bool
    frontend_origin: str


def load_settings(env: Mapping[str, str]) -> Settings:
    missing = [key for key in REQUIRED_KEYS if not env.get(key)]
    if missing:
        raise MissingConfigError(
            "Missing required environment variables: " + ", ".join(sorted(missing))
        )
    return Settings(
        openai_api_key=env["OPENAI_API_KEY"],
        mem0_api_key=env["MEM0_API_KEY"],
        langsmith_api_key=env["LANGSMITH_API_KEY"],
        langsmith_project=env["LANGSMITH_PROJECT"],
        google_client_id=env["GOOGLE_CLIENT_ID"],
        google_client_secret=env["GOOGLE_CLIENT_SECRET"],
        database_url=env["DATABASE_URL"],
        session_secret=env["SESSION_SECRET"],
        memory_retrieval_enabled=env.get("MEMORY_RETRIEVAL_ENABLED", "true").lower()
        != "false",
        frontend_origin=env.get("FRONTEND_ORIGIN", "http://localhost:5173"),
    )
```

- [ ] **Step 4: Create the supporting project files**

```toml
# use_mem0/backend/pyproject.toml
[project]
name = "mem0-chatbot-backend"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = [
    "fastapi>=0.115",
    "uvicorn[standard]>=0.32",
    "psycopg[binary,pool]>=3.2",
    "langgraph>=0.2",
    "langgraph-checkpoint-postgres==3.1.2",
    "langchain-openai>=0.2",
    "langsmith==0.11.2",
    "mem0ai==2.0.19",
    "ag-ui-protocol==0.1.21",
    "ag-ui-langgraph==0.0.44",
    "httpx>=0.27",
    "itsdangerous>=2.2",
    "google-auth>=2.35",
]

[project.optional-dependencies]
dev = ["pytest>=8.3", "pytest-asyncio>=0.24"]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/app"]

[tool.pytest.ini_options]
pythonpath = ["src"]
asyncio_mode = "auto"
```

```yaml
# use_mem0/docker-compose.yml
services:
  app-postgres:
    image: postgres:16
    environment:
      POSTGRES_USER: app
      POSTGRES_PASSWORD: app
      POSTGRES_DB: app
    ports:
      - "5432:5432"
    volumes:
      - app-postgres-data:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U app"]
      interval: 5s
      retries: 10

volumes:
  app-postgres-data:
```

```bash
# use_mem0/.env.example
OPENAI_API_KEY=
MEM0_API_KEY=
LANGSMITH_TRACING=true
LANGSMITH_API_KEY=
LANGSMITH_PROJECT=mem0-chatbot
GOOGLE_CLIENT_ID=
GOOGLE_CLIENT_SECRET=
DATABASE_URL=postgresql://app:app@localhost:5432/app
SESSION_SECRET=change-me-to-32-plus-random-chars
MEMORY_RETRIEVAL_ENABLED=true
FRONTEND_ORIGIN=http://localhost:5173
```

Also create empty `use_mem0/backend/src/app/__init__.py`, and add `use_mem0/.env` to the
repository `.gitignore` if not already covered.

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd use_mem0/backend && pip install -e ".[dev]" && pytest tests/test_config.py -v`
Expected: 4 passed

- [ ] **Step 6: Verify Postgres starts**

Run: `cd use_mem0 && docker compose up -d && docker compose ps`
Expected: `app-postgres` healthy

- [ ] **Step 7: Commit**

```bash
git add use_mem0/
git commit -m "feat: scaffold mem0 chatbot backend with config and postgres"
```

---

## Task 2: Database schema and migrations

**Files:**
- Create: `use_mem0/backend/src/app/db/__init__.py`
- Create: `use_mem0/backend/src/app/db/engine.py`
- Create: `use_mem0/backend/src/app/db/schema.sql`
- Create: `use_mem0/backend/src/app/db/migrate.py`
- Test: `use_mem0/backend/tests/test_migrate.py`

**Interfaces:**
- Consumes: `Settings.database_url` from Task 1.
- Produces: `open_pool(database_url: str) -> ConnectionPool`;
  `run_migrations(database_url: str) -> None` which applies `schema.sql` **and** calls
  `PostgresSaver.setup()`. Tables: `users(sub TEXT PK, email TEXT, name TEXT, picture TEXT,
  created_at TIMESTAMPTZ)`, `auth_sessions(id UUID PK, user_sub TEXT, created_at, expires_at,
  revoked_at)`, `conversations(id UUID PK, user_sub TEXT, title TEXT, created_at, updated_at,
  archived_at)`.

- [ ] **Step 1: Write the failing test**

```python
# use_mem0/backend/tests/test_migrate.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd use_mem0/backend && pytest tests/test_migrate.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.db'`

- [ ] **Step 3: Write the schema**

```sql
-- use_mem0/backend/src/app/db/schema.sql
CREATE TABLE IF NOT EXISTS users (
    sub         TEXT PRIMARY KEY,
    email       TEXT NOT NULL,
    name        TEXT,
    picture     TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS auth_sessions (
    id          UUID PRIMARY KEY,
    user_sub    TEXT NOT NULL REFERENCES users(sub) ON DELETE CASCADE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at  TIMESTAMPTZ NOT NULL,
    revoked_at  TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS conversations (
    id          UUID PRIMARY KEY,
    user_sub    TEXT NOT NULL REFERENCES users(sub) ON DELETE CASCADE,
    title       TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    archived_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS conversations_user_updated_idx
    ON conversations (user_sub, updated_at DESC)
    WHERE archived_at IS NULL;
```

- [ ] **Step 4: Write the migration runner and pool**

```python
# use_mem0/backend/src/app/db/engine.py
from psycopg_pool import ConnectionPool
from psycopg.rows import dict_row


def open_pool(database_url: str) -> ConnectionPool:
    pool = ConnectionPool(
        conninfo=database_url,
        kwargs={"row_factory": dict_row, "autocommit": True},
        open=True,
    )
    pool.wait()
    return pool
```

```python
# use_mem0/backend/src/app/db/migrate.py
from pathlib import Path

from langgraph.checkpoint.postgres import PostgresSaver
from psycopg import connect

SCHEMA_PATH = Path(__file__).with_name("schema.sql")


def run_migrations(database_url: str) -> None:
    """Apply the application schema, then LangGraph's checkpointer schema.

    PostgresSaver.setup() must be called explicitly by the application; the
    checkpointer does not create its own tables lazily.
    """
    with connect(database_url, autocommit=True) as conn:
        conn.execute(SCHEMA_PATH.read_text())

    with PostgresSaver.from_conn_string(database_url) as checkpointer:
        checkpointer.setup()
```

Create an empty `use_mem0/backend/src/app/db/__init__.py`.

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd use_mem0 && docker compose up -d && cd backend && pytest tests/test_migrate.py -v`
Expected: 3 passed

- [ ] **Step 6: Commit**

```bash
git add use_mem0/backend/src/app/db use_mem0/backend/tests/test_migrate.py
git commit -m "feat: add database schema and migrations including checkpointer setup"
```

---

## Task 3: Google OAuth and session cookies

**Files:**
- Create: `use_mem0/backend/src/app/auth/__init__.py`
- Create: `use_mem0/backend/src/app/auth/google.py`
- Create: `use_mem0/backend/src/app/auth/session.py`
- Create: `use_mem0/backend/src/app/auth/routes.py`
- Test: `use_mem0/backend/tests/test_auth.py`

**Interfaces:**
- Consumes: `Settings` (Task 1), `open_pool` (Task 2).
- Produces:
  - `exchange_code(code: str, settings: Settings, http: httpx.Client) -> GoogleIdentity`
    where `GoogleIdentity` has `sub: str, email: str, name: str | None, picture: str | None`.
  - `create_session(pool, user_sub: str, secret: str) -> str` returning the signed cookie
    value.
  - `resolve_session(pool, cookie_value: str, secret: str) -> str | None` returning the
    `user_sub` or `None`.
  - `get_current_user` — a FastAPI dependency raising `HTTPException(401)` when absent,
    returning `str` (the user's `sub`).
  - Router mounted at `/auth` with `GET /login`, `GET /callback`, `GET /me`,
    `POST /logout`.
  - Cookie name constant `SESSION_COOKIE = "session"`.

- [ ] **Step 1: Write the failing test**

```python
# use_mem0/backend/tests/test_auth.py
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
            "INSERT INTO users (sub, email) VALUES (%s, %s)", ("google-sub-1", "a@example.com")
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd use_mem0/backend && pytest tests/test_auth.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.auth'`

- [ ] **Step 3: Write the session module**

```python
# use_mem0/backend/src/app/auth/session.py
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import Cookie, Depends, HTTPException, Request
from itsdangerous import BadSignature, URLSafeSerializer

SESSION_COOKIE = "session"
SESSION_TTL = timedelta(days=14)
_SALT = "session"


def _serializer(secret: str) -> URLSafeSerializer:
    return URLSafeSerializer(secret, salt=_SALT)


def create_session(pool, user_sub: str, secret: str) -> str:
    session_id = uuid.uuid4()
    expires_at = datetime.now(timezone.utc) + SESSION_TTL
    with pool.connection() as conn:
        conn.execute(
            "INSERT INTO auth_sessions (id, user_sub, expires_at) VALUES (%s, %s, %s)",
            (session_id, user_sub, expires_at),
        )
    return _serializer(secret).dumps(str(session_id))


def resolve_session(pool, cookie_value: str, secret: str) -> str | None:
    try:
        session_id = _serializer(secret).loads(cookie_value)
    except BadSignature:
        return None
    with pool.connection() as conn:
        row = conn.execute(
            """
            SELECT user_sub FROM auth_sessions
            WHERE id = %s AND revoked_at IS NULL AND expires_at > now()
            """,
            (session_id,),
        ).fetchone()
    return row["user_sub"] if row else None


def revoke_session(pool, cookie_value: str, secret: str) -> None:
    try:
        session_id = _serializer(secret).loads(cookie_value)
    except BadSignature:
        return
    with pool.connection() as conn:
        conn.execute("UPDATE auth_sessions SET revoked_at = now() WHERE id = %s", (session_id,))


def get_current_user(request: Request, session: str | None = Cookie(default=None)) -> str:
    """FastAPI dependency: returns the authenticated user's Google `sub`, or 401."""
    if not session:
        raise HTTPException(status_code=401, detail="Not authenticated")
    user_sub = resolve_session(
        request.app.state.pool, session, request.app.state.settings.session_secret
    )
    if user_sub is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return user_sub
```

- [ ] **Step 4: Write the Google exchange**

```python
# use_mem0/backend/src/app/auth/google.py
from dataclasses import dataclass

import httpx
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token

AUTH_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
SCOPES = "openid email profile"


@dataclass(frozen=True)
class GoogleIdentity:
    sub: str
    email: str
    name: str | None
    picture: str | None


def authorization_url(client_id: str, redirect_uri: str, state: str) -> str:
    from urllib.parse import urlencode

    return AUTH_ENDPOINT + "?" + urlencode(
        {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": SCOPES,
            "state": state,
            "access_type": "online",
            "prompt": "select_account",
        }
    )


def exchange_code(
    code: str, client_id: str, client_secret: str, redirect_uri: str, http: httpx.Client
) -> GoogleIdentity:
    response = http.post(
        TOKEN_ENDPOINT,
        data={
            "code": code,
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
        },
    )
    response.raise_for_status()
    raw_id_token = response.json()["id_token"]
    claims = id_token.verify_oauth2_token(
        raw_id_token, google_requests.Request(), client_id
    )
    return GoogleIdentity(
        sub=claims["sub"],
        email=claims["email"],
        name=claims.get("name"),
        picture=claims.get("picture"),
    )
```

- [ ] **Step 5: Write the routes**

```python
# use_mem0/backend/src/app/auth/routes.py
import secrets

import httpx
from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse, RedirectResponse

from .google import authorization_url, exchange_code
from .session import SESSION_COOKIE, create_session, get_current_user, revoke_session

router = APIRouter(prefix="/auth", tags=["auth"])

STATE_COOKIE = "oauth_state"


def _redirect_uri(request: Request) -> str:
    return str(request.url_for("auth_callback"))


@router.get("/login")
def login(request: Request) -> RedirectResponse:
    state = secrets.token_urlsafe(24)
    settings = request.app.state.settings
    url = authorization_url(settings.google_client_id, _redirect_uri(request), state)
    response = RedirectResponse(url)
    response.set_cookie(
        STATE_COOKIE, state, httponly=True, samesite="lax", max_age=600, secure=False
    )
    return response


@router.get("/callback", name="auth_callback")
def callback(
    request: Request,
    code: str,
    state: str,
    oauth_state: str | None = Cookie(default=None),
) -> RedirectResponse:
    if not oauth_state or not secrets.compare_digest(oauth_state, state):
        raise HTTPException(status_code=400, detail="Invalid OAuth state")

    settings = request.app.state.settings
    with httpx.Client(timeout=10) as http:
        identity = exchange_code(
            code,
            settings.google_client_id,
            settings.google_client_secret,
            _redirect_uri(request),
            http,
        )

    pool = request.app.state.pool
    with pool.connection() as conn:
        conn.execute(
            """
            INSERT INTO users (sub, email, name, picture) VALUES (%s, %s, %s, %s)
            ON CONFLICT (sub) DO UPDATE
              SET email = EXCLUDED.email, name = EXCLUDED.name, picture = EXCLUDED.picture
            """,
            (identity.sub, identity.email, identity.name, identity.picture),
        )

    cookie = create_session(pool, identity.sub, settings.session_secret)
    response = RedirectResponse(settings.frontend_origin)
    response.delete_cookie(STATE_COOKIE)
    response.set_cookie(
        SESSION_COOKIE, cookie, httponly=True, samesite="lax", secure=False, max_age=1209600
    )
    return response


@router.get("/me")
def me(request: Request, user_sub: str = Depends(get_current_user)) -> JSONResponse:
    with request.app.state.pool.connection() as conn:
        row = conn.execute(
            "SELECT sub, email, name, picture FROM users WHERE sub = %s", (user_sub,)
        ).fetchone()
    return JSONResponse(dict(row))


@router.post("/logout")
def logout(request: Request, response: Response, session: str | None = Cookie(default=None)):
    if session:
        revoke_session(
            request.app.state.pool, session, request.app.state.settings.session_secret
        )
    response.delete_cookie(SESSION_COOKIE)
    return {"ok": True}
```

Create an empty `use_mem0/backend/src/app/auth/__init__.py`.

- [ ] **Step 6: Run tests to verify they pass**

Run: `cd use_mem0/backend && pytest tests/test_auth.py -v`
Expected: 5 passed

- [ ] **Step 7: Commit**

```bash
git add use_mem0/backend/src/app/auth use_mem0/backend/tests/test_auth.py
git commit -m "feat: add google oauth login and server-side sessions"
```

---

## Task 4: Application wiring and CORS

**Files:**
- Create: `use_mem0/backend/src/app/main.py`
- Test: `use_mem0/backend/tests/test_app.py`

**Interfaces:**
- Consumes: `load_settings` (Task 1), `open_pool`/`run_migrations` (Task 2), auth router
  (Task 3).
- Produces: `create_app(settings: Settings) -> FastAPI` with `app.state.settings` and
  `app.state.pool` populated, CORS configured for `settings.frontend_origin` with
  credentials allowed, and `GET /health` returning `{"status": "ok"}`.

- [ ] **Step 1: Write the failing test**

```python
# use_mem0/backend/tests/test_app.py
import os
from fastapi.testclient import TestClient

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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd use_mem0/backend && pytest tests/test_app.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.main'`

- [ ] **Step 3: Write the implementation**

```python
# use_mem0/backend/src/app/main.py
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .auth.routes import router as auth_router
from .config import Settings, load_settings
from .db.engine import open_pool
from .db.migrate import run_migrations


def create_app(settings: Settings) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        run_migrations(settings.database_url)
        app.state.settings = settings
        app.state.pool = open_pool(settings.database_url)
        yield
        app.state.pool.close()

    app = FastAPI(title="mem0 chatbot", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[settings.frontend_origin],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(auth_router)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app(load_settings(os.environ))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd use_mem0/backend && pytest tests/test_app.py -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add use_mem0/backend/src/app/main.py use_mem0/backend/tests/test_app.py
git commit -m "feat: wire fastapi app with lifespan, pool and cors"
```

---

## Task 5: Conversation registry with ownership enforcement

**Files:**
- Create: `use_mem0/backend/src/app/conversations/__init__.py`
- Create: `use_mem0/backend/src/app/conversations/store.py`
- Create: `use_mem0/backend/src/app/conversations/ownership.py`
- Create: `use_mem0/backend/src/app/conversations/routes.py`
- Modify: `use_mem0/backend/src/app/main.py` (include the conversations router)
- Test: `use_mem0/backend/tests/test_conversations.py`

**Interfaces:**
- Consumes: `get_current_user` (Task 3), pool (Task 2).
- Produces:
  - `create_conversation(pool, user_sub: str) -> dict` — row with `id`, `title` (None),
    `created_at`, `updated_at`.
  - `list_conversations(pool, user_sub: str) -> list[dict]` — newest-first, excludes
    archived.
  - `rename_conversation(pool, conversation_id, user_sub, title: str) -> None`
  - `delete_conversation(pool, conversation_id, user_sub) -> None` — deletes the row **and**
    the checkpointer rows for that `thread_id`.
  - `touch_conversation(pool, conversation_id, first_user_message: str | None) -> None` —
    bumps `updated_at`; sets `title` only when currently NULL.
  - `require_owned_conversation` — FastAPI dependency returning the conversation id as `str`
    or raising `HTTPException(404)`.
  - Router mounted at `/conversations`.

- [ ] **Step 1: Write the failing test**

```python
# use_mem0/backend/tests/test_conversations.py
import os
import uuid

import pytest
from psycopg import connect

from app.conversations.store import (
    create_conversation,
    delete_conversation,
    list_conversations,
    rename_conversation,
    touch_conversation,
)
from app.conversations.ownership import owns_conversation
from app.db.engine import open_pool
from app.db.migrate import run_migrations

DB_URL = os.environ.get("TEST_DATABASE_URL", "postgresql://app:app@localhost:5432/app")


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
    assert [r["id"] for r in list_conversations(pool, "user-a")] == [first["id"], second["id"]]


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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd use_mem0/backend && pytest tests/test_conversations.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.conversations'`

- [ ] **Step 3: Write the store**

```python
# use_mem0/backend/src/app/conversations/store.py
import uuid

TITLE_MAX_LENGTH = 50


def create_conversation(pool, user_sub: str) -> dict:
    conversation_id = uuid.uuid4()
    with pool.connection() as conn:
        return conn.execute(
            """
            INSERT INTO conversations (id, user_sub) VALUES (%s, %s)
            RETURNING id::text, title, created_at, updated_at
            """,
            (conversation_id, user_sub),
        ).fetchone()


def list_conversations(pool, user_sub: str) -> list[dict]:
    with pool.connection() as conn:
        return conn.execute(
            """
            SELECT id::text, title, created_at, updated_at
            FROM conversations
            WHERE user_sub = %s AND archived_at IS NULL
            ORDER BY updated_at DESC
            """,
            (user_sub,),
        ).fetchall()


def rename_conversation(pool, conversation_id: str, user_sub: str, title: str) -> None:
    with pool.connection() as conn:
        conn.execute(
            "UPDATE conversations SET title = %s WHERE id = %s AND user_sub = %s",
            (title[:TITLE_MAX_LENGTH], conversation_id, user_sub),
        )


def delete_conversation(pool, conversation_id: str, user_sub: str) -> None:
    """Remove the registry row and the checkpointer state for this thread.

    Deleting only the registry row would leave orphaned checkpoint rows still
    holding the message content the user asked to have deleted.
    """
    with pool.connection() as conn:
        deleted = conn.execute(
            "DELETE FROM conversations WHERE id = %s AND user_sub = %s RETURNING id",
            (conversation_id, user_sub),
        ).fetchone()
        if deleted is None:
            return
        for table in ("checkpoint_writes", "checkpoint_blobs", "checkpoints"):
            conn.execute(f"DELETE FROM {table} WHERE thread_id = %s", (conversation_id,))


def touch_conversation(pool, conversation_id: str, first_user_message: str | None) -> None:
    """Bump updated_at, and set the title from the first message if unset."""
    title = first_user_message[:TITLE_MAX_LENGTH] if first_user_message else None
    with pool.connection() as conn:
        conn.execute(
            """
            UPDATE conversations
            SET updated_at = now(),
                title = COALESCE(title, %s)
            WHERE id = %s
            """,
            (title, conversation_id),
        )
```

- [ ] **Step 4: Write the ownership dependency**

```python
# use_mem0/backend/src/app/conversations/ownership.py
from fastapi import Depends, HTTPException, Request

from ..auth.session import get_current_user


def owns_conversation(pool, conversation_id: str, user_sub: str) -> bool:
    with pool.connection() as conn:
        row = conn.execute(
            "SELECT 1 FROM conversations WHERE id = %s AND user_sub = %s",
            (conversation_id, user_sub),
        ).fetchone()
    return row is not None


def require_owned_conversation(
    conversation_id: str,
    request: Request,
    user_sub: str = Depends(get_current_user),
) -> str:
    """Resolve a conversation the caller owns, or 404.

    404 rather than 403: a 403 would confirm that another user's conversation
    exists. This is the single place the check lives — every route that accepts
    a conversation id must depend on it.
    """
    if not owns_conversation(request.app.state.pool, conversation_id, user_sub):
        raise HTTPException(status_code=404, detail="Conversation not found")
    return conversation_id
```

- [ ] **Step 5: Write the routes**

```python
# use_mem0/backend/src/app/conversations/routes.py
from fastapi import APIRouter, Body, Depends, Request

from ..auth.session import get_current_user
from .ownership import require_owned_conversation
from .store import (
    create_conversation,
    delete_conversation,
    list_conversations,
    rename_conversation,
)

router = APIRouter(prefix="/conversations", tags=["conversations"])


@router.get("")
def list_all(request: Request, user_sub: str = Depends(get_current_user)) -> list[dict]:
    return list_conversations(request.app.state.pool, user_sub)


@router.post("")
def create(request: Request, user_sub: str = Depends(get_current_user)) -> dict:
    return create_conversation(request.app.state.pool, user_sub)


@router.patch("/{conversation_id}")
def rename(
    request: Request,
    title: str = Body(embed=True),
    conversation_id: str = Depends(require_owned_conversation),
    user_sub: str = Depends(get_current_user),
) -> dict:
    rename_conversation(request.app.state.pool, conversation_id, user_sub, title)
    return {"ok": True}


@router.delete("/{conversation_id}")
def delete(
    request: Request,
    conversation_id: str = Depends(require_owned_conversation),
    user_sub: str = Depends(get_current_user),
) -> dict:
    delete_conversation(request.app.state.pool, conversation_id, user_sub)
    return {"ok": True}
```

Create an empty `use_mem0/backend/src/app/conversations/__init__.py`, and add
`app.include_router(conversations_router)` to `create_app` in `main.py`.

- [ ] **Step 6: Run tests to verify they pass**

Run: `cd use_mem0/backend && pytest tests/test_conversations.py -v`
Expected: 10 passed

- [ ] **Step 7: Commit**

```bash
git add use_mem0/backend/src/app/conversations use_mem0/backend/src/app/main.py use_mem0/backend/tests/test_conversations.py
git commit -m "feat: add conversation registry with ownership enforcement"
```

---

## Task 6: mem0 memory wrapper with failure degradation

**Files:**
- Create: `use_mem0/backend/src/app/agent/__init__.py`
- Create: `use_mem0/backend/src/app/agent/memory.py`
- Test: `use_mem0/backend/tests/test_memory.py`

**Interfaces:**
- Consumes: `Settings.mem0_api_key` (Task 1).
- Produces: `MemoryStore` class with:
  - `__init__(self, client, timeout_seconds: float = 2.0)`
  - `search(self, query: str, user_id: str) -> list[str]` — returns memory strings,
    **never raises**; returns `[]` on any failure.
  - `add(self, messages: list[dict], user_id: str) -> None` — **never raises**.
  - `build_client(api_key: str)` module function returning a `mem0.MemoryClient`.

Both methods are decorated with `@traceable` so they appear in LangSmith traces; mem0 calls
are plain SDK calls and are not auto-traced by LangSmith.

- [ ] **Step 1: Write the failing test**

```python
# use_mem0/backend/tests/test_memory.py
from app.agent.memory import MemoryStore


class FakeClient:
    def __init__(self, search_result=None, raises=False):
        self.search_result = search_result or []
        self.raises = raises
        self.added = []

    def search(self, query, filters=None, **kwargs):
        if self.raises:
            raise RuntimeError("mem0 unavailable")
        return self.search_result

    def add(self, messages, user_id=None, **kwargs):
        if self.raises:
            raise RuntimeError("mem0 unavailable")
        self.added.append((messages, user_id))


def test_search_returns_memory_strings():
    client = FakeClient(search_result=[{"memory": "is vegetarian"}, {"memory": "likes jazz"}])
    assert MemoryStore(client).search("food", "user-a") == ["is vegetarian", "likes jazz"]


def test_search_scopes_the_query_to_the_user():
    captured = {}

    class CapturingClient(FakeClient):
        def search(self, query, filters=None, **kwargs):
            captured["filters"] = filters
            return []

    MemoryStore(CapturingClient()).search("food", "user-a")
    assert captured["filters"] == {"user_id": "user-a"}


def test_search_degrades_to_empty_on_failure():
    assert MemoryStore(FakeClient(raises=True)).search("food", "user-a") == []


def test_search_tolerates_unexpected_result_shape():
    assert MemoryStore(FakeClient(search_result=[{"unexpected": "shape"}])).search("q", "u") == []


def test_add_passes_the_user_id():
    client = FakeClient()
    messages = [{"role": "user", "content": "hi"}]
    MemoryStore(client).add(messages, "user-a")
    assert client.added == [(messages, "user-a")]


def test_add_swallows_failures():
    MemoryStore(FakeClient(raises=True)).add([{"role": "user", "content": "hi"}], "user-a")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd use_mem0/backend && pytest tests/test_memory.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.agent'`

- [ ] **Step 3: Write the implementation**

```python
# use_mem0/backend/src/app/agent/memory.py
import logging

from langsmith import traceable

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT_SECONDS = 2.0


def build_client(api_key: str):
    from mem0 import MemoryClient

    return MemoryClient(api_key=api_key)


class MemoryStore:
    """mem0 wrapper that degrades instead of failing the chat.

    Memory is enrichment, not the critical path: a memory service that is slow
    or down must produce a reply without recall rather than an error.
    """

    def __init__(self, client, timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS):
        self._client = client
        self._timeout_seconds = timeout_seconds

    @traceable(name="mem0.search", run_type="retriever")
    def search(self, query: str, user_id: str) -> list[str]:
        try:
            results = self._client.search(query, filters={"user_id": user_id})
        except Exception:
            logger.warning("mem0 search failed; continuing without recall", exc_info=True)
            return []
        memories = []
        for item in results or []:
            memory = item.get("memory") if isinstance(item, dict) else None
            if isinstance(memory, str):
                memories.append(memory)
        return memories

    @traceable(name="mem0.add", run_type="tool")
    def add(self, messages: list[dict], user_id: str) -> None:
        try:
            self._client.add(messages, user_id=user_id)
        except Exception:
            logger.warning("mem0 add failed; memory not written", exc_info=True)
```

Create an empty `use_mem0/backend/src/app/agent/__init__.py`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd use_mem0/backend && pytest tests/test_memory.py -v`
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add use_mem0/backend/src/app/agent use_mem0/backend/tests/test_memory.py
git commit -m "feat: add mem0 wrapper with failure degradation and langsmith tracing"
```

---

## Task 7: Graph state and nodes

**Files:**
- Create: `use_mem0/backend/src/app/agent/state.py`
- Create: `use_mem0/backend/src/app/agent/nodes.py`
- Test: `use_mem0/backend/tests/test_nodes.py`

**Interfaces:**
- Consumes: `MemoryStore` (Task 6).
- Produces:
  - `ChatState` TypedDict: `messages: Annotated[list[AnyMessage], add_messages]`,
    `user_id: str`, `memory_enabled: bool`, `memories: list[str]`.
  - `make_retrieve_memories(store: MemoryStore) -> Callable[[ChatState], dict]`
  - `make_call_model(model, system_prompt_builder) -> Callable[[ChatState], dict]`
  - `make_write_memories(store: MemoryStore) -> Callable[[ChatState], dict]`
  - `build_system_prompt(memories: list[str]) -> str`

- [ ] **Step 1: Write the failing test**

```python
# use_mem0/backend/tests/test_nodes.py
from langchain_core.messages import AIMessage, HumanMessage

from app.agent.memory import MemoryStore
from app.agent.nodes import (
    build_system_prompt,
    make_call_model,
    make_retrieve_memories,
    make_write_memories,
)


class FakeClient:
    def __init__(self, results=None):
        self.results = results or []
        self.added = []

    def search(self, query, filters=None, **kwargs):
        return self.results

    def add(self, messages, user_id=None, **kwargs):
        self.added.append((messages, user_id))


def test_retrieve_puts_memories_on_state():
    store = MemoryStore(FakeClient([{"memory": "is vegetarian"}]))
    node = make_retrieve_memories(store)
    state = {"messages": [HumanMessage("what should I eat?")], "user_id": "u", "memory_enabled": True}
    assert node(state) == {"memories": ["is vegetarian"]}


def test_retrieve_is_skipped_when_memory_is_disabled():
    class ExplodingClient:
        def search(self, *a, **k):
            raise AssertionError("search must not be called when memory_enabled is False")

    node = make_retrieve_memories(MemoryStore(ExplodingClient()))
    state = {"messages": [HumanMessage("hi")], "user_id": "u", "memory_enabled": False}
    assert node(state) == {"memories": []}


def test_retrieve_uses_the_latest_human_message_as_the_query():
    captured = {}

    class CapturingClient:
        def search(self, query, filters=None, **kwargs):
            captured["query"] = query
            return []

    node = make_retrieve_memories(MemoryStore(CapturingClient()))
    node({
        "messages": [HumanMessage("first"), AIMessage("reply"), HumanMessage("second")],
        "user_id": "u",
        "memory_enabled": True,
    })
    assert captured["query"] == "second"


def test_system_prompt_includes_memories():
    prompt = build_system_prompt(["is vegetarian", "likes jazz"])
    assert "is vegetarian" in prompt and "likes jazz" in prompt


def test_system_prompt_without_memories_has_no_memory_section():
    assert "What you remember" not in build_system_prompt([])


def test_call_model_appends_the_reply():
    class FakeModel:
        def invoke(self, messages):
            return AIMessage("hello there")

    node = make_call_model(FakeModel(), build_system_prompt)
    result = node({"messages": [HumanMessage("hi")], "memories": [], "user_id": "u"})
    assert result["messages"][0].content == "hello there"


def test_write_memories_sends_the_exchange():
    client = FakeClient()
    node = make_write_memories(MemoryStore(client))
    node({
        "messages": [HumanMessage("I am vegetarian"), AIMessage("noted")],
        "user_id": "user-a",
        "memory_enabled": True,
    })
    messages, user_id = client.added[0]
    assert user_id == "user-a"
    assert messages == [
        {"role": "user", "content": "I am vegetarian"},
        {"role": "assistant", "content": "noted"},
    ]


def test_write_memories_runs_even_when_retrieval_is_disabled():
    client = FakeClient()
    node = make_write_memories(MemoryStore(client))
    node({
        "messages": [HumanMessage("hi"), AIMessage("hello")],
        "user_id": "user-a",
        "memory_enabled": False,
    })
    assert len(client.added) == 1


def test_call_model_retries_once_on_transient_failure():
    class FlakyModel:
        def __init__(self):
            self.calls = 0

        def invoke(self, messages):
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("rate limit exceeded")
            return AIMessage("recovered")

    model = FlakyModel()
    node = make_call_model(model)
    result = node({"messages": [HumanMessage("hi")], "memories": [], "user_id": "u"})
    assert model.calls == 2
    assert result["messages"][0].content == "recovered"


def test_call_model_raises_after_a_second_failure():
    class BrokenModel:
        def invoke(self, messages):
            raise RuntimeError("upstream is down")

    node = make_call_model(BrokenModel())
    with pytest.raises(RuntimeError):
        node({"messages": [HumanMessage("hi")], "memories": [], "user_id": "u"})
```

Add `import pytest` to the top of this test file.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd use_mem0/backend && pytest tests/test_nodes.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.agent.nodes'`

- [ ] **Step 3: Write the state**

```python
# use_mem0/backend/src/app/agent/state.py
from typing import Annotated, TypedDict

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages


class ChatState(TypedDict, total=False):
    messages: Annotated[list[AnyMessage], add_messages]
    user_id: str
    memory_enabled: bool
    memories: list[str]
```

- [ ] **Step 4: Write the nodes**

```python
# use_mem0/backend/src/app/agent/nodes.py
import logging

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from .memory import MemoryStore
from .state import ChatState

logger = logging.getLogger(__name__)

BASE_PROMPT = (
    "You are a helpful personal assistant. Be concise and direct. "
    "Use what you remember about the user when it is relevant, and never "
    "invent memories you were not given."
)


def build_system_prompt(memories: list[str]) -> str:
    """Render memories into the prompt in the order mem0 returned them.

    Deliberately no deduplication, recency weighting or contradiction filtering:
    the spec's conflict-handling decision is to observe mem0's own consolidation
    behaviour rather than mask it.
    """
    if not memories:
        return BASE_PROMPT
    lines = "\n".join(f"- {memory}" for memory in memories)
    return f"{BASE_PROMPT}\n\nWhat you remember about this user:\n{lines}"


def _latest_human_message(state: ChatState) -> str:
    for message in reversed(state.get("messages", [])):
        if isinstance(message, HumanMessage):
            return message.content
    return ""


def make_retrieve_memories(store: MemoryStore):
    def retrieve_memories(state: ChatState) -> dict:
        if not state.get("memory_enabled", True):
            return {"memories": []}
        query = _latest_human_message(state)
        if not query:
            return {"memories": []}
        return {"memories": store.search(query, state["user_id"])}

    return retrieve_memories


def make_call_model(model, system_prompt_builder=build_system_prompt):
    def call_model(state: ChatState) -> dict:
        system = SystemMessage(system_prompt_builder(state.get("memories", [])))
        messages = [system, *state["messages"]]
        try:
            reply = model.invoke(messages)
        except Exception:
            # The model call is the critical path: unlike memory, it cannot
            # degrade silently. Retry once for transient/rate-limit errors, then
            # let the exception propagate so the AG-UI layer reports a real
            # failure to the user.
            logger.warning("model call failed; retrying once", exc_info=True)
            reply = model.invoke(messages)
        return {"messages": [reply]}

    return call_model


def make_write_memories(store: MemoryStore):
    def write_memories(state: ChatState) -> dict:
        messages = state.get("messages", [])
        exchange = []
        for message in messages[-2:]:
            if isinstance(message, HumanMessage):
                exchange.append({"role": "user", "content": message.content})
            elif isinstance(message, AIMessage):
                exchange.append({"role": "assistant", "content": message.content})
        if exchange:
            store.add(exchange, state["user_id"])
        return {}

    return write_memories
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd use_mem0/backend && pytest tests/test_nodes.py -v`
Expected: 10 passed

- [ ] **Step 6: Commit**

```bash
git add use_mem0/backend/src/app/agent use_mem0/backend/tests/test_nodes.py
git commit -m "feat: add graph state and memory-aware nodes"
```

---

## Task 8: Graph assembly with Postgres checkpointing

**Files:**
- Create: `use_mem0/backend/src/app/agent/graph.py`
- Test: `use_mem0/backend/tests/test_graph.py`

**Interfaces:**
- Consumes: nodes (Task 7), `MemoryStore` (Task 6), `run_migrations` (Task 2).
- Produces:
  - `build_graph(store: MemoryStore, model, checkpointer) -> CompiledStateGraph` with the
    fixed order `retrieve_memories → call_model → write_memories`.
  - `read_messages(graph, thread_id: str) -> list[dict]` returning
    `[{"role": "user"|"assistant", "content": str}, ...]` from the checkpointer, used by the
    history endpoint in Task 10.

- [ ] **Step 1: Write the failing test**

```python
# use_mem0/backend/tests/test_graph.py
import os
import uuid

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.postgres import PostgresSaver

from app.agent.graph import build_graph, read_messages
from app.agent.memory import MemoryStore
from app.db.migrate import run_migrations

DB_URL = os.environ.get("TEST_DATABASE_URL", "postgresql://app:app@localhost:5432/app")


class FakeClient:
    def __init__(self):
        self.added = []
        self.searched = []

    def search(self, query, filters=None, **kwargs):
        self.searched.append(query)
        return [{"memory": "is vegetarian"}]

    def add(self, messages, user_id=None, **kwargs):
        self.added.append((messages, user_id))


class ScriptedModel:
    def __init__(self):
        self.seen_prompts = []
        self.turn = 0

    def invoke(self, messages):
        self.seen_prompts.append(messages[0].content)
        self.turn += 1
        return AIMessage(f"reply {self.turn}")


@pytest.fixture
def graph_parts():
    run_migrations(DB_URL)
    client = FakeClient()
    model = ScriptedModel()
    with PostgresSaver.from_conn_string(DB_URL) as checkpointer:
        yield build_graph(MemoryStore(client), model, checkpointer), client, model


def test_single_turn_produces_a_reply(graph_parts):
    graph, _, _ = graph_parts
    config = {"configurable": {"thread_id": str(uuid.uuid4())}}
    result = graph.invoke(
        {"messages": [HumanMessage("hello")], "user_id": "u", "memory_enabled": True}, config
    )
    assert result["messages"][-1].content == "reply 1"


def test_memories_reach_the_system_prompt(graph_parts):
    graph, _, model = graph_parts
    config = {"configurable": {"thread_id": str(uuid.uuid4())}}
    graph.invoke(
        {"messages": [HumanMessage("what to eat?")], "user_id": "u", "memory_enabled": True},
        config,
    )
    assert "is vegetarian" in model.seen_prompts[0]


def test_memory_toggle_skips_retrieval_but_still_writes(graph_parts):
    graph, client, model = graph_parts
    config = {"configurable": {"thread_id": str(uuid.uuid4())}}
    graph.invoke(
        {"messages": [HumanMessage("hi")], "user_id": "u", "memory_enabled": False}, config
    )
    assert client.searched == []
    assert len(client.added) == 1
    assert "is vegetarian" not in model.seen_prompts[0]


def test_conversation_resumes_across_invocations(graph_parts):
    graph, _, _ = graph_parts
    thread_id = str(uuid.uuid4())
    config = {"configurable": {"thread_id": thread_id}}
    graph.invoke({"messages": [HumanMessage("first")], "user_id": "u", "memory_enabled": True}, config)
    result = graph.invoke(
        {"messages": [HumanMessage("second")], "user_id": "u", "memory_enabled": True}, config
    )
    contents = [m.content for m in result["messages"]]
    assert contents == ["first", "reply 1", "second", "reply 2"]


def test_read_messages_returns_the_transcript(graph_parts):
    graph, _, _ = graph_parts
    thread_id = str(uuid.uuid4())
    config = {"configurable": {"thread_id": thread_id}}
    graph.invoke({"messages": [HumanMessage("hello")], "user_id": "u", "memory_enabled": True}, config)
    assert read_messages(graph, thread_id) == [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "reply 1"},
    ]


def test_read_messages_for_an_unknown_thread_is_empty(graph_parts):
    graph, _, _ = graph_parts
    assert read_messages(graph, str(uuid.uuid4())) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd use_mem0/backend && pytest tests/test_graph.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.agent.graph'`

- [ ] **Step 3: Write the implementation**

```python
# use_mem0/backend/src/app/agent/graph.py
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.graph import END, START, StateGraph

from .memory import MemoryStore
from .nodes import make_call_model, make_retrieve_memories, make_write_memories
from .state import ChatState


def build_graph(store: MemoryStore, model, checkpointer):
    """Fixed three-node graph: retrieve -> model -> write.

    Memory is invoked as graph nodes rather than as LLM tools so that every turn
    runs the same way and every LangSmith trace has the same shape.
    """
    builder = StateGraph(ChatState)
    builder.add_node("retrieve_memories", make_retrieve_memories(store))
    builder.add_node("call_model", make_call_model(model))
    builder.add_node("write_memories", make_write_memories(store))

    builder.add_edge(START, "retrieve_memories")
    builder.add_edge("retrieve_memories", "call_model")
    builder.add_edge("call_model", "write_memories")
    builder.add_edge("write_memories", END)

    return builder.compile(checkpointer=checkpointer)


def read_messages(graph, thread_id: str) -> list[dict]:
    """Read a thread's transcript from the checkpointer for UI rehydration."""
    state = graph.get_state({"configurable": {"thread_id": thread_id}})
    transcript = []
    for message in (state.values or {}).get("messages", []):
        if isinstance(message, HumanMessage):
            transcript.append({"role": "user", "content": message.content})
        elif isinstance(message, AIMessage):
            transcript.append({"role": "assistant", "content": message.content})
    return transcript
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd use_mem0/backend && pytest tests/test_graph.py -v`
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add use_mem0/backend/src/app/agent/graph.py use_mem0/backend/tests/test_graph.py
git commit -m "feat: assemble langgraph graph with postgres checkpointing"
```

---

## Task 9: AG-UI endpoint, session gate, and LangSmith metadata

**Files:**
- Create: `use_mem0/backend/src/app/agui/__init__.py`
- Create: `use_mem0/backend/src/app/agui/routes.py`
- Modify: `use_mem0/backend/src/app/main.py` (build the graph at startup, mount the endpoint)
- Test: `use_mem0/backend/tests/test_agui.py`

**Interfaces:**
- Consumes: `build_graph` (Task 8), `require_owned_conversation` (Task 5),
  `get_current_user` (Task 3).
- Produces:
  - `mount_agent_endpoint(app, graph) -> None` mounting `POST /agent`.
  - `guard_agent_request(request, thread_id) -> tuple[str, str]` returning
    `(user_sub, thread_id)` or raising 401/404.
  - `app.state.graph` populated during lifespan.

**Note on the AG-UI adapter:** `add_langgraph_fastapi_endpoint(app, graph, "/agent")` mounts
the endpoint itself, so the auth gate is applied as FastAPI middleware or a router
dependency wrapping that path rather than as a handler argument. `threadId` arrives as a
top-level field of AG-UI's `RunAgentInput` and is mapped by `ag-ui-langgraph` onto
`config["configurable"]["thread_id"]`.

- [ ] **Step 1: Write the failing test**

```python
# use_mem0/backend/tests/test_agui.py
import os
import uuid

import pytest
from fastapi.testclient import TestClient
from psycopg import connect

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

    cookie = create_session(client.app.state.pool, user_sub, "t" * 32)
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd use_mem0/backend && pytest tests/test_agui.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.agui'`

- [ ] **Step 3: Write the guard and mount**

```python
# use_mem0/backend/src/app/agui/routes.py
import json

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from ..auth.session import resolve_session
from ..conversations.ownership import owns_conversation

AGENT_PATH = "/agent"


class AgentAuthMiddleware(BaseHTTPMiddleware):
    """Authenticate and authorise AG-UI runs before the adapter handles them.

    The AG-UI adapter mounts its own route, so the ownership check is applied
    here rather than as a route dependency. Without it, any caller who knows a
    thread_id could read and append to another user's conversation, because the
    LangGraph checkpointer has no concept of users.
    """

    async def dispatch(self, request: Request, call_next):
        if request.url.path != AGENT_PATH or request.method != "POST":
            return await call_next(request)

        settings = request.app.state.settings
        cookie = request.cookies.get("session")
        user_sub = (
            resolve_session(request.app.state.pool, cookie, settings.session_secret)
            if cookie
            else None
        )
        if user_sub is None:
            return JSONResponse({"detail": "Not authenticated"}, status_code=401)

        body = await request.body()
        try:
            payload = json.loads(body or b"{}")
        except json.JSONDecodeError:
            return JSONResponse({"detail": "Invalid JSON"}, status_code=400)

        thread_id = payload.get("threadId")
        if not thread_id:
            return JSONResponse({"detail": "threadId is required"}, status_code=400)

        if not owns_conversation(request.app.state.pool, thread_id, user_sub):
            return JSONResponse({"detail": "Conversation not found"}, status_code=404)

        request.state.user_sub = user_sub
        request.state.thread_id = thread_id
        return await call_next(request)


def mount_agent_endpoint(app, graph) -> None:
    from ag_ui_langgraph import add_langgraph_fastapi_endpoint

    add_langgraph_fastapi_endpoint(app, graph, AGENT_PATH)
    app.add_middleware(AgentAuthMiddleware)
```

- [ ] **Step 4: Wire the graph into startup**

Modify `use_mem0/backend/src/app/main.py`. Inside `lifespan`, after the pool is opened:

```python
        from langchain_openai import ChatOpenAI
        from langgraph.checkpoint.postgres import PostgresSaver

        from .agent.graph import build_graph
        from .agent.memory import MemoryStore, build_client
        from .agui.routes import mount_agent_endpoint

        os.environ.setdefault("LANGSMITH_TRACING", "true")
        os.environ.setdefault("LANGSMITH_API_KEY", settings.langsmith_api_key)
        os.environ.setdefault("LANGSMITH_PROJECT", settings.langsmith_project)

        checkpointer_cm = PostgresSaver.from_conn_string(settings.database_url)
        checkpointer = checkpointer_cm.__enter__()
        app.state.checkpointer_cm = checkpointer_cm
        app.state.graph = build_graph(
            MemoryStore(build_client(settings.mem0_api_key)),
            ChatOpenAI(model="gpt-4o-mini", api_key=settings.openai_api_key),
            checkpointer,
        )
        mount_agent_endpoint(app, app.state.graph)
```

and on shutdown, after `app.state.pool.close()`:

```python
        app.state.checkpointer_cm.__exit__(None, None, None)
```

Create an empty `use_mem0/backend/src/app/agui/__init__.py`.

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd use_mem0/backend && pytest tests/test_agui.py -v`
Expected: 4 passed

- [ ] **Step 6: Commit**

```bash
git add use_mem0/backend/src/app/agui use_mem0/backend/src/app/main.py use_mem0/backend/tests/test_agui.py
git commit -m "feat: mount ag-ui agent endpoint behind session and ownership gates"
```

---

## Task 10: Conversation history endpoint

**Files:**
- Modify: `use_mem0/backend/src/app/conversations/routes.py`
- Modify: `use_mem0/backend/src/app/conversations/store.py` (call `touch_conversation`
  after a run — see Step 4)
- Test: `use_mem0/backend/tests/test_history_endpoint.py`

**Interfaces:**
- Consumes: `read_messages` (Task 8), `require_owned_conversation` (Task 5).
- Produces: `GET /conversations/{conversation_id}/messages` returning
  `{"messages": [{"role": "user"|"assistant", "content": str}, ...]}`. This is the endpoint
  assistant-ui's history adapter calls in Task 14.

- [ ] **Step 1: Write the failing test**

```python
# use_mem0/backend/tests/test_history_endpoint.py
import os
import uuid

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import HumanMessage
from psycopg import connect

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


def _login(client, user_sub):
    client.cookies.set(
        SESSION_COOKIE, create_session(client.app.state.pool, user_sub, "t" * 32)
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
    client.app.state.graph.invoke(
        {"messages": [HumanMessage("hello")], "user_id": "user-a", "memory_enabled": False},
        {"configurable": {"thread_id": convo["id"]}},
    )
    messages = client.get(f"/conversations/{convo['id']}/messages").json()["messages"]
    assert messages[0] == {"role": "user", "content": "hello"}
    assert messages[1]["role"] == "assistant"


def test_history_requires_authentication(client):
    response = client.get(f"/conversations/{uuid.uuid4()}/messages")
    assert response.status_code == 401


def test_history_rejects_another_users_conversation(client):
    _login(client, "user-a")
    convo = client.post("/conversations").json()
    client.cookies.clear()
    _login(client, "user-b")
    assert client.get(f"/conversations/{convo['id']}/messages").status_code == 404
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd use_mem0/backend && pytest tests/test_history_endpoint.py -v`
Expected: FAIL — 404 on the messages route (it does not exist yet)

- [ ] **Step 3: Add the route**

Append to `use_mem0/backend/src/app/conversations/routes.py`:

```python
from ..agent.graph import read_messages


@router.get("/{conversation_id}/messages")
def messages(
    request: Request,
    conversation_id: str = Depends(require_owned_conversation),
) -> dict:
    """Transcript for assistant-ui's history adapter.

    Read from the LangGraph checkpointer rather than a table of our own: the
    checkpointer is the source of truth for message content.
    """
    return {"messages": read_messages(request.app.state.graph, conversation_id)}
```

- [ ] **Step 4: Bump `updated_at` and set the title after each run**

Add to `use_mem0/backend/src/app/agui/routes.py`, inside `AgentAuthMiddleware.dispatch`,
replacing the final `return await call_next(request)`:

```python
        first_user_message = None
        for message in payload.get("messages") or []:
            if message.get("role") == "user":
                first_user_message = message.get("content")
                break

        response = await call_next(request)

        from ..conversations.store import touch_conversation

        touch_conversation(request.app.state.pool, thread_id, first_user_message)
        return response
```

`touch_conversation` sets the title only when it is currently NULL, so the first message
names the conversation and later messages only reorder it.

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd use_mem0/backend && pytest tests/ -v`
Expected: all tests pass

- [ ] **Step 6: Commit**

```bash
git add use_mem0/backend/src/app/conversations use_mem0/backend/src/app/agui use_mem0/backend/tests/test_history_endpoint.py
git commit -m "feat: add conversation history endpoint for ui rehydration"
```

---

## Task 11: Frontend scaffold, design system, and auth gate

**Files:**
- Create: `use_mem0/frontend/package.json`
- Create: `use_mem0/frontend/vite.config.ts`
- Create: `use_mem0/frontend/index.html`
- Create: `use_mem0/frontend/components.json` (written by `shadcn init`)
- Create: `use_mem0/frontend/src/main.tsx`
- Create: `use_mem0/frontend/src/index.css` (Tailwind base + mockup tokens)
- Create: `use_mem0/frontend/src/components/ui/*` (copied in by `shadcn add`)
- Create: `use_mem0/frontend/src/api.ts`
- Create: `use_mem0/frontend/src/App.tsx`
- Create: `use_mem0/frontend/src/Login.tsx`

**Interfaces:**
- Consumes: `/auth/me`, `/auth/login` (Task 3).
- Produces:
  - `api.ts` exporting `getMe(): Promise<User | null>`, `listConversations()`,
    `createConversation()`, `renameConversation(id, title)`, `deleteConversation(id)`,
    `getMessages(id)`, and `logout()`. All use `credentials: "include"`.
  - `User` type: `{ sub: string; email: string; name: string | null; picture: string | null }`.
  - `Conversation` type: `{ id: string; title: string | null; updated_at: string }`.

- [ ] **Step 1: Scaffold the project**

```bash
cd use_mem0 && npm create vite@latest frontend -- --template react-ts && cd frontend && npm install
npm install @assistant-ui/react@0.15.17 @assistant-ui/react-ag-ui@0.0.57 @ag-ui/client@0.0.58
```

- [ ] **Step 2: Initialise Tailwind and shadcn/ui**

Run shadcn's official initialiser rather than hand-writing a Tailwind config — it configures
Tailwind for whichever version is current and writes the correct `components.json`,
path aliases, and base CSS:

```bash
cd use_mem0/frontend && npx shadcn@latest init
```

Accept the defaults except: choose the **Neutral** base colour (our palette is warm-neutral
and overriding a neutral base is cleaner than fighting a tinted one).

Then install the primitives this project uses:

```bash
npx shadcn@latest add button input dialog dropdown-menu scroll-area avatar
```

shadcn copies component **source** into `src/components/ui/`. These files are ours to edit
directly — they are not a vendored dependency, and editing them is the intended workflow.

- [ ] **Step 3: Apply the mockup's design tokens**

Replace the colour variables shadcn generated in `src/index.css` with the approved mockup
values. Keep shadcn's variable *names* — its components reference them — and change only the
values, so every generated component inherits our palette automatically:

Values below are the designer's canonical list, confirmed against the mockup source. Do not
re-derive them from the rendered artifact.

```css
/* use_mem0/frontend/src/index.css — palette from the approved mockup */
@import url("https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700;800&display=swap");

:root {
  /* shadcn semantic tokens — names kept, values replaced */
  --background: #FAF9F6;
  --foreground: #21201C;
  --card: #FAF9F6;
  --card-foreground: #21201C;
  --popover: #FAF9F6;
  --popover-foreground: #21201C;
  --primary: #2E6F7E;
  --primary-foreground: #FFFFFF;
  --secondary: #F4F1EA;
  --secondary-foreground: #21201C;
  --muted: #F4F1EA;
  --muted-foreground: #6B675F;
  --accent: #E9E2D3;            /* active sidebar item fill */
  --accent-foreground: #21201C;
  --destructive: #9C4A2E;       /* delete button, retry button, error icon */
  --destructive-foreground: #FFFFFF;
  --border: #E7E4DD;
  --input: #E7E4DD;
  --ring: #2E6F7E;

  /* radius scale — NOT a single value */
  --radius-sm: 6px;             /* rename input */
  --radius-mark: 7px;           /* avatar and logo marks */
  --radius: 10px;               /* interactive controls: buttons, conversation items, popovers */
  --radius-card: 13px;          /* cards and modals */
  --radius-logo: 16px;          /* sign-in logo mark */

  /* message bubbles — directional; the sharp corner is the tail */
  --radius-bubble-user: 14px 14px 2px 14px;
  --radius-bubble-assistant: 14px 14px 14px 2px;

  /* error family — "destructive" is not one token in this design */
  --error-bg: #FBF1EC;
  --error-border: #E7CFC4;
  --error-text: #8A3F27;

  /* project-specific */
  --sidebar-width: 260px;
  --message-max-width: 640px;
  --bubble-user-bg: #EAF2F1;
  --bubble-skeleton-bg: #ECE8DF;
  --text-tertiary: #9A968C;     /* placeholder and caption text */
  --text-account: #4B4740;      /* sidebar account-row name */
  --avatar-placeholder-bg: #EFEBE3;
}

body {
  font-family: "Plus Jakarta Sans", system-ui, sans-serif;
  font-size: 14.5px;
  line-height: 1.55;
}
```

**Typography is a deliberate choice, not an incidental value.** The mockup specifies Plus
Jakarta Sans (weights 400/500/600/700/800) with a `system-ui, sans-serif` fallback —
explicitly not Inter, Roboto, or Arial. Configure it as the sans font in the Tailwind theme
so `font-sans` resolves to it.

**Two values that must not be collapsed:**

- **Radius is a scale**, not one number. Controls sit at 9–10px, marks at 7px, the rename
  input at 6px, cards and modals at 12–14px, the sign-in logo mark at 16px. `--radius: 10px`
  is the base default; do not apply it uniformly.
- **Bubble radius is directional.** Three corners at 14px and one sharp corner at 2px, and
  the sharp corner mirrors the side the bubble is aligned to — it is the tail. A flat
  `border-radius: 14px` loses the design.

**All components in this and later tasks use Tailwind utility classes**, referencing these
tokens (`bg-background`, `text-muted-foreground`, `border-border`, and so on) rather than
inline `style` objects.

- [ ] **Step 3: Write the API client**

```ts
// use_mem0/frontend/src/api.ts
const BASE = import.meta.env.VITE_API_BASE ?? "http://localhost:8000";

export type User = {
  sub: string;
  email: string;
  name: string | null;
  picture: string | null;
};

export type Conversation = {
  id: string;
  title: string | null;
  updated_at: string;
};

export type ChatMessage = { role: "user" | "assistant"; content: string };

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(`${BASE}${path}`, {
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (!response.ok) throw new Error(`${init.method ?? "GET"} ${path}: ${response.status}`);
  return response.json() as Promise<T>;
}

export async function getMe(): Promise<User | null> {
  try {
    return await request<User>("/auth/me");
  } catch {
    return null;
  }
}

export const listConversations = () => request<Conversation[]>("/conversations");

export const createConversation = () =>
  request<Conversation>("/conversations", { method: "POST" });

export const renameConversation = (id: string, title: string) =>
  request<{ ok: boolean }>(`/conversations/${id}`, {
    method: "PATCH",
    body: JSON.stringify({ title }),
  });

export const deleteConversation = (id: string) =>
  request<{ ok: boolean }>(`/conversations/${id}`, { method: "DELETE" });

export const getMessages = (id: string) =>
  request<{ messages: ChatMessage[] }>(`/conversations/${id}/messages`);

export const logout = () => request<{ ok: boolean }>("/auth/logout", { method: "POST" });

export const loginUrl = `${BASE}/auth/login`;
```

- [ ] **Step 4: Write the login screen**

```tsx
// use_mem0/frontend/src/Login.tsx
import { Button } from "@/components/ui/button";
import { loginUrl } from "./api";

export function Login() {
  return (
    <div className="grid min-h-screen place-items-center bg-background">
      <div className="max-w-sm text-center">
        <h1 className="mb-2 text-[22px] font-medium text-foreground">Assistant Agent</h1>
        <p className="mb-7 text-muted-foreground">
          A personal assistant that remembers what matters across your conversations.
        </p>
        <Button asChild>
          <a href={loginUrl}>Sign in with Google</a>
        </Button>
      </div>
    </div>
  );
}
```

- [ ] **Step 5: Write the auth gate**

```tsx
// use_mem0/frontend/src/App.tsx
import { useEffect, useState } from "react";
import { getMe, User } from "./api";
import { Login } from "./Login";
import { Workspace } from "./Workspace";

export default function App() {
  const [user, setUser] = useState<User | null>(null);
  const [checked, setChecked] = useState(false);

  useEffect(() => {
    getMe().then((value) => {
      setUser(value);
      setChecked(true);
    });
  }, []);

  if (!checked) return null;
  return user ? <Workspace user={user} /> : <Login />;
}
```

```tsx
// use_mem0/frontend/src/main.tsx
import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import "./index.css";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
```

`Workspace` is created in Task 13. Until then, stub it as
`export const Workspace = () => <div>signed in</div>;` in
`use_mem0/frontend/src/Workspace.tsx` so the app compiles.

- [ ] **Step 6: Verify the login screen renders**

Run: `cd use_mem0/frontend && npm run dev`
Expected: visiting `http://localhost:5173` shows the login screen; clicking through Google
completes and returns to the app showing "signed in".

- [ ] **Step 7: Commit**

```bash
git add use_mem0/frontend
git commit -m "feat: scaffold react frontend with theme tokens and auth gate"
```

---

## Task 12: assistant-ui smoke test (REQUIRED before further UI work)

**Files:**
- Create: `use_mem0/frontend/src/Chat.tsx`
- Modify: `use_mem0/frontend/src/Workspace.tsx`

**Interfaces:**
- Consumes: `POST /agent` (Task 9), `GET /conversations/{id}/messages` (Task 10).
- Produces: `<Chat threadId={string} />` rendering an assistant-ui `<Thread />` bound to the
  AG-UI endpoint.

**Why this task exists:** the spec requires this before any further frontend work. Nothing in
this stack has been run against a live server — assistant-ui's AG-UI integration was verified
only at the API-shape level, against shipped type definitions. If `useAgUiRuntime` or the
history adapter does not behave as documented, that must be discovered now, not after a
sidebar and styling have been built on the assumption.

**If this task fails:** stop and report. Do not work around it. The fallback is hand-building
the chat surface on `@ag-ui/client` directly, which is a design decision requiring approval,
not an implementation detail.

- [ ] **Step 1: Write the minimal chat component**

```tsx
// use_mem0/frontend/src/Chat.tsx
import { useMemo } from "react";
import { HttpAgent } from "@ag-ui/client";
import { AssistantRuntimeProvider } from "@assistant-ui/react";
import { Thread } from "@assistant-ui/react";
import { useAgUiRuntime } from "@assistant-ui/react-ag-ui";

const BASE = import.meta.env.VITE_API_BASE ?? "http://localhost:8000";

export function Chat({ threadId }: { threadId: string }) {
  const agent = useMemo(
    () => new HttpAgent({ url: `${BASE}/agent`, threadId }),
    [threadId],
  );
  const runtime = useAgUiRuntime({ agent });

  return (
    <AssistantRuntimeProvider runtime={runtime}>
      <Thread />
    </AssistantRuntimeProvider>
  );
}
```

- [ ] **Step 2: Wire a single hardcoded conversation into the workspace**

```tsx
// use_mem0/frontend/src/Workspace.tsx
import { useEffect, useState } from "react";
import { Conversation, createConversation, listConversations, User } from "./api";
import { Chat } from "./Chat";

export function Workspace({ user }: { user: User }) {
  const [conversation, setConversation] = useState<Conversation | null>(null);

  useEffect(() => {
    listConversations().then(async (existing) => {
      setConversation(existing[0] ?? (await createConversation()));
    });
  }, []);

  if (!conversation) return null;
  return <Chat threadId={conversation.id} />;
}
```

- [ ] **Step 3: Run the smoke test manually**

Start Postgres, the backend, and the frontend:

```bash
cd use_mem0 && docker compose up -d
cd use_mem0/backend && uvicorn app.main:app --reload --port 8000
cd use_mem0/frontend && npm run dev
```

Then, in the browser:

1. Sign in with Google.
2. Send the message "Hello, my name is Sam."
3. Confirm a streamed reply appears.
4. **Reload the page.**
5. Confirm both messages are still visible.

Expected: step 5 shows the transcript. If it shows an empty pane, the history adapter is not
being exercised — proceed to Step 4.

- [ ] **Step 4: Add the history adapter if reload shows an empty pane**

```tsx
// use_mem0/frontend/src/historyAdapter.ts
import { ExportedMessageRepository } from "@assistant-ui/react";
import { fromAgUiMessages } from "@assistant-ui/react-ag-ui";
import { getMessages } from "./api";

export function makeHistoryAdapter(threadId: string) {
  return {
    async load() {
      const { messages } = await getMessages(threadId);
      return ExportedMessageRepository.fromArray(fromAgUiMessages(messages));
    },
    async append() {
      /* The LangGraph checkpointer already persists every turn server-side. */
    },
  };
}
```

Then pass it into the runtime in `Chat.tsx`:

```tsx
  const runtime = useAgUiRuntime({
    agent,
    adapters: { history: makeHistoryAdapter(threadId) },
  });
```

Re-run Step 3 and confirm the reload now shows the transcript.

- [ ] **Step 5: Record the outcome**

Add a short "Verified integrations" section to `use_mem0/README.md` stating whether reload
rehydration worked, and which of the two shapes (automatic vs. explicit history adapter) was
required. This is the first empirical confirmation of the stack's central assumption.

- [ ] **Step 6: Commit**

```bash
git add use_mem0/frontend use_mem0/README.md
git commit -m "feat: wire assistant-ui to ag-ui endpoint with history rehydration"
```

---

## Task 13: Conversation sidebar with rename and delete

**Files:**
- Create: `use_mem0/frontend/src/ConversationList.tsx`
- Create: `use_mem0/frontend/src/DeleteDialog.tsx`
- Modify: `use_mem0/frontend/src/Workspace.tsx`

**Interfaces:**
- Consumes: `listConversations`, `createConversation`, `renameConversation`,
  `deleteConversation` (Task 11).
- Produces: `<ConversationList activeId, onSelect, conversations, onChanged />` and
  `<DeleteDialog open, title, onCancel, onConfirm />`.

**Design requirements from the approved mockup — implement exactly:**
- Sidebar width `260px`, "+ New chat" at the top, account and sign-out at the **bottom**.
- Conversation titles truncate with a single-line CSS ellipsis; do not truncate in JS. The
  backend's 50-character title cap and the display truncation are independent.
- The active conversation is marked by a **background fill** (`--accent`, `#E9E2D3` — a
  distinct value from the `#F4F1EA` sidebar background), not a left border accent.
- A `⋮` affordance appears on hover, opening a menu with Rename and Delete. Dismissal on
  outside-click comes free from shadcn's `DropdownMenu` — do not hand-roll a document
  click listener.
- **Rename is inline**: the title becomes an editable field in place. Enter or blur commits;
  Escape cancels.
- **Delete opens a confirmation dialog** with this exact copy: "This removes the conversation
  and its messages. Anything Assistant Agent has already learned from it stays remembered —
  deleting a conversation doesn't erase that memory." Buttons: Cancel, Delete.
- **Never offer an undo affordance.** The dialog copy promises permanence; an undo toast
  would contradict it. Deletion must never be a single click.
- **Failed replies show an error with a Retry action**, per the mockup's error artboard. The
  backend retries a transient model failure once (Task 7) and then propagates it; this is
  the user-facing half of that path. Retry re-sends the same user message rather than
  resuming a partial stream.

- [ ] **Step 1: Write the delete dialog**

```tsx
// use_mem0/frontend/src/DeleteDialog.tsx
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";

export function DeleteDialog({
  open,
  title,
  onCancel,
  onConfirm,
}: {
  open: boolean;
  title: string;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  return (
    <Dialog open={open} onOpenChange={(next) => !next && onCancel()}>
      <DialogContent className="max-w-[420px]">
        <DialogHeader>
          <DialogTitle className="text-base">Delete “{title}”?</DialogTitle>
          <DialogDescription className="text-[13.5px]">
            This removes the conversation and its messages. Anything Assistant Agent has
            already learned from it stays remembered — deleting a conversation doesn’t
            erase that memory.
          </DialogDescription>
        </DialogHeader>
        <DialogFooter>
          <Button variant="ghost" onClick={onCancel}>
            Cancel
          </Button>
          <Button variant="destructive" onClick={onConfirm}>
            Delete
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
```

Using shadcn's `Dialog` rather than a hand-rolled overlay gives focus trapping, Escape
handling, and correct ARIA roles without extra work — which matters here because this dialog
is the guard on an irreversible action.

- [ ] **Step 2: Write the sidebar**

```tsx
// use_mem0/frontend/src/ConversationList.tsx
import { useEffect, useRef, useState } from "react";
import { MoreVertical } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Conversation, deleteConversation, renameConversation } from "./api";
import { DeleteDialog } from "./DeleteDialog";

export function ConversationList({
  conversations,
  activeId,
  onSelect,
  onChanged,
}: {
  conversations: Conversation[];
  activeId: string | null;
  onSelect: (id: string) => void;
  onChanged: () => void;
}) {
  const [renamingId, setRenamingId] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const [pendingDelete, setPendingDelete] = useState<Conversation | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (renamingId) inputRef.current?.focus();
  }, [renamingId]);

  const commitRename = async (id: string) => {
    const title = draft.trim();
    setRenamingId(null);
    if (title) {
      await renameConversation(id, title);
      onChanged();
    }
  };

  return (
    <>
      <nav className="flex flex-col gap-0.5">
        {conversations.map((conversation) => {
          const isActive = conversation.id === activeId;
          const label = conversation.title ?? "New chat";
          return (
            <div
              key={conversation.id}
              onClick={() => onSelect(conversation.id)}
              className={`group flex cursor-pointer items-center gap-1.5 rounded-md px-2.5 py-1.5 ${
                isActive ? "bg-accent" : "hover:bg-accent/50"
              }`}
            >
              {renamingId === conversation.id ? (
                <Input
                  ref={inputRef}
                  value={draft}
                  onChange={(event) => setDraft(event.target.value)}
                  onBlur={() => commitRename(conversation.id)}
                  onKeyDown={(event) => {
                    if (event.key === "Enter") commitRename(conversation.id);
                    if (event.key === "Escape") setRenamingId(null);
                  }}
                  onClick={(event) => event.stopPropagation()}
                  className="h-7 flex-1 rounded-[6px] text-[13.5px]"
                />
              ) : (
                <span className="flex-1 truncate text-[13.5px]">{label}</span>
              )}

              <DropdownMenu>
                <DropdownMenuTrigger asChild>
                  <Button
                    variant="ghost"
                    size="icon"
                    aria-label="Conversation actions"
                    onClick={(event) => event.stopPropagation()}
                    className="h-6 w-6 opacity-0 group-hover:opacity-100 data-[state=open]:opacity-100"
                  >
                    <MoreVertical className="h-3.5 w-3.5" />
                  </Button>
                </DropdownMenuTrigger>
                <DropdownMenuContent align="end">
                  <DropdownMenuItem
                    onSelect={() => {
                      setDraft(label);
                      setRenamingId(conversation.id);
                    }}
                  >
                    Rename
                  </DropdownMenuItem>
                  <DropdownMenuItem
                    variant="destructive"
                    onSelect={() => setPendingDelete(conversation)}
                  >
                    Delete
                  </DropdownMenuItem>
                </DropdownMenuContent>
              </DropdownMenu>
            </div>
          );
        })}
      </nav>

      <DeleteDialog
        open={pendingDelete !== null}
        title={pendingDelete?.title ?? "New chat"}
        onCancel={() => setPendingDelete(null)}
        onConfirm={async () => {
          const target = pendingDelete!;
          setPendingDelete(null);
          await deleteConversation(target.id);
          onChanged();
        }}
      />
    </>
  );
}
```

- [ ] **Step 3: Assemble the workspace**

```tsx
// use_mem0/frontend/src/Workspace.tsx
import { useCallback, useEffect, useState } from "react";
import {
  Conversation,
  createConversation,
  listConversations,
  logout,
  User,
} from "./api";
import { Chat } from "./Chat";
import { ConversationList } from "./ConversationList";

export function Workspace({ user }: { user: User }) {
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    const rows = await listConversations();
    setConversations(rows);
    setActiveId((current) =>
      current && rows.some((row) => row.id === current) ? current : rows[0]?.id ?? null,
    );
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const startNewChat = async () => {
    const created = await createConversation();
    await refresh();
    setActiveId(created.id);
  };

  return (
    <div className="flex h-screen bg-background">
      <aside className="flex w-[var(--sidebar-width)] flex-col border-r border-border bg-secondary p-2.5">
        <Button variant="ghost" onClick={startNewChat} className="mb-2.5 justify-start gap-2">
          <Plus className="h-4 w-4" />
          New chat
        </Button>

        <ScrollArea className="flex-1">
          <ConversationList
            conversations={conversations}
            activeId={activeId}
            onSelect={setActiveId}
            onChanged={refresh}
          />
        </ScrollArea>

        <div className="flex items-center gap-2 border-t border-border pt-2.5">
          <Avatar className="h-6 w-6 rounded-[7px]">
            {user.picture && <AvatarImage src={user.picture} alt="" />}
            <AvatarFallback className="rounded-[7px] bg-[var(--avatar-placeholder-bg)] text-[10px]">
              {(user.name ?? user.email).slice(0, 1).toUpperCase()}
            </AvatarFallback>
          </Avatar>
          <span className="flex-1 truncate text-[12.5px] text-[var(--text-account)]">
            {user.name ?? user.email}
          </span>
          <Button
            variant="ghost"
            size="sm"
            onClick={async () => {
              await logout();
              window.location.reload();
            }}
          >
            Sign out
          </Button>
        </div>
      </aside>

      <main className="flex-1">
        {activeId ? (
          <Chat key={activeId} threadId={activeId} />
        ) : (
          <div className="grid h-full place-items-center p-10">
            <p className="max-w-md text-center text-muted-foreground">
              You don’t have any conversations yet. Start one below, and I’ll remember what
              matters as we go — in this conversation and any you start later.
            </p>
          </div>
        )}
      </main>
    </div>
  );
}
```

Add to the imports at the top of this file:

```tsx
import { Plus } from "lucide-react";
import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar";
import { Button } from "@/components/ui/button";
import { ScrollArea } from "@/components/ui/scroll-area";
```

Note the `key={activeId}` on `<Chat>`: it forces a fresh runtime per conversation, so the
history adapter re-runs on switch rather than reusing the previous thread's state.

- [ ] **Step 4: Verify the interactions manually**

Run the stack as in Task 12, then confirm:
- "+ New chat" creates a conversation and selects it.
- Sending a first message titles the conversation and moves it to the top of the list.
- Switching conversations loads that conversation's transcript.
- Rename edits in place; Escape cancels; Enter commits.
- Delete opens the confirmation dialog, and Cancel leaves the conversation intact.
- With zero conversations, the empty-state copy appears.
- With `OPENAI_API_KEY` set to an invalid value, sending a message shows the error state with
  a working Retry action — not a silent hang or a blank reply.

- [ ] **Step 5: Commit**

```bash
git add use_mem0/frontend/src
git commit -m "feat: add conversation sidebar with inline rename and delete confirmation"
```

---

## Task 14: Chat surface styling

**Files:**
- Modify: `use_mem0/frontend/src/Chat.tsx`
- Create: `use_mem0/frontend/src/chat/` — composed assistant-ui primitives

**Interfaces:**
- Consumes: assistant-ui primitives, the shadcn tokens from Task 11.
- Produces: a styled `<Chat threadId />` matching the mockup's chat artboard.

**Why this is its own task:** assistant-ui is headless — it ships behaviour with no
appearance. Task 12 renders the default `<Thread />` to prove the integration works; this
task makes it look like the approved design. Those are separate concerns and separate
review gates.

**Design requirements from the approved mockup:**
- Message column `max-width: var(--message-max-width)` (640px), centred.
- **Bubble radius is directional** — `var(--radius-bubble-user)` /
  `var(--radius-bubble-assistant)`. Three corners at 14px, one sharp at 2px, and the sharp
  corner mirrors the side the bubble aligns to. In Tailwind use an arbitrary value:
  `rounded-[14px_14px_2px_14px]`. A flat `rounded-[14px]` loses the tail and is wrong.
- **User bubbles use `var(--bubble-user-bg)` (`#EAF2F1`), a light teal — not solid
  `--primary`.** Text stays `--foreground`. Assistant bubbles use `--secondary`.
- A small avatar mark (radius `var(--radius-mark)`, 7px) sits beside assistant messages
  **and** beside the typing indicator, so the assistant's identity is consistent between
  streaming and settled states.
- The composer is custom-styled, not a default input.
- A `~450ms` skeleton shows while a conversation's history loads on switch, using
  `var(--bubble-skeleton-bg)` (`#ECE8DF`). Real network latency exists even when rehydration
  works, so this is an honest loading state rather than cover for a gap.
- Failed replies render an error banner using the error family — background
  `var(--error-bg)`, border `var(--error-border)`, message text `var(--error-text)` — with
  the Retry button and error icon in `var(--destructive)`. Note this is **four distinct
  values, not one destructive colour**.

- [ ] **Step 1: Compose the styled thread**

Replace the bare `<Thread />` with assistant-ui's composable primitives, which accept
`className` and `asChild` in the Radix style. Follow the structure in assistant-ui's
docs for `ThreadPrimitive`, `MessagePrimitive`, and `ComposerPrimitive`, applying the
tokens from Task 11.

Keep each piece in its own file under `src/chat/` (`ThreadView.tsx`, `UserMessage.tsx`,
`AssistantMessage.tsx`, `Composer.tsx`, `LoadingSkeleton.tsx`) rather than one large
component — these are edited independently and reviewed independently.

- [ ] **Step 2: Add the history-loading skeleton**

Render the skeleton while the history adapter's `load()` is in flight, keyed on `threadId`
so it appears on every conversation switch.

- [ ] **Step 3: Add the error and retry state**

Surface a failed run with the mockup's error treatment and a Retry action that re-sends the
last user message. Verify against a real failure by setting `OPENAI_API_KEY` to an invalid
value.

- [ ] **Step 4: Verify against the mockup**

Compare each artboard side by side with the running app: default conversation, empty
conversation, zero-conversations first login, and the error state. Confirm the palette,
bubble radius, column width, and avatar placement match.

- [ ] **Step 5: Commit**

```bash
git add use_mem0/frontend/src
git commit -m "feat: style chat surface to match the approved design"
```

---

## Task 15: End-to-end acceptance

**Files:**
- Modify: `use_mem0/README.md`

**Interfaces:**
- Consumes: the whole stack.
- Produces: a documented record of observed behaviour, including the mem0 contradiction
  finding required by the spec.

**This task has no automated assertions.** Its deliverable is written findings. The
contradiction scenario in particular is an observation exercise, not a pass/fail test — do
not convert it into an assertion against behaviour that has not been observed yet.

- [ ] **Step 1: Memory recall across conversations**

1. Sign in as user A. Say "I'm vegetarian."
2. Start a **new** conversation. Ask "What should I have for dinner?"
3. Confirm the reply reflects the stored preference.

Record the result in `use_mem0/README.md`.

- [ ] **Step 2: Per-user memory isolation**

1. Sign out. Sign in as a **different** Google account (user B).
2. Ask "What do you know about me?"
3. Confirm none of user A's memories appear.
4. Confirm user B's sidebar shows none of user A's conversations.

- [ ] **Step 3: The contradiction scenario**

1. As user A, in a new conversation: "Actually, I eat fish now."
2. In a further new conversation: "Suggest me dinner."
3. Open the mem0 dashboard. Record which of these happened:
   - mem0 **updated** the original memory,
   - mem0 **stored both** and returns both on search, or
   - mem0 **kept them separate** with no relationship.
4. Open the LangSmith trace for that turn and record what `retrieve_memories` actually
   returned, and whether the model followed the newer statement.

Write the finding into `use_mem0/README.md` under "Observed mem0 behaviour". If mem0 returns
contradictory memories rather than reconciling them, note that a resolution strategy has
become a real requirement, and say so in the README — the spec defers that decision until
there is evidence for it.

- [ ] **Step 4: The memory on/off comparison**

1. Set `MEMORY_RETRIEVAL_ENABLED=false`, restart the backend.
2. Ask the same memory-dependent question from Step 1.
3. Compare the reply and the LangSmith trace against the memory-enabled run.
4. Confirm `retrieve_memories` is skipped in the trace while `write_memories` still runs.

Record whether memory made an observable difference.

- [ ] **Step 5: Failure degradation**

1. Set `MEM0_API_KEY` to an invalid value, restart the backend.
2. Send a message.
3. Confirm a reply is still produced, with a logged warning and no user-visible error.

- [ ] **Step 6: Observability**

Confirm in LangSmith that each turn appears as a single trace, that turns from one
conversation group under a shared `thread_id`, and that the `mem0.search` and `mem0.add`
spans appear nested inside it — the `@traceable` decorators from Task 6 are what make the
memory steps visible.

- [ ] **Step 7: Commit**

```bash
git add use_mem0/README.md
git commit -m "docs: record end-to-end acceptance findings and mem0 behaviour"
```

---

## Deferred to a second iteration

Recorded in the spec, deliberately excluded from this plan. Both require verifying mem0 API
surfaces that the design work did not confirm — only `add` and `search` were verified — so
each needs a research step before it can be planned.

- **Explicit forget capability** — "forget that I mentioned my address." Needs verification
  of mem0's delete/update endpoints. Note this is what makes conversation deletion honest:
  today, deleting a conversation leaves its derived memories in place.
- **Control over what gets extracted** — mem0 performs its own extraction on `add` with no
  steer from us. Needs verification that mem0 Platform exposes custom instructions or
  categories.

