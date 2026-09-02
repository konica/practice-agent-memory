# AWS Deployment — Application Changes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the `use_mem0` chatbot correct behind a reverse proxy and packageable as one portable container image, so it can be deployed to AWS without silent production-only breakage.

**Architecture:** Nine focused changes to the FastAPI backend and Vite frontend, then a multi-stage Dockerfile that produces a single arm64 image runnable under `docker compose`, Lambda, Fargate, and Lightsail. Nothing here touches AWS; every task is verifiable locally against the existing Postgres from `make up`.

**Tech Stack:** Python 3.11+ (image pins 3.12), FastAPI, uvicorn, psycopg3 + psycopg_pool, LangGraph postgres checkpointer, pytest, React + Vite, Docker (buildx, arm64).

**Spec:** `docs/superpowers/specs/2026-09-02-aws-serverless-deployment-design.md`

## Global Constraints

- Python floor stays `>=3.11` in `pyproject.toml`; the Docker image pins **3.12** (better wheel coverage, and the floor for SnapStart should it become viable).
- Image architecture is **`linux/arm64`**.
- Tests need a running Postgres. `make up` provides it; tests read `TEST_DATABASE_URL`, defaulting to `postgresql://app:app@localhost:5432/app`.
- Run the suite with `cd use_mem0/backend && UV_PROJECT_ENVIRONMENT="$(../venv-path)" uv run pytest`.
- The existing test conventions are the house style: a module-level `ENV`/`COMPLETE_ENV` dict, `TestClient(create_app(load_settings(ENV)))`, no `conftest.py`. Follow them.
- `asyncio_mode = "auto"` is set, so async tests need no decorator.
- **Never split the SPA and API onto different origins.** `samesite="lax"` is only correct because they share one.
- Do not bake a backend hostname into the frontend bundle.

---

### Task 1: `PUBLIC_BASE_URL` replaces `request.url_for()`

The OAuth redirect URI is currently derived from the inbound request. Behind CloudFront → Lambda Function URL that yields the `*.lambda-url.on.aws` host, and Google rejects it as unregistered. This is the single highest-value change in the plan: it breaks only in production, and only at login.

**Files:**
- Modify: `use_mem0/backend/src/app/config.py:5-14` (REQUIRED_KEYS), `:22-31` (Settings), `:34-52` (load_settings)
- Modify: `use_mem0/backend/src/app/auth/routes.py:21-22`, `:29`, `:53`, `:69`
- Modify: `use_mem0/backend/tests/test_config.py:4-13`, `use_mem0/backend/tests/test_app.py:11-20`
- Modify: `use_mem0/.env.example`
- Test: `use_mem0/backend/tests/test_config.py`, `use_mem0/backend/tests/test_auth_routes.py` (create)

**Interfaces:**
- Consumes: nothing.
- Produces: `Settings.public_base_url: str`, required env var `PUBLIC_BASE_URL`. Tasks 4, 8 and 9 rely on this field existing.

- [ ] **Step 1: Write the failing config test**

Add to `use_mem0/backend/tests/test_config.py`, and add `"PUBLIC_BASE_URL": "http://localhost:8000"` to the `COMPLETE_ENV` dict at the top of that file:

```python
def test_public_base_url_is_required():
    env = {k: v for k, v in COMPLETE_ENV.items() if k != "PUBLIC_BASE_URL"}
    with pytest.raises(MissingConfigError) as exc:
        load_settings(env)
    assert "PUBLIC_BASE_URL" in str(exc.value)


def test_public_base_url_loads():
    env = {**COMPLETE_ENV, "PUBLIC_BASE_URL": "https://d123.cloudfront.net"}
    assert load_settings(env).public_base_url == "https://d123.cloudfront.net"
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `cd use_mem0/backend && UV_PROJECT_ENVIRONMENT="$(../venv-path)" uv run pytest tests/test_config.py -v`
Expected: FAIL — `AttributeError: 'Settings' object has no attribute 'public_base_url'`, and `test_public_base_url_is_required` fails because nothing raises.

- [ ] **Step 3: Add the setting**

In `use_mem0/backend/src/app/config.py`, add `"PUBLIC_BASE_URL"` to `REQUIRED_KEYS`, add the field to `Settings`, and read it in `load_settings`:

```python
REQUIRED_KEYS = (
    "OPENAI_API_KEY",
    "MEM0_API_KEY",
    "LANGSMITH_API_KEY",
    "LANGSMITH_PROJECT",
    "GOOGLE_CLIENT_ID",
    "GOOGLE_CLIENT_SECRET",
    "DATABASE_URL",
    "SESSION_SECRET",
    "PUBLIC_BASE_URL",
)
```

In the `Settings` dataclass add, after `session_secret: str`:

```python
    public_base_url: str
```

In `load_settings`, add to the `Settings(...)` construction:

```python
        public_base_url=env["PUBLIC_BASE_URL"].rstrip("/"),
```

`rstrip("/")` so that `https://host/` and `https://host` produce the same redirect URI. A trailing slash would otherwise yield `https://host//auth/callback`, which Google treats as a different, unregistered URI.

- [ ] **Step 4: Run the config tests**

Run: `cd use_mem0/backend && UV_PROJECT_ENVIRONMENT="$(../venv-path)" uv run pytest tests/test_config.py -v`
Expected: PASS

- [ ] **Step 5: Write the failing redirect-URI test**

Create `use_mem0/backend/tests/test_auth_routes.py`:

```python
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
    "PUBLIC_BASE_URL": "https://d123.cloudfront.net",
}


def test_redirect_uri_comes_from_config_not_the_request_host():
    """The proxy's Host must not decide where Google sends the user back.

    Behind CloudFront the inbound Host is the Lambda function URL, which is not
    registered with Google. Deriving the redirect URI from the request is how
    login breaks in production and only in production.
    """
    with TestClient(create_app(load_settings(ENV))) as client:
        response = client.get(
            "/auth/login",
            follow_redirects=False,
            headers={"Host": "abc123.lambda-url.us-east-1.on.aws"},
        )

    assert response.status_code == 307
    location = response.headers["location"]
    assert "redirect_uri=https%3A%2F%2Fd123.cloudfront.net%2Fauth%2Fcallback" in location
    assert "lambda-url" not in location
```

- [ ] **Step 6: Run it to confirm it fails**

Run: `cd use_mem0/backend && UV_PROJECT_ENVIRONMENT="$(../venv-path)" uv run pytest tests/test_auth_routes.py -v`
Expected: FAIL — the location contains the `lambda-url` host, because `_redirect_uri` used `request.url_for`.

- [ ] **Step 7: Build the redirect URI from settings**

In `use_mem0/backend/src/app/auth/routes.py`, replace lines 21-22:

```python
def _redirect_uri(request: Request) -> str:
    """Where Google sends the user back.

    Read from configuration, never from the request: behind a proxy the inbound
    Host is the origin's own hostname, which Google has never heard of.
    """
    return f"{request.app.state.settings.public_base_url}/auth/callback"
```

The two call sites (`:29` in `login`, `:53` in `callback`) already pass `request`, so they need no change.

Then change the post-callback redirect at line 69 from `settings.frontend_origin` to the public base URL, since in a single-origin deployment the SPA is served from the same host:

```python
    response = RedirectResponse(settings.public_base_url + "/")
```

- [ ] **Step 8: Run the tests**

