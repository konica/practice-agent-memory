"""The three graph nodes: recall, reply, remember."""

import logging

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from .memory import MemoryStore
from .state import ChatState

logger = logging.getLogger(__name__)

BASE_PROMPT = (
    "You are a helpful personal assistant. Be concise and direct. "
    "Use what you remember about the user when it is relevant, and never "
    "invent memories you were not given."
)


def build_system_prompt(memories: list[str]) -> str:
    """Render memories into the prompt in the order mem0 returned them.

    Deliberately no deduplication, recency weighting or contradiction filtering:
    the spec's conflict-handling decision is to observe mem0's own consolidation
    behaviour rather than mask it.
    """
    if not memories:
        return BASE_PROMPT
    lines = "\n".join(f"- {memory}" for memory in memories)
    return f"{BASE_PROMPT}\n\nWhat you remember about this user:\n{lines}"


def _latest_human_message(state: ChatState) -> str:
    for message in reversed(state.get("messages", [])):
        if isinstance(message, HumanMessage):
            return message.content
    return ""


def make_retrieve_memories(store: MemoryStore):
    def retrieve_memories(state: ChatState) -> dict:
        if not state.get("memory_enabled", True):
            return {"memories": []}
        query = _latest_human_message(state)
        if not query:
            return {"memories": []}
        return {"memories": store.search(query, state["user_id"])}

    return retrieve_memories


def make_call_model(model, system_prompt_builder=build_system_prompt):
    def call_model(state: ChatState) -> dict:
        system = SystemMessage(system_prompt_builder(state.get("memories", [])))
        messages = [system, *state["messages"]]
        try:
            reply = model.invoke(messages)
        except Exception:
            # The model call is the critical path: unlike memory, it cannot
            # degrade silently. Retry once for transient/rate-limit errors, then
            # let the exception propagate so the AG-UI layer reports a real
            # failure to the user.
            logger.warning("model call failed; retrying once", exc_info=True)
            reply = model.invoke(messages)
        return {"messages": [reply]}

    return call_model


def make_write_memories(store: MemoryStore):
    """Write the turn's exchange to memory.

    Runs regardless of ``memory_enabled``: that toggle only suppresses recall,
    so a comparison session still leaves no gap in the user's memory history.
    """

    def write_memories(state: ChatState) -> dict:
        messages = state.get("messages", [])
        exchange = []
        for message in messages[-2:]:
            if isinstance(message, HumanMessage):
                exchange.append({"role": "user", "content": message.content})
            elif isinstance(message, AIMessage):
                exchange.append({"role": "assistant", "content": message.content})
        if exchange:
            store.add(exchange, state["user_id"])
        return {}

    return write_memories
