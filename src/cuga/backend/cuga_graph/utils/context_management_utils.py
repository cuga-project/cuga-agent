"""
Context Management Utilities

Helper functions for managing message context and summarization across different graph nodes.
"""

import json
from typing import Any, List, Optional

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from loguru import logger

from cuga.backend.cuga_graph.state.agent_state import AgentState
from cuga.backend.cuga_graph.utils.token_counter import resolve_model_identifier
from cuga.backend.activity_tracker.tracker import ActivityTracker, Step


def truncate_text_for_context(text: str, max_chars: int, *, label: str = "content") -> str:
    """Trim long prompt sections so downstream LLM calls stay within context."""
    trimmed = (text or "").strip()
    if max_chars <= 0 or len(trimmed) <= max_chars:
        return trimmed
    return f"{trimmed[:max_chars]}...\n\n[{label} trimmed to {max_chars} chars]"


def messages_to_history_text(messages: List[BaseMessage]) -> str:
    """Format chat messages as a plain-text history block."""
    parts: list[str] = []
    for msg in messages:
        if isinstance(msg, HumanMessage):
            parts.append(f"User: {msg.content}")
        elif isinstance(msg, AIMessage):
            parts.append(f"Assistant: {msg.content}")
        else:
            parts.append(f"{type(msg).__name__}: {getattr(msg, 'content', str(msg))}")
    return "\n".join(parts) if parts else "No previous conversation history"


async def prepare_reflection_context(
    chat_messages: List[BaseMessage],
    coder_agent_output: str,
    model: Any,
    *,
    max_output_chars: int,
    max_history_chars: int,
    tracker: Optional[ActivityTracker] = None,
) -> tuple[str, str]:
    """Summarize chat history and trim execution output for reflection prompts."""
    summarized = await apply_context_summarization(
        list(chat_messages),
        model,
        tracker=tracker,
        message_list_name="chat_messages",
    )
    history = truncate_text_for_context(
        messages_to_history_text(summarized),
        max_history_chars,
        label="Agent history",
    )
    output = truncate_text_for_context(
        coder_agent_output,
        max_output_chars,
        label="Execution output",
    )
    return history, output


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