Run: `cd use_mem0/backend && UV_PROJECT_ENVIRONMENT="$(../venv-path)" uv run pytest tests/test_auth_routes.py tests/test_config.py -v`
Expected: PASS

- [ ] **Step 9: Update the other test fixtures and the env example**

Add `"PUBLIC_BASE_URL": "http://localhost:8000",` to the `ENV` dict in `use_mem0/backend/tests/test_app.py` (after `SESSION_SECRET`).

Add to `use_mem0/.env.example`, after `FRONTEND_ORIGIN`:

```
PUBLIC_BASE_URL=http://localhost:8000
```

- [ ] **Step 10: Run the whole suite**

Run: `cd use_mem0/backend && UV_PROJECT_ENVIRONMENT="$(../venv-path)" uv run pytest -v`
Expected: PASS. If `test_app.py` fails with `MissingConfigError`, its `ENV` dict was not updated.

- [ ] **Step 11: Commit**

```bash
git add use_mem0/backend/src/app/config.py use_mem0/backend/src/app/auth/routes.py \
        use_mem0/backend/tests/test_config.py use_mem0/backend/tests/test_app.py \
        use_mem0/backend/tests/test_auth_routes.py use_mem0/.env.example
git commit -m "feat: build the OAuth redirect URI from PUBLIC_BASE_URL

Deriving it from the request Host breaks behind any reverse proxy, and
breaks only in production, at login."
```

---

### Task 2: Secure cookies

Both cookies are minted with `secure=False`. Over HTTPS that is a downgrade risk; `localhost` is a secure context so local development is unaffected by the change.

**Files:**
- Modify: `use_mem0/backend/src/app/auth/routes.py:32`, `:76`
- Test: `use_mem0/backend/tests/test_auth_routes.py`

**Interfaces:**
- Consumes: `ENV` and the app factory from Task 1's `test_auth_routes.py`.
- Produces: nothing new.

- [ ] **Step 1: Write the failing tests**

Append to `use_mem0/backend/tests/test_auth_routes.py`:

```python
def test_oauth_state_cookie_is_secure():
    with TestClient(create_app(load_settings(ENV))) as client:
        response = client.get("/auth/login", follow_redirects=False)

    cookie = response.headers["set-cookie"]
    assert "secure" in cookie.lower()
    assert "httponly" in cookie.lower()


def test_callback_sets_a_secure_session_cookie(monkeypatch):
    from app.auth import routes as auth_routes
    from app.auth.google import GoogleIdentity

    monkeypatch.setattr(
        auth_routes,
        "exchange_code",
        lambda code, client_id, client_secret, redirect_uri, http: GoogleIdentity(
            sub="google-sub-secure-test",
            email="secure@example.com",
            name=None,
            picture=None,
        ),
    )

    with TestClient(create_app(load_settings(ENV))) as client:
        client.cookies.set("oauth_state", "state-value")
        response = client.get(
            "/auth/callback?code=any&state=state-value", follow_redirects=False
        )

    assert response.status_code == 307
    session_cookie = next(
        c for c in response.headers.get_list("set-cookie") if c.startswith("session=")
    )
    assert "secure" in session_cookie.lower()
    assert "httponly" in session_cookie.lower()
```

This test writes a real row to `users`, so Postgres must be running. `exchange_code` is imported into `routes` by name, so patching it on the `routes` module is what intercepts the call.

- [ ] **Step 2: Run to confirm they fail**

Run: `cd use_mem0/backend && UV_PROJECT_ENVIRONMENT="$(../venv-path)" uv run pytest tests/test_auth_routes.py -k secure -v`
Expected: FAIL — `assert "secure" in cookie.lower()` fails; the header carries no `Secure` attribute.

- [ ] **Step 3: Set `secure=True`**

In `use_mem0/backend/src/app/auth/routes.py` line 32, change the state cookie:

```python
    response.set_cookie(
        STATE_COOKIE, state, httponly=True, samesite="lax", max_age=600, secure=True
    )
```

And line 76, the session cookie:

```python
        secure=True,
```

- [ ] **Step 4: Run the tests**

Run: `cd use_mem0/backend && UV_PROJECT_ENVIRONMENT="$(../venv-path)" uv run pytest tests/test_auth_routes.py -v`
Expected: PASS

- [ ] **Step 5: Run the whole suite**

Run: `cd use_mem0/backend && UV_PROJECT_ENVIRONMENT="$(../venv-path)" uv run pytest -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add use_mem0/backend/src/app/auth/routes.py use_mem0/backend/tests/test_auth_routes.py
git commit -m "feat: mint session and OAuth state cookies with Secure

localhost is a secure context, so local development is unaffected."
```

---

### Task 3: Configurable connection pool size

`open_pool` constructs `ConnectionPool` without `min_size`, so it defaults to **4**. Under Lambda, connections multiply by concurrency: 10 reserved executions × (4 pool + 1 checkpointer) = 50 connections against a free-tier Postgres.

`min_size` stays at **1**, not 0. `open_pool` calls `pool.wait()`, which waits for `min_size` connections to be established — that is what makes `test_startup_fails_loudly_when_postgres_is_unavailable` pass. With `min_size=0` the wait returns immediately and an unreachable database would no longer abort startup, silently converting a loud failure into a broken deployment. Bound Lambda by `max_size` instead.

**Files:**
- Modify: `use_mem0/backend/src/app/db/engine.py:5-12`
- Modify: `use_mem0/backend/src/app/config.py`
- Modify: `use_mem0/backend/src/app/main.py:47` (the `open_pool` call)
- Test: `use_mem0/backend/tests/test_engine.py` (create), `use_mem0/backend/tests/test_config.py`

**Interfaces:**
- Consumes: `Settings` from Task 1.
- Produces: `Settings.db_pool_min_size: int`, `Settings.db_pool_max_size: int`; `open_pool(database_url, *, min_size=1, max_size=4)`.

- [ ] **Step 1: Write the failing settings test**

Append to `use_mem0/backend/tests/test_config.py`:

```python
def test_pool_sizes_default_to_one_and_four():
    settings = load_settings(COMPLETE_ENV)
    assert settings.db_pool_min_size == 1
    assert settings.db_pool_max_size == 4


def test_pool_sizes_read_from_env():
    env = {**COMPLETE_ENV, "DB_POOL_MIN_SIZE": "1", "DB_POOL_MAX_SIZE": "1"}
    settings = load_settings(env)
    assert settings.db_pool_min_size == 1
    assert settings.db_pool_max_size == 1
```

- [ ] **Step 2: Run to confirm failure**

Run: `cd use_mem0/backend && UV_PROJECT_ENVIRONMENT="$(../venv-path)" uv run pytest tests/test_config.py -k pool -v`
Expected: FAIL — `AttributeError: 'Settings' object has no attribute 'db_pool_min_size'`

- [ ] **Step 3: Add the settings**

In `use_mem0/backend/src/app/config.py`, add two fields to `Settings` after `frontend_origin: str`:

```python
    db_pool_min_size: int
    db_pool_max_size: int
```

And in `load_settings`, add to the `Settings(...)` construction:

```python
        db_pool_min_size=int(env.get("DB_POOL_MIN_SIZE", "1")),
        db_pool_max_size=int(env.get("DB_POOL_MAX_SIZE", "4")),
```

These are optional with defaults — unlike `PUBLIC_BASE_URL`, a wrong value degrades throughput rather than breaking correctness, and the default is right for every deployment except Lambda.

