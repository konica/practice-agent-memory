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
