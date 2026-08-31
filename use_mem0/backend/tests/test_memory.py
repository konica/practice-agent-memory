import time

from app.agent.memory import MemoryStore


class FakeClient:
    """Stands in for ``mem0.MemoryClient``, mirroring its 2.0.19 signatures.

    Both ``search`` and ``add`` take identity via ``filters``; the real client
    rejects a top-level ``user_id``.
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
        self.searched.append((query, kwargs.get("filters")))
        return self.search_result

    def add(self, messages, options=None, **kwargs):
        if self.raises:
            raise RuntimeError("mem0 unavailable")
        self.added.append((messages, (kwargs.get("filters") or {}).get("user_id")))


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


def test_add_swallows_failures():
    MemoryStore(FakeClient(raises=True)).add([{"role": "user", "content": "hi"}], "user-a")