- [ ] **Step 4: Run the config tests**

Run: `cd use_mem0/backend && UV_PROJECT_ENVIRONMENT="$(../venv-path)" uv run pytest tests/test_config.py -v`
Expected: PASS

- [ ] **Step 5: Write the failing engine test**

Create `use_mem0/backend/tests/test_engine.py`:

```python
import os

from app.db.engine import open_pool

DB_URL = os.environ.get("TEST_DATABASE_URL", "postgresql://app:app@localhost:5432/app")


def test_open_pool_defaults_bound_the_pool_at_four():
    pool = open_pool(DB_URL)
    try:
        assert pool.min_size == 1
        assert pool.max_size == 4
    finally:
        pool.close()


def test_open_pool_accepts_an_explicit_ceiling():
    """Lambda bounds connections by max_size, keeping min_size at 1.

    min_size must stay >= 1 because open_pool's pool.wait() is what makes an
    unreachable database abort startup rather than fail on the first request.
    """
    pool = open_pool(DB_URL, min_size=1, max_size=1)
    try:
        assert pool.max_size == 1
    finally:
        pool.close()
```

- [ ] **Step 6: Run to confirm failure**

Run: `cd use_mem0/backend && UV_PROJECT_ENVIRONMENT="$(../venv-path)" uv run pytest tests/test_engine.py -v`
Expected: FAIL — `TypeError: open_pool() got an unexpected keyword argument 'min_size'`, and the default `max_size` assertion fails.

- [ ] **Step 7: Make the sizes parameters**

Replace `use_mem0/backend/src/app/db/engine.py` entirely:

```python
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

DEFAULT_MIN_SIZE = 1
DEFAULT_MAX_SIZE = 4


def open_pool(
    database_url: str,
    *,
    min_size: int = DEFAULT_MIN_SIZE,
    max_size: int = DEFAULT_MAX_SIZE,
) -> ConnectionPool:
    """Open a pool and prove the database is reachable before returning.

    min_size stays at least 1 on purpose: `wait()` blocks until that many
    connections exist, which is what turns an unreachable database into a
    failed startup rather than a failure on the first request.
    """
    pool = ConnectionPool(
        conninfo=database_url,
        min_size=min_size,
        max_size=max_size,
        kwargs={"row_factory": dict_row, "autocommit": True},
        open=True,
    )
    pool.wait()
    return pool
```

- [ ] **Step 8: Wire it into the app**

In `use_mem0/backend/src/app/main.py`, change the `open_pool` call in the lifespan:

```python
        app.state.pool = open_pool(
            settings.database_url,
            min_size=settings.db_pool_min_size,
            max_size=settings.db_pool_max_size,
        )
```

- [ ] **Step 9: Run the whole suite**

Run: `cd use_mem0/backend && UV_PROJECT_ENVIRONMENT="$(../venv-path)" uv run pytest -v`
Expected: PASS, including `test_startup_fails_loudly_when_postgres_is_unavailable` — verify that one specifically, it is the guarantee this task must not break.

- [ ] **Step 10: Commit**

```bash
git add use_mem0/backend/src/app/db/engine.py use_mem0/backend/src/app/config.py \
        use_mem0/backend/src/app/main.py use_mem0/backend/tests/test_engine.py \
        use_mem0/backend/tests/test_config.py
git commit -m "feat: make the connection pool size configurable

Defaults 1/4. Lambda sets max_size=1 so connections do not multiply by
concurrency; min_size stays >=1 so startup still fails loudly."
```

---

### Task 4: Split `/health` (liveness) from `/ready` (readiness)

`/health` currently returns `ok` unconditionally, which is correct for a liveness probe and useless as a readiness signal. The Lambda Web Adapter polls `/health` at startup, and any future ECS health check would too — so it must not touch the database, or a database blip would kill healthy containers. `/ready` is the endpoint that answers "can this process actually serve a chat?"

This task comes before the checkpointer change because Task 5 uses `/ready` as its regression test.

**Files:**
- Modify: `use_mem0/backend/src/app/main.py:81-83`
- Test: `use_mem0/backend/tests/test_app.py`

**Interfaces:**
- Consumes: `app.state.pool`, `app.state.checkpointer`.
- Produces: `GET /health` → `{"status": "ok"}` (no I/O); `GET /ready` → `{"status": "ready"}` 200 or `{"status": "unready", "detail": str}` 503. Task 5 asserts against `/ready`; the Dockerfile in Task 10 and the Terraform in the infra plan point their readiness checks at `/health`.

- [ ] **Step 1: Write the failing tests**

Append to `use_mem0/backend/tests/test_app.py`:

```python
def test_health_does_no_io():
    """Liveness must not depend on Postgres.

    The Lambda Web Adapter polls this at startup, as would an ECS health check.
    If it touched the database, a blip would kill otherwise-healthy containers.
    """
    settings = load_settings({**ENV, "DATABASE_URL": DB_URL})
    with TestClient(create_app(settings)) as client:
        with connect(DB_URL, autocommit=True) as conn:
            conn.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE pid <> pg_backend_pid() AND datname = current_database()"
            )
        assert client.get("/health").json() == {"status": "ok"}


def test_ready_reports_readiness():
    with TestClient(create_app(load_settings(ENV))) as client:
        response = client.get("/ready")
    assert response.status_code == 200
    assert response.json()["status"] == "ready"
```

Add `from psycopg import connect` to the imports at the top of `test_app.py` (it already imports `OperationalError` from `psycopg`, so extend that line to `from psycopg import OperationalError, connect`).

- [ ] **Step 2: Run to confirm failure**

Run: `cd use_mem0/backend && UV_PROJECT_ENVIRONMENT="$(../venv-path)" uv run pytest tests/test_app.py -k "health_does_no_io or ready" -v`
Expected: FAIL on `test_ready_reports_readiness` with a 404 — `/ready` does not exist.

- [ ] **Step 3: Keep the checkpointer on app state**

In `use_mem0/backend/src/app/main.py`, inside the lifespan, capture the checkpointer so `/ready` can probe it. Change:

```python
        app.state.graph = build_graph(
            MemoryStore(LazyMemoryClient(settings.mem0_api_key)),
            ChatOpenAI(model=CHAT_MODEL, api_key=settings.openai_api_key),
            await checkpointer_cm.__aenter__(),
        )
```

to:

```python
        app.state.checkpointer = await checkpointer_cm.__aenter__()
        app.state.graph = build_graph(
            MemoryStore(LazyMemoryClient(settings.mem0_api_key)),
            ChatOpenAI(model=CHAT_MODEL, api_key=settings.openai_api_key),
            app.state.checkpointer,
        )
```

- [ ] **Step 4: Add the two endpoints**

In `use_mem0/backend/src/app/main.py`, replace the existing `/health` handler with:

```python
    @app.get("/health")
    def health() -> dict[str, str]:
        """Liveness. Deliberately does no I/O — see /ready for readiness."""
        return {"status": "ok"}

    @app.get("/ready")
    async def ready() -> JSONResponse:
        """Readiness: can this process actually serve a chat turn?

        Probes both the request pool and the checkpointer connection. The
        checkpointer is the one that can die silently — it holds its own
        connection, and without this probe a poisoned one fails every chat
        forever while /health still answers ok.
        """
        try:
            with app.state.pool.connection() as conn:
                conn.execute("SELECT 1")
            await app.state.checkpointer.aget_tuple(
                {"configurable": {"thread_id": READINESS_THREAD_ID}}
            )
        except Exception as exc:
            logger.warning("readiness probe failed: %s", exc)
            return JSONResponse(
                {"status": "unready", "detail": str(exc)}, status_code=503
            )
        return JSONResponse({"status": "ready"})
```

