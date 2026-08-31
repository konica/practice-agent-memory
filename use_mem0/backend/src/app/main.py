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
        # Migrations and the pool both talk to Postgres, so an unreachable
        # database raises here and startup aborts. That is deliberate: a
        # conversation that cannot persist is broken, not degraded.
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


def app() -> FastAPI:
    """ASGI entry point: `uvicorn app.main:app --factory`.

    A factory rather than a module-level instance, so importing this module
    never reads the environment; tests build an app from explicit settings.
    """
    return create_app(load_settings(os.environ))
