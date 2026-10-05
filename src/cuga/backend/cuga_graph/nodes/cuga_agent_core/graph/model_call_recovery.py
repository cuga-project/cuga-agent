"""Recover from a model call that failed for size reasons, instead of ending the run.

Two failure shapes, both handled the way free-code's ``query.ts`` loop handles them:

- **Context overflow.** The provider rejects the request as too long for its context
  window. Proactive summarization (``apply_context_summarization``) runs on a token
  *estimate*, which undercounts on some providers, so a request it let through can
  still be rejected. :func:`reactive_compact_command` forces a compaction and sends the
  graph back to ``call_model`` — once per step; a second overflow at the same step
  re-raises the provider error.
- **Output cut off.** The model hit its output-token limit: a CodeAct block that ends
  mid-statement, a native tool call whose JSON arguments never closed, or a truncated
  answer. The reply is discarded (never persisted) and the same request is sent again
  with :data:`OUTPUT_TRUNCATED_NOTE` appended to the outbound copy only, up to
  ``cuga_lite_output_truncation_retries`` times in a row. Each retry costs a step, so
  the step limit still bounds it.
- **A code call with no code.** Hybrid mode: gpt-oss sometimes sends ``execute_python``
  with empty arguments (AppWorld baeb104: 13 in a row, each answered "needs a non-empty
  code string", each copied by the next turn). Code that landed elsewhere in the reply is
  salvaged (``helpers/execute_python.salvage_empty_code``); otherwise the reply is
  discarded like a truncated one and retried once with :data:`EMPTY_CODE_NOTE`, so the
  empty call never enters the history the model imitates.
"""

from __future__ import annotations

import ast
from typing import Any, List, Optional

from langchain_core.messages import BaseMessage
from langgraph.types import Command
from loguru import logger


OUTPUT_TRUNCATED_KEY = "_output_truncated_retries"
REACTIVE_COMPACT_KEY = "_reactive_compact_step"
EMPTY_CODE_KEY = "_empty_code_retries"

OUTPUT_TRUNCATED_NOTE = (
    "Your previous reply hit the output-token limit and was cut off, so it was discarded "
    "and nothing in it was run. Redo that step with much less output: write shorter code "
    "(print only what you need, never echo data back), split long work into smaller steps, "
    "or give a more concise answer."
)

EMPTY_CODE_NOTE = (
    "Your previous reply called execute_python, but its `code` argument arrived empty, so it was "
    "discarded and nothing ran. Call execute_python again with the complete program as the `code` "
    "string argument, or call the tool you meant natively."
)

# Keys the per-response metadata cleanup must drop, so a marker lives for exactly one
# call_model round trip (the retry sets them again after the cleanup).
ONE_SHOT_RECOVERY_KEYS = (OUTPUT_TRUNCATED_KEY, REACTIVE_COMPACT_KEY, EMPTY_CODE_KEY)


def _advanced_setting(settings: Any, name: str, default: int) -> int:
    value = getattr(getattr(settings, "advanced_features", None), name, None)
    try:
        return int(value) if value is not None else default
    except (TypeError, ValueError):
        return default


def max_truncation_retries(settings: Any) -> int:
    return max(0, _advanced_setting(settings, "cuga_lite_output_truncation_retries", 2))


def truncation_retries_used(metadata: Optional[dict]) -> int:
    try:
        return int((metadata or {}).get(OUTPUT_TRUNCATED_KEY) or 0)
    except (TypeError, ValueError):
        return 0


def pending_truncation_note(metadata: Optional[dict]) -> Optional[str]:
    """The outbound note for a call that retries a discarded, truncated reply."""
    return OUTPUT_TRUNCATED_NOTE if truncation_retries_used(metadata) > 0 else None


def empty_code_retried(metadata: Optional[dict]) -> bool:
    return bool((metadata or {}).get(EMPTY_CODE_KEY))


def pending_empty_code_note(metadata: Optional[dict]) -> Optional[str]:
    """The outbound note for a call that retries a discarded reply whose code call was empty."""
    return EMPTY_CODE_NOTE if empty_code_retried(metadata) else None


def code_parses(code: Optional[str]) -> bool:
    """True when ``code`` is complete Python (top-level ``await`` allowed)."""
    if not code or not code.strip():
        return False
    try:
        compile(code, "<model-code>", "exec", flags=ast.PyCF_ALLOW_TOP_LEVEL_AWAIT, dont_inherit=True)
    except (SyntaxError, ValueError):
        return False
    return True


def truncation_retry_command(
    adapter: Any,
    state: Any,
    *,
    messages: List[Any],
    base_meta: dict,
    new_step_count: int,
) -> Command:
    """Go back to ``call_model`` without the truncated reply, counting the retry."""
    meta = dict(base_meta)
    meta[OUTPUT_TRUNCATED_KEY] = truncation_retries_used(adapter.get_metadata(state)) + 1
    return _discard_and_retry(adapter, messages=messages, meta=meta, new_step_count=new_step_count)


def empty_code_retry_command(
    adapter: Any,
    *,
    messages: List[Any],
    base_meta: dict,
    new_step_count: int,
) -> Command:
    """Go back to ``call_model`` without the reply whose code call was empty (once)."""
    meta = dict(base_meta)
    meta[EMPTY_CODE_KEY] = 1
    return _discard_and_retry(adapter, messages=messages, meta=meta, new_step_count=new_step_count)


def _discard_and_retry(adapter: Any, *, messages: List[Any], meta: dict, new_step_count: int) -> Command:
    return Command(
        goto="call_model",
        update={
            adapter.messages_key: messages,
            "script": None,
            "final_answer": "",
            "execution_complete": False,
            "step_count": new_step_count,
            adapter.metadata_key: meta,
        },
    )


async def reactive_compact_command(
    adapter: Any,
    state: Any,
    *,
    error: BaseException,
    messages: List[BaseMessage],
    model: Any,
    base_meta: dict,
    settings: Any,
) -> Command:
    """Compact ``messages`` and retry ``call_model``; re-raise ``error`` when that cannot help.

    The marker holds the step the compaction ran at. ``call_model`` does not advance
    ``step_count`` on this path, so a second overflow at the same step means the
    compaction was not enough — the provider error is raised as before.
    """
    from cuga.backend.cuga_graph.utils.context_management_utils import force_context_compaction

    if (adapter.get_metadata(state) or {}).get(REACTIVE_COMPACT_KEY) == state.step_count:
        logger.error(
            "{}: context still overflows after a reactive compaction — giving up", adapter.sender_name
        )
        raise error
    compacted = await force_context_compaction(
        messages,
        model,
        max_message_chars=_advanced_setting(settings, "cuga_lite_reactive_compact_max_message_chars", 20000),
        tracker=adapter.get_tracker(),
    )
    if compacted is None:
        raise error
    logger.warning(
        "{}: context window overflow — compacted the history, retrying the call", adapter.sender_name
    )
    return Command(
        goto="call_model",
        update={
            adapter.messages_key: compacted,
            "script": None,
            adapter.metadata_key: {**base_meta, REACTIVE_COMPACT_KEY: state.step_count},
        },
    )
