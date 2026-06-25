"""``/clear`` — clear stop event for the current thread, mint a new thread id, return as new_thread_id."""

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
                logger.exception("clear_stop_event hook failed for thread_id=%s", ctx.thread_id)
        new_thread_id = str(uuid.uuid4())
        return DispatchResult(
            kind="builtin",
            text="Started a fresh conversation.",
            new_thread_id=new_thread_id,
        )


BUILTIN = ClearCommand()