Add at the top of `main.py`, after the existing imports:

```python
import logging

from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)

# A thread id that will never hold a checkpoint. aget_tuple returns None for it,
# which is a complete round-trip through the checkpointer's connection without
# depending on any conversation existing.
READINESS_THREAD_ID = "00000000-0000-0000-0000-000000000000"
```

- [ ] **Step 5: Run the tests**

Run: `cd use_mem0/backend && UV_PROJECT_ENVIRONMENT="$(../venv-path)" uv run pytest tests/test_app.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add use_mem0/backend/src/app/main.py use_mem0/backend/tests/test_app.py
git commit -m "feat: split /health liveness from /ready readiness

/health does no I/O so a database blip cannot kill healthy containers.
/ready probes the pool and the checkpointer, which is the connection
that can otherwise die silently."
```

---

### Task 5: Give the checkpointer a connection pool

`AsyncPostgresSaver.from_conn_string` opens exactly one `AsyncConnection` guarded by a single `asyncio.Lock`, held for the process lifetime, with no reconnect. A dropped connection means every chat in the process fails forever. `AsyncPostgresSaver.__init__` accepts an `AsyncConnectionPool`, which fixes reconnection and removes the lock bottleneck.

`prepare_threshold=None` is load-bearing: `from_conn_string` passes `0`, meaning *prepare on first execution*, which makes the saver incompatible with any transaction-mode pooler — including Neon's pooled endpoint. `None` disables prepared statements.

`row_factory=dict_row` is equally load-bearing: the saver's queries expect dict rows, which `from_conn_string` sets for you and a hand-built pool does not.

**Files:**
- Modify: `use_mem0/backend/src/app/main.py:53-66` (lifespan checkpointer construction and shutdown)
- Test: `use_mem0/backend/tests/test_app.py`

**Interfaces:**
- Consumes: `/ready` from Task 4.
- Produces: `app.state.checkpointer_pool: AsyncConnectionPool`. Replaces `app.state.checkpointer_cm`.

- [ ] **Step 1: Write the failing test**

Append to `use_mem0/backend/tests/test_app.py`:

```python
def test_checkpointer_recovers_after_its_connection_is_dropped():
    """A dropped checkpointer connection must not poison the process.

    from_conn_string holds one connection for the process lifetime with no
    reconnect, so a failover or an idle kill breaks every subsequent chat while
    /health still answers ok. A pool reconnects.
    """
    with TestClient(create_app(load_settings(ENV))) as client:
        assert client.get("/ready").status_code == 200

        with connect(DB_URL, autocommit=True) as conn:
            conn.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE pid <> pg_backend_pid() AND datname = current_database()"
            )

        assert client.get("/ready").status_code == 200
```

- [ ] **Step 2: Run to confirm failure**

Run: `cd use_mem0/backend && UV_PROJECT_ENVIRONMENT="$(../venv-path)" uv run pytest tests/test_app.py -k recovers -v`
Expected: FAIL — the second `/ready` returns 503; the single connection is dead and nothing re-establishes it.

- [ ] **Step 3: Replace the checkpointer construction**

In `use_mem0/backend/src/app/main.py`, add the imports:

```python
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool
```

Then replace the `checkpointer_cm` block in the lifespan:

```python
        # A pool, not from_conn_string's single connection: that one is held for
        # the process lifetime with no reconnect, so a failover poisons every
        # later chat while /health still answers ok.
        #
        # prepare_threshold=None disables prepared statements. from_conn_string
        # passes 0 (prepare on first use), which makes the saver incompatible
        # with any transaction-mode pooler, Neon's pooled endpoint included.
        #
        # row_factory=dict_row is required by the saver's own queries;
        # from_conn_string sets it for you and a hand-built pool does not.
        checkpointer_pool = AsyncConnectionPool(
            conninfo=settings.database_url,
            min_size=1,
            max_size=settings.db_pool_max_size,
            open=False,
            kwargs={
                "autocommit": True,
                "prepare_threshold": None,
                "row_factory": dict_row,
            },
        )
        await checkpointer_pool.open(wait=True)
        app.state.checkpointer_pool = checkpointer_pool
        app.state.checkpointer = AsyncPostgresSaver(conn=checkpointer_pool)
        app.state.graph = build_graph(
            MemoryStore(LazyMemoryClient(settings.mem0_api_key)),
            ChatOpenAI(model=CHAT_MODEL, api_key=settings.openai_api_key),
            app.state.checkpointer,
        )
```

And replace the shutdown block after `yield`:

```python
        app.state.pool.close()
        await checkpointer_pool.close()
```

Remove the now-unused `app.state.checkpointer_cm` assignment and the `checkpointer_cm.__aexit__(...)` call.

- [ ] **Step 4: Run the test**

Run: `cd use_mem0/backend && UV_PROJECT_ENVIRONMENT="$(../venv-path)" uv run pytest tests/test_app.py -k recovers -v`
Expected: PASS

- [ ] **Step 5: Run the whole suite**

Run: `cd use_mem0/backend && UV_PROJECT_ENVIRONMENT="$(../venv-path)" uv run pytest -v`
Expected: PASS. `tests/test_agui.py` and `tests/test_history_endpoint.py` exercise the graph through the checkpointer; if either fails with a row-access error, `row_factory=dict_row` is missing from the pool kwargs.

- [ ] **Step 6: Commit**

```bash
git add use_mem0/backend/src/app/main.py use_mem0/backend/tests/test_app.py
git commit -m "fix: give the checkpointer a connection pool

from_conn_string's single connection has no reconnect: one drop breaks
every later chat while /health still answers ok. prepare_threshold=None
also unlocks transaction-mode poolers such as Neon's pooled endpoint."
```

---

### Task 6: Move migrations out of the lifespan and take an advisory lock

Migrations run inside the FastAPI lifespan. With more than one process starting at once — every Lambda cold start, every rolling deploy — they race, and all three failure modes are real:

1. Concurrent `CREATE TABLE IF NOT EXISTS` collides in the catalog: `duplicate key value violates unique constraint "pg_type_typname_nsp_index"`.
2. LangGraph's `checkpoint_migrations` has an `INTEGER PRIMARY KEY`; the loser gets a `UniqueViolation` and dies rather than retrying.
3. An aborted `CREATE INDEX CONCURRENTLY` leaves a permanently INVALID index that `IF NOT EXISTS` skips forever — silent and permanent.

**Files:**
- Modify: `use_mem0/backend/src/app/db/migrate.py`
- Modify: `use_mem0/backend/src/app/main.py` (drop the `run_migrations` call and its import)
- Modify: `use_mem0/up` (run migrations before starting the backend)
- Test: `use_mem0/backend/tests/test_migrate.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `python -m app.db.migrate` CLI entrypoint reading `DATABASE_URL`; `run_migrations(database_url)` keeps its signature and gains the lock. The infra plan's CI `migrate` stage invokes the CLI.

- [ ] **Step 1: Write the failing concurrency test**

Append to `use_mem0/backend/tests/test_migrate.py`:

```python
def test_concurrent_migrations_do_not_race(clean_db):
    """Four processes starting at once must not corrupt the schema.

    Without a lock this raises either a pg_type catalog collision from the
    concurrent CREATE TABLE IF NOT EXISTS, or a UniqueViolation on LangGraph's
    checkpoint_migrations integer primary key.
    """
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda _: run_migrations(clean_db), range(4)))

    with connect(clean_db) as conn:
        rows = conn.execute(
            "SELECT indexrelid::regclass::text FROM pg_index WHERE NOT indisvalid"
        ).fetchall()
    assert rows == [], f"migrations left invalid indexes behind: {rows}"
