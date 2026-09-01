"""Pre-execute VERIFY: inspect generated code before the sandbox runs."""

from __future__ import annotations

from typing import Any, Optional

from loguru import logger

from cuga.backend.cuga_graph.nodes.cuga_lite.reflection.verify import verify_task
from cuga.backend.cuga_graph.nodes.cuga_lite.reflection.verify_result import (
    VerifyDecision,
    parse_verify_output,
)
from cuga.backend.cuga_graph.utils.context_management_utils import prepare_verify_context
from cuga.backend.cuga_graph.utils.token_counter import clamp_watsonx_completion_for_messages

VERIFY_BLOCKED_PREFIX = "VERIFY blocked this code block before execution."
VERIFY_REVISE_STREAK_CAP = 2


async def decide_pre_execute_verify(
    *,
    enabled: bool,
    streak: int,
    script: Optional[str],
    chat_messages: list,
    variables_snapshot: str,
    current_task: str,
    model: Any,
    config: Any,
    max_chars: int,
) -> VerifyDecision:
    """Return whether the proposed script should run.

    ``ok`` / ``unknown`` → execute. ``revise`` → skip. Fail open on errors
    and after ``VERIFY_REVISE_STREAK_CAP`` consecutive revises.
    """
    if not enabled or not (script or "").strip():
        return VerifyDecision(gate="ok")
    if streak >= VERIFY_REVISE_STREAK_CAP:
        logger.info("Pre-execute VERIFY skipped: revise streak {}", streak)
        return VerifyDecision(gate="ok")
    try:
        history, variables, proposed = prepare_verify_context(
            list(chat_messages or []),
            variables_snapshot,
            script or "",
            max_chars=max_chars,
        )
        clamp_watsonx_completion_for_messages(
            model,
            [
                {
                    "role": "user",
                    "content": "\n".join([current_task, history, variables, proposed]),
                }
            ],
        )
        result = await verify_task(llm=model).ainvoke(
            {
                "current_task": current_task or "(no task text)",
                "agent_history": history,
                "variables_snapshot": variables,
                "proposed_code": proposed,
            },
            config=config or {},
        )
        decision = parse_verify_output(getattr(result, "content", "") or "")
        logger.debug("Pre-execute VERIFY gate={} alert={!r}", decision.gate, decision.alert)
        return decision
    except Exception as e:
        logger.warning(f"Pre-execute VERIFY failed: {e}")
        return VerifyDecision(gate="unknown")


def verify_blocked_message(alert: str) -> str:
    body = (alert or "").strip() or "ungrounded or contradictory write"
    return (
        f"{VERIFY_BLOCKED_PREFIX}\n"
        f"{body}\n"
        "Rewrite the block: bind write arguments to retrieved variables or to "
        "results from reads in this same block. Do not repeat the ungrounded literals."
    )
