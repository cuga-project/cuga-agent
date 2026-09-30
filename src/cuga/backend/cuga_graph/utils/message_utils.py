"""
Shared utilities for message type conversion.

This module provides common message handling utilities used across
the codebase to avoid duplication.
"""

from typing import Any, List

from langchain_core.messages import (
    BaseMessage,
    AIMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
    ChatMessage,
)
from loguru import logger


def convert_to_proper_message_type(message: BaseMessage) -> BaseMessage:
    """
    Convert a generic BaseMessage to a proper message subclass.

    This handles cases where messages are instantiated as BaseMessage
    instead of specific types like AIMessage, HumanMessage, etc.

    Args:
        message: A BaseMessage instance (possibly generic)

    Returns:
        A properly typed message (AIMessage, HumanMessage, etc.)
    """
    # Validate that message has content attribute and it's not None
    if not hasattr(message, 'content') or message.content is None:
        logger.warning(f"Message missing content attribute or content is None: {message}")
        return HumanMessage(content='')

    # If already a specific subclass (not just BaseMessage), return as-is
    if type(message) is not BaseMessage:
        return message

    # Try to infer the correct type from message attributes
    # Check for 'type' attribute first (common in serialized messages)
    msg_type = getattr(message, 'type', None)

    if msg_type == 'ai' or msg_type == 'AIMessage':
        return AIMessage(
            content=message.content,
            additional_kwargs=message.additional_kwargs,
            response_metadata=getattr(message, 'response_metadata', {}),
            id=message.id,
            tool_calls=getattr(message, 'tool_calls', []),
        )
    elif msg_type == 'human' or msg_type == 'HumanMessage':
        return HumanMessage(
            content=message.content, additional_kwargs=message.additional_kwargs, id=message.id
        )
    elif msg_type == 'system' or msg_type == 'SystemMessage':
        return SystemMessage(
            content=message.content, additional_kwargs=message.additional_kwargs, id=message.id
        )
    elif msg_type == 'tool' or msg_type == 'ToolMessage':
        # ToolMessage requires tool_call_id
        # First try to get tool_call_id from direct attribute, then fall back to additional_kwargs
        tool_call_id = getattr(message, 'tool_call_id', None) or message.additional_kwargs.get(
            'tool_call_id', 'unknown'
        )
        return ToolMessage(
            content=message.content,
            tool_call_id=tool_call_id,
            additional_kwargs=message.additional_kwargs,
            id=message.id,
        )
    elif msg_type == 'chat' or msg_type == 'ChatMessage':
        role = message.additional_kwargs.get('role', 'user')
        return ChatMessage(
            content=message.content, role=role, additional_kwargs=message.additional_kwargs, id=message.id
        )

    # Default to HumanMessage if we can't determine the type
    logger.debug(f"Could not determine message type for {message}, defaulting to HumanMessage")
    return HumanMessage(content=message.content, additional_kwargs=message.additional_kwargs, id=message.id)


# Made with Bob


def _tool_call_ids(message: Any) -> set:
    calls = getattr(message, "tool_calls", None) or []
    return {c.get("id") for c in calls if isinstance(c, dict) and c.get("id")}


def pair_safe_tail(messages: List[BaseMessage], keep: int) -> List[BaseMessage]:
    """The last ``keep`` messages, never starting on an orphaned tool reply.

    A plain ``messages[-keep:]`` can cut between an ``AIMessage`` carrying
    ``tool_calls`` and its ``ToolMessage`` replies, leaving replies to a call the
    provider never sees — which strict function-calling providers reject. Same
    rule as LangChain's ``SummarizationMiddleware._find_safe_cutoff_point``: if
    the cut lands on tool replies, move it back to include the assistant turn that
    issued them; if that turn cannot be found, move it forward past the orphans.
    ``keep <= 0`` keeps nothing (``messages[-0:]`` would keep everything).
    """
    if keep <= 0 or not messages:
        return []
    if len(messages) <= keep:
        return list(messages)
    cut = len(messages) - keep

    def is_tool(m: Any) -> bool:
        return getattr(m, "type", None) == "tool"

    if not is_tool(messages[cut]):
        return list(messages[cut:])
    reply_ids = set()
    idx = cut
    while idx < len(messages) and is_tool(messages[idx]):
        reply_ids.add(getattr(messages[idx], "tool_call_id", None))
        idx += 1
    for back in range(cut - 1, -1, -1):
        if getattr(messages[back], "type", None) == "ai" and _tool_call_ids(messages[back]) & reply_ids:
            return list(messages[back:])
    return list(messages[idx:])
