"""Graph assembly: the fixed three-node turn, checkpointed in Postgres."""

from langchain_core.messages import AIMessage, HumanMessage
from langgraph.graph import END, START, StateGraph

from .memory import MemoryStore
from .nodes import make_call_model, make_retrieve_memories, make_write_memories
from .state import ChatState


def build_graph(store: MemoryStore, model, checkpointer):
    """Fixed three-node graph: retrieve -> model -> write.

    Memory is invoked as graph nodes rather than as LLM tools so that every turn
    runs the same way and every LangSmith trace has the same shape. Tools can be
    added later without disturbing this.
    """
    builder = StateGraph(ChatState)
    builder.add_node("retrieve_memories", make_retrieve_memories(store))
    builder.add_node("call_model", make_call_model(model))
    builder.add_node("write_memories", make_write_memories(store))

    builder.add_edge(START, "retrieve_memories")
    builder.add_edge("retrieve_memories", "call_model")
    builder.add_edge("call_model", "write_memories")
    builder.add_edge("write_memories", END)

    return builder.compile(checkpointer=checkpointer)


def _transcript(state) -> list[dict]:
    """Shape a checkpointed state into the transcript the UI reads."""
    transcript = []
    for message in (state.values or {}).get("messages", []):
        if isinstance(message, HumanMessage):
            transcript.append({"role": "user", "content": message.content})
        elif isinstance(message, AIMessage):
            transcript.append({"role": "assistant", "content": message.content})
    return transcript


def read_messages(graph, thread_id: str) -> list[dict]:
    """Read a thread's transcript from a synchronously checkpointed graph.

    An unknown thread has no checkpoint, so `state.values` is empty and the
    transcript comes back as `[]` rather than raising.

    Only valid for a graph built on `PostgresSaver`. The served graph uses
    `AsyncPostgresSaver`; read that one with `aread_messages`.
    """
    return _transcript(graph.get_state({"configurable": {"thread_id": thread_id}}))


async def aread_messages(graph, thread_id: str) -> list[dict]:
    """The same read, for a graph checkpointed by `AsyncPostgresSaver`.

    The async saver's synchronous interface bridges back onto the loop it was
    built on and reuses the one connection, so calling `get_state` on the served
    graph raises `another command is already in progress` — a runtime failure
    the sync-saver tests cannot reach.
    """
    return _transcript(await graph.aget_state({"configurable": {"thread_id": thread_id}}))
