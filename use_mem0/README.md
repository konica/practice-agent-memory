# mem0 chatbot

A personal chat assistant that remembers you between conversations. Google sign-in, a
LangGraph agent whose long-term memory lives in [mem0 Platform](https://mem0.ai), Postgres
for both application data and conversation checkpoints, and a React chat UI.

The project exists to make memory behaviour *observable*: every mem0 call is traced into
LangSmith, and the graph deliberately does not clean up what mem0 returns, so its own
consolidation behaviour stays visible.

## Status

The backend and frontend both run today, but **the chat path is not wired up yet**. You can
start both processes and sign in with Google; there is no chat endpoint behind it.

| Area | State |
| --- | --- |
| Config, Postgres schema, checkpointer migrations | done |
| Google OAuth + server-side sessions (`/auth/*`) | done |
| mem0 memory wrapper, graph nodes, compiled graph | done, unit-tested |
| Frontend scaffold, design tokens, auth gate | done |
| `/conversations` registry | not built — issue #5 |
| AG-UI `/agent` endpoint | not built — issue #9 |
| Conversation history endpoint | not built — issue #10 |
| Sidebar, chat surface styling | not built — issues #13, #17 |

Concretely: `build_graph()` is implemented and tested, but nothing exposes it over HTTP —
`create_app()` mounts only the auth router and `/health`. `src/api.ts` already calls
`/conversations*`, and those routes return 404 until #5/#10 land. `Workspace.tsx` is a
placeholder that renders `signed in`.

## Architecture

Two processes and a database. mem0 and OpenAI are external services; nothing runs locally
for them.

```mermaid
flowchart LR
    UI["Frontend<br/>React 19 + Vite<br/>:5173"]
    API["Backend<br/>FastAPI + LangGraph<br/>:8000"]
    PG[("Postgres 16<br/>:5432")]
    M0["mem0 Platform"]
    OAI["OpenAI"]
    G["Google OAuth"]

    UI -- "fetch, credentials: include" --> API
    API -- "app tables + checkpoints" --> PG
    API -- "search / add" --> M0
    API -- "chat completion" --> OAI
    UI -- "redirect to sign in" --> G
    G -- "code -> /auth/callback" --> API
```

The frontend never talks to mem0, OpenAI or Postgres. It holds no token: the backend sets a
signed, `httpOnly` session cookie, and every call from `src/api.ts` sends
`credentials: "include"`.

### A chat turn

The graph is a fixed three-node sequence, compiled with a `PostgresSaver` checkpointer:

```
START -> retrieve_memories -> call_model -> write_memories -> END
```

- **`retrieve_memories`** searches mem0 for the latest user message, scoped to `user_id`.
  Skipped when `memory_enabled` is false.
- **`call_model`** renders the recalled memories into the system prompt and calls the model.
- **`write_memories`** writes the last user/assistant exchange back to mem0. This runs
  *regardless* of `memory_enabled` — that flag suppresses recall only, so a comparison
  session still leaves no gap in the user's memory history.

Memory is invoked as graph **nodes rather than LLM tools**, so every turn runs identically
and every LangSmith trace has the same shape. Tools can be added later without disturbing
this.

### Failure policy

The two external calls are deliberately treated differently:

| Call | On failure | Why |
| --- | --- | --- |
| mem0 `search` / `add` | Logged, returns `[]` / no-op. Never raises. | Memory is enrichment. An outage should produce a reply *without* recall, not an error. |
| Model `invoke` | Retried once, then propagates. | The reply is the critical path; it cannot degrade silently. |
| Postgres at startup | Raises, startup aborts. | A conversation that cannot persist is broken, not degraded. |

`MemoryStore` enforces a ~2s deadline on search by running it on a worker thread — mem0's own
httpx client defaults to a 300s timeout, long enough for a hung service to stall every turn.
It also tolerates an unrecognised response shape rather than raising, and unwraps mem0's
v1.1 `{"results": [...]}` envelope.

Both `MemoryStore` methods carry `@traceable`. mem0 calls are plain SDK calls, not LangChain
runnables, so LangSmith does **not** auto-trace them; without the decorators the memory steps
are invisible in the very observability layer this project exists to demonstrate.

### Where state lives

Two separate concerns in one database:

- **Application tables** (`users`, `auth_sessions`, `conversations`) come from
  `db/schema.sql`.
- **Transcripts** live in LangGraph's checkpoint tables (`checkpoints`, `checkpoint_blobs`,
  `checkpoint_writes`, `checkpoint_migrations`), keyed by `thread_id`. There is no
  `messages` table; `read_messages(graph, thread_id)` rehydrates a transcript from the
  checkpointer.

`run_migrations()` applies the schema and then calls `PostgresSaver.setup()` **explicitly** —
the checkpointer does not create its tables lazily, and the graph fails on missing tables
without it.

### Identity

