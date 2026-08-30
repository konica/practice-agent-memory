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
