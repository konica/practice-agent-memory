import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from .agent.graph import build_graph
from .agent.memory import LazyMemoryClient, MemoryStore
from .agui.routes import add_agent_gate, mount_agent_endpoint
from .auth.routes import router as auth_router
from .config import Settings, load_settings
from .conversations.routes import router as conversations_router
from .db.engine import open_pool
from .db.migrate import run_migrations

CHAT_MODEL = "gpt-4o-mini"


def configure_langsmith(settings: Settings) -> None:
    """Point LangSmith at the project, by environment and nothing else.

    LangGraph runnables trace themselves when these variables are set, so there
    is no callback handler to build and none to thread through the graph. Only
    the plain mem0 SDK calls need help, and they carry their own `@traceable`.

    `setdefault` so a developer who exported LANGSMITH_TRACING=false to silence
    tracing for a run keeps it silenced.
    """
    os.environ.setdefault("LANGSMITH_TRACING", "true")
    os.environ.setdefault("LANGSMITH_API_KEY", settings.langsmith_api_key)
    os.environ.setdefault("LANGSMITH_PROJECT", settings.langsmith_project)


def create_app(settings: Settings) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # Migrations and the pool both talk to Postgres, so an unreachable
        # database raises here and startup aborts. That is deliberate: a
        # conversation that cannot persist is broken, not degraded.
        run_migrations(settings.database_url)
        app.state.settings = settings
        app.state.pool = open_pool(settings.database_url)

        configure_langsmith(settings)

        # The ASYNC saver, not PostgresSaver: the AG-UI adapter reads thread
        # state with `await graph.aget_state(...)`, and the synchronous saver
        # answers that with NotImplementedError. Both write the same tables,
        # which run_migrations already created.
        #
        # It holds a connection for the process's lifetime, so it is entered
        # here and exited on shutdown rather than per request.
        checkpointer_cm = AsyncPostgresSaver.from_conn_string(settings.database_url)
        app.state.checkpointer_cm = checkpointer_cm
        app.state.graph = build_graph(
            MemoryStore(LazyMemoryClient(settings.mem0_api_key)),
            ChatOpenAI(model=CHAT_MODEL, api_key=settings.openai_api_key),
            await checkpointer_cm.__aenter__(),
        )
        mount_agent_endpoint(app, app.state.graph)

        yield

        app.state.pool.close()
        await checkpointer_cm.__aexit__(None, None, None)

    app = FastAPI(title="mem0 chatbot", lifespan=lifespan)
    # Added before CORS so that CORS ends up the outer layer: `add_middleware`
    # inserts at the front of the stack, and a 401 from the gate still has to
    # carry the headers the browser needs to read it.
    add_agent_gate(app)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[settings.frontend_origin],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(auth_router)
    app.include_router(conversations_router)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


def app() -> FastAPI:
    """ASGI entry point: `uvicorn app.main:app --factory`.

    A factory rather than a module-level instance, so importing this module
    never reads the environment; tests build an app from explicit settings.
    """
    return create_app(load_settings(os.environ))
