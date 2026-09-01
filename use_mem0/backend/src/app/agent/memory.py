"""mem0 Platform wrapper that degrades instead of failing the chat.

Memory is enrichment, not the critical path: a memory service that is slow or
down must produce a reply without recall rather than an error.
"""

import logging
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from typing import Any

from langsmith import traceable

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT_SECONDS = 2.0


def build_client(api_key: str, timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS):
    """Build a mem0 Platform client whose transport gives up quickly.

    mem0's own httpx client is built with a 300s timeout, long enough for a hung
    memory service to stall every turn, so we supply our own.
    """
    import httpx
    from mem0 import MemoryClient

    return MemoryClient(api_key=api_key, client=httpx.Client(timeout=timeout_seconds))


def _extract_memories(results: Any) -> list[str]:
    """Pull memory strings out of a mem0 search response, tolerating any shape.

    mem0 Platform answers in v1.1 format — ``{"results": [...]}`` — but the
    wrapper never assumes it: an unrecognised shape yields no recall, not an
    exception on the chat's critical path.
    """
    if isinstance(results, dict):
        results = results.get("results")
    if not isinstance(results, list):
        return []
    memories = []
    for item in results:
        memory = item.get("memory") if isinstance(item, dict) else None
        if isinstance(memory, str):
            memories.append(memory)
    return memories


class MemoryStore:
    """Search and write mem0 memories without ever raising into the chat."""

    def __init__(self, client, timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS):
        self._client = client
        self._timeout_seconds = timeout_seconds
        # Searches run on worker threads so the deadline holds for any client,
        # however its transport is configured. An abandoned search keeps its
        # thread only until the client's own timeout fires; the pool is bounded
        # so a sustained outage cannot spawn threads without limit.
        self._executor = ThreadPoolExecutor(max_workers=8, thread_name_prefix="mem0-search")

    @traceable(name="mem0.search", run_type="retriever")
    def search(self, query: str, user_id: str) -> list[str]:
        """Return this user's relevant memories, or `[]` on any failure."""
        # `search` takes identity in `filters` and raises on a top-level user_id.
        # `add` is the exact opposite — see the note there before touching either.
        future = self._executor.submit(
            self._client.search, query, filters={"user_id": user_id}
        )
        try:
            results = future.result(timeout=self._timeout_seconds)
        except FutureTimeoutError:
            future.cancel()
            logger.warning(
                "mem0 search exceeded %.1fs; continuing without recall", self._timeout_seconds
            )
            return []
        except Exception:
            logger.warning("mem0 search failed; continuing without recall", exc_info=True)
            return []
        return _extract_memories(results)

    @traceable(name="mem0.add", run_type="tool")
    def add(self, messages: list[dict], user_id: str) -> None:
        """Write the turn's exchange to this user's memory; failures are logged only."""
        # Identity goes top-level here, unlike `search` above: `add` copies its
        # kwargs straight into the request body, so a `filters` dict travels as
        # a `filters` field and the Platform answers 400 "At least one entity ID
        # is required". mem0 2.0.19's own `AddMemoryOptions` docstring claims the
        # opposite — `add`'s method docstring and its missing entity-param guard
        # are the accurate ones. Failures here are only logged, so getting this
        # wrong loses every write silently.
        try:
            self._client.add(messages, user_id=user_id)
        except Exception:
            logger.warning("mem0 add failed; memory not written", exc_info=True)


class LazyMemoryClient:
    """A mem0 client that is not built until the first memory call.

    `MemoryClient.__init__` validates its API key over the network, so building
    one eagerly at startup would let a mem0 outage abort the whole application —
    exactly the coupling this module exists to avoid. Deferring it moves that
    failure inside `MemoryStore`'s own degradation, where it is logged and the
    turn continues without recall.
    """

    def __init__(self, api_key: str, timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS):
        self._api_key = api_key
        self._timeout_seconds = timeout_seconds
        self._delegate = None

    def _resolve(self):
        if self._delegate is None:
            self._delegate = build_client(self._api_key, self._timeout_seconds)
        return self._delegate

    def search(self, *args, **kwargs):
        return self._resolve().search(*args, **kwargs)

    def add(self, *args, **kwargs):
        return self._resolve().add(*args, **kwargs)
