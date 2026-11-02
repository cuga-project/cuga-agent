"""Repair lone UTF-16 surrogates in a model reply so it can be encoded as UTF-8 (#963).

A lone surrogate (``"\\ud83d"``, half of an emoji) is a valid Python ``str``
but cannot be encoded as UTF-8, so ``compile()``, the next request body,
pydantic's ``model_dump_json()`` and the trajectory file all fail on it. A model
reply holds one when the provider sends a JSON ``\\udXXX`` escape.

The reply is repaired before anything reads it, so the history stays encodable:

- a split pair is joined back into the character it encodes
  (``"\\ud83d\\ude00"`` becomes ``"😀"``);
- a truly lone half becomes U+FFFD (``"caf\\ud800"`` becomes ``"caf�"``).

Text with no surrogate is returned unchanged (the same object), and the
``isascii`` fast path keeps the common case cheap.
"""

from __future__ import annotations

import copy
import re
from typing import Any

_SURROGATE = re.compile("[\ud800-\udfff]")

# The reply fields that hold model text: content (a string or a list of content
# blocks), additional_kwargs (where reasoning_content lives) and tool-call arguments.
_MESSAGE_FIELDS = ("content", "additional_kwargs", "tool_calls", "invalid_tool_calls")


def repair_surrogates(text: str) -> str:
    """Return ``text`` with split pairs joined and lone surrogates replaced by U+FFFD."""
    if not isinstance(text, str) or text.isascii() or not _SURROGATE.search(text):
        return text
    return text.encode("utf-16", "surrogatepass").decode("utf-16", "replace")


def _repair_reply_value(value: Any) -> Any:
    """Repair the strings in one reply field: a string, or JSON-like lists and dict values.

    Reply fields are parsed from the provider's JSON, so they hold only strings,
    lists and dicts, with no cycles. Dict keys are not changed. Returns the same
    object when nothing needed repair.
    """
    if isinstance(value, str):
        return repair_surrogates(value)
    if isinstance(value, list):
        items = [_repair_reply_value(v) for v in value]
        return value if all(n is o for n, o in zip(items, value)) else items
    if isinstance(value, dict):
        items = {k: _repair_reply_value(v) for k, v in value.items()}
        return value if all(items[k] is v for k, v in value.items()) else items
    return value


def repair_message(message: Any) -> Any:
    """Repair a model reply without mutating it.

    Returns the same object when nothing needed repair, otherwise a copy. An
    object without ``model_copy`` (``_FakeResponse`` in ``ainvoke_model``) gets
    a shallow copy.
    """
    update = {}
    for field in _MESSAGE_FIELDS:
        current = getattr(message, field, None)
        if current is None:
            continue
        repaired = _repair_reply_value(current)
        if repaired is not current:
            update[field] = repaired
    if not update:
        return message
    if hasattr(message, "model_copy"):
        return message.model_copy(update=update)
    repaired = copy.copy(message)
    for field, value in update.items():
        setattr(repaired, field, value)
    return repaired
