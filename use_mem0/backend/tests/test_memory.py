import time

from app.agent.memory import MemoryStore


ENTITY_PARAMS = frozenset({"user_id", "agent_id", "app_id", "run_id"})


class FakeClient:
    """Stands in for ``mem0.MemoryClient``, mirroring its 2.0.19 identity rules.

    The two calls want identity in opposite places, and the real client enforces
    both: ``search`` rejects a top-level entity id and demands ``filters``, while
    ``add`` copies its kwargs straight into the request body and the Platform
    answers 400 unless an entity id sits at the top level. A fake that merely
    records whatever it is handed cannot tell the two conventions apart, so it
    would let a wrapper that mixes them up pass — which is how every memory
    write once reached production broken. Enforcing both rules here is the point
    of this double.
    """

    def __init__(self, search_result=None, raises=False, delay_seconds=0.0):
        self.search_result = search_result if search_result is not None else []
        self.raises = raises
        self.delay_seconds = delay_seconds
        self.added = []
        self.searched = []

    def search(self, query, options=None, **kwargs):
        if self.delay_seconds:
            time.sleep(self.delay_seconds)
        if self.raises:
            raise RuntimeError("mem0 unavailable")
        invalid = ENTITY_PARAMS & set(kwargs)
        if invalid:
            raise ValueError(
                f"Top-level entity parameters {set(invalid)} are not supported in "
                "search(). Use filters={'user_id': '...'} instead."
            )
        self.searched.append((query, kwargs.get("filters")))
        return self.search_result

    def add(self, messages, options=None, **kwargs):
        if self.raises:
            raise RuntimeError("mem0 unavailable")
        if not ENTITY_PARAMS & set(kwargs):
            raise RuntimeError(
                "At least one entity ID is required (user_id, agent_id, app_id, or run_id)."
            )
        self.added.append((messages, kwargs.get("user_id")))


def test_search_returns_memory_strings():
    client = FakeClient(search_result=[{"memory": "is vegetarian"}, {"memory": "likes jazz"}])
    assert MemoryStore(client).search("food", "user-a") == ["is vegetarian", "likes jazz"]


def test_search_unwraps_the_results_envelope():
    """mem0 Platform returns v1.1 format: ``{"results": [...]}``, not a bare list."""
    client = FakeClient(search_result={"results": [{"memory": "is vegetarian"}]})
    assert MemoryStore(client).search("food", "user-a") == ["is vegetarian"]


def test_search_scopes_the_query_to_the_user():
    client = FakeClient()
    MemoryStore(client).search("food", "user-a")
    assert client.searched == [("food", {"user_id": "user-a"})]


def test_search_degrades_to_empty_on_failure():
    assert MemoryStore(FakeClient(raises=True)).search("food", "user-a") == []


def test_search_degrades_to_empty_when_the_service_hangs():
    client = FakeClient(search_result=[{"memory": "never arrives"}], delay_seconds=5.0)
    store = MemoryStore(client, timeout_seconds=0.2)

    started = time.monotonic()
    assert store.search("food", "user-a") == []
    assert time.monotonic() - started < 2.0


def test_search_tolerates_unexpected_result_shape():
    assert MemoryStore(FakeClient(search_result=[{"unexpected": "shape"}])).search("q", "u") == []


def test_add_passes_the_user_id():
    client = FakeClient()
    messages = [{"role": "user", "content": "hi"}]
    MemoryStore(client).add(messages, "user-a")
    assert client.added == [(messages, "user-a")]


def test_add_sends_the_entity_id_at_the_top_level():
    """``add`` takes identity top-level, the opposite of ``search``.

    Passing it as ``filters={"user_id": ...}`` instead — the convention ``search``
    requires — reaches the Platform as a body with no entity id and is rejected
    with "At least one entity ID is required", which `add` then swallows: every
    write is lost and the chat looks healthy.
    """
    client = FakeClient()
    MemoryStore(client).add([{"role": "user", "content": "hi"}], "user-a")
    assert client.added, "add sent no entity ID that mem0 would accept"


def test_add_swallows_failures():
    MemoryStore(FakeClient(raises=True)).add([{"role": "user", "content": "hi"}], "user-a")
