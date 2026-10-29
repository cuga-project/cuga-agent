"""Pre-execute VERIFY: inspect generated code before the sandbox runs."""

from __future__ import annotations

import ast
import asyncio

import json
from typing import Any, Callable, Optional

from langchain_core.messages import AIMessage, ToolMessage
from loguru import logger

from cuga.backend.activity_tracker.tracker import Step
from cuga.backend.cuga_graph.nodes.cuga_lite.reflection.verify import verify_prompt, verify_task
from cuga.backend.cuga_graph.nodes.cuga_lite.reflection.verify_result import (
    VERIFY_BLOCKED_PREFIX,
    VerifyDecision,
    parse_verify_output,
)
from cuga.backend.cuga_graph.nodes.cuga_lite.reflection.write_args import (
    describe_write_arguments,
    has_write_call,
)
from cuga.backend.cuga_graph.utils.context_management_utils import (
    context_fraction_setting,
    model_input_budget_chars,
    prepare_verify_context,
    rendered_prompt_chars,
)
from cuga.backend.cuga_graph.utils.token_counter import clamp_watsonx_completion_for_messages

VERIFY_REVISE_STREAK_CAP = 2
# The streak above only counts *consecutive* revises and is reset by any successful
# execution, so revise/ok/revise/ok never trips it. Each revise still costs a step
# against cuga_lite_max_steps plus two LLM calls, so a task could spend its whole
# budget in the gate and never reach its own work. This is the bound that holds.
VERIFY_REVISE_TOTAL_CAP = 5
# The gate runs *before* the block, so unlike post-execution reflection a hung
# provider stalls the work rather than just delaying a summary. Every other
# failure here degrades to "run the block"; without this, a hang did not.
# 60s, not 15: on Gemini 3.8 Flash at default reasoning, 15s fired on ~1% of
# verify calls (12 of 1,266) -- and they concentrate on the largest write
# blocks, whose histories are longest, so the gate failed open on exactly the
# writes it exists to judge. A hang is the case this guards; a slow verdict is
# not, and the post-execution reflection call has no timeout at all.
VERIFY_LLM_TIMEOUT_SECONDS = 60.0


def verify_timeout_seconds() -> float:
    """Time limit for the VERIFY model call.

    ``advanced_features.pre_execute_verify_timeout`` (env
    ``DYNACONF_ADVANCED_FEATURES__PRE_EXECUTE_VERIFY_TIMEOUT``) overrides the
    default, e.g. for evaluations whose reasoning model is slow on long write blocks.
    """
    import math

    from cuga.config import settings

    value = getattr(settings.advanced_features, "pre_execute_verify_timeout", None)
    if value is None or value == "":
        return VERIFY_LLM_TIMEOUT_SECONDS
    try:
        seconds = float(value) if not isinstance(value, bool) else math.nan
    except (TypeError, ValueError):
        seconds = math.nan
    if not math.isfinite(seconds) or seconds <= 0:
        # A bad value must not time VERIFY out at once or raise before the gate;
        # either would let write blocks run unverified.
        logger.warning(
            "Invalid pre_execute_verify_timeout {!r}; using {}s", value, VERIFY_LLM_TIMEOUT_SECONDS
        )
        return VERIFY_LLM_TIMEOUT_SECONDS
    return seconds


def _is_blocked_reply(message: Any) -> bool:
    content = getattr(message, "content", None)
    return isinstance(content, str) and content.startswith(VERIFY_BLOCKED_PREFIX)


def _without_blocked_replies(messages: list) -> list:
    """Drop the replies to blocks VERIFY stopped; such a block never ran.

    A stopped function-calling batch also loses its pre-decided replies (a deferred
    or unknown call), which do not carry the blocked prefix: none of them is output.
    """
    blocked_ids = {m.tool_call_id for m in messages if isinstance(m, ToolMessage) and _is_blocked_reply(m)}
    batch_ids: set = set()
    if blocked_ids:
        for m in messages:
            ids = (
                {c.get("id") for c in getattr(m, "tool_calls", None) or []}
                if isinstance(m, AIMessage)
                else set()
            )
            if ids & blocked_ids:
                batch_ids |= ids
    return [
        m
        for m in messages
        if not _is_blocked_reply(m) and not (isinstance(m, ToolMessage) and m.tool_call_id in batch_ids)
    ]


def log_pre_execute_verify(tracker: Any, decision: VerifyDecision) -> bool:
    if tracker is None:
        return True
    try:
        tracker.collect_step(
            step=Step(
                name="PreExecuteVerify",
                data=json.dumps(
                    {
                        "gate": decision.gate,
                        "alert": decision.alert,
                        "output": decision.raw,
                    }
                ),
            )
        )
        return True
    except Exception as e:
        logger.warning("Failed to record pre-execute VERIFY decision: {}", e)
        return False


