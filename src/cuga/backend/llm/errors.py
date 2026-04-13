"""
LLM error handling utilities.

Handles provider-specific errors (e.g. Groq tool_use_failed with failed_generation)
and extracts recoverable content for fallback execution.
"""

import json
import re
from typing import Any, Optional, Tuple


def _parse_failed_generation_json(raw_fg: str) -> Optional[dict]:
    try:
        return json.loads(raw_fg)
    except json.JSONDecodeError:
        try:
            return json.loads(raw_fg.replace("\\'", '"'))
        except json.JSONDecodeError:
            return None


def _decode_json_string_value_lenient(s: str, start: int) -> Tuple[Optional[str], int]:
    """Decode a JSON string starting at s[start] == '\"'. Tolerates invalid ``\\'`` (common in model output)."""
    if start >= len(s) or s[start] != '"':
        return None, start
    parts: list[str] = []
    i = start + 1
    while i < len(s):
        c = s[i]
        if c == '"':
            return "".join(parts), i + 1
        if c == "\\" and i + 1 < len(s):
            esc = s[i + 1]
            if esc == "\\":
                parts.append("\\")
                i += 2
                continue
            if esc == '"':
                parts.append('"')
                i += 2
                continue
            if esc == "/":
                parts.append("/")
                i += 2
                continue
            if esc == "n":
                parts.append("\n")
                i += 2
                continue
            if esc == "r":
                parts.append("\r")
                i += 2
                continue
            if esc == "t":
                parts.append("\t")
                i += 2
                continue
            if esc == "b":
                parts.append("\b")
                i += 2
                continue
            if esc == "f":
                parts.append("\f")
                i += 2
                continue
            if esc == "'":
                parts.append("'")
                i += 2
                continue
            if esc == "u" and i + 5 < len(s):
                hx = s[i + 2 : i + 6]
                if len(hx) == 4 and all(c in "0123456789abcdefABCDEF" for c in hx):
                    parts.append(chr(int(hx, 16)))
                    i += 6
                    continue
        parts.append(c)
        i += 1
    return None, start


def recover_execute_python_code_from_failed_generation_string(raw_fg: str) -> Optional[str]:
    """
    When Groq rejects tool arguments as invalid JSON, the failed_generation blob may still
    contain an execute_python payload. Try strict JSON first, then scan for \"code\" and
    decode the string value leniently.
    """
    if not raw_fg or "execute_python" not in raw_fg:
        return None
    parsed = _parse_failed_generation_json(raw_fg)
    if isinstance(parsed, dict) and parsed.get("name") == "execute_python":
        args = parsed.get("arguments")
        if isinstance(args, dict):
            code = args.get("code")
            if isinstance(code, str):
                return code.strip()
    m = re.search(r'"code"\s*:\s*"', raw_fg)
    if not m:
        return None
    # Regex ends at the opening quote of the code string; that quote is at m.end() - 1.
    # Do not use find('"', ...) — it can match the closing quote of the key "code".
    start_quote = m.end() - 1
    code, _end = _decode_json_string_value_lenient(raw_fg, start_quote)
    if code is not None:
        return code.strip()
    return None


def _parse_tool_use_failed_from_body(body: Any) -> Optional[dict]:
    if not isinstance(body, dict):
        return None
    error = body.get("error")
    if not isinstance(error, dict):
        return None
    if error.get("code") != "tool_use_failed" and "tool_use_failed" not in str(error):
        return None

    failed_generation = error.get("failed_generation")
    if isinstance(failed_generation, dict):
        return failed_generation
    if isinstance(failed_generation, str):
        parsed = _parse_failed_generation_json(failed_generation)
        if isinstance(parsed, dict):
            return parsed
        recovered = recover_execute_python_code_from_failed_generation_string(failed_generation)
        if recovered is not None:
            return {"name": "execute_python", "arguments": {"code": recovered}}
    return None


def _error_payload_dict(err: Any) -> Optional[dict]:
    """Groq/OpenAI-style exceptions: body may be None; JSON often lives on response."""
    body = getattr(err, "body", None)
    if isinstance(body, dict):
        return body
    resp = getattr(err, "response", None)
    if resp is not None and callable(getattr(resp, "json", None)):
        try:
            j = resp.json()
            if isinstance(j, dict):
                return j
        except Exception:
            return None
    return None


def parse_tool_use_failed_generation(err: Any) -> Optional[dict]:
    """
    Parse tool_use_failed error with failed_generation (e.g. Groq malformed tool call).

    Returns the extracted tool call dict (name, arguments) or None if not parseable.
    """
    payload = _error_payload_dict(err)
    if payload:
        parsed = _parse_tool_use_failed_from_body(payload)
        if parsed:
            return parsed

    if isinstance(err, dict):
        parsed = _parse_tool_use_failed_from_body(err)
        if parsed:
            return parsed

    err_str = err if isinstance(err, str) else str(err)
    if "failed_generation" not in err_str or "tool_use_failed" not in err_str:
        return None
    m = re.search(r"'failed_generation':\s*'([^']+)'", err_str)
    if not m:
        m = re.search(r'"failed_generation":\s*"([^"]+)"', err_str)
    raw_fg = m.group(1) if m else None
    if not raw_fg:
        return None
    failed_gen = _parse_failed_generation_json(raw_fg)
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
    if tool_name == "execute_python":
        if isinstance(tool_args, dict):
            code = tool_args.get("code")
            if isinstance(code, str):
                return code.strip()
        if isinstance(tool_args, str):
            try:
                loaded = json.loads(tool_args)
                if isinstance(loaded, dict) and isinstance(loaded.get("code"), str):
                    return loaded["code"].strip()
            except json.JSONDecodeError:
                pass
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


def extract_code_from_tool_use_failed(err: Any) -> Optional[str]:
    """
    Extract executable code from tool_use_failed error if recoverable.

    Returns code string to run in sandbox, or None if error is not recoverable.
    """
    failed_gen = parse_tool_use_failed_generation(err)
    if not failed_gen:
        return None
    return failed_gen_to_code(failed_gen)


def is_tool_use_failed_retryable_error(err: Exception) -> bool:
    """
    True when the provider rejected the model output due to invalid tool-call JSON
    (e.g. Groq ``tool_use_failed``). Safe to retry the same chat completion request.
    """
    payload = _error_payload_dict(err)
    if isinstance(payload, dict):
        e = payload.get("error")
        if isinstance(e, dict) and e.get("code") == "tool_use_failed":
            return True
    body = getattr(err, "body", None)
    if isinstance(body, dict):
        e = body.get("error")
        if isinstance(e, dict) and e.get("code") == "tool_use_failed":
            return True
    text = str(err).lower()
    if "tool_use_failed" in text:
        return True
    if "failed to parse tool call arguments" in text:
        return True
    return False
