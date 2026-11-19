"""Durable child ownership metadata beside a conversation's execution checkpoints."""

import asyncio

from langgraph.checkpoint.base import BaseCheckpointSaver, empty_checkpoint

CHILD_REGISTRY_NAMESPACE = "cuga_child_registry"
CHILD_IDS = "child_checkpoint_ids"


class ChildCheckpointRecorder:
    """Record before delegation without advancing the parent's execution graph.

    The registry uses the parent's thread ID and a reserved checkpoint namespace.
    Normal graph writes cannot overwrite it, and deleting the parent thread removes
    it through the saver's standard deletion API. One recorder is shared by all
    delegations in a turn; the parent turn lease serializes successive web turns.
    """

    def __init__(self, saver: BaseCheckpointSaver, parent_thread_id: str):
        self.saver = saver
        self.config = {
            "configurable": {"thread_id": parent_thread_id, "checkpoint_ns": CHILD_REGISTRY_NAMESPACE}
        }
        self._lock = asyncio.Lock()

    async def __call__(self, child_id: str) -> None:
        async with self._lock:
            previous = await self.saver.aget_tuple(self.config)
            values = previous.checkpoint.get("channel_values", {}) if previous else {}
            children = list(values.get(CHILD_IDS) or [])
            if child_id in children:
                return
            children.append(child_id)
            checkpoint = empty_checkpoint()
            old_version = previous.checkpoint.get("channel_versions", {}).get(CHILD_IDS) if previous else None
            version = self.saver.get_next_version(old_version, None)
            checkpoint["channel_values"] = {CHILD_IDS: children}
            checkpoint["channel_versions"] = {CHILD_IDS: version}
            await self.saver.aput(
                previous.config if previous else self.config,
                checkpoint,
                {
                    "source": "update",
                    "step": previous.metadata.get("step", -1) + 1 if previous else 0,
                    "parents": {},
                },
                {CHILD_IDS: version},
            )


async def recorded_child_ids(saver: BaseCheckpointSaver, parent_thread_id: str) -> list[str]:
    checkpoint = await saver.aget_tuple(
        {
            "configurable": {
                "thread_id": parent_thread_id,
                "checkpoint_ns": CHILD_REGISTRY_NAMESPACE,
            }
        }
    )
    if checkpoint is None:
        return []
    values = checkpoint.checkpoint.get("channel_values", {})
    return [value for value in values.get(CHILD_IDS, []) if isinstance(value, str) and value]
