"""Response and metadata helpers for the Lite graph adapter."""

from __future__ import annotations

import json
from typing import Any, Collection, Dict, Optional

from langchain_core.messages import HumanMessage

from cuga.backend.cuga_graph.nodes.cuga_agent_core.graph.graph_nodes import (
    EMPTY_RESPONSE_CORRECTION,
    EMPTY_RESPONSE_CORRECTION_KEY,
    EXECUTION_OUTPUT_PREFIX,
)
from cuga.backend.cuga_graph.nodes.cuga_lite.bind_tools import resolve_tool_name
from cuga.backend.cuga_graph.nodes.cuga_lite.reflection.verify_result import VERIFY_BLOCKED_PREFIX
from cuga.backend.llm.errors import failed_gen_to_code, parse_tool_use_failed_generation


def clean_empty_response_retry_meta(meta: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    cleaned = {**(meta or {})}
    cleaned.pop(EMPTY_RESPONSE_CORRECTION_KEY, None)
    return cleaned


def reflection_current_task(state: Any) -> str:
    """Prefer ``sub_task``; else last user message that is not sandbox or VERIFY feedback."""
    if (state.sub_task or "").strip():
        return state.sub_task.strip()
    if state.chat_messages:
        feedback_prefixes = (
            EXECUTION_OUTPUT_PREFIX,
            VERIFY_BLOCKED_PREFIX,
            EMPTY_RESPONSE_CORRECTION,
        )
        for msg in reversed(state.chat_messages):
            if isinstance(msg, HumanMessage):
                content = (msg.content or "").strip()
                if content and not content.startswith(feedback_prefixes):
                    return content
    return ""


def tool_call_kwarg_literal(value: Any) -> str:
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    return repr(value)


def extract_code_from_response_tool_calls(response: Any, tool_names: Collection[str] = ()) -> Optional[str]:
    """Recover fenced Python from AIMessage.tool_calls when content is empty.

    A provider-safe alias the model was bound with is mapped back to its real
    name in ``tool_names``, so the code (and the approval check that reads it)
    names the real tool.
    """
    tool_calls = getattr(response, "tool_calls", None) or (
        getattr(response, "additional_kwargs", None) or {}
    ).get("tool_calls")
    if not tool_calls:
        return None

    tool_call = tool_calls[0]
    if not isinstance(tool_call, dict):
        return None

    name = tool_call.get("name") or (tool_call.get("function") or {}).get("name")
    args = tool_call.get("args") or (tool_call.get("function") or {}).get("arguments") or {}
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except json.JSONDecodeError:
            args = {}

    if not name:
        return None
    if isinstance(name, str):
        name = resolve_tool_name(name, tool_names)

    args_str = ", ".join(
        f"{k}={tool_call_kwarg_literal(v)}" for k, v in (args if isinstance(args, dict) else {}).items()
    )
    return f"```python\nresult = await {name}({args_str})\nprint(result)\n```"


def extract_code_from_failed_tool_call(err: Any, tool_names: Collection[str] = ()) -> Optional[str]:
    """``llm.errors.extract_code_from_tool_use_failed``, with an alias mapped back as above."""
    failed_gen = parse_tool_use_failed_generation(err)
    if not isinstance(failed_gen, dict):
        return None
    if isinstance(failed_gen.get("name"), str):
        failed_gen = {**failed_gen, "name": resolve_tool_name(failed_gen["name"], tool_names)}
    return failed_gen_to_code(failed_gen)
