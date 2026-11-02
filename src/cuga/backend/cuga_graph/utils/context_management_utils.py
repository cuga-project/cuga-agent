"""
Context Management Utilities

Helper functions for managing message context and summarization across different graph nodes.
"""

import json
import math
import re
from collections.abc import Mapping
from typing import Any, List, Optional

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from loguru import logger

from cuga.backend.cuga_graph.nodes.cuga_agent_core.graph.graph_nodes import (
    EXECUTION_OUTPUT_PREFIX,
    REFLECTION_SUMMARY_SEPARATOR,
)
from cuga.backend.cuga_graph.state.agent_state import AgentState
from cuga.backend.cuga_graph.utils.token_counter import TokenCounter, resolve_model_identifier
from cuga.backend.activity_tracker.tracker import ActivityTracker, Step

# Reflection and pre-execute VERIFY size their inputs from the model's context window
# (#1001). A fixed 30,000-char cap per field used about 20% of a 128K window and could
# still overflow a 32K one, and it cut the newest messages, the ones both tasks judge.
# 3 chars per token is conservative: JSON-heavy tool output counts more tokens per char.
CHARS_PER_TOKEN = 3
DEFAULT_CONTEXT_FRACTION = 0.70  # same as context_summarization.trigger_fraction
COMPLETION_RESERVE_FRACTION = 0.25
# Each field takes at most its share; history gets the rest of the budget.
REFLECTION_OUTPUT_SHARE = 0.40
REFLECTION_SKILLS_SHARE = 0.15
VERIFY_CODE_SHARE = 0.25
VERIFY_VARIABLES_SHARE = 0.35
# The latest output takes what code and variables leave, minus this share for history
# (or all of history, if shorter).
VERIFY_HISTORY_FLOOR_SHARE = 0.20
_MARKER_ROOM = 120


def truncate_text_for_context(text: str, max_chars: int, *, label: str = "content") -> str:
    """Trim long prompt sections so downstream LLM calls stay within context."""
    trimmed = (text or "").strip()
    if max_chars <= 0:
        return ""
    if len(trimmed) <= max_chars:
        return trimmed
    marker = f"...\n\n[{label} trimmed to {max_chars} chars]"
    if len(marker) >= max_chars:
        return trimmed[:max_chars]
    return trimmed[: max_chars - len(marker)] + marker


def truncate_head_tail(text: str, max_chars: int, *, label: str = "content") -> str:
    """Keep the start and the end of *text*; errors and final results are at the end."""
    trimmed = (text or "").strip()
    if max_chars <= 0:
        return ""
    if len(trimmed) <= max_chars:
        return trimmed
    marker = f"\n\n[... {len(trimmed)} chars of {label} omitted ...]\n\n"
    if len(marker) >= max_chars:
        return trimmed[-max_chars:]
    keep = max_chars - len(marker)
    head = keep // 3
    tail = keep - head
    omitted = len(trimmed) - head - tail
    return (
        f"{trimmed[:head]}\n\n[... {omitted} chars of {label} omitted ...]\n\n"
        f"{trimmed[len(trimmed) - tail :]}"
    )


def _message_line(msg: BaseMessage) -> str:
    if isinstance(msg, HumanMessage):
        return f"User: {msg.content}"
    if isinstance(msg, AIMessage):
        return f"Assistant: {msg.content}"
    return f"{type(msg).__name__}: {getattr(msg, 'content', str(msg))}"


def messages_to_history_text(messages: List[BaseMessage]) -> str:
    """Format chat messages as a plain-text history block."""
    parts = [_message_line(msg) for msg in messages]
    return "\n".join(parts) if parts else "No previous conversation history"


def _tool_result_line(msg: ToolMessage) -> str:
    status = " (error)" if getattr(msg, "status", None) == "error" else ""
    return f"{msg.name or 'tool'}{status}: {msg.content}"


