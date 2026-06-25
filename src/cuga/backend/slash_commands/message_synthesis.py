"""Pure function: synthesize the four-message ``load_skill`` invocation pair.

When a user types ``/<skill> args`` we don't want the model to re-decide
whether to load the skill — we want it to see "the skill is already loaded,
now act on it." The shape that achieves this with the existing CUGA / OpenAI /
Anthropic tool-call protocol is a four-message sequence injected into the
conversation history before the planner runs:

    1. HumanMessage(raw_input)               — what the user typed verbatim
    2. AIMessage(tool_calls=[load_skill])    — "the assistant called load_skill"
    3. ToolMessage(wrapped_body)             — "load_skill returned this"
    4. HumanMessage(raw_args)  *if any args* — "act on these args now"

The AIMessage carries ``additional_kwargs={'invoked_via': 'slash', ...}`` so
the frontend can detect the pair and render it as a collapsed chip, and so the
persistence layer can distinguish user-initiated slash invocations from
model-initiated ``load_skill`` calls in the same thread.

This module is a pure function with no graph, registry, or persistence
dependencies, so the synthesis logic can be unit-tested in isolation.
"""

from __future__ import annotations

import uuid
from typing import List

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage


def new_tool_call_id() -> str:
    """Produce a tool-call id that's unique and traceable to slash dispatch."""
    return f"slash_load_skill_{uuid.uuid4().hex[:12]}"


def synthesize_skill_invocation(
    *,
    raw_input: str,
    raw_args: str,
    resolved_name: str,
    wrapped_body: str,
    tool_call_id: str | None = None,
) -> List[BaseMessage]:
    """Return the four-message sequence representing a slash skill invocation."""
    tid = tool_call_id or new_tool_call_id()
    messages: List[BaseMessage] = [
        HumanMessage(content=raw_input),
        AIMessage(
            content="",
            tool_calls=[
                {
                    "id": tid,
                    "name": "load_skill",
                    "args": {"name": resolved_name},
                    "type": "tool_call",
                }
            ],
            additional_kwargs={
                "invoked_via": "slash",
                "raw_input": raw_input,
                "resolved_name": resolved_name,
            },
        ),
        ToolMessage(content=wrapped_body, tool_call_id=tid),
    ]
    if raw_args:
        messages.append(HumanMessage(content=raw_args))
    return messages
