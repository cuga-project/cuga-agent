"""Response and metadata helpers for the Lite graph adapter."""

from __future__ import annotations

import json
from typing import Any, Dict, Optional

from langchain_core.messages import HumanMessage


def clean_empty_response_retry_meta(meta: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    cleaned = {**(meta or {})}
    cleaned.pop("_empty_response_correction", None)
    return cleaned


def reflection_current_task(state: Any) -> str:
    """Prefer ``sub_task``; else last user message that is not sandbox feedback."""
    if (state.sub_task or "").strip():
        return state.sub_task.strip()
    if state.chat_messages:
        execution_prefix = "Execution output:"
        for msg in reversed(state.chat_messages):
            if isinstance(msg, HumanMessage):
                content = (msg.content or "").strip()
                if content and not content.startswith(execution_prefix):
                    return content
    return ""


def tool_call_kwarg_literal(value: Any) -> str:
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    return repr(value)


def extract_code_from_response_tool_calls(response: Any) -> Optional[str]:
    """Recover fenced Python from AIMessage.tool_calls when content is empty."""
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

    args_str = ", ".join(
        f"{k}={tool_call_kwarg_literal(v)}" for k, v in (args if isinstance(args, dict) else {}).items()
    )
    return f"```python\nresult = await {name}({args_str})\nprint(result)\n```"
