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
    prompt for this turn only. Skipped entirely when `memory_enabled` is false (see
    [Memory toggle](#memory-toggle)).
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
MEMORY_RETRIEVAL_ENABLED=true   # default for the memory toggle
```

## Memory semantics

Storing and retrieving a memory is the easy half of a memory layer. These two requirements
cover the half that is actually hard, and both are acceptance criteria rather than
implementation details.

### Memory conflict handling

Users contradict themselves over time: "I'm vegetarian" in one session, "actually I eat
fish now" three sessions later. The system must have a defined answer for what happens
when stored memories disagree.

**Decision for this iteration: observe, do not resolve.** `retrieve_memories` passes
mem0's results into the system prompt neutrally, without deduplication, recency weighting,
or contradiction-filtering logic of our own. Whatever mem0 returns is what the model sees.

This is deliberate. mem0 performs its own extraction and consolidation on `add`, and we do
not yet know empirically whether it updates a superseded memory, stores both, or surfaces
both on search. Building resolution logic before observing that behaviour would mask the
very semantics this project exists to learn. The contradiction scenario in the acceptance
tests exists to produce that observation.

**Follow-up, once observed:** if mem0 returns contradictory memories rather than
reconciling them, a resolution strategy becomes a real requirement — most likely presenting
memories to the model with recency information and instructing it to prefer the most
recent. That decision is deferred until there is evidence for it, and the finding should be
recorded in the README.

### Memory toggle

A boolean `memory_enabled` field on the graph's input state controls whether
`retrieve_memories` runs. It defaults from `MEMORY_RETRIEVAL_ENABLED` and is read per
request, so flipping it does not require a restart.

The purpose is comparison: run the same prompt with recall on and off and see what memory
is actually contributing. Keeping the flag in graph state rather than reading the
environment inside the node means the "retrieval is skipped" unit test sets one field
instead of patching the environment, and it costs no frontend work — the UI stays exactly
as designed.

Writes are unaffected by the toggle. `write_memories` always runs, so a comparison session
does not create a gap in the user's memory history.

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
   filters={"user_id": uid})`, results folded into this turn's system prompt neutrally, in
   the order mem0 returned them. Skipped when `memory_enabled` is false, in which case the
   turn proceeds with no recall.
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
  exception; that it is skipped entirely when `memory_enabled` is false while
  `write_memories` still runs; that `write_memories` passes the right `user_id` and
  swallows failures.
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
- **Manual contradiction scenario** — the observation exercise behind
  [Memory conflict handling](#memory-conflict-handling):
  1. As user A, state a preference ("I'm vegetarian").
  2. In a later conversation, contradict it ("actually I eat fish now").
  3. In a third conversation, ask a question that depends on the answer ("suggest me
     dinner").
  4. Inspect the mem0 dashboard: did mem0 update the original memory, store both, or keep
     them separate? Inspect the LangSmith trace: what did `retrieve_memories` actually
     return, and did the model follow the newer statement?

  There is no pass/fail assertion here — the deliverable is a recorded finding in the
  README describing mem0's observed consolidation behaviour, which then informs whether a
  resolution strategy is needed.
- **Manual memory-value comparison** — ask an identical, memory-dependent question with
  `MEMORY_RETRIEVAL_ENABLED` true and false, and compare the two replies and their
  LangSmith traces. Confirms the memory layer is contributing something observable rather
  than being decorative.

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

## Planned second iteration

Deliberately excluded from this spec, but expected next — recorded here so they are not
rediscovered as surprises. **Both require verifying mem0 API surfaces that this design work
did not confirm** (only `add` and `search` were verified against the docs), so each needs a
research step before planning.

- **Explicit forget capability.** The ability to say "forget that I mentioned my address."
  A memory system that can only accumulate is a liability, and this forces engagement with
  mem0's delete/update API rather than only `add`/`search`. Needs verification of mem0's
  delete and update endpoints.
- **Control over what gets extracted.** mem0 performs its own extraction on `add`, and this
  design gives it no steer, so it may store things the user would rather it did not.
  Deciding whether to constrain extraction via custom instructions or categories is a real
  requirements question. Needs verification that mem0 Platform exposes such controls.

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
