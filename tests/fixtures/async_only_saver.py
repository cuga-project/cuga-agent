"""A checkpointer that behaves like a durable async saver on the event loop.

``AsyncSqliteSaver`` / ``AsyncPostgresSaver`` raise when their sync methods are called
from the event loop thread. ``AsyncOnlySaver`` simulates that: its sync ``get_tuple`` /
``put`` / ``put_writes`` / ``list`` raise, and its async twins use ``MemorySaver``'s
storage. A code path that touches graph state through the sync API fails loudly.
"""

from __future__ import annotations

from langgraph.checkpoint.memory import MemorySaver


class AsyncOnlySaver(MemorySaver):
    """MemorySaver whose sync API raises; the async API uses the parent's storage."""

    def get_tuple(self, config):
        raise AssertionError("sync get_tuple called")

    def list(self, config, **kwargs):
        raise AssertionError("sync list called")

    def put(self, config, checkpoint, metadata, new_versions):
        raise AssertionError("sync put called")

    def put_writes(self, config, writes, task_id, task_path=""):
        raise AssertionError("sync put_writes called")

    async def aget_tuple(self, config):
        return super().get_tuple(config)

    async def alist(self, config, *, filter=None, before=None, limit=None):
        for item in super().list(config, filter=filter, before=before, limit=limit):
            yield item

    async def aput(self, config, checkpoint, metadata, new_versions):
        return super().put(config, checkpoint, metadata, new_versions)

    async def aput_writes(self, config, writes, task_id, task_path=""):
        return super().put_writes(config, writes, task_id, task_path)
