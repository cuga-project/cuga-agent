"""Keep the outbound prompt cacheable.

Providers cache a request's prefix: OpenAI, Azure, Gemini and Groq automatically for
long prefixes, Anthropic (here: Claude behind LiteLLM or OpenRouter) at explicit
``cache_control`` breakpoints. Either way a cached prefix is reused only up to the first
byte that changed, so anything that changes mid-task must sit at the end.

Two switches:

- ``cuga_lite_stable_system_prompt`` (default on): todos and observed tool shapes leave
  the system prompt and ride on the tail of the outbound messages (free-code keeps its
  system prompt fixed the same way and sends run state as ``<system-reminder>``
  attachments). In function-calling mode the variables summary moves from the last
  ``HumanMessage`` — the user's request at the *start* of the turn — to the tail too.
- ``cuga_lite_prompt_cache_markers`` (``off`` | ``auto`` | ``on``, default off): adds
  Anthropic ``cache_control`` breakpoints. ``auto`` marks only Claude/Anthropic models.
  Off by default because a gateway that does not understand content blocks would reject
  every request.
"""

from __future__ import annotations

from typing import Any, List

from cuga.config import settings

STABLE_SYSTEM_PROMPT_SETTING = "cuga_lite_stable_system_prompt"
CACHE_MARKERS_SETTING = "cuga_lite_prompt_cache_markers"

_CACHE_CONTROL = {"type": "ephemeral"}
# Anthropic allows four breakpoints; the system prompt plus the last two history turns
# leaves one spare and lets a call read the prefix the previous call wrote.
_HISTORY_BREAKPOINTS = 2


def _setting(configurable: dict | None, key: str, default: Any) -> Any:
    if key in (configurable or {}):
        return (configurable or {})[key]
    return getattr(settings.advanced_features, key, default)


def stable_system_prompt(configurable: dict | None) -> bool:
    value = _setting(configurable, STABLE_SYSTEM_PROMPT_SETTING, True)
    if isinstance(value, str):
        return value.strip().lower() not in ("false", "0", "off", "no")
    return bool(value)


def cache_markers_enabled(configurable: dict | None, model_name: str) -> bool:
    mode = str(_setting(configurable, CACHE_MARKERS_SETTING, "off") or "off").strip().lower()
    if mode in ("on", "true", "always"):
        return True
    if mode == "auto":
        name = (model_name or "").lower()
        return "claude" in name or "anthropic" in name
    return False


def _role(message: Any) -> str:
    if isinstance(message, dict):
        return str(message.get("role") or "")
    return str(getattr(message, "type", "") or "")


def _content(message: Any) -> Any:
    return message.get("content") if isinstance(message, dict) else getattr(message, "content", None)


def _with_marker(message: Any) -> Any:
    blocks = [{"type": "text", "text": _content(message), "cache_control": dict(_CACHE_CONTROL)}]
    if isinstance(message, dict):
        return {**message, "content": blocks}
    return message.model_copy(update={"content": blocks})


def add_cache_markers(messages: List[Any], history_end: int | None = None) -> List[Any]:
    """Mark the system prompt and the last two user/assistant turns before ``history_end``.

    ``history_end`` excludes the outbound-only tail (run state, notes), which changes on
    every call. Only plain-text system/user/assistant messages are marked: tool replies
    lose ``cache_control`` in ``langchain_openai``, and Anthropic rejects an empty text
    block. Works on CodeAct's role dicts and on message objects. Returns a copy.
    """
    out = list(messages)
    end = len(out) if history_end is None else min(history_end, len(out))

    def markable(message: Any) -> bool:
        content = _content(message)
        return isinstance(content, str) and bool(content.strip())

    if out and _role(out[0]) == "system" and markable(out[0]):
        out[0] = _with_marker(out[0])
    marked = 0
    for i in range(end - 1, 0, -1):
        if marked >= _HISTORY_BREAKPOINTS:
            break
        if _role(out[i]) in ("user", "human", "assistant", "ai") and markable(out[i]):
            out[i] = _with_marker(out[i])
            marked += 1
    return out
