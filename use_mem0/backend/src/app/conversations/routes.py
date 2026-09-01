from fastapi import APIRouter, Body, Depends, Request

from ..agent.graph import aread_messages
from ..auth.session import get_current_user
from .ownership import require_owned_conversation
from .store import (
    create_conversation,
    delete_conversation,
    list_conversations,
    rename_conversation,
)

router = APIRouter(prefix="/conversations", tags=["conversations"])


@router.get("")
def list_all(request: Request, user_sub: str = Depends(get_current_user)) -> list[dict]:
    return list_conversations(request.app.state.pool, user_sub)


@router.post("")
def create(request: Request, user_sub: str = Depends(get_current_user)) -> dict:
    return create_conversation(request.app.state.pool, user_sub)


@router.patch("/{conversation_id}")
def rename(
    request: Request,
    title: str = Body(embed=True),
    conversation_id: str = Depends(require_owned_conversation),
    user_sub: str = Depends(get_current_user),
) -> dict:
    rename_conversation(request.app.state.pool, conversation_id, user_sub, title)
    return {"ok": True}


@router.delete("/{conversation_id}")
def delete(
    request: Request,
    conversation_id: str = Depends(require_owned_conversation),
    user_sub: str = Depends(get_current_user),
) -> dict:
    delete_conversation(request.app.state.pool, conversation_id, user_sub)
    return {"ok": True}


@router.get("/{conversation_id}/messages")
async def messages(
    request: Request,
    conversation_id: str = Depends(require_owned_conversation),
) -> dict:
    """Transcript for assistant-ui's history adapter.

    Read from the LangGraph checkpointer rather than a table of our own: the
    checkpointer is the source of truth for message content. A table of ours
    would be a second copy to keep in step with what the graph actually replayed.

    `async def` and `aget_state`: the app's checkpointer is `AsyncPostgresSaver`,
    whose synchronous interface bridges back onto the running loop and reuses the
    same connection, failing with "another command is already in progress".
    """
    return {"messages": await aread_messages(request.app.state.graph, conversation_id)}
