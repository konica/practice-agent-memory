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
| Agent UI transport | **AG-UI protocol**, with assistant-ui on the React side |
| Persistence | Postgres (Docker) — LangGraph checkpoints + users/sessions |
| Auth | Google OAuth (Gmail provider) |

### Verified package facts

Confirmed against official docs as of August 2026:

- `ag-ui-protocol` (PyPI v0.1.21) — core typed event models + SSE encoding.
- `ag-ui-langgraph` (PyPI v0.0.44) — official LangGraph adapter, provides
  `add_langgraph_fastapi_endpoint(app, graph, "/agent")`.
- `@assistant-ui/react` (npm v0.15.17) + `@assistant-ui/react-ag-ui` (v0.0.57) — the React
  client. MIT-licensed, actively maintained (both published 2026-08-27; AG-UI package
  commits 2026-08-29). Provides `useAgUiRuntime`, `AssistantRuntimeProvider`, `<Thread />`,
  and the `adapters.history` seam that makes conversation resumption work — see
  [Frontend client choice](#frontend-client-choice-assistant-ui-over-copilotkit).
  `@assistant-ui/react-markdown` is a separate (also MIT) package if markdown rendering is
  wanted.
- `@ag-ui/client` (v0.0.58) — provides `HttpAgent`, the transport assistant-ui's runtime
  wraps. Not used directly for rendering.
- **CopilotKit (`@copilotkit/react-*` v1.69.3) was evaluated and rejected** — see the same
  section for why.
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
- **Frontend**: Vite dev server running React + assistant-ui, talking to the backend's
  AG-UI endpoint and OAuth routes.

**Infra services** — Docker Compose, stateful and disposable:

- `app-postgres` — LangGraph checkpoints (thread state) plus the `users`, `auth_sessions`,
  and `conversations` tables.

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
  id token, upsert user, set session cookie), `/auth/me` (session check for the frontend),
  `/auth/logout` (revoke the auth session).
