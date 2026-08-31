"""The one place a conversation id is checked against its owner."""

import uuid

from fastapi import Depends, HTTPException, Request

from ..auth.session import get_current_user


def owns_conversation(pool, conversation_id: str, user_sub: str) -> bool:
    """Whether this user owns this conversation.

    A malformed id is simply not owned by anyone: the id arrives from a URL, and
    letting Postgres reject it as a bad UUID would turn a guess into a 500.
    """
    try:
        uuid.UUID(str(conversation_id))
    except (ValueError, AttributeError, TypeError):
        return False
    with pool.connection() as conn:
        row = conn.execute(
            "SELECT 1 FROM conversations WHERE id = %s AND user_sub = %s",
            (conversation_id, user_sub),
        ).fetchone()
    return row is not None


def require_owned_conversation(
    conversation_id: str,
    request: Request,
    user_sub: str = Depends(get_current_user),
) -> str:
    """Resolve a conversation the caller owns, or 404.

    404 rather than 403: a 403 would confirm that another user's conversation
    exists. This is the single place the check lives — every route that accepts
    a conversation id must depend on it.
    """
    if not owns_conversation(request.app.state.pool, conversation_id, user_sub):
        raise HTTPException(status_code=404, detail="Conversation not found")
    return conversation_id
