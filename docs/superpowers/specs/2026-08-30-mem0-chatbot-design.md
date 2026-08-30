# mem0 Chatbot — Design Spec

**Date**: 2026-08-30
**Location**: `use_mem0/`
**Status**: Approved, ready for implementation planning

## Purpose

Build a personal-assistant chatbot that exercises a full agent stack end to end, so the
integration between each layer is visible and understandable. The value is in seeing the
wiring work, not in the chatbot's sophistication.

The demo scenario: a user tells the assistant facts and preferences about themselves over
multiple turns and sessions, and the assistant recalls them later. Memory recall across a
session boundary is the acceptance criterion that proves the stack works.

AWS deployment is explicitly **out of scope** for this spec and is a follow-up sub-project.
The design containerizes stateful services now so that deployment becomes a configuration
change rather than a rewrite.

## Stack

| Layer | Choice |
|---|---|
| Backend | Python, FastAPI |
| Frontend | React + Vite + TypeScript |
| Agent framework | LangGraph |
| LLM | OpenAI |
| Agent memory | mem0 **Platform (cloud)** |
| Observability | **LangSmith** (managed) |
| Agent UI transport | **AG-UI protocol**, with CopilotKit on the React side |
| Persistence | Postgres (Docker) — LangGraph checkpoints + users/sessions |
| Auth | Google OAuth (Gmail provider) |

### Verified package facts

Confirmed against official docs as of August 2026:

- `ag-ui-protocol` (PyPI v0.1.21) — core typed event models + SSE encoding.
- `ag-ui-langgraph` (PyPI v0.0.44) — official LangGraph adapter, provides
  `add_langgraph_fastapi_endpoint(app, graph, "/agent")`.
- `@copilotkit/react-core` + `@copilotkit/react-ui` (npm v1.69.3) — recommended React
  client for AG-UI, built by the same team. `@ag-ui/client` (v0.0.58) is the lower-level
  alternative but has no documented bare-React sample.
- `mem0ai` (PyPI v2.0.19) — cloud client is `from mem0 import MemoryClient`
  (`AsyncMemoryClient` for async). Auth via `MEM0_API_KEY`.
  API: `client.add(messages, user_id=...)`, `client.search(query, filters={"user_id": ...})`.
- `langsmith` (PyPI v0.11.2) — separate install. **No manual callback wiring needed**;
  LangGraph runnables auto-trace once env vars are set.
- `langgraph-checkpoint-postgres` (PyPI v3.1.2) — `PostgresSaver` /
  `AsyncPostgresSaver`. **`.setup()` must be called once** to create tables.

## Architecture

Two runtime groups.

**App services** — run locally as processes, for fast iteration:

- **Backend**: one FastAPI process serving both the Google OAuth routes and the AG-UI
  endpoint that wraps the compiled LangGraph graph.
- **Frontend**: Vite dev server running React + CopilotKit, talking to the backend's
  AG-UI endpoint and OAuth routes.

**Infra services** — Docker Compose, stateful and disposable:

- `app-postgres` — LangGraph checkpoints (thread state) plus `users` and `sessions` tables.

mem0 and LangSmith are both managed services, so neither adds local infrastructure. The
only container is Postgres.

### Identity

The backend handles the Google OAuth Authorization Code exchange directly, verifies the
id token, and issues its own signed session cookie. The Google `sub` claim — not the email,
which a user can change — becomes the application user key, the mem0 `user_id`, and the
LangSmith `user_id` metadata value. One identity threads through all three systems, which
is what makes per-user memory isolation observable. Email is stored alongside it for
display purposes only.

## Components

### Backend

- `auth/` — `/auth/login` (redirect to Google), `/auth/callback` (exchange code, verify
  id token, upsert user, set session cookie), `/auth/me` (session check for the frontend).
