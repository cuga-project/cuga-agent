"""
LLM error handling utilities.

Handles provider-specific errors (e.g. Groq tool_use_failed with failed_generation)
and extracts recoverable content for fallback execution.
"""

import json
import re
from typing import Any, Optional


def _parse_failed_generation_json(raw_fg: str) -> Optional[dict]:
    try:
        return json.loads(raw_fg)
    except json.JSONDecodeError:
        try:
            return json.loads(raw_fg.replace("\\'", '"'))
        except json.JSONDecodeError:
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
        return _parse_failed_generation_json(failed_generation)
    return None


def is_tool_choice_none_tool_use_failed(err: Any) -> bool:
    """
    True when the provider rejected the request because the model emitted a tool call
    while tool_choice was none (e.g. Groq 400 tool_use_failed).
    Safe to retry the same plain-text completion call.

    Detects both forms: the stringified error, and a structured provider ``body``
    dict on the exception (whose ``str()`` may only be the status code).
    """
    err_str = err if isinstance(err, str) else str(err)
    if "tool_use_failed" in err_str and "Tool choice is none" in err_str:
        return True
    body = getattr(err, "body", None)
    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict):
            code_matches = error.get("code") == "tool_use_failed" or "tool_use_failed" in str(error)
            if code_matches and "Tool choice is none" in str(error.get("message") or ""):
                return True
    return False


def is_ollama_tool_call_parse_error(err: Any) -> bool:
    """
    True when Ollama returns a 500 because the model emitted code/text into the
    tool-call channel and Ollama's parser expected JSON
    (``error parsing tool call: raw='...' ... looking for beginning of value``).
    Safe to retry the same completion call.
    """
    err_str = err if isinstance(err, str) else str(err)
    return "error parsing tool call" in err_str


async def ainvoke_with_retry_on_tool_choice_none(
    chain: Any, input_data: dict, *, max_attempts: int = 2
) -> Any:
    """Invoke a LangChain runnable; retry when the provider rejects a spurious tool call (tool_choice none)."""
    from loguru import logger

    for attempt in range(max_attempts):
        try:
            return await chain.ainvoke(input_data)
        except Exception as e:
            if attempt < max_attempts - 1 and (
                is_tool_choice_none_tool_use_failed(e) or is_ollama_tool_call_parse_error(e)
            ):
                logger.warning("Retrying LLM call after retryable provider tool-call error: {}", e)
                continue
            raise


def parse_tool_use_failed_generation(err: Any) -> Optional[dict]:
    """
    Parse tool_use_failed error with failed_generation (e.g. Groq malformed tool call).

    Returns the extracted tool call dict (name, arguments) or None if not parseable.
    """
    if hasattr(err, "body"):
        parsed = _parse_tool_use_failed_from_body(getattr(err, "body"))
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


# Lower-cased fragments of the errors providers raise when the request does not fit the
# context window. OpenAI/vLLM/Groq/LiteLLM ("maximum context length", code
# context_length_exceeded), Anthropic ("prompt is too long"), Bedrock ("input is too
# long"), WatsonX ("cannot exceed the total tokens limit"), Gemini ("exceeds the maximum
# number of tokens"), newer vLLM ("maximum model length").
_CONTEXT_OVERFLOW_FRAGMENTS = (
    "context_length_exceeded",
    "maximum context length",
    "context window",
    "prompt is too long",
    "input is too long",
    "cannot exceed the total tokens limit",
    "exceeds the maximum number of tokens",
    "maximum model length",
    "reduce the length of the messages",
)
_CONTEXT_OVERFLOW_TYPE_FRAGMENTS = ("contextwindowexceeded", "contextoverflow", "contextlengthexceeded")


def is_context_overflow_error(err: Any) -> bool:
    """True when the provider rejected the request because it does not fit the context window.

    Checks the exception, its ``body`` and its cause chain: LangChain wrappers re-raise
    the provider error with the original as ``__cause__``. Deliberately narrow — a
    tokens-per-minute rate limit ("Request too large ... tokens per min") is not an
    overflow and must not trigger a compaction.
    """
    seen = 0
    current = err
    while current is not None and seen < 5:
        seen += 1
        type_name = type(current).__name__.lower()
        if any(fragment in type_name for fragment in _CONTEXT_OVERFLOW_TYPE_FRAGMENTS):
            return True
        text = (current if isinstance(current, str) else str(current)).lower()
        body = getattr(current, "body", None)
        if body is not None:
            text += " " + str(body).lower()
        if "per min" not in text and any(fragment in text for fragment in _CONTEXT_OVERFLOW_FRAGMENTS):
            return True
        if isinstance(current, str):
            break
        current = getattr(current, "__cause__", None) or getattr(current, "__context__", None)
    return False


# Stop reasons meaning "the output-token limit cut the reply": OpenAI/WatsonX/vLLM
# ``finish_reason="length"``, Anthropic ``stop_reason="max_tokens"``, Gemini
# ``finish_reason="MAX_TOKENS"``, Ollama ``done_reason="length"``.
_TRUNCATED_STOP_REASONS = frozenset({"length", "max_tokens", "max_output_tokens"})


def is_output_truncated(response: Any) -> bool:
    """True when the model stopped because it hit the output-token limit."""
    meta = getattr(response, "response_metadata", None) or {}
    if not isinstance(meta, dict):
        return False
    generation_info = meta.get("generation_info") if isinstance(meta.get("generation_info"), dict) else {}
    for reason in (
        meta.get("finish_reason"),
        meta.get("stop_reason"),
        meta.get("done_reason"),
        generation_info.get("finish_reason"),
    ):
        if (
            reason is not None
            and str(getattr(reason, "name", reason)).strip().lower() in _TRUNCATED_STOP_REASONS
        ):
            return True
    return False
