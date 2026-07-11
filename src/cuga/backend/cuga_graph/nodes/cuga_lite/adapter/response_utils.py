"""Response and metadata helpers for the Lite graph adapter."""

from __future__ import annotations

import json
import keyword
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


def _parse_one_tool_call(tool_call: Any) -> Optional[tuple]:
    """Parse a single tool-call entry to ``(name, args_dict)``, or None if
    malformed/nameless. Matches the legacy per-element logic exactly."""
    if not isinstance(tool_call, dict):
        return None
    name = tool_call.get("name") or (tool_call.get("function") or {}).get("name")
    if not name:
        return None
    args = tool_call.get("args") or (tool_call.get("function") or {}).get("arguments") or {}
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except json.JSONDecodeError:
            args = {}
    return name, args if isinstance(args, dict) else {}


def _get_tool_calls(response: Any) -> list:
    return (
        getattr(response, "tool_calls", None)
        or (getattr(response, "additional_kwargs", None) or {}).get("tool_calls")
        or []
    )


def _parse_tool_calls(response: Any) -> list:
    """All tool calls as ``(name, args_dict)``; skips malformed / nameless entries."""
    return [p for tc in _get_tool_calls(response) if (p := _parse_one_tool_call(tc)) is not None]


def _tool_call_statements(name: str, args: dict, result_var: str) -> list:
    """One transpiled call: ``<result_var> = await name(**{...})`` + a print, or a
    visible skip marker when ``name`` is not a valid callable identifier.

    Args are passed via ``**{...}`` (never spliced as ``k=v`` kwargs) so keyword
    or non-identifier parameter names — Gmail ``from``, OData ``$filter``,
    ``page-size``, ``X-Api-Key`` — do not turn the block into a SyntaxError. The
    tool name is never spliced into a dynamic lookup (``getattr``/``globals``/
    ``eval`` are blocked by SecurityValidator and the prompt), so a
    non-identifier/keyword name is reported rather than executed unsafely.
    """
    if not name.isidentifier() or keyword.iskeyword(name):
        marker = f"[skipped tool with non-callable name: {name!r}]"
        return [f"print({json.dumps(marker)})"]
    if args:
        pairs = ", ".join(f"{k!r}: {tool_call_kwarg_literal(v)}" for k, v in args.items())
        call = f"{result_var} = await {name}(**{{{pairs}}})"
    else:
        call = f"{result_var} = await {name}()"
    return [call, f"print({result_var})"]


def extract_code_from_response_tool_calls(response: Any, *, multi: bool = False) -> Optional[str]:
    """Recover fenced Python from AIMessage.tool_calls.

    ``multi=False`` (default) preserves the legacy single-call behavior byte for
    byte — it inspects only ``tool_calls[0]`` and returns None if that entry is
    malformed (matching main). ``multi=True`` transpiles *every* well-formed tool
    call in the turn into a sequential block so parallel native tool calls are
    executed rather than silently dropped (issue #471 D1).
    """
    if not multi:
        tool_calls = _get_tool_calls(response)
        if not tool_calls:
            return None
        parsed = _parse_one_tool_call(tool_calls[0])
        if parsed is None:
            return None
        name, args = parsed
        args_str = ", ".join(f"{k}={tool_call_kwarg_literal(v)}" for k, v in args.items())
        return f"```python\nresult = await {name}({args_str})\nprint(result)\n```"

    calls = _parse_tool_calls(response)
    if not calls:
        return None
    lines: list = []
    for i, (name, args) in enumerate(calls):
        lines.extend(_tool_call_statements(name, args, f"result_{i}"))
    return "```python\n" + "\n".join(lines) + "\n```"