def split_latest_execution_output(
    messages: List[BaseMessage],
) -> tuple[List[BaseMessage], str]:
    """Separate the output of the last executed block from the rest of the history.

    CodeAct feeds a block's output back as a user message that starts with
    ``EXECUTION_OUTPUT_PREFIX``. Function-calling mode answers each native call with
    a ``ToolMessage``, so its output is the newest run of ``ToolMessage``s. Any other
    user message (a new request on a later thread turn, a retry correction) is not
    output and stays in history. A reflection summary appended to the CodeAct message
    is model-written, not printed, so it is dropped.
    """
    msgs = list(messages)
    for end in range(len(msgs) - 1, -1, -1):
        msg = msgs[end]
        if isinstance(msg, ToolMessage):
            start = end
            while start > 0 and isinstance(msgs[start - 1], ToolMessage):
                start -= 1
            output = "\n".join(_tool_result_line(m) for m in msgs[start : end + 1])
            return msgs[:start] + msgs[end + 1 :], output
        content = getattr(msg, "content", None)
        if (
            isinstance(msg, HumanMessage)
            and isinstance(content, str)
            and content.startswith(EXECUTION_OUTPUT_PREFIX)
        ):
            # Split at the first separator, before stripping newlines (an empty output
            # would otherwise eat the separator's own), so text the summary repeats
            # cannot pass as printed output.
            printed = content[len(EXECUTION_OUTPUT_PREFIX) :].split(REFLECTION_SUMMARY_SEPARATOR, 1)[0]
            return msgs[:end] + msgs[end + 1 :], printed.lstrip("\n")
    return msgs, ""


