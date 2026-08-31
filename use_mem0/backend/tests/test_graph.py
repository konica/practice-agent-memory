import os
import uuid

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.postgres import PostgresSaver

from app.agent.graph import build_graph, read_messages
from app.agent.memory import MemoryStore
from app.db.migrate import run_migrations

DB_URL = os.environ.get("TEST_DATABASE_URL", "postgresql://app:app@localhost:5432/app")


class FakeClient:
    """Stands in for ``mem0.MemoryClient``; identity arrives via ``filters``."""

    def __init__(self):
        self.added = []
        self.searched = []

    def search(self, query, filters=None, **kwargs):
        self.searched.append(query)
        return [{"memory": "is vegetarian"}]

    def add(self, messages, filters=None, **kwargs):
        self.added.append((messages, (filters or {}).get("user_id")))


class ScriptedModel:
    def __init__(self):
        self.seen_prompts = []
        self.turn = 0

    def invoke(self, messages):
        self.seen_prompts.append(messages[0].content)
        self.turn += 1
        return AIMessage(f"reply {self.turn}")


@pytest.fixture
def graph_parts():
    run_migrations(DB_URL)
    client = FakeClient()
    model = ScriptedModel()
    with PostgresSaver.from_conn_string(DB_URL) as checkpointer:
        yield build_graph(MemoryStore(client), model, checkpointer), client, model


def test_single_turn_produces_a_reply(graph_parts):
    graph, _, _ = graph_parts
    config = {"configurable": {"thread_id": str(uuid.uuid4())}}
    result = graph.invoke(
        {"messages": [HumanMessage("hello")], "user_id": "u", "memory_enabled": True}, config
    )
    assert result["messages"][-1].content == "reply 1"


def test_memories_reach_the_system_prompt(graph_parts):
    graph, _, model = graph_parts
    config = {"configurable": {"thread_id": str(uuid.uuid4())}}
    graph.invoke(
        {"messages": [HumanMessage("what to eat?")], "user_id": "u", "memory_enabled": True},
        config,
    )
    assert "is vegetarian" in model.seen_prompts[0]


def test_memory_toggle_skips_retrieval_but_still_writes(graph_parts):
    graph, client, model = graph_parts
    config = {"configurable": {"thread_id": str(uuid.uuid4())}}
    graph.invoke(
        {"messages": [HumanMessage("hi")], "user_id": "u", "memory_enabled": False}, config
    )
    assert client.searched == []
    assert len(client.added) == 1
    assert "is vegetarian" not in model.seen_prompts[0]


def test_conversation_resumes_across_invocations(graph_parts):
    graph, _, _ = graph_parts
    thread_id = str(uuid.uuid4())
    config = {"configurable": {"thread_id": thread_id}}
    graph.invoke({"messages": [HumanMessage("first")], "user_id": "u", "memory_enabled": True}, config)
    result = graph.invoke(
        {"messages": [HumanMessage("second")], "user_id": "u", "memory_enabled": True}, config
    )
    contents = [m.content for m in result["messages"]]
    assert contents == ["first", "reply 1", "second", "reply 2"]


def test_read_messages_returns_the_transcript(graph_parts):
    graph, _, _ = graph_parts
    thread_id = str(uuid.uuid4())
    config = {"configurable": {"thread_id": thread_id}}
    graph.invoke({"messages": [HumanMessage("hello")], "user_id": "u", "memory_enabled": True}, config)
    assert read_messages(graph, thread_id) == [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "reply 1"},
    ]


def test_read_messages_for_an_unknown_thread_is_empty(graph_parts):
    graph, _, _ = graph_parts
    assert read_messages(graph, str(uuid.uuid4())) == []
