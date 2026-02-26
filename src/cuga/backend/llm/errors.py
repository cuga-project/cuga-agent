"""
LLM error handling utilities.

Handles provider-specific errors (e.g. Groq tool_use_failed with failed_generation)
and extracts recoverable content for fallback execution.
"""

import json
import re
from typing import Optional


def parse_tool_use_failed_generation(err_str: str) -> Optional[dict]:
    """
    Parse tool_use_failed error with failed_generation (e.g. Groq malformed tool call).

    Returns the extracted tool call dict (name, arguments) or None if not parseable.
    """
    if "failed_generation" not in err_str or "tool_use_failed" not in err_str:
        return None
    m = re.search(r"'failed_generation':\s*'([^']+)'", err_str)
    if not m:
        m = re.search(r'"failed_generation":\s*"([^"]+)"', err_str)
    raw_fg = m.group(1) if m else None
    if not raw_fg:
        return None
    try:
        failed_gen = json.loads(raw_fg.replace("\\'", '"'))
    except json.JSONDecodeError:
        failed_gen = None
    if not failed_gen and '"name": "python"' in raw_fg:
        arg_m = re.search(r'"arguments":\s*(.+?)\s*\}', raw_fg, re.DOTALL)
        if arg_m:
            failed_gen = {"name": "python", "arguments": arg_m.group(1).strip()}
    return failed_gen


def failed_gen_to_code(failed_gen: dict) -> Optional[str]:
    """
    Convert parsed failed_generation tool call to executable code string.

    Returns code to run in sandbox, or None if not convertible.
    """
    tool_name = failed_gen.get("name")
    tool_args = failed_gen.get("arguments", {})
    if tool_name == "python" and isinstance(tool_args, str):
        return tool_args.replace("\\n", "\n").strip()
    if tool_name:
        if isinstance(tool_args, str):
            try:
                tool_args = json.loads(tool_args)
            except json.JSONDecodeError:
                tool_args = {}
        if isinstance(tool_args, dict):
            args_str = ", ".join(f"{k}={repr(v)}" for k, v in tool_args.items())
            return f"result = await {tool_name}({args_str})\nprint(result)"
    return None


def extract_code_from_tool_use_failed(err_str: str) -> Optional[str]:
    """
    Extract executable code from tool_use_failed error if recoverable.

    Returns code string to run in sandbox, or None if error is not recoverable.
    """
    failed_gen = parse_tool_use_failed_generation(err_str)
    if not failed_gen:
        return None
    return failed_gen_to_code(failed_gen)
