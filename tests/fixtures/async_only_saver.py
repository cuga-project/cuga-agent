"""A checkpointer that behaves like a durable async saver on the event loop.

``AsyncSqliteSaver`` / ``AsyncPostgresSaver`` raise when their sync methods are called
from the event loop thread. ``AsyncOnlySaver`` simulates that: its sync ``get_tuple`` /
``put`` / ``put_writes`` / ``list`` raise, and its async twins use ``MemorySaver``'s
storage. A code path that touches graph state through the sync API fails loudly.

Each rejected sync call is also appended to ``sync_calls`` before raising, so a test
can assert ``saver.sync_calls == []`` even when the code under test swallows the error.
"""

from __future__ import annotations

from typing import Any, Optional, TypedDict

from langgraph.checkpoint.memory import MemorySaver


class AsyncOnlySaver(MemorySaver):
    """MemorySaver whose sync API raises; the async API uses the parent's storage."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.sync_calls: list[str] = []

    def _reject(self, name: str) -> None:
        self.sync_calls.append(name)
        raise AssertionError(f"sync {name} called")

    def get_tuple(self, config):
        self._reject("get_tuple")

    def list(self, config, **kwargs):
        self._reject("list")

    def put(self, config, checkpoint, metadata, new_versions):
        self._reject("put")

    def put_writes(self, config, writes, task_id, task_path=""):
        self._reject("put_writes")

    async def aget_tuple(self, config):
        return super().get_tuple(config)

    async def alist(self, config, *, filter=None, before=None, limit=None):
        for item in super().list(config, filter=filter, before=before, limit=limit):
            yield item

    async def aput(self, config, checkpoint, metadata, new_versions):
        return super().put(config, checkpoint, metadata, new_versions)

    async def aput_writes(self, config, writes, task_id, task_path=""):
        return super().put_writes(config, writes, task_id, task_path)


_ParkedState = TypedDict("_ParkedState", {"hitl_action": Optional[dict], "answer": str}, total=False)


def parked_hitl_graph(hitl_action: dict[str, Any]) -> tuple[Any, AsyncOnlySaver]:
    """Real graph that parks before ``WaitForResponse`` on an ``AsyncOnlySaver``.

    ``Produce`` sets ``hitl_action`` (a copy of ``hitl_action``) and the graph stops
    before ``WaitForResponse``. Resuming with ``ainvoke(None, config)`` clears the
    action and sets ``answer`` to ``"done"``. Returns ``(compiled_graph, saver)``.
    """
    from langgraph.graph import END, START, StateGraph

    def produce(_state: _ParkedState) -> dict[str, Any]:
        return {"hitl_action": dict(hitl_action)}

    def wait_for_response(_state: _ParkedState) -> dict[str, Any]:
        return {"hitl_action": None, "answer": "done"}

    g = StateGraph(_ParkedState)
    g.add_node("Produce", produce)
    g.add_node("WaitForResponse", wait_for_response)
    g.add_edge(START, "Produce")
    g.add_edge("Produce", "WaitForResponse")
    g.add_edge("WaitForResponse", END)
    saver = AsyncOnlySaver()
    return g.compile(checkpointer=saver, interrupt_before=["WaitForResponse"]), saver