async def decide_pre_execute_verify(
    *,
    enabled: bool,
    streak: int,
    total_revises: int = 0,
    script: Optional[str],
    chat_messages: list,
    variables_snapshot: str,
    current_task: str,
    model: Any = None,
    model_factory: Optional[Callable[[], Any]] = None,
    config: Any,
    max_chars: Optional[int] = None,
) -> VerifyDecision:
    """Return whether the proposed script should run.

    ``ok`` / ``unknown`` → execute. ``revise`` → skip. Fail open on errors,
    after ``VERIFY_REVISE_STREAK_CAP`` consecutive revises, and after
    ``VERIFY_REVISE_TOTAL_CAP`` revises in the run however they are spaced.

    ``max_chars`` is the character budget shared by history, latest output,
    variables, and the proposed code. ``None`` sizes it from the model's context window
    (``advanced_features.pre_execute_verify_context_fraction``).
    """
    if not enabled or not (script or "").strip():
        return VerifyDecision(gate="ok")
    if streak >= VERIFY_REVISE_STREAK_CAP:
        logger.info("Pre-execute VERIFY skipped: revise streak {}", streak)
        return VerifyDecision(gate="ok")
    if total_revises >= VERIFY_REVISE_TOTAL_CAP:
        logger.info("Pre-execute VERIFY disabled for the rest of the run: {} revises", total_revises)
        return VerifyDecision(gate="ok")
    timeout = verify_timeout_seconds()
    try:
        if not has_write_call(script):
            logger.debug("Pre-execute VERIFY skipped: read-only block")
            return VerifyDecision(gate="ok")
        active_model = model
        if active_model is None:
            if model_factory is None:
                raise ValueError("No model or model factory configured for pre-execute VERIFY")
            active_model = model_factory()
        task_text = current_task or "(no task text)"
        write_arguments = describe_write_arguments(script)
        if max_chars is None:
            fixed_prompt_chars = rendered_prompt_chars(
                verify_prompt(),
                {
                    "current_task": task_text,
                    "agent_history": "",
                    # Non-empty, so the longer with-output header is the one counted.
                    "latest_output": " ",
                    "variables_snapshot": "",
                    "proposed_code": "",
                    "write_arguments": write_arguments,
                },
            )
            max_chars = await asyncio.to_thread(
                model_input_budget_chars,
                active_model,
                fixed_prompt_chars,
                context_fraction=context_fraction_setting("pre_execute_verify_context_fraction"),
            )
        history, latest_output, variables, proposed = prepare_verify_context(
            _without_blocked_replies(list(chat_messages or [])),
            variables_snapshot,
            script or "",
            max_chars=max_chars,
        )
        clamp_watsonx_completion_for_messages(
            active_model,
            [
                {
                    "role": "user",
                    "content": "\n".join(
                        [task_text, history, latest_output, variables, proposed, write_arguments]
                    ),
                }
            ],
        )
        result = await asyncio.wait_for(
            verify_task(llm=active_model).ainvoke(
                {
                    "current_task": task_text,
                    "agent_history": history,
                    "latest_output": latest_output,
                    "variables_snapshot": variables,
                    "proposed_code": proposed,
                    "write_arguments": write_arguments,
                },
                config=config or {},
            ),
            timeout=timeout,
        )
        decision = parse_verify_output(getattr(result, "content", "") or "")
        logger.debug("Pre-execute VERIFY gate={} alert={!r}", decision.gate, decision.alert)
        return decision
    except asyncio.TimeoutError:
        # str(TimeoutError()) is empty; an unnamed failure is undiagnosable.
        logger.warning(
            "Pre-execute VERIFY timed out after {}s -- running the block unverified",
            timeout,
        )
        return VerifyDecision(gate="unknown", alert="verify timed out")
    except Exception as e:
        logger.warning(f"Pre-execute VERIFY failed: {e}")
        return VerifyDecision(gate="unknown", alert=str(e))


def _names_read(script: Optional[str]) -> set:
    try:
        tree = ast.parse(script or "")
    except SyntaxError:
        return set()
    return {n.id for n in ast.walk(tree) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)}


def ordered_variables_summary(variables_manager: Any, script: Optional[str]) -> str:
    """Variables for VERIFY, most relevant first: those the block reads, then the rest newest first.

    The VERIFY budget keeps whole variables from the start of this text, so a long run
    drops its oldest unrelated variables, not the ones the write is grounded in.
    """
    get_summary = getattr(variables_manager, "get_variables_summary", None)
    if not callable(get_summary):
        return ""
    try:
        # Creation order, where a rebound variable moves to the end.
        count = len(variables_manager.get_variable_names() or [])
        names = list(variables_manager.get_last_n_variable_names(count) or []) if count else []
    except Exception:
        names = []
    if not names:
        return get_summary() or ""
    read = _names_read(script)
    newest_first = names[::-1]
    ordered = [n for n in newest_first if n in read] + [n for n in newest_first if n not in read]
    return get_summary(variable_names=ordered, preserve_order=True) or ""


def verify_blocked_message(alert: str) -> str:
    # Scoped to the flagged value: a blanket "ground every argument" made the
    # model strip needed flags (clear_cart_first, repeat_days) on rewrite.
    body = (alert or "").strip() or "ungrounded or contradictory write"
    return (
        f"{VERIFY_BLOCKED_PREFIX}\n"
        f"{body}\n"
        "Rewrite the block so the flagged value comes from retrieved data or the task. "
        "Keep every other argument the task needs, including flags and defaults."
    )
