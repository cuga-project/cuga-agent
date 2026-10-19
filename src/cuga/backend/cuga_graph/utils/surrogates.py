"""Repair lone UTF-16 surrogates so text can be encoded as UTF-8 (#963).

A lone surrogate (``"\\ud83d"``, half of an emoji) is a valid Python ``str``
but cannot be encoded as UTF-8, so ``compile()``, ``json.dumps(...).encode()``,
pydantic's ``model_dump_json()``, httpx request bodies and the trajectory file
all fail on it. It reaches CUGA from model replies (a JSON ``\\udXXX`` escape),
from tool results and code output (``print('\\ud83d\\ude00')`` builds two lone
halves at runtime), and from user input.

Repair happens where text enters the agent, so history and state stay
encodable for every later step:

- a split pair is joined back into the character it encodes
  (``"\\ud83d\\ude00"`` becomes ``"😀"``);
- a truly lone half becomes U+FFFD (``"caf\\ud800"`` becomes ``"caf�"``).

Text with no surrogate is returned unchanged (the same object), and the
``isascii`` fast path keeps the common case cheap.
"""

from __future__ import annotations

import re
from typing import Any

_SURROGATE = re.compile("[\ud800-\udfff]")


def repair_surrogates(text: str) -> str:
    """Return ``text`` with split pairs joined and lone surrogates replaced by U+FFFD."""
    if not isinstance(text, str) or text.isascii() or not _SURROGATE.search(text):
        return text
    return text.encode("utf-16", "surrogatepass").decode("utf-16", "replace")


def repair_surrogates_in(value: Any) -> Any:
    """Repair every string inside ``value``: dict keys and values, list and tuple items.

    Keys are repaired by :func:`_repair_key`, so two keys never merge into one.

    Other objects are returned as-is. A container is copied only when something
    inside it changed; otherwise the same object comes back.
    """
    if isinstance(value, str):
        return repair_surrogates(value)
    if isinstance(value, dict):
        keys = [_repair_key(k) for k in value]
        values = [repair_surrogates_in(v) for v in value.values()]
        if _unchanged(keys, value) and _unchanged(values, value.values()):
            return value
        return dict(zip(keys, values))
    if isinstance(value, (list, tuple)):
        items = [repair_surrogates_in(v) for v in value]
        if _unchanged(items, value):
            return value
        return items if isinstance(value, list) else tuple(items)
    return value


def _repair_key(key: Any) -> Any:
    """Repair a dict key without merging keys.

    U+FFFD would turn ``"\\ud800"`` and ``"\\udc00"`` into the same key and drop a
    value. A key keeps a lone half as its escape text instead (``"\\ud800"``), which
    differs per code point; a split pair is still joined.
    """
    if isinstance(key, tuple):
        repaired = tuple(_repair_key(k) for k in key)
        return key if _unchanged(repaired, key) else repaired
    if not isinstance(key, str) or key.isascii() or not _SURROGATE.search(key):
        return key
    joined = key.encode("utf-16", "surrogatepass").decode("utf-16", "surrogatepass")
    return joined.encode("utf-8", "backslashreplace").decode("utf-8")


def _unchanged(repaired: list, original: Any) -> bool:
    return all(new is old for new, old in zip(repaired, original))


_MESSAGE_FIELDS = ("content", "additional_kwargs", "tool_calls", "invalid_tool_calls")


def repair_message(message: Any) -> Any:
    """Repair a LangChain message, or a ``{"role", "content"}`` dict, without mutating it.

    Covers ``content`` (a string or a list of content blocks), ``additional_kwargs``
    (where ``reasoning_content`` lives) and the tool-call arguments. Returns the
    same object when nothing needed repair, otherwise a copy.
    """
    if isinstance(message, dict):
        return repair_surrogates_in(message)
    update = {}
    for field in _MESSAGE_FIELDS:
        current = getattr(message, field, None)
        if current is None:
            continue
        repaired = repair_surrogates_in(current)
        if repaired is not current:
            update[field] = repaired
    if not update or not hasattr(message, "model_copy"):
        return message
    return message.model_copy(update=update)


def repair_messages(messages: list) -> list:
    """Apply :func:`repair_message` to each message; returns the same list if all were clean."""
    repaired = [repair_message(m) for m in messages]
    return messages if _unchanged(repaired, messages) else repaired