```

- [ ] **Step 2: Run to confirm it fails**

Run: `cd use_mem0/backend && UV_PROJECT_ENVIRONMENT="$(../venv-path)" uv run pytest tests/test_migrate.py -k concurrent -v`
Expected: FAIL — a `UniqueViolation` or a duplicate-key error on `pg_type_typname_nsp_index`. It is timing-dependent; if it passes on the first run, run it a few times (`--count` via `pytest-repeat` is not installed, so just re-run) before concluding the lock is unnecessary. It is not.

- [ ] **Step 3: Take the lock and add the entrypoint**

Replace `use_mem0/backend/src/app/db/migrate.py` entirely:

```python
"""Apply the schema. Run as a deploy step, never from a serving process.

Both statements below race when several processes start at once: the app tables
collide in the pg_type catalog, and LangGraph's checkpoint_migrations has an
INTEGER PRIMARY KEY whose loser raises UniqueViolation and dies. The advisory
lock serialises them.
"""

import os
import sys
from pathlib import Path

from langgraph.checkpoint.postgres import PostgresSaver
from psycopg import connect

SCHEMA_PATH = Path(__file__).with_name("schema.sql")

# An arbitrary constant. Any process migrating this database must use the same
# one; it is namespaced by the database, so it need not be unique globally.
MIGRATION_LOCK_KEY = 8_927_431_005


def run_migrations(database_url: str) -> None:
    """Apply the application schema, then LangGraph's checkpointer schema.

    PostgresSaver.setup() must be called explicitly by the application; the
    checkpointer does not create its own tables lazily, and the graph fails on
    missing tables without it.

    The lock is session-level and held across both steps on one connection.
    Blocking, not try-lock: a late starter must wait rather than proceed against
    a half-migrated schema.
    """
    with connect(database_url, autocommit=True) as conn:
        conn.execute("SET lock_timeout = '30s'")
        conn.execute("SELECT pg_advisory_lock(%s)", (MIGRATION_LOCK_KEY,))
        try:
            conn.execute(SCHEMA_PATH.read_text())
            with PostgresSaver.from_conn_string(database_url) as checkpointer:
                checkpointer.setup()
        finally:
            conn.execute("SELECT pg_advisory_unlock(%s)", (MIGRATION_LOCK_KEY,))


def main() -> int:
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        print("DATABASE_URL is not set", file=sys.stderr)
        return 1
    run_migrations(database_url)
    print("migrations applied")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run the migrate tests**

Run: `cd use_mem0/backend && UV_PROJECT_ENVIRONMENT="$(../venv-path)" uv run pytest tests/test_migrate.py -v`
Expected: PASS, all four tests.

- [ ] **Step 5: Write the failing entrypoint test**

Append to `use_mem0/backend/tests/test_migrate.py`:

```python
def test_module_entrypoint_applies_migrations(clean_db):
    """CI runs migrations as a deploy step via this entrypoint."""
    import subprocess
    import sys

    result = subprocess.run(
        [sys.executable, "-m", "app.db.migrate"],
        env={**os.environ, "DATABASE_URL": clean_db},
        capture_output=True,
        text=True,
        cwd=str(Path(__file__).parent.parent / "src"),
    )

    assert result.returncode == 0, result.stderr
    with connect(clean_db) as conn:
        rows = conn.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema='public'"
        ).fetchall()
    assert "users" in {r[0] for r in rows}
```

Add `from pathlib import Path` to the imports at the top of `test_migrate.py`.

- [ ] **Step 6: Run it**

Run: `cd use_mem0/backend && UV_PROJECT_ENVIRONMENT="$(../venv-path)" uv run pytest tests/test_migrate.py -k entrypoint -v`
Expected: PASS

- [ ] **Step 7: Stop migrating from the lifespan**

In `use_mem0/backend/src/app/main.py`, remove the `run_migrations(settings.database_url)` line from the lifespan and delete `from .db.migrate import run_migrations` from the imports. Replace the removed line's comment block with:

```python
        # Migrations are a deploy step (`python -m app.db.migrate`), not a
        # startup step: several processes starting at once race, and the loser
        # of LangGraph's checkpoint_migrations insert dies rather than retrying.
        # The pool below still aborts startup if Postgres is unreachable.
```

- [ ] **Step 8: Keep local development working**

In `use_mem0/up`, run migrations before starting the backend. Find where the backend is started and insert, immediately before it:

```bash
# Migrations are no longer run at app startup: several processes racing on them
# corrupts the schema. Apply them here, once, before the backend comes up.
( cd backend && UV_PROJECT_ENVIRONMENT="$(../venv-path)" uv run python -m app.db.migrate )
```

- [ ] **Step 9: Verify the app still starts against a migrated database**

Run: `cd use_mem0/backend && UV_PROJECT_ENVIRONMENT="$(../venv-path)" uv run pytest -v`
Expected: PASS. Note that `test_app.py`'s tests depend on the schema already existing — `test_auth.py`'s fixture calls `run_migrations` directly, so a full-suite run is self-sufficient. If `test_app.py` fails in isolation with "relation does not exist", run `uv run python -m app.db.migrate` first.

- [ ] **Step 10: Verify the dev loop end to end**

Run: `make down && make up`
Expected: the app starts, and the log shows migrations applied before the backend boots.

- [ ] **Step 11: Commit**

```bash
git add use_mem0/backend/src/app/db/migrate.py use_mem0/backend/src/app/main.py \
        use_mem0/backend/tests/test_migrate.py use_mem0/up
git commit -m "fix: migrate as a deploy step under an advisory lock

Concurrent startups race three ways: a pg_type catalog collision, a
UniqueViolation on checkpoint_migrations whose loser dies, and an
aborted CREATE INDEX CONCURRENTLY leaving a permanently invalid index."
```

---

### Task 7: SSE heartbeat

CloudFront's origin response timeout is an inter-packet timeout, so a stream that goes quiet longer than the timeout is dropped. Token streaming is chatty, but the gap between the request and the first token — a mem0 search plus the model's time to first token — is the exposed window. A periodic SSE comment closes it.

An SSE comment (`: ping`) is ignored by every conforming client, including the AG-UI client, so nothing downstream needs to change.

**Files:**
- Create: `use_mem0/backend/src/app/agui/heartbeat.py`
- Modify: `use_mem0/backend/src/app/agui/routes.py:122-124`
- Test: `use_mem0/backend/tests/test_heartbeat.py` (create)

**Interfaces:**
- Consumes: nothing.
- Produces: `with_heartbeat(stream: AsyncIterator[bytes], interval: float = 15.0) -> AsyncIterator[bytes]`.

- [ ] **Step 1: Write the failing tests**

Create `use_mem0/backend/tests/test_heartbeat.py`:

