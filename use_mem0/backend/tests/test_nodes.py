import pytest
from langchain_core.messages import AIMessage, HumanMessage

from app.agent.memory import MemoryStore
from app.agent.nodes import (
    build_system_prompt,
    make_call_model,
    make_retrieve_memories,
    make_write_memories,
)


class FakeClient:
    """Stands in for ``mem0.MemoryClient``; ``add`` takes identity top-level.

    ``search`` is the opposite and wants ``filters`` — see ``test_memory`` for the
    double that enforces both rules.
    """

    def __init__(self, results=None):
        self.results = results or []
        self.added = []

    def search(self, query, options=None, **kwargs):
        return self.results

    def add(self, messages, options=None, **kwargs):
        self.added.append((messages, kwargs.get("user_id")))


def test_retrieve_puts_memories_on_state():
    store = MemoryStore(FakeClient([{"memory": "is vegetarian"}]))
    node = make_retrieve_memories(store)
    state = {
        "messages": [HumanMessage("what should I eat?")],
        "user_id": "u",
        "memory_enabled": True,
    }
    assert node(state) == {"memories": ["is vegetarian"]}


def test_retrieve_is_skipped_when_memory_is_disabled():
    class ExplodingClient:
        def search(self, *a, **k):
            raise AssertionError("search must not be called when memory_enabled is False")

    node = make_retrieve_memories(MemoryStore(ExplodingClient()))
    state = {"messages": [HumanMessage("hi")], "user_id": "u", "memory_enabled": False}
    assert node(state) == {"memories": []}


def test_retrieve_uses_the_latest_human_message_as_the_query():
    captured = {}

    class CapturingClient:
        def search(self, query, options=None, **kwargs):
            captured["query"] = query
            return []

    node = make_retrieve_memories(MemoryStore(CapturingClient()))
    node(
        {
            "messages": [HumanMessage("first"), AIMessage("reply"), HumanMessage("second")],
            "user_id": "u",
            "memory_enabled": True,
        }
    )
    assert captured["query"] == "second"


def test_system_prompt_includes_memories():
    prompt = build_system_prompt(["is vegetarian", "likes jazz"])
    assert "is vegetarian" in prompt and "likes jazz" in prompt


def test_system_prompt_without_memories_has_no_memory_section():
    assert "What you remember" not in build_system_prompt([])


def test_call_model_appends_the_reply():
    class FakeModel:
        def invoke(self, messages):
            return AIMessage("hello there")

    node = make_call_model(FakeModel(), build_system_prompt)
    result = node({"messages": [HumanMessage("hi")], "memories": [], "user_id": "u"})
    assert result["messages"][0].content == "hello there"


def test_write_memories_sends_the_exchange():
    client = FakeClient()
    node = make_write_memories(MemoryStore(client))
    node(
        {
            "messages": [HumanMessage("I am vegetarian"), AIMessage("noted")],
            "user_id": "user-a",
            "memory_enabled": True,
        }
    )
    messages, user_id = client.added[0]
    assert user_id == "user-a"
    assert messages == [
        {"role": "user", "content": "I am vegetarian"},
        {"role": "assistant", "content": "noted"},
    ]


def test_write_memories_runs_even_when_retrieval_is_disabled():
    client = FakeClient()
    node = make_write_memories(MemoryStore(client))
    node(
        {
            "messages": [HumanMessage("hi"), AIMessage("hello")],
            "user_id": "user-a",
            "memory_enabled": False,
        }
    )
    assert len(client.added) == 1


def test_call_model_retries_once_on_transient_failure():
    class FlakyModel:
        def __init__(self):
            self.calls = 0

        def invoke(self, messages):
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("rate limit exceeded")
            return AIMessage("recovered")

    model = FlakyModel()
    node = make_call_model(model)
    result = node({"messages": [HumanMessage("hi")], "memories": [], "user_id": "u"})
    assert model.calls == 2
    assert result["messages"][0].content == "recovered"


def test_call_model_raises_after_a_second_failure():
    class BrokenModel:
        def invoke(self, messages):
            raise RuntimeError("upstream is down")

    node = make_call_model(BrokenModel())
    with pytest.raises(RuntimeError):
        node({"messages": [HumanMessage("hi")], "memories": [], "user_id": "u"})
