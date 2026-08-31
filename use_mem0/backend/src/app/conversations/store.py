"""CRUD for the conversation registry.

The registry exists because LangGraph's checkpointer has no concept of users:
it hands any thread's state to any caller that names its `thread_id`. These
rows are what makes a thread belong to somebody.
"""

import uuid

TITLE_MAX_LENGTH = 50

# LangGraph's checkpointer tables, keyed by thread_id. Children before parent:
# they carry no foreign keys, so nothing cascades on our behalf.
CHECKPOINT_TABLES = ("checkpoint_writes", "checkpoint_blobs", "checkpoints")


def create_conversation(pool, user_sub: str) -> dict:
    conversation_id = uuid.uuid4()
    with pool.connection() as conn:
        return conn.execute(
            """
            INSERT INTO conversations (id, user_sub) VALUES (%s, %s)
            RETURNING id::text, title, created_at, updated_at
            """,
            (conversation_id, user_sub),
        ).fetchone()


def list_conversations(pool, user_sub: str) -> list[dict]:
    with pool.connection() as conn:
        return conn.execute(
            """
            SELECT id::text, title, created_at, updated_at
            FROM conversations
            WHERE user_sub = %s AND archived_at IS NULL
            ORDER BY updated_at DESC
            """,
            (user_sub,),
        ).fetchall()


def rename_conversation(pool, conversation_id: str, user_sub: str, title: str) -> None:
    with pool.connection() as conn:
        conn.execute(
            "UPDATE conversations SET title = %s WHERE id = %s AND user_sub = %s",
            (title[:TITLE_MAX_LENGTH], conversation_id, user_sub),
        )


def delete_conversation(pool, conversation_id: str, user_sub: str) -> None:
    """Remove the registry row and the checkpointer state for this thread.

    Deleting only the registry row would leave orphaned checkpoint rows still
    holding the message content the user asked to have deleted.
    """
    with pool.connection() as conn:
        deleted = conn.execute(
            "DELETE FROM conversations WHERE id = %s AND user_sub = %s RETURNING id",
            (conversation_id, user_sub),
        ).fetchone()
        if deleted is None:
            return
        for table in CHECKPOINT_TABLES:
            conn.execute(f"DELETE FROM {table} WHERE thread_id = %s", (conversation_id,))


def touch_conversation(pool, conversation_id: str, first_user_message: str | None) -> None:
    """Bump updated_at, and set the title from the first message if unset.

    COALESCE, not an assignment: the title is the *first* thing the user said,
    so every later turn must leave it alone.
    """
    title = first_user_message[:TITLE_MAX_LENGTH] if first_user_message else None
    with pool.connection() as conn:
        conn.execute(
            """
            UPDATE conversations
            SET updated_at = now(),
                title = COALESCE(title, %s)
            WHERE id = %s
            """,
            (title, conversation_id),
        )