- `conversations/` — the conversation registry and its ownership check
  (see [Conversation management](#conversation-management)).
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
- Authenticated: a conversation sidebar (list, new, rename, delete) alongside an
  `AssistantRuntimeProvider` + `<Thread />` pointed at `/agent`, keyed by the selected
  `threadId`, with a history adapter that rehydrates the transcript on switch.

The sidebar is the application's own component, driven by the `/conversations` API rather
than assistant-ui's `@experimental` thread-list adapter — see
[Frontend client choice](#frontend-client-choice-assistant-ui-over-copilotkit).

assistant-ui's primitives are headless, so the visual design is fully the application's
own; the mockup is implemented directly rather than approximated through theme props.

**Design system: Tailwind CSS + shadcn/ui.** This is assistant-ui's own supported path — its
CLI copies styled component source into the repository, where it is edited directly rather
than configured through a vendor theme. The mockup's palette is applied by overriding
shadcn's CSS variable *values* while keeping its variable *names*, so every generated
component inherits the design automatically. Primitives that carry focus management and ARIA
behaviour (dialog, dropdown, scroll area) come from shadcn rather than being hand-rolled —
which matters most for the delete confirmation, the guard on an irreversible action.

The canonical token list lives in the implementation plan (Task 11), supplied by the designer
from the mockup source. It must not be re-derived by inspecting the rendered mockup: an
extraction attempt produced a `--destructive` value absent from the design, mislabelled an
avatar-placeholder fill as the systemic accent, and collapsed two scales into single values.
Three properties resist naive extraction and are easy to get wrong:

- **Radius is a scale**, not one value — 6px inputs, 7px marks, 9–10px controls, 12–14px
  cards, 16px the sign-in logo mark.
- **Bubble radius is directional** — `14px 14px 2px 14px` for user, `14px 14px 14px 2px` for
  assistant. The sharp corner is the tail and mirrors the alignment side.
- **"Destructive" is a family, not a token** — an action colour plus a separate banner
  background, border, and message text.

Typography is Plus Jakarta Sans, a deliberate choice rather than an incidental value.

The UI is otherwise deliberately minimal — no in-app memory inspector and no per-message
trace links.
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

## Conversation management

A user has many conversations and can browse, resume, rename, and delete them. This section
covers how that list is owned and secured.

### Why a conversations table is required

**LangGraph's `PostgresSaver` has no concept of users.** It stores checkpoints keyed by
`thread_id` and will return any thread's full history to any caller that names it. Since
`thread_id` is visible to the browser, an endpoint that trusts a client-supplied
`thread_id` without an ownership check lets one user:

- read another user's entire conversation, and
- append turns to it, which then writes memories derived from that exchange into the wrong
  user's mem0 space.

This is a genuine authorisation vulnerability, not a theoretical one, and it is the primary
reason for the table below. The conversation list is the secondary benefit.

### Division of responsibility

**The application owns conversation identity and ownership; LangGraph owns message
content.** The `conversations` table is the index and access-control layer; the checkpointer
holds the messages behind each row. The application must never query LangGraph's checkpoint
tables directly to build the list — that schema is LangGraph's internal detail and not a
stable interface.

```
conversations
  id           uuid primary key   -- this IS the thread_id passed to LangGraph
  user_id      text not null      -- FK to users, the Google `sub`
  title        text
  created_at   timestamptz
  updated_at   timestamptz        -- newest-first ordering
  archived_at  timestamptz null   -- soft delete
```

### Endpoints

All are scoped to the authenticated user, without exception.

| Route | Purpose |
|---|---|
| `GET /conversations` | List for the current user, newest first |
| `POST /conversations` | Create; returns the new id |
| `GET /conversations/{id}` | Conversation metadata, after the ownership check |
| `GET /conversations/{id}/messages` | Transcript for rehydration, read via `graph.get_state(config)`. Consumed by assistant-ui's history adapter |
| `PATCH /conversations/{id}` | Rename |
| `DELETE /conversations/{id}` | Delete the row **and** the associated checkpoint rows |

**The ownership check lives in exactly one place** — a shared dependency resolving
(`thread_id`, auth session) to a conversation or a 404, used by `/agent` and by every
`/conversations/{id}` route. Implemented per-route, one route will eventually omit it. It
returns **404 rather than 403**, so the response does not confirm that another user's
conversation exists.

**Titles** are the first user message truncated to ~50 characters, set on the first turn.
LLM-generated titles read better but cost an extra model call per conversation, which is not
worth it here; renaming covers the shortfall.

**Deletion** must remove the checkpoint rows as well as the registry row. Otherwise orphaned
checkpoint state retains the message content the user asked to have deleted.

### Interaction with mem0 — important

Memories are scoped to **`user_id`, not `thread_id`**. This is deliberate and is the
mechanism behind the core demo: memories cross conversation boundaries, so a brand-new
conversation still knows the user is vegetarian.

The consequence must be stated plainly: **deleting a conversation does not delete the
memories derived from it.** The transcript disappears; mem0 retains what it extracted. A
user who expects "delete" to mean "forgotten" will be surprised. This is precisely why the
[forget capability](#planned-second-iteration) is a distinct feature rather than something
conversation deletion provides for free.

### Auth sessions

Distinct from conversations despite the overlapping word. The table is named
**`auth_sessions`** rather than `sessions` specifically to prevent that confusion.

```
auth_sessions
  id           uuid primary key
  user_id      text not null
  created_at   timestamptz
  expires_at   timestamptz
  revoked_at   timestamptz null
```

Server-side sessions rather than stateless JWTs: Postgres is already present, and DB-backed
sessions support revocation and "log out everywhere," which JWTs cannot do without
additional machinery. The cookie is `httpOnly`, `secure`, `sameSite=lax`, with absolute
expiry.

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

### Extraction control

**Resolved (was blocked on research — see issue #16).** mem0 Platform exposes two levers
over what `add` extracts, both confirmed against the current docs:

- **`custom_instructions`** — a natural-language guideline string. Settable project-wide via
  `client.project.update(custom_instructions=...)`, or per call as a `custom_instructions`
  kwarg on `client.add(...)`, which overrides the project setting for that call only.
- **`custom_categories`** — a list of `{name: description}` categories mem0 tags memories
  with. Settable project-wide via `client.project.update(custom_categories=...)`, or per call
  on `add(...)`; a per-call list fully replaces the project list rather than merging with it.
  The built-in defaults (`personal_details`, `family`, `professional_details`, `sports`,
  `travel`, `food`, `music`, `health`, `technology`, `hobbies`, `fashion`, `entertainment`,
  `milestones`, `user_preferences`, `misc`) already cover what a general personal assistant
  needs to track.

**Decision: use `custom_instructions`, passed per call from `write_memories`; do not define
`custom_categories`.** The default categories are broad enough for this app's scenario, and
adding project-specific ones would be tuning without a use case that needs it. Extraction
quality — what counts as worth remembering — is the actual gap: mem0 with no steer has no
way to know this is a personal assistant rather than, say, a support bot, and no way to know
which facts a user would consider sensitive enough to withhold by default.

`write_memories` (Task 6, `MemoryStore.add`) passes a fixed instruction string:

```python
CUSTOM_INSTRUCTIONS = (
    "Extract only durable facts about the user that would help a personal assistant "
    "serve them better across future conversations: stable preferences, interests, "
    "constraints, relationships, and professional or personal context they share about "
    "themselves.\n\n"
    "Do not extract one-off requests or tasks (e.g. \"remind me to...\"), transient "
    "conversational content, or sensitive identifiers such as passwords, API keys, "
    "financial account or card numbers, or government ID numbers, even if the user "
    "states them."
)
```

Kept as a per-call argument rather than a one-time `client.project.update()` provisioning
step: everything about this app's mem0 configuration stays in version control and requires
no manual dashboard or setup-script step, consistent with how the rest of the stack is
configured from `.env` and code alone.

**Follow-up, once observed:** whether this instruction actually changes what gets stored is
an empirical question, same as memory conflict handling above — worth a line in the README's
acceptance findings (Task 15) once `write_memories` exists and can be exercised against real
mem0 Platform calls.

## Data flow — one chat turn

1. **Frontend** sends the user's message to `/agent` over SSE, carrying the current
   `thread_id` (obtained from `POST /conversations` for a new conversation) and the session
   cookie.
2. **Backend** resolves the authenticated user from the session cookie, yielding the mem0
   `user_id`, then runs the shared ownership check to confirm this user owns `thread_id`,
   returning 404 if not. No graph invocation happens before that check passes.
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
6. **`write_memories`** — `mem0.add(messages=[user_msg, assistant_reply], user_id=uid,
   custom_instructions=CUSTOM_INSTRUCTIONS)`. mem0 performs extraction on its side, steered
   by the instructions decided in [Extraction control](#extraction-control) above.
7. **Streaming back** — the AG-UI adapter emits `RUN_STARTED` → per-node events →
   `MESSAGES_SNAPSHOT` with the assistant reply → `RUN_FINISHED`. assistant-ui's runtime
   renders these incrementally.
8. **Automatic side effects** — `PostgresSaver` persists updated conversation state under
   `thread_id`, so a reload resumes the same conversation. The conversation's `updated_at`
   is bumped so it sorts to the top of the list, and its title is set from this message if
   it was the first turn. LangSmith captures the whole invocation as one trace tagged by
   `thread_id` and `user_id`, with no code beyond the metadata in step 3.

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
- **Authorization (required, not optional)** — user B requests user A's `thread_id` against
  `/agent` and against every `/conversations/{id}` route, and receives 404 in each case.
  This is the regression test for the vulnerability described in
  [Why a conversations table is required](#why-a-conversations-table-is-required); without
  it, a future refactor can silently reintroduce cross-user access.
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

No frontend unit tests — assistant-ui renders the chat, and testing their components is not
this project's integration.

**Required first frontend step: a manual smoke test.** Connect assistant-ui to the running
`ag-ui-langgraph` endpoint, send one message, reload the page, and confirm the transcript
rehydrates through the history adapter. Nothing in this stack has been run end to end
against a live server, only verified at the API-shape level. No further UI work should be
built on the assumption until this passes.

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
      conversations/          # routes.py, ownership.py (the shared 404 check)
      db/                     # engine, schema (users/auth_sessions/conversations), migrations
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
      ConversationList.tsx    # sidebar: list, new, rename, delete
      Chat.tsx                # AssistantRuntimeProvider + <Thread/>, keyed by threadId
      historyAdapter.ts       # adapters.history -> GET /conversations/{id}/messages
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
  delete and update endpoints. Tracked as issue #15.
- ~~**Control over what gets extracted.**~~ Resolved — see
  [Extraction control](#extraction-control). mem0 Platform confirmed to expose
  `custom_instructions`; folded into Task 6's `write_memories` implementation rather than
  deferred, since no separate research or design work remained once the API was confirmed.

## Out of scope

- AWS deployment — a follow-up sub-project. Postgres runs in a container specifically so
  that RDS becomes a connection-string change.
- In-app memory inspector and per-message trace links — inspection happens in the mem0 and
  LangSmith dashboards.
- Agent tools beyond memory — the graph has no tool-calling layer in this iteration.
- `use_graphiti/` — a separate experiment, untouched by this spec.

## Frontend client choice: assistant-ui over CopilotKit

**Status: resolved.** CopilotKit was the original choice and was rejected after research;
this section records why, so the decision is not relitigated or accidentally reverted.

### Why CopilotKit was rejected

Resuming a conversation has two independent halves, and CopilotKit fails the second:

1. **Agent-side resumption works.** `threadId` is a top-level field of AG-UI's
   `RunAgentInput`, and `ag-ui-langgraph` maps it unconditionally onto
   `config["configurable"]["thread_id"]` (`agent.py:1638`), so LangGraph checkpointing keys
   off it correctly and the model sees full history.
2. **UI-side rehydration does not.** The messages live in Postgres; the browser has nothing
   until something sends them. On conversation switch, the message list is empty.

CopilotKit's v1 documentation states that setting `threadId` loads previous messages, backed
by a `loadAgentState` GraphQL resolver. In the shipped 1.69.3 package that resolver is
**stubbed** — its agent list is hardcoded empty, so it always throws
`CopilotKitAgentDiscoveryError` and returns `{}` — and `react-core` no longer issues the
query. The documented behaviour is dead code. Open and unresolved:
[#2200](https://github.com/CopilotKit/CopilotKit/issues/2200),
[#2624](https://github.com/CopilotKit/CopilotKit/issues/2624). No `initialMessages`-style
prop exists.

Their working alternative, `CopilotThreadsDrawer` / `useThreads`, **requires CopilotKit
Intelligence** — a licensed tier that mirrors AG-UI events into their platform and replays
them on resume. Their docs are explicit that without it, history is "re-derived from the
event history, which nobody kept."

That sentence identifies the underlying issue: **the checkpointer stores LangGraph state,
not AG-UI events.** Rich UI artifacts (rendered tool-call components, streamed reasoning,
attachments) are not reconstructible from LangGraph state. Plain messages are — which is
sufficient for this project, since it has no custom tool-call UI.

Net effect if built on CopilotKit: clicking a past conversation shows an **empty pane** until
the user sends a message. That fails the conversation-list requirement outright.

### Why assistant-ui resolves it

`@assistant-ui/react-ag-ui` exposes a **history adapter** as a first-class, documented API.
Verified in the shipped `.d.ts`, not only in docs:

```ts
type ThreadHistoryAdapter = {
  load(): Promise<ExportedMessageRepository & { state?: ...; unstable_resume?: boolean }>;
  append(item: ExportedMessageRepositoryItem): Promise<void>;
}
```

```tsx
const agent = useMemo(() => new HttpAgent({ url: "/agent" }), []);
const runtime = useAgUiRuntime({
  agent,
  adapters: {
    history: {
      async load() {
        const { messages } = await fetch(`/conversations/${id}/messages`).then(r => r.json());
        return ExportedMessageRepository.fromArray(fromAgUiMessages(messages));
      },
      async append({ message }) { /* persistence handled by the checkpointer */ },
    },
  },
});
return <AssistantRuntimeProvider runtime={runtime}><Thread /></AssistantRuntimeProvider>;
```

`load()` calls **our** backend. There is no vendor cloud in the path — exactly where
CopilotKit's stubbed resolver fails. Thread switching hydrates through the same mechanism.

Decisive differences:

| Requirement | CopilotKit | assistant-ui |
|---|---|---|
| Rehydrate past conversation | ✗ non-functional | ✓ `adapters.history` |
| Conversation list | Requires paid Intelligence | ✓ `ThreadListPrimitive`, MIT |
| Styling | Constrained to their theme props | ✓ Headless, unstyled primitives |
| Licence | Open-core; thread features gated | ✓ MIT throughout |

Licensing verified: MIT across `@assistant-ui/react`, `react-ag-ui`, `react-markdown`, and
the CLI. The paid tier covers only their optional hosted persistence backend, which this
project does not use.

**Hand-building on `@ag-ui/client` was the fallback and is no longer needed.** assistant-ui
supplies the message view, composer, auto-scroll, streaming/running state, and error states
for free, while still permitting the custom design, so it dominates hand-building on both
effort and capability.

### Backend impact: none beyond one endpoint

assistant-ui wraps `@ag-ui/client`'s `HttpAgent` against the same
`add_langgraph_fastapi_endpoint(app, graph, "/agent")` already specified. The only addition
is `GET /conversations/{id}/messages`, reading via LangGraph's `graph.get_state(config)` —
which the conversation-management design required regardless of frontend choice.

### Caveats

- The **thread-list adapter is marked `@experimental`** by its authors. The history adapter,
  which resolves the actual blocker, is not. The application supplies its own conversation
  sidebar against its own `/conversations` API, so this is low-exposure.
- API shapes were verified against shipped type definitions and docs, but **nothing has been
  run against a live `ag-ui-langgraph` server**. The first frontend implementation step must
  be a smoke test — connect, send one message, reload, confirm history renders — before any
  further UI is built on the assumption.
- Markdown rendering and syntax highlighting are separate (MIT) packages, not bundled.

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
