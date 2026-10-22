"""Turn prelude for /stream (W09.1, design §6.2)."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from langgraph.pregel import Pregel

    from cuga.backend.server.conversation_threads import ThreadRecord
    from cuga.backend.server.thread_lease import LeaseToken, ThreadLease

logger = logging.getLogger(__name__)


class ThreadUnavailable(Exception):
    """The thread cannot be continued (checkpoint missing or unreadable)."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


@dataclass
class PreludeResult:
    record: "ThreadRecord"
    lease_token: "LeaseToken"
    snapshot: Optional[object]  # None => fresh thread; StateSnapshot from aget_state
    notices: list  # list of notice dicts, emitted first in the stream
    resume_allowed: bool  # False if a pending interrupt was cancelled (W10 — always True in PR2)


async def run_turn_prelude(
    *,
    record: "ThreadRecord",
    thread_id: str,
    graph: "Pregel",
    lease: "ThreadLease",
    checkpointer_kind: str,
    ttl_seconds: float = 60.0,
) -> PreludeResult:
    """Run the turn prelude: acquire the lease, then load the checkpoint (fail-closed).

    ``record`` is already resolved by ``/stream``. ``ThreadBusy`` from the lease
    propagates. On success the caller owns the returned ``lease_token`` and must
    release it; on any failure here the lease is released before re-raising.
    """
    from cuga.backend.server.conversation_threads import ThreadStatus

    token = await lease.acquire(thread_id, ttl_seconds)
    try:
        try:
            snap = await graph.aget_state({"configurable": {"thread_id": thread_id}})
        except Exception as e:
            logger.exception("checkpoint load failed for thread %s", thread_id)
            raise ThreadUnavailable(reason="unreadable") from e

        has_checkpoint = bool(snap and (snap.values or snap.next))

        if has_checkpoint:
            snapshot = snap
        elif record.status == ThreadStatus.NEW:
            snapshot = None
        elif checkpointer_kind == "memory" and record.status == ThreadStatus.ACTIVE:
            # W09.4: memory checkpointer loses state on restart; start from default state.
            logger.warning(
                "memory checkpointer has no checkpoint for active thread %s; starting fresh",
                thread_id,
            )
            snapshot = None
        else:
            raise ThreadUnavailable(reason="missing")

        return PreludeResult(
            record=record,
            lease_token=token,
            snapshot=snapshot,
            notices=[],
            resume_allowed=True,
        )
    except BaseException:
        await lease.release(token)
        raise
