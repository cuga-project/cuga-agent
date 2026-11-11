"""Fail-closed checkpoint check before a ``/stream`` turn (#774, design §6.2 step 4)."""

from typing import Any

from loguru import logger


class ThreadUnavailable(Exception):
    """The conversation's saved state cannot be continued. ``reason`` is sent to the client."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


async def check_turn_state(graph: Any, checkpoint_key: str, status: str, checkpointer_kind: str) -> Any:
    """Return the checkpoint snapshot, or raise ``ThreadUnavailable`` if the turn cannot continue.

    A present checkpoint always wins. Without one, only a ``new`` conversation starts empty;
    an ``active`` one fails closed, except with the in-memory checkpointer, where a restart
    loses state by design. The caller runs the turn on the returned snapshot instead of reading
    it again, so a second read cannot fail and silently start the conversation empty.
    """
    try:
        snapshot = await graph.aget_state({"configurable": {"thread_id": checkpoint_key}})
    except Exception as exc:
        logger.exception("checkpoint load failed")
        raise ThreadUnavailable("unreadable") from exc

    if snapshot and (snapshot.values or snapshot.next):
        if snapshot.values:
            from cuga.backend.cuga_graph.state.agent_state import AgentState

            try:
                AgentState(**snapshot.values)
            except Exception as exc:
                logger.exception("checkpoint does not load as AgentState")
                raise ThreadUnavailable("incompatible") from exc
        return snapshot
    if status == "new":
        return snapshot
    if checkpointer_kind == "memory":
        logger.warning("no checkpoint for an active conversation (in-memory checkpointer); starting fresh")
        return snapshot
    raise ThreadUnavailable("missing")