- `agent/` — the LangGraph graph, three nodes in fixed order:
  - `retrieve_memories` — searches mem0 scoped to `user_id`, folds results into the system
    prompt for this turn only.
  - `call_model` — OpenAI chat completion.
  - `write_memories` — writes the turn's exchange back to mem0 under `user_id`.

  Compiled with `PostgresSaver` as checkpointer; `thread_id` identifies a conversation.
- `agui/` — `add_langgraph_fastapi_endpoint(app, graph, "/agent")`, gated behind the
  session cookie.
- `config.py` — env loading with fail-fast validation on missing keys.

**Memory invocation is fixed graph nodes, not LLM tools.** This was a deliberate choice
over exposing `search_memory`/`add_memory` as tools the model may call. A fixed graph runs
the same way every turn, so the memory loop is legible in the graph definition and every
LangSmith trace has an identical shape. Tool-based memory is more agentic but fires
inconsistently, which would make both the demo and the traces unreliable. Tools can be
added later without disturbing this structure.

### Frontend

- Unauthenticated: a single "Sign in with Google" screen that redirects to the backend.
- Authenticated: CopilotKit provider plus `<CopilotChat/>` pointed at `/agent`.

The UI is deliberately minimal — no in-app memory inspector and no per-message trace links.
Memory writes are inspected in the mem0 dashboard and traces in the LangSmith UI. Visual
design of the chat surface is being specified separately in a designer brief.

### Environment configuration

```
OPENAI_API_KEY
MEM0_API_KEY
LANGSMITH_TRACING=true
LANGSMITH_API_KEY
LANGSMITH_PROJECT
GOOGLE_CLIENT_ID
GOOGLE_CLIENT_SECRET
DATABASE_URL
```

## Data flow — one chat turn

1. **Frontend** sends the user's message to `/agent` over SSE, carrying the current
   `thread_id` (or none, for a new conversation) and the session cookie.
2. **Backend** resolves the authenticated user from the session cookie, yielding the mem0
   `user_id` and the conversation `thread_id`.
3. **Graph invocation**:

   ```python
   graph.invoke(
       state,
       config={
           "configurable": {"thread_id": tid},          # LangGraph checkpointer
           "metadata": {"thread_id": tid, "user_id": uid},  # LangSmith grouping
       },
   )
   ```

   The same id serves two independent consumers: `configurable.thread_id` drives Postgres
   checkpointing, and the `metadata` keys drive LangSmith's thread grouping and user
   filtering. LangSmith recognises `thread_id` (or `session_id`) as a first-class grouping
   key; `user_id` is an arbitrary metadata key, filterable via trace query syntax.
4. **`retrieve_memories`** — `mem0.search(query=<latest user message>,
   filters={"user_id": uid})`, results folded into this turn's system prompt.
5. **`call_model`** — OpenAI call using that system prompt, the message history LangGraph
   restored from the checkpointer, and the new user message.
6. **`write_memories`** — `mem0.add(messages=[user_msg, assistant_reply], user_id=uid)`.
   mem0 performs extraction on its side.
7. **Streaming back** — the AG-UI adapter emits `RUN_STARTED` → per-node events →
   `MESSAGES_SNAPSHOT` with the assistant reply → `RUN_FINISHED`. CopilotKit renders these
   incrementally.
8. **Automatic side effects** — `PostgresSaver` persists updated conversation state under
   `thread_id`, so a reload resumes the same conversation. LangSmith captures the whole
   invocation as one trace tagged by `thread_id` and `user_id`, with no code beyond the
   metadata in step 3.

## Auth flow

Runs on first visit or expired session only.

1. Frontend shows "Sign in with Google" → redirects to backend `/auth/login` → Google
   consent screen → Google redirects to `/auth/callback?code=...`.
2. Backend exchanges the code, verifies the id token, upserts the user row (keyed by Google
   `sub`) in `app-postgres`, sets a signed session cookie, redirects to the frontend.
3. Frontend calls `/auth/me` on load to choose between the login screen and the chat.

## Error handling

**Guiding rule: memory and observability failures must never break the chat.** mem0 and
LangSmith are enrichment, not the critical path.

