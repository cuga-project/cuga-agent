"""Scheduled checkpoint retention sweep for the durable checkpointer (#774).

Removes checkpoint data for conversations idle longer than
``storage.checkpoint_retention_days`` and marks them ``legacy`` so they
can still be read in the UI but cannot be continued.

Active only when ``storage.mode == "prod"`` and ``storage.checkpointer == "durable"``
and ``storage.checkpoint_retention_days > 0``.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Callable

from loguru import logger

from cuga.backend.server.conversation_history import get_conversation_db
from cuga.backend.server.thread_lease import ThreadBusy, get_thread_lease, keep_lease

if TYPE_CHECKING:
    from cuga.backend.storage.checkpoint import CheckpointerHandle

_BATCH_SIZE = 200


@dataclass(frozen=True)
class PruneBatchResult:
    pruned: int
    next_cursor: tuple[str, str] | None


async def _prune_batch(
    *,
    saver,
    conversation_db,
    lease,
    checkpoint_thread_id_fn: Callable[[str, str, str], str],
    cutoff_iso: str,
    after: tuple[str, str] | None = None,
) -> PruneBatchResult:
    """Prune one page and advance past every selected row, including failures.

    A conversation whose lease is held (a turn is running) or whose prune fails is left
    ``new``/``active`` and is retried by the next sweep.  Conversation-scoped supervisor
    child checkpoints are deleted before the parent.
    """
    from cuga.backend.server.main import _delete_child_checkpoints

    rows = await conversation_db.get_idle_before(cutoff_iso, limit=_BATCH_SIZE, after=after)

    pruned = 0
    for row in rows:
        thread_id = row["thread_id"]
        user_id = row["user_id"]
        agent_id = row["agent_id"]
        key = checkpoint_thread_id_fn(thread_id, user_id, agent_id)
        try:
            token = await lease.acquire(key)
        except ThreadBusy:
            logger.debug("retention: thread busy, skipping {}", key[:16])
            continue
        try:
            lost = asyncio.Event()
            async with keep_lease(lease, token, stop_event=lost):
                eligible = await conversation_db.get_idle_thread_before(
                    thread_id, user_id, agent_id, cutoff_iso
                )
                if eligible is None:
                    continue

                def still_held() -> None:
                    if lost.is_set():
                        raise RuntimeError("Retention lease lost; retry on next sweep")

                still_held()
                # Children first; failure leaves the parent and registry retriable.
                await _delete_child_checkpoints(
                    saver=saver,
                    agent_id=agent_id,
                    conversation_id=thread_id,
                    user_id=user_id,
                )
                still_held()
                await saver.adelete_thread(key)
                still_held()
                await conversation_db.set_thread_status(
                    thread_id, user_id, "legacy", only_from=eligible["status"]
                )
                pruned += 1
        except Exception as exc:
            logger.warning("retention: failed to prune thread {}: {}", key[:16], type(exc).__name__)

    cursor = (rows[-1]["last_activity"], rows[-1]["thread_id"]) if rows else None
    return PruneBatchResult(pruned=pruned, next_cursor=cursor)


async def run_retention_loop(
    *,
    handle: CheckpointerHandle,
    checkpoint_thread_id_fn: Callable[[str, str, str], str],
    interval_seconds: float,
    retention_days: int,
) -> None:
    """Background task: sweep idle conversations every ``interval_seconds`` until cancelled."""
    logger.info(
        "retention loop started (interval={}s, retention_days={})",
        interval_seconds,
        retention_days,
    )
    conversation_db = get_conversation_db()
    lease = get_thread_lease()
    saver = handle.saver

    while True:
        await asyncio.sleep(interval_seconds)
        cutoff_iso = (datetime.now(timezone.utc) - timedelta(days=retention_days)).isoformat()
        try:
            total = 0
            after = None
            while True:
                batch = await _prune_batch(
                    saver=saver,
                    conversation_db=conversation_db,
                    lease=lease,
                    checkpoint_thread_id_fn=checkpoint_thread_id_fn,
                    cutoff_iso=cutoff_iso,
                    after=after,
                )
                total += batch.pruned
                if batch.next_cursor is None:
                    break
                after = batch.next_cursor
            if total:
                logger.info("retention: pruned {} conversation(s)", total)
        except Exception as exc:
            logger.error("retention sweep failed: {}", type(exc).__name__)