```python
import asyncio

from app.agui.heartbeat import HEARTBEAT_COMMENT, with_heartbeat


async def test_passes_chunks_through_unchanged():
    async def source():
        yield b"a"
        yield b"b"

    assert [c async for c in with_heartbeat(source(), interval=10.0)] == [b"a", b"b"]


async def test_injects_a_comment_while_the_source_is_quiet():
    async def source():
        await asyncio.sleep(0.25)
        yield b"late"

    chunks = [c async for c in with_heartbeat(source(), interval=0.05)]

    assert chunks[-1] == b"late"
    assert HEARTBEAT_COMMENT in chunks
    assert chunks.count(HEARTBEAT_COMMENT) >= 2


async def test_stops_when_the_source_is_exhausted():
    async def source():
        yield b"only"

    chunks = [c async for c in with_heartbeat(source(), interval=0.01)]
    assert chunks == [b"only"]
```

- [ ] **Step 2: Run to confirm failure**

Run: `cd use_mem0/backend && UV_PROJECT_ENVIRONMENT="$(../venv-path)" uv run pytest tests/test_heartbeat.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.agui.heartbeat'`

- [ ] **Step 3: Write the helper**

Create `use_mem0/backend/src/app/agui/heartbeat.py`:

```python
"""Keep a quiet SSE stream alive through an idle-timeout proxy.

CloudFront's origin response timeout is an inter-packet timeout: it fires when
no packet arrives for N seconds, not when the response takes too long overall.
Token streaming is chatty enough on its own; the exposed gap is between the
request and the first token, where a memory search and the model's time to first
token both land. A comment frame costs nothing and every conforming SSE client
ignores it.
"""

import asyncio
from typing import AsyncIterator

HEARTBEAT_COMMENT = b": ping\n\n"
DEFAULT_INTERVAL_SECONDS = 15.0


async def with_heartbeat(
    stream: AsyncIterator[bytes],
    interval: float = DEFAULT_INTERVAL_SECONDS,
    comment: bytes = HEARTBEAT_COMMENT,
) -> AsyncIterator[bytes]:
    """Yield the upstream's chunks, emitting `comment` whenever it goes quiet."""
    iterator = stream.__aiter__()
    pending = asyncio.ensure_future(iterator.__anext__())
    try:
        while True:
            done, _ = await asyncio.wait({pending}, timeout=interval)
            if not done:
                yield comment
                continue
            try:
                chunk = pending.result()
            except StopAsyncIteration:
                return
            yield chunk
            pending = asyncio.ensure_future(iterator.__anext__())
    finally:
        pending.cancel()
```

- [ ] **Step 4: Run the tests**

Run: `cd use_mem0/backend && UV_PROJECT_ENVIRONMENT="$(../venv-path)" uv run pytest tests/test_heartbeat.py -v`
Expected: PASS

- [ ] **Step 5: Wrap the agent response**

In `use_mem0/backend/src/app/agui/routes.py`, add the import at the top:

```python
from .heartbeat import with_heartbeat
```

Then in `AgentAuthMiddleware.dispatch`, replace the tail of the method:

```python
        title = first_user_message(payload)
        response = await call_next(request)
        touch_conversation(request.app.state.pool, thread_id, title)

        # The adapter answers with a stream that is still unconsumed here, so
        # wrapping its iterator is what puts a heartbeat between the request and
        # the first token — the one window long enough for a proxy to give up.
        if hasattr(response, "body_iterator"):
            response.body_iterator = with_heartbeat(response.body_iterator)
            response.headers["Cache-Control"] = "no-cache, no-store, no-transform"
            response.headers["X-Accel-Buffering"] = "no"
        return response
```

- [ ] **Step 6: Run the agui tests**

Run: `cd use_mem0/backend && UV_PROJECT_ENVIRONMENT="$(../venv-path)" uv run pytest tests/test_agui.py -v`
Expected: PASS — the gate's behaviour is unchanged; only the response body is wrapped.

- [ ] **Step 7: Run the whole suite**

Run: `cd use_mem0/backend && UV_PROJECT_ENVIRONMENT="$(../venv-path)" uv run pytest -v`
Expected: PASS

- [ ] **Step 8: Commit**

```bash
git add use_mem0/backend/src/app/agui/heartbeat.py use_mem0/backend/src/app/agui/routes.py \
        use_mem0/backend/tests/test_heartbeat.py
git commit -m "feat: heartbeat the AG-UI stream so proxies do not drop it

CloudFront's origin timeout is inter-packet. The exposed gap is between
the request and the first token, where the memory search and the model's
TTFT both land."
```

---

### Task 8: Load secrets from SSM Parameter Store

So the eight secrets never enter Terraform state. Terraform declares parameter *names*; values are seeded out of band; the app reads them at init with one batched call.

**Files:**
- Create: `use_mem0/backend/src/app/config_ssm.py`
- Modify: `use_mem0/backend/src/app/main.py` (the `app()` entry point)
- Modify: `use_mem0/backend/pyproject.toml` (add `boto3`)
- Test: `use_mem0/backend/tests/test_config_ssm.py` (create)

**Interfaces:**
- Consumes: `load_settings` from `app.config`.
- Produces: `env_from_ssm(path: str, client) -> dict[str, str]`, mapping each parameter's basename to its value. `main.app()` merges it over `os.environ` when `CONFIG_SSM_PATH` is set.

- [ ] **Step 1: Add the dependency**

In `use_mem0/backend/pyproject.toml`, add to `dependencies`:

```toml
    "boto3>=1.35",
```

Run: `cd use_mem0/backend && UV_PROJECT_ENVIRONMENT="$(../venv-path)" uv sync`
Expected: boto3 installed, `uv.lock` updated.

- [ ] **Step 2: Write the failing tests**

Create `use_mem0/backend/tests/test_config_ssm.py`:

```python
import pytest

from app.config_ssm import env_from_ssm


class FakeSSM:
    """Stands in for boto3's SSM client; records how it was called."""

    def __init__(self, parameters, invalid=()):
        self._parameters = parameters
        self._invalid = list(invalid)
        self.calls = []

    def get_parameters_by_path(self, **kwargs):
        self.calls.append(kwargs)
        return {
            "Parameters": [
                {"Name": name, "Value": value}
                for name, value in self._parameters.items()
            ]
        }


def test_maps_parameter_basenames_to_values():
    client = FakeSSM(
        {
            "/mem0-chatbot/prod/OPENAI_API_KEY": "sk-live",
            "/mem0-chatbot/prod/SESSION_SECRET": "s" * 32,
        }
    )

    assert env_from_ssm("/mem0-chatbot/prod", client) == {
        "OPENAI_API_KEY": "sk-live",
        "SESSION_SECRET": "s" * 32,
    }


def test_requests_decrypted_values():
    """SecureString parameters come back encrypted unless asked otherwise."""
    client = FakeSSM({"/p/A": "1"})
    env_from_ssm("/p", client)
    assert client.calls[0]["WithDecryption"] is True


def test_empty_path_yields_a_clear_error():
    client = FakeSSM({})
    with pytest.raises(ValueError, match="no parameters"):
        env_from_ssm("/mem0-chatbot/prod", client)
```

- [ ] **Step 3: Run to confirm failure**

Run: `cd use_mem0/backend && UV_PROJECT_ENVIRONMENT="$(../venv-path)" uv run pytest tests/test_config_ssm.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.config_ssm'`

- [ ] **Step 4: Write the loader**

