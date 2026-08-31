-- Application tables. LangGraph's checkpointer tables are created separately by
-- PostgresSaver.setup(); see migrate.py.
CREATE TABLE IF NOT EXISTS users (
    sub         TEXT PRIMARY KEY,
    email       TEXT NOT NULL,
    name        TEXT,
    picture     TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS auth_sessions (
    id          UUID PRIMARY KEY,
    user_sub    TEXT NOT NULL REFERENCES users(sub) ON DELETE CASCADE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at  TIMESTAMPTZ NOT NULL,
    revoked_at  TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS conversations (
    id          UUID PRIMARY KEY,
    user_sub    TEXT NOT NULL REFERENCES users(sub) ON DELETE CASCADE,
    title       TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    archived_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS conversations_user_updated_idx
    ON conversations (user_sub, updated_at DESC)
    WHERE archived_at IS NULL;
