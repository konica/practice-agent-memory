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
| `/conversations` registry with ownership enforcement | done, tested |
| AG-UI `/agent` endpoint | not built — issue #9 |
| Conversation history endpoint | not built — issue #10 |
| Sidebar, chat surface styling | not built — issues #13, #17 |

Concretely: `build_graph()` is implemented and tested, but nothing exposes it over HTTP —
`create_app()` mounts the auth and conversations routers and `/health`. `src/api.ts` can
list, create, rename and delete conversations; `GET /conversations/{id}/messages` still
returns 404 until #10 lands. `Workspace.tsx` is a placeholder that renders `signed in`.

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
  Makefile                    (repo root) make up / down / clean / test / logs
use_mem0/
  up, down                    start / stop everything (see Running in dev)
  venv-path                   where uv should put the backend virtualenv
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
      conversations/
        store.py              CRUD; title set once, delete clears checkpoints
        ownership.py          the one owner check; 404, never 403
        routes.py             /conversations list, create, rename, delete
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

```bash
cp use_mem0/.env.example use_mem0/.env    # fill in the real values
make up
```

`make up` starts Postgres, the backend and the frontend, waits until each
actually answers, and prints the URLs. It is safe to run twice. `make down`
stops everything and keeps your data; `make clean` also drops the database
volume. `make` on its own lists every target.

The Makefile is a front door: the logic lives in `use_mem0/up` and
`use_mem0/down`, which you can run directly (`cd use_mem0 && ./up`) if you
prefer. There is one implementation, not two.

### Prerequisites

- Docker (for Postgres)
- [uv](https://docs.astral.sh/uv/) and Python 3.11+
- Node 20+ (no `engines` field is declared; verified on Node 22)

Plus credentials for OpenAI, mem0 Platform, LangSmith, and a Google OAuth client.
Every key in `REQUIRED_KEYS` must be non-empty; `up` names the missing ones and
stops before starting anything. `DATABASE_URL` already matches the compose
credentials.

`up` exports `.env` itself, because `load_settings()` reads `os.environ` and
nothing else loads that file.

### Register the Google OAuth redirect URI

The one step no script can do for you. The callback URL is derived from the
incoming request, so for the default port register exactly:

```
http://localhost:8000/auth/callback
```

Run on another port (`API_PORT=8001 make up`) and this changes with it.

### Knobs

| Variable | Effect |
| --- | --- |
| `API_PORT`, `WEB_PORT` | Override the default 8000 / 5173 |
| `MEMORY_RETRIEVAL_ENABLED=false` | Suppresses *recall* while still writing memories — for comparing answers with and without memory |
| `FRONTEND_ORIGIN` | The single origin CORS allows, with credentials. Defaults to the web URL `up` serves |
| `VITE_API_BASE` | Points the frontend client at the backend (`frontend/.env.example`) |

Logs are in `use_mem0/.dev/` (`make logs` follows them). Migrations run on
backend startup, so the first `make up` creates the three application tables and
the four checkpointer tables.

Open the app URL and sign in. On success you land back on the frontend with a
session cookie and the app renders the `Workspace` placeholder.

## Tests

```bash
make test              # 55 tests
```

> **The suite needs the compose Postgres running (`make up` starts it), and it is destructive.** `test_migrate.py`
> drops `conversations`, `auth_sessions` and `users` in the target database,
> `test_conversations.py` empties those three tables, and `test_graph.py`
> writes real checkpoints. Both default to `postgresql://app:app@localhost:5432/app` — the same
> database the dev server uses. Point `TEST_DATABASE_URL` at a scratch database if you have
> local data worth keeping.

Frontend:

```bash
make build         # tsc -b && vite build
make lint          # oxlint
```

## Gotchas

- **The virtualenv may not live in `backend/.venv`.** uv builds `.venv/bin/python`
  as a symlink, and some filesystems (including the `/c/...` mount this repo is
  often checked out on) refuse to create symlinks — uv then leaves a `.venv` with
  no interpreter and every later command fails with *"not a valid Python
  environment"*. `use_mem0/venv-path` prints the directory actually in use, and
  `up` and `make test` both consult it. Running uv by hand needs the same:
  ```bash
  cd use_mem0/backend
  UV_PROJECT_ENVIRONMENT="$(../venv-path)" uv run pytest
  ```
  Where symlinks work this is just `backend/.venv` and nothing changes.
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