Create `use_mem0/backend/src/app/config_ssm.py`:

```python
"""Read configuration from SSM Parameter Store.

Terraform declares the parameter names; the values are seeded out of band and
never enter Terraform state. One paginated call at process init, so the cost is
paid once per cold start rather than per request.
"""

from typing import Mapping


def env_from_ssm(path: str, client) -> dict[str, str]:
    """Return every parameter under `path`, keyed by its basename.

    `/mem0-chatbot/prod/OPENAI_API_KEY` becomes `OPENAI_API_KEY`, so the result
    drops straight into `load_settings` alongside os.environ.
    """
    env: dict[str, str] = {}
    token = None
    while True:
        kwargs = {"Path": path, "WithDecryption": True, "Recursive": False}
        if token:
            kwargs["NextToken"] = token
        page = client.get_parameters_by_path(**kwargs)
        for parameter in page.get("Parameters", []):
            env[parameter["Name"].rsplit("/", 1)[-1]] = parameter["Value"]
        token = page.get("NextToken")
        if not token:
            break

    if not env:
        raise ValueError(f"no parameters found under {path}")
    return env


def merged_env(base: Mapping[str, str], path: str | None, client=None) -> dict[str, str]:
    """`base` overlaid with SSM values, or `base` unchanged when path is unset.

    SSM wins over the process environment: a value that is in both is a secret
    that someone also exported, and the stored one is authoritative.
    """
    if not path:
        return dict(base)
    if client is None:
        import boto3

        client = boto3.client("ssm")
    return {**base, **env_from_ssm(path, client)}
```

- [ ] **Step 5: Run the tests**

Run: `cd use_mem0/backend && UV_PROJECT_ENVIRONMENT="$(../venv-path)" uv run pytest tests/test_config_ssm.py -v`
Expected: PASS

- [ ] **Step 6: Wire it into the entry point**

In `use_mem0/backend/src/app/main.py`, replace the `app()` factory:

```python
def app() -> FastAPI:
    """ASGI entry point: `uvicorn app.main:app --factory`.

    A factory rather than a module-level instance, so importing this module
    never reads the environment; tests build an app from explicit settings.

    CONFIG_SSM_PATH, when set, overlays SSM Parameter Store on the environment,
    which is how the deployed function gets its secrets without them ever
    entering Terraform state.
    """
    from .config_ssm import merged_env

    env = merged_env(os.environ, os.environ.get("CONFIG_SSM_PATH"))
    return create_app(load_settings(env))
```

- [ ] **Step 7: Run the whole suite**

Run: `cd use_mem0/backend && UV_PROJECT_ENVIRONMENT="$(../venv-path)" uv run pytest -v`
Expected: PASS. With `CONFIG_SSM_PATH` unset, `merged_env` returns the environment unchanged and no AWS call is made, so local development and tests are untouched.

- [ ] **Step 8: Commit**

```bash
git add use_mem0/backend/src/app/config_ssm.py use_mem0/backend/src/app/main.py \
        use_mem0/backend/tests/test_config_ssm.py use_mem0/backend/pyproject.toml \
        use_mem0/backend/uv.lock
git commit -m "feat: optionally load configuration from SSM Parameter Store

Terraform declares parameter names only; values are seeded out of band,
so no secret enters Terraform state. Inert when CONFIG_SSM_PATH is unset."
```

---

### Task 9: Single-origin frontend and static mount

Two halves of one change: the bundle must not carry a backend hostname, and the app must be able to serve that bundle so the image is single-origin wherever it runs.

**Files:**
- Create: `use_mem0/frontend/.env.production`
- Create: `use_mem0/backend/src/app/static_site.py`
- Modify: `use_mem0/backend/src/app/main.py` (mount after the routers)
- Test: `use_mem0/backend/tests/test_static_site.py` (create)

**Interfaces:**
- Consumes: the app factory.
- Produces: `mount_static_site(app: FastAPI, directory: Path) -> None`, a no-op when the directory is absent.

- [ ] **Step 1: Pin the production API base to empty**

Create `use_mem0/frontend/.env.production`:

```
# Same-origin in every deployment: CloudFront serves the SPA and proxies the API
# under one hostname. `api.ts` uses `??`, so an empty string survives and paths
# become relative ("/auth/login"), which is what keeps the session cookie
# first-party and SameSite=Lax valid.
#
# Never put a backend hostname here. A bundle carrying one forces a coordinated
# frontend redeploy on every backend move.
VITE_API_BASE=
```

- [ ] **Step 2: Verify the built bundle is same-origin**

Run: `cd use_mem0/frontend && npm run build && grep -c "localhost:8000" dist/assets/*.js || echo "no absolute API base in bundle"`
Expected: `no absolute API base in bundle`. If the grep finds matches, `.env.production` is not being picked up — confirm the file sits beside `package.json`.

- [ ] **Step 3: Write the failing static-mount tests**

Create `use_mem0/backend/tests/test_static_site.py`:

```python
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.static_site import mount_static_site


def test_serves_index_at_the_root(tmp_path):
    (tmp_path / "index.html").write_text("<html>app</html>")

    app = FastAPI()
    mount_static_site(app, tmp_path)

    with TestClient(app) as client:
        response = client.get("/")
    assert response.status_code == 200
    assert "app" in response.text


def test_unknown_paths_fall_back_to_index(tmp_path):
    """Client-side routing: a deep link must not 404."""
    (tmp_path / "index.html").write_text("<html>app</html>")

    app = FastAPI()
    mount_static_site(app, tmp_path)

    with TestClient(app) as client:
        response = client.get("/some/client/route")
    assert response.status_code == 200
    assert "app" in response.text


def test_missing_directory_is_a_no_op(tmp_path):
    """A dev checkout with no build must still start."""
    app = FastAPI()
    mount_static_site(app, tmp_path / "absent")

    with TestClient(app) as client:
        assert client.get("/").status_code == 404


def test_api_routes_are_not_shadowed(tmp_path):
    (tmp_path / "index.html").write_text("<html>app</html>")

    app = FastAPI()

    @app.get("/health")
    def health():
        return {"status": "ok"}

    mount_static_site(app, tmp_path)

    with TestClient(app) as client:
        assert client.get("/health").json() == {"status": "ok"}
```

- [ ] **Step 4: Run to confirm failure**

Run: `cd use_mem0/backend && UV_PROJECT_ENVIRONMENT="$(../venv-path)" uv run pytest tests/test_static_site.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.static_site'`

- [ ] **Step 5: Write the mount**

Create `use_mem0/backend/src/app/static_site.py`:

```python
"""Serve the built SPA from the app, so one image is single-origin anywhere.

Unused in the CloudFront deployment, where S3 serves the bundle. It exists so
that `docker run` and any future box or container deployment need no second web
server, and so the session cookie stays first-party in all of them.
"""

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles


class _SPAFiles(StaticFiles):
    """StaticFiles that answers unknown paths with index.html.

    The SPA owns its routes, so a deep link must reach the bundle rather than a
    404 from the server that has never heard of it.
    """

    async def get_response(self, path: str, scope):
        response = await super().get_response(path, scope)
        if response.status_code == 404:
            return FileResponse(Path(self.directory) / "index.html")
        return response


def mount_static_site(app: FastAPI, directory: Path) -> None:
    """Mount `directory` at `/`, or do nothing if it is not there.

    Call this AFTER every router: a mount at "/" matches everything, so an
    earlier mount would shadow the API.
    """
    if not (directory / "index.html").is_file():
        return
    app.mount("/", _SPAFiles(directory=str(directory), html=True), name="spa")
```

