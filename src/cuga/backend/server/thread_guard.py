"""Thread ownership guard (W06.1, design §11.1 steps 1–3, 5)."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from fastapi import HTTPException

if TYPE_CHECKING:
    from cuga.backend.server.conversation_threads import Scope, ThreadRecord

logger = logging.getLogger(__name__)

_NOT_FOUND_DETAIL = "Conversation not found"


class ThreadNotFound(HTTPException):
    def __init__(self) -> None:
        super().__init__(status_code=404, detail=_NOT_FOUND_DETAIL)


async def resolve_owned_thread(scope: "Scope", thread_id: str) -> "ThreadRecord":
    """Fail-closed ownership check.

    Returns the ThreadRecord for the caller's scope, or raises ThreadNotFound.
    - Unknown thread, unauthorized thread, and DB error all raise ThreadNotFound
      with the same byte-identical body (no information leak).
    - Empty or oversized thread_id raises ThreadNotFound.
    """
    tid = (thread_id or "").strip()
    if not tid or len(tid) > 128:
        raise ThreadNotFound()
    try:
        from cuga.backend.server.conversation_threads import get_conversation_threads_db

        rec = await get_conversation_threads_db().get_in_scope(scope, tid)
    except Exception:
        logger.exception("thread ownership lookup failed")
        raise ThreadNotFound()
    if rec is None:
        raise ThreadNotFound()
    return rec