The Google `sub` claim is the user key and doubles as the mem0 `user_id`. Email is stored for
display only, since a user can change it. Sessions are server-side rows in `auth_sessions`
(14-day TTL) behind a signed cookie, not JWTs — so log-out and revocation take effect
immediately instead of waiting out a token's lifetime.

### Layout

```
use_mem0/
  docker-compose.yml          Postgres 16, healthcheck, named volume
  backend/
    src/app/
      config.py               Settings; fails fast on missing env vars
      main.py                 create_app() + lifespan; ASGI factory
      db/
        schema.sql            users, auth_sessions, conversations
        engine.py             psycopg ConnectionPool (dict_row, autocommit)
        migrate.py            schema + PostgresSaver.setup()
      auth/
        google.py             authorization URL, code exchange, id-token verify
        session.py            create/resolve/revoke; get_current_user dependency
        routes.py             /auth/login, /callback, /me, /logout
      agent/
        state.py              ChatState
        memory.py             MemoryStore: mem0 wrapper that never raises
        nodes.py              retrieve_memories / call_model / write_memories
        graph.py              build_graph(), read_messages()
    tests/
  frontend/
    src/
      api.ts                  typed client; every call sends credentials
      App.tsx                 auth gate: Login when signed out, Workspace when in
      components/ui/          shadcn/ui primitives
```

## Running in dev

### Prerequisites

- Docker (for Postgres)
- [uv](https://docs.astral.sh/uv/) and Python 3.11+
- Node 20+ (no `engines` field is declared; verified on Node 22)

You also need credentials for OpenAI, mem0 Platform, LangSmith, and a Google OAuth client.

### 1. Start Postgres

```bash
cd use_mem0
docker compose up -d app-postgres
```

Postgres 16 on `localhost:5432`, database/user/password all `app`, stored in the named volume
`app-postgres-data`. Wait for health with `docker compose ps`.

### 2. Configure the backend

```bash
cp .env.example .env      # then fill in the real values
```

Every key in `REQUIRED_KEYS` must be non-empty or startup raises `MissingConfigError` listing
exactly what is missing. `DATABASE_URL` already matches the compose credentials.

> **The backend does not read `.env` itself.** `load_settings()` reads `os.environ`, and there
> is no dotenv dependency. Export the file into your shell before starting the server:
>
> ```bash
> set -a && . ../.env && set +a
> ```

`MEMORY_RETRIEVAL_ENABLED=false` turns off *recall* while still writing memories — useful for
comparing answers with and without memory. `FRONTEND_ORIGIN` is the single origin allowed by
CORS, with credentials enabled.

### 3. Register the Google OAuth redirect URI

The callback URL is derived from the incoming request, so for a backend on port 8000 register
exactly:

```
http://localhost:8000/auth/callback
```

Change the port and this changes with it.

### 4. Run the backend

```bash
cd backend
uv sync --all-extras
uv run uvicorn app.main:app --factory --reload --port 8000
```

`--factory` is required: `app` is a function, not a module-level instance, so that importing
`app.main` never reads the environment. Migrations run on startup, so first boot creates the
three application tables and the four checkpointer tables.

Check it:

```bash
curl http://localhost:8000/health          # {"status":"ok"}
curl -i http://localhost:8000/auth/me      # 401 until you sign in
```

### 5. Run the frontend

```bash
cd frontend
npm install
npm run dev        # http://localhost:5173
```

`VITE_API_BASE` (see `frontend/.env.example`) points the client at the backend and defaults to
`http://localhost:8000`.

Open http://localhost:5173 and sign in. On success you land back on the frontend with a
session cookie and the app renders the `Workspace` placeholder.

## Tests

```bash
cd backend
uv run pytest          # 40 tests
```

> **The suite needs the compose Postgres running, and it is destructive.** `test_migrate.py`
> drops `conversations`, `auth_sessions` and `users` in the target database, and `test_graph.py`
> writes real checkpoints. Both default to `postgresql://app:app@localhost:5432/app` — the same
> database the dev server uses. Point `TEST_DATABASE_URL` at a scratch database if you have
> local data worth keeping.

Frontend:

```bash
cd frontend
npm run build      # tsc -b && vite build
npm run lint       # oxlint
```

## Gotchas

- **Cookies are `secure=False`, `samesite=lax`.** Fine over `http://localhost`; must be
  revisited before any non-local deployment.
- **CORS allows exactly one origin.** If the frontend is not on `FRONTEND_ORIGIN`, browser
  calls fail even though `curl` works, because credentialed requests cannot use a wildcard.
- **`src/lib/utils.ts` is force-tracked.** The repo-root `.gitignore` carries the standard
  Python `lib/` rule; with no leading slash it matches at any depth and silently swallowed the
  shadcn `cn` helper. A negation for `use_mem0/frontend/src/lib/` keeps it tracked — if you add
  files under another `lib/` directory, check `git status` actually sees them.
- **mem0 scoping goes in `filters`.** `filters={"user_id": ...}`, never a top-level `user_id`;
  the v3 API rejects the latter outright.