def history_text_keep_recent(messages: List[BaseMessage], max_chars: int) -> str:
    """Format *messages* within *max_chars*, keeping the newest whole messages.

    The first user message is kept too: ``current_task`` can be a sub-task, so the
    original request would otherwise be lost. Dropped messages are replaced by a
    marker with their count. Only when the newest message alone does not fit is it
    cut, head and tail.
    """
    full = messages_to_history_text(messages)
    if max_chars <= 0:
        return ""
    if not messages:
        return full[:max_chars]
    if len(full) <= max_chars:
        return full
    if max_chars < 2 * _MARKER_ROOM:
        return truncate_head_tail(full, max_chars, label="Agent history")
    lines = [_message_line(msg) for msg in messages]
    first_idx = next((i for i, m in enumerate(messages) if isinstance(m, HumanMessage)), None)
    if first_idx == len(lines) - 1:
        return truncate_head_tail(full, max_chars, label="Agent history")

    first_line = ""
    if first_idx is not None:
        first_line = truncate_head_tail(lines[first_idx], max(1, max_chars // 5), label="first user message")
    remaining = max_chars - len(first_line) - _MARKER_ROOM
    stop = first_idx if first_idx is not None else -1
    kept: list[str] = []
    oldest_kept = len(lines)
    for i in range(len(lines) - 1, stop, -1):
        cost = len(lines[i]) + 1
        if cost <= remaining:
            kept.append(lines[i])
            remaining -= cost
            oldest_kept = i
            continue
        if not kept:
            kept.append(truncate_head_tail(lines[i], max(remaining, 1), label="latest message"))
            oldest_kept = i
        break
    kept.reverse()

    # Messages before the first user message (rare) are dropped as well.
    omitted = oldest_kept - (stop + 1) + (first_idx or 0)
    parts = [first_line] if first_line else []
    if omitted > 0:
        parts.append(f"[{omitted} earlier messages omitted to fit the context budget]")
    parts.extend(kept)
    return truncate_head_tail("\n".join(parts), max_chars, label="Agent history")


_VARIABLE_SECTION_RE = re.compile(r"(?m)^(?=## )")


def truncate_variables_summary(summary: str, max_chars: int) -> str:
    """Keep whole variables from the start of a ``get_variables_summary`` text.

    Callers order the summary most relevant first, so the variables dropped are the
    least relevant ones.
    """
    text = (summary or "").strip()
    if max_chars <= 0:
        return ""
    if len(text) <= max_chars:
        return text
    header, *sections = _VARIABLE_SECTION_RE.split(text)
    if not sections:
        return truncate_head_tail(text, max_chars, label="Variables")
    out = header
    room = max_chars - len(header) - _MARKER_ROOM
    kept = 0
    for section in sections:
        if len(section) <= room:
            out += section
            room -= len(section)
            kept += 1
            continue
        if kept == 0:
            out += truncate_head_tail(section, max(room, 1), label="variable") + "\n"
            kept = 1
        break
    omitted = len(sections) - kept
    if omitted:
        out = f"{out.rstrip()}\n\n[{omitted} more variables omitted to fit the context budget]"
    return out[:max_chars]


def _model_completion_tokens(model: Any) -> Optional[int]:
    """Configured completion budget of *model*, if it exposes one as an int."""
    llm = model
    keys = ("max_completion_tokens", "max_new_tokens", "max_tokens")
    candidates = []
    bound_kwargs = {}
    for _ in range(5):  # RunnableBinding wraps the chat model in .bound
        kwargs = getattr(llm, "kwargs", None)
        if isinstance(kwargs, Mapping):
            # Outer bindings override inner ones for the same keyword.
            bound_kwargs = {**kwargs, **bound_kwargs}
        inner = getattr(llm, "bound", None)
        if inner is None or inner is llm:
            break
        llm = inner
    # Providers normalize legacy aliases differently. If several are bound, reserve
    # the largest allowance rather than assuming which alias the provider sends.
    bound_limits = [bound_kwargs.get(k) for k in keys]
    bound_limits = [v for v in bound_limits if isinstance(v, int) and not isinstance(v, bool) and v > 0]
    if bound_limits:
        return max(bound_limits)
    candidates.extend(getattr(llm, k, None) for k in keys)
    model_kwargs = getattr(llm, "model_kwargs", None)
    if isinstance(model_kwargs, Mapping):
        candidates.extend(model_kwargs.get(k) for k in keys)
    params = getattr(llm, "params", None)
    if isinstance(params, Mapping):
        candidates += [params.get(k) for k in ("max_completion_tokens", "max_new_tokens", "max_tokens")]
    for value in candidates:
        if isinstance(value, int) and not isinstance(value, bool) and value > 0:
            return value
    return None


def model_input_budget_chars(model: Any, fixed_prompt_chars: int, *, context_fraction: float) -> int:
    """Characters left for the trimmed prompt fields of one call to *model*.

    ``context_size × context_fraction`` minus a completion reserve (the model's
    full configured completion budget, or a quarter when unknown), converted at
    ``CHARS_PER_TOKEN``, minus the prompt text that is never trimmed. Unknown models
    use ``DEFAULT_CONTEXT_SIZE``, as context summarization does.
    """
    # No "gpt-4" fallback name here: its 8,192-token entry would shrink the budget of
    # any model whose name cannot be read.
    model_name = resolve_model_identifier(model, fallback_name="")
    context_tokens = TokenCounter(model=model, model_name=model_name).get_model_context_size(model)
    reserve_cap = int(context_tokens * COMPLETION_RESERVE_FRACTION)
    completion = _model_completion_tokens(model)
    reserve = completion if completion else reserve_cap
    available_tokens = int(context_tokens * context_fraction) - reserve
    budget = available_tokens * CHARS_PER_TOKEN - max(0, fixed_prompt_chars)
    if budget <= 0:
        raise ValueError(
            f"No context space remains for reflection/VERIFY inputs "
            f"(window={context_tokens}, completion={reserve}, fixed_prompt_chars={fixed_prompt_chars})"
        )
    return budget


def context_fraction_setting(name: str) -> float:
    """Read an ``advanced_features`` context fraction; fall back to the default if invalid."""
    from cuga.config import settings

    value = getattr(settings.advanced_features, name, None)
    if value is None or value == "":
        return DEFAULT_CONTEXT_FRACTION
    try:
        fraction = float(value) if not isinstance(value, bool) else math.nan
    except (TypeError, ValueError):
        fraction = math.nan
    if not math.isfinite(fraction) or not 0 < fraction <= 1:
        logger.warning("Invalid {} {!r}; using {}", name, value, DEFAULT_CONTEXT_FRACTION)
        return DEFAULT_CONTEXT_FRACTION
    return fraction


def rendered_prompt_chars(prompt: Any, inputs: dict) -> int:
    """Length of *prompt* rendered with *inputs*: the part of a call that is never trimmed."""
    try:
        return sum(len(str(m.content)) for m in prompt.format_messages(**inputs))
    except Exception as e:
        logger.warning("Could not render prompt to size it: {}", e)
        return sum(len(v) for v in inputs.values() if isinstance(v, str))


def _log_input_budget(kind: str, budget_chars: int, original: dict, kept: dict) -> None:
    sizes = ", ".join(f"{k} {original[k]}->{kept[k]}" for k in original)
    if any(kept[k] < original[k] for k in original):
        logger.info("{} input trimmed to a {}-char budget: {}", kind, budget_chars, sizes)
    else:
        logger.debug("{} input within its {}-char budget: {}", kind, budget_chars, sizes)


def prepare_verify_context(
    chat_messages: List[BaseMessage],
    variables_snapshot: str,
    proposed_code: str,
    *,
    max_chars: int,
) -> tuple[str, str, str, str]:
    """Fit history, latest output, variables, and code into *max_chars*. Never summarize.

    The code takes at most ``VERIFY_CODE_SHARE`` of the budget and the variables at most
    ``VERIFY_VARIABLES_SHARE``. The latest output takes the rest, except
    ``VERIFY_HISTORY_FLOOR_SHARE`` kept for history, so a short history leaves it nearly
    the whole remainder. History gets what the output leaves and keeps its newest
    messages. The latest output is ``""`` when no block has run.
    """
    earlier, latest_output = split_latest_execution_output(list(chat_messages))
    raw_code = proposed_code or ""
    raw_variables = variables_snapshot or "(no variables)"
    code = truncate_head_tail(raw_code, int(max_chars * VERIFY_CODE_SHARE), label="Proposed code")
    variables = truncate_variables_summary(raw_variables, int(max_chars * VERIFY_VARIABLES_SHARE))
    remainder = max(0, max_chars - len(code) - len(variables))
    raw_history = messages_to_history_text(earlier)
    history_floor = min(len(raw_history), int(max_chars * VERIFY_HISTORY_FLOOR_SHARE))
    latest = truncate_head_tail(
        latest_output, max(0, remainder - history_floor), label="Most recent execution output"
    )
    history = history_text_keep_recent(earlier, max(0, remainder - len(latest)))
    _log_input_budget(
        "Pre-execute VERIFY",
        max_chars,
        {
            "history": len(raw_history),
            "latest_output": len(latest_output.strip()),
            "variables": len(raw_variables.strip()),
            "code": len(raw_code.strip()),
        },
        {
            "history": len(history),
            "latest_output": len(latest),
            "variables": len(variables),
            "code": len(code),
        },
    )
    return history, latest, variables, code


async def prepare_reflection_context(
    chat_messages: List[BaseMessage],
    coder_agent_output: str,
    model: Any,
    *,
    budget_chars: int,
    skills_prompt_section: str = "",
    tracker: Optional[ActivityTracker] = None,
) -> tuple[str, str, str]:
    """Summarize chat history, then fit history, execution output, and skills into *budget_chars*.

    The output takes at most ``REFLECTION_OUTPUT_SHARE`` of the budget and the skills
    section at most ``REFLECTION_SKILLS_SHARE``; history gets the rest and keeps its
    newest messages.
    """
    summarized = await apply_context_summarization(
        list(chat_messages),
        model,
        tracker=tracker,
        message_list_name="chat_messages",
    )
    output = truncate_head_tail(
        coder_agent_output,
        int(budget_chars * REFLECTION_OUTPUT_SHARE),
        label="Execution output",
    )
    skills = truncate_text_for_context(
        skills_prompt_section or "",
        int(budget_chars * REFLECTION_SKILLS_SHARE),
        label="Skills prompt section",
    )
    history = history_text_keep_recent(summarized, max(0, budget_chars - len(output) - len(skills)))
    _log_input_budget(
        "Reflection",
        budget_chars,
        {
            "history": len(messages_to_history_text(summarized)),
            "output": len((coder_agent_output or "").strip()),
            "skills": len((skills_prompt_section or "").strip()),
        },
        {"history": len(history), "output": len(output), "skills": len(skills)},
    )
    return history, output, skills


async def apply_context_summarization(
    messages: List[BaseMessage],
    model: Any,
    *,
    system_prompt: Optional[str] = None,
    tools: Optional[List[Any]] = None,
    tracker: Optional[ActivityTracker] = None,
    variables_storage: Optional[dict] = None,
    variable_counter_state: Optional[int] = None,
    variable_creation_order: Optional[List[str]] = None,
    message_list_name: str = "chat_messages",
) -> List[BaseMessage]:
    """Summarize messages in-place via a temporary AgentState.

    This function creates a temporary AgentState, applies context summarization,
    and returns the (possibly summarized) message list. Falls back to the
    original list on any error.

    Args:
        messages: List of messages to potentially summarize
        model: The language model to use for token counting
        system_prompt: Optional system prompt for context calculation
        tools: Optional list of tools for context calculation
        tracker: Optional activity tracker for recording metrics
        variables_storage: Optional variables storage to pass to temp state
        variable_counter_state: Optional variable counter state (int)
        variable_creation_order: Optional variable creation order
        message_list_name: Name of the message list to use in AgentState
                          ("chat_messages", "chat_agent_messages", or "supervisor_chat_messages")

    Returns:
        List of messages (possibly summarized), or original messages on error
    """
    if not messages:
        return messages

    model_name = resolve_model_identifier(model, fallback_name="gpt-4")

    # Do not use model.with_config(callbacks=...) here: it wraps the model in
    # RunnableBinding and breaks ContextSummarizer._setup_model_profile (profile field).
    # Langfuse callbacks for summarization LLM calls are propagated via the
    # contextvar set in call_model (sync_langfuse_callbacks_from_config) before
    # this runs; middleware wiring is tracked separately.

    try:
        # Create temporary AgentState with a copy of messages
        # Only pass optional parameters if they are not None
        state_kwargs = {
            "input": "",
            "url": "",
            message_list_name: list(messages),
        }

        if variables_storage is not None:
            state_kwargs["variables_storage"] = variables_storage
        if variable_counter_state is not None:
            state_kwargs["variable_counter_state"] = variable_counter_state
        if variable_creation_order is not None:
            state_kwargs["variable_creation_order"] = variable_creation_order

        temp_state = AgentState(**state_kwargs)

        # Apply context management
        await temp_state.manage_message_context(
            model=model,
            model_name=model_name,
            tools=tools,
            system_prompt=system_prompt,
        )

        # Get the (possibly summarized) messages from the correct list
        summarized = getattr(temp_state, message_list_name, None) or messages

        # Log and track metrics
        _log_and_track_metrics(messages, summarized, temp_state, tracker)

        return summarized

    except Exception as e:
        logger.exception(f"Context summarization failed, using original messages: {e}")
        return messages


def _log_and_track_metrics(
    original: List[BaseMessage],
    summarized: List[BaseMessage],
    temp_state: AgentState,
    tracker: Optional[ActivityTracker],
) -> None:
    """Log summarization metrics and record them in the tracker.

    Args:
        original: Original message list
        summarized: Summarized message list
        temp_state: Temporary AgentState with metrics
        tracker: Optional activity tracker
    """
    # Log basic message count change
    if len(summarized) < len(original):
        logger.info(f"Context summarization: {len(original)} → {len(summarized)} messages")

    # Get detailed metrics if available
    metrics = (getattr(temp_state, 'last_summarization_metrics', None) or {}).get('chat_messages')

    if not metrics:
        return

    # Failure shape: hard truncation happened — emit a tracker event so eval analysis
    # can find affected tasks (issue #563), then stop (no success metrics to log).
    # Detect by key presence, not truthiness: str(exception) can be empty.
    if "error" in metrics or metrics.get("hard_truncation"):
        if tracker:
            try:
                tracker.collect_step(Step(name="ContextSummarizationFailure", data=json.dumps(metrics)))
            except Exception as e:
                logger.debug(f"Failed to record summarization failure in tracker: {e}")
        return

    # Log detailed metrics
    logger.info(
        f"📊 Summarization: messages {metrics['before']['message_count']} → "
        f"{metrics['after']['message_count']}, "
        f"saved {metrics['tokens_saved']} tokens "
        f"({(1 - metrics['compression_ratio']):.1%} reduction)"
    )

    # Record in tracker if available
    if tracker:
        try:
            tracker.collect_step(
                Step(
                    name="ContextSummarization",
                    data=json.dumps(
                        {
                            "before": metrics['before'],
                            "after": metrics['after'],
                            "tokens_saved": metrics['tokens_saved'],
                            "compression_ratio": metrics['compression_ratio'],
                            "messages_summarized": metrics.get('messages_summarized', 0),
                            "messages_kept": metrics.get('messages_kept', 0),
                        }
                    ),
                )
            )
        except Exception as e:
            logger.debug(f"Failed to record summarization in tracker: {e}")
