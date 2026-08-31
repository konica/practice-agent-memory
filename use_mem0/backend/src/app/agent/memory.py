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
        # Identity goes in `filters`; mem0's v3 API rejects a top-level user_id.
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
        try:
            self._client.add(messages, filters={"user_id": user_id})
        except Exception:
            logger.warning("mem0 add failed; memory not written", exc_info=True)
