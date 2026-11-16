"""A checkpointer that behaves like a durable async saver on the event loop.

``AsyncSqliteSaver`` / ``AsyncPostgresSaver`` raise when their sync methods are called
from the event loop thread. ``AsyncOnlySaver`` simulates that: its sync ``get_tuple`` /
``put`` / ``put_writes`` / ``list`` raise, and its async twins use ``MemorySaver``'s
storage. A code path that touches graph state through the sync API fails loudly.

Each rejected sync call is also appended to ``sync_calls`` before raising, so a test
can assert ``saver.sync_calls == []`` even when the code under test swallows the error.
"""

from __future__ import annotations

from typing import Any

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
