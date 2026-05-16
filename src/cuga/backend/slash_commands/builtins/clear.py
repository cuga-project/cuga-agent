"""``/clear`` — start a fresh conversation in a new thread.

Mirrors the soft-reset idiom of ``POST /reset``: the stop event for the old
thread is cleared, but persisted messages stay put — the old thread remains
visible in ``GET /api/conversation-threads``. The slash response carries a
freshly minted ``thread_id`` that the frontend adopts as its next
``X-Thread-ID`` header (and the SDK surfaces via ``InvokeResult.thread_id``).
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import Optional

from cuga.backend.slash_commands.types import DispatchContext, DispatchResult

logger = logging.getLogger(__name__)


@dataclass
class ClearCommand:
    name: str = "clear"
    description: str = "Start a fresh conversation in a new thread."
    argument_hint: Optional[str] = None

    async def handle(self, ctx: DispatchContext) -> DispatchResult:
        if ctx.thread_id and ctx.clear_stop_event is not None:
            try:
                ctx.clear_stop_event(ctx.thread_id)
            except Exception:
                # Don't swallow silently — surface the failure so operators
                # can diagnose a misbehaving clear-stop hook. Control flow
                # is unchanged: we still rotate to a new thread id below.
                logger.exception("clear_stop_event hook failed for thread_id=%s", ctx.thread_id)
        new_thread_id = str(uuid.uuid4())
        return DispatchResult(
            kind="builtin",
            text="Started a fresh conversation.",
            new_thread_id=new_thread_id,
        )


BUILTIN = ClearCommand()