| Failure | Behaviour |
|---|---|
| mem0 `search` fails or times out | Log, proceed with an empty memory set — the user gets a reply without recall rather than an error. Timeout ~2s so a hung memory service cannot stall every turn. |
| mem0 `add` fails | Log and swallow. The reply is already generated; one lost memory write does not justify failing a completed turn. |
| OpenAI call fails | Critical path — surface it. Retry once on a transient or rate-limit error, then emit an AG-UI error event so the user sees a real failure. |
| Postgres checkpointer unavailable | Fail loudly at startup. A conversation that cannot persist is broken, not degraded. |
| LangSmith unreachable | Nothing to do — the SDK fails open and buffers or drops traces without touching request flow. |
| Expired or invalid session on `/agent` | Return 401; the frontend drops back to the login screen. |

## Testing

Scoped to what protects the integration, not blanket coverage.

- **Unit, external calls mocked** — each graph node in isolation: that `retrieve_memories`
  builds the system prompt correctly from mem0 results and degrades to empty on a mem0
  exception; that `write_memories` passes the right `user_id` and swallows failures.
- **Graph-level, OpenAI and mem0 mocked** — one full `graph.invoke` asserting node order
  and that the checkpointer persists state under `thread_id`.
- **Integration, real Postgres** (Compose container, throwaway DB) — a two-turn
  conversation on one `thread_id` resumes correctly from the checkpointer.
- **Auth** — the callback handler with a stubbed Google token response: user upsert, cookie
  issuance, and the 401 path on `/agent`.
- **Manual end-to-end** — the real acceptance test:
  1. Log in as user A, state a preference ("I'm vegetarian").
  2. Start a **new** conversation; ask something that should use it; confirm recall.
  3. Log in as user B; confirm A's memories do not leak.
  4. Confirm both turns appear as grouped threads in LangSmith and the memories appear in
     the mem0 dashboard.

No frontend unit tests — CopilotKit renders the chat, and testing their component is not
this project's integration.

## Repo layout

```
use_mem0/
  docker-compose.yml          # app-postgres only
  .env.example
  README.md                   # setup: keys, compose up, run both sides
  backend/
    pyproject.toml
    src/app/
      main.py                 # FastAPI app, mounts auth + agui routers
      config.py               # env loading/validation, fail-fast on missing keys
      auth/                   # routes.py, google.py, session.py
      db/                     # engine, users/sessions schema, migrations
      agent/
        graph.py              # graph definition + compile w/ PostgresSaver
        nodes.py              # retrieve_memories, call_model, write_memories
        memory.py             # mem0 client wrapper + degradation logic
      agui/routes.py          # /agent endpoint, session-gated
    tests/
  frontend/
    package.json, vite.config.ts
    src/
      App.tsx                 # auth gate: login screen vs. chat
      Login.tsx
      Chat.tsx                # CopilotKit provider + <CopilotChat/>
```

Two independent processes, one Compose file, secrets only in `.env`.

## Out of scope

- AWS deployment — a follow-up sub-project. Postgres runs in a container specifically so
  that RDS becomes a connection-string change.
- In-app memory inspector and per-message trace links — inspection happens in the mem0 and
  LangSmith dashboards.
- Agent tools beyond memory — the graph has no tool-calling layer in this iteration.
- `use_graphiti/` — a separate experiment, untouched by this spec.

## Known risks

- **`.setup()` on the Postgres checkpointer must run once** before first use, or the graph
  fails on missing tables. Implementation must handle this explicitly at startup or via
  migration rather than assuming it.
- **Non-LangChain SDK calls inside graph nodes are not auto-traced** by LangSmith. The mem0
  client calls are plain SDK calls, so they need `@traceable` wrapping to appear nested in
  the trace — otherwise the memory steps are invisible in the very observability layer this
  project exists to demonstrate.
- **LangSmith metadata must be set consistently on all runs**, including child runs, for
  thread filtering and token counting to work across a conversation.