- [ ] **Step 6: Run the tests**

Run: `cd use_mem0/backend && UV_PROJECT_ENVIRONMENT="$(../venv-path)" uv run pytest tests/test_static_site.py -v`
Expected: PASS

- [ ] **Step 7: Mount it in the app**

In `use_mem0/backend/src/app/main.py`, add the imports:

```python
from pathlib import Path

from .static_site import mount_static_site
```

And at the very end of `create_app`, immediately before `return app`:

```python
    # Last, after every router: a mount at "/" matches everything.
    mount_static_site(app, Path(os.environ.get("STATIC_DIR", "/app/static")))
```

- [ ] **Step 8: Run the whole suite**

Run: `cd use_mem0/backend && UV_PROJECT_ENVIRONMENT="$(../venv-path)" uv run pytest -v`
Expected: PASS. `/app/static` does not exist in a dev checkout, so the mount is a no-op and no existing route changes.

- [ ] **Step 9: Commit**

```bash
git add use_mem0/frontend/.env.production use_mem0/backend/src/app/static_site.py \
        use_mem0/backend/src/app/main.py use_mem0/backend/tests/test_static_site.py
git commit -m "feat: same-origin frontend and an optional static mount

An empty VITE_API_BASE keeps requests relative so the session cookie is
first-party. The mount lets one image serve both halves anywhere."
```

---

### Task 10: The portable Dockerfile

One arm64 image, three roles (web, migrate, local dev), four targets (compose, Lambda, Fargate, Lightsail). The Lambda Web Adapter binary is inert outside Lambda — a file in `/opt/extensions` that nothing reads unless the Lambda runtime is present — which is what makes the image portable for ~10 MB.

**Files:**
- Create: `use_mem0/Dockerfile`
- Create: `use_mem0/.dockerignore`
- Test: manual build-and-run verification (steps below)

**Interfaces:**
- Consumes: everything above. `PUBLIC_BASE_URL`, `DB_POOL_MAX_SIZE`, `CONFIG_SSM_PATH`, `STATIC_DIR` are all read by the image's process.
- Produces: an image whose default command serves the app on `$PORT` (default 8080), and which runs migrations when the command is overridden with `python -m app.db.migrate`. The infra plan builds and pushes this image.

- [ ] **Step 1: Write the ignore file**

Create `use_mem0/.dockerignore`:

```
**/node_modules
**/.venv
**/__pycache__
**/dist
.dev
.env
frontend/.env
```

- [ ] **Step 2: Write the Dockerfile**

Create `use_mem0/Dockerfile`:

```dockerfile
# One image, three roles (web, migrate, local dev), four targets (compose,
# Lambda, Fargate, Lightsail). See
# docs/superpowers/specs/2026-09-02-aws-serverless-deployment-design.md §3.
#
# Python 3.12, not 3.14: better wheel coverage across this dependency set.

FROM python:3.12-slim AS deps
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
WORKDIR /app
COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY backend/src ./src
RUN uv sync --frozen --no-dev

FROM node:22-slim AS web
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim AS runtime
# Inert outside Lambda: nothing reads /opt/extensions unless the Lambda runtime
# is present. This is what makes the same image portable across all four targets.
COPY --from=public.ecr.aws/awsguru/aws-lambda-adapter:0.9.1 /lambda-adapter /opt/extensions/lambda-adapter

WORKDIR /app
COPY --from=deps /app/.venv /app/.venv
COPY --from=deps /app/src /app/src
COPY --from=web /web/dist /app/static

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONPATH=/app/src \
    PYTHONUNBUFFERED=1 \
    PORT=8080 \
    STATIC_DIR=/app/static

EXPOSE 8080
CMD ["sh", "-c", "exec uvicorn app.main:app --factory --host 0.0.0.0 --port ${PORT} --proxy-headers --forwarded-allow-ips='*'"]
```

`--proxy-headers` with `--forwarded-allow-ips='*'` is safe here only because the container is never directly reachable — CloudFront and the Function URL are the only paths in. If this image is ever exposed without a proxy in front, narrow that to the proxy's CIDR.

Verify the adapter tag before building: `docker manifest inspect public.ecr.aws/awsguru/aws-lambda-adapter:0.9.1`. If it 404s, list the available tags and pin the newest release rather than `latest` — an unpinned adapter is a silent behaviour change on every rebuild.

- [ ] **Step 3: Build the image**

Run: `cd use_mem0 && docker buildx build --platform linux/arm64 -t mem0-chatbot:local --load .`
Expected: a successful build. On an x86 host this uses emulation and is slow; that is fine for a one-off check. If buildx is unavailable, build without `--platform` to verify the Dockerfile is correct, and leave the arm64 build to CI.

- [ ] **Step 4: Verify the image serves and does not need AWS**

Run:

```bash
docker run --rm -p 8080:8080 \
  -e DATABASE_URL="postgresql://app:app@host.docker.internal:5432/app" \
  -e OPENAI_API_KEY=sk-test -e MEM0_API_KEY=m0-test \
  -e LANGSMITH_API_KEY=ls-test -e LANGSMITH_PROJECT=test \
  -e GOOGLE_CLIENT_ID=gid -e GOOGLE_CLIENT_SECRET=gsecret \
  -e SESSION_SECRET="$(python3 -c 'import secrets;print(secrets.token_urlsafe(32))')" \
  -e PUBLIC_BASE_URL=http://localhost:8080 \
  mem0-chatbot:local
```

Then in another terminal: `curl -s localhost:8080/health` → `{"status":"ok"}`, and `curl -s localhost:8080/ready` → `{"status":"ready"}`.
Expected: both succeed. `make up` must be running to provide Postgres. If `/ready` returns 503, the container cannot reach the host's Postgres — check the `host.docker.internal` address for your platform.

- [ ] **Step 5: Verify the SPA is served from the same origin**

Run: `curl -s localhost:8080/ | head -5`
Expected: the SPA's `index.html`. This proves Task 9's mount works inside the image.

- [ ] **Step 6: Verify the migrate role**

Run:

```bash
docker run --rm \
  -e DATABASE_URL="postgresql://app:app@host.docker.internal:5432/app" \
  mem0-chatbot:local python -m app.db.migrate
```

Expected: `migrations applied`, exit code 0. This is the command CI runs as its deploy step.

- [ ] **Step 7: Commit**

```bash
git add use_mem0/Dockerfile use_mem0/.dockerignore
git commit -m "feat: one portable image for compose, Lambda, Fargate, Lightsail

The Lambda Web Adapter binary is inert outside Lambda, so a single arm64
image serves every target and migration becomes a deployment change."
```

---

## Done criteria

- [ ] Full suite green: `cd use_mem0/backend && UV_PROJECT_ENVIRONMENT="$(../venv-path)" uv run pytest -v`
- [ ] `make up` still brings the app up, with migrations applied before the backend starts.
- [ ] `docker run` of the built image answers `/health`, `/ready`, and `/` with no AWS credentials present.
- [ ] `docker run ... python -m app.db.migrate` exits 0.
- [ ] The built frontend bundle contains no absolute API hostname.

The infrastructure plan (`2026-09-02-aws-deploy-infrastructure.md`) picks up from here and needs only the image and the environment variable contract this plan establishes.
