"""Messages the runtime injects into the conversation, told apart from the user's.

Function-calling corrections (empty reply, mode violation, search-only, no-op answer,
repeated calls) are persisted as ``HumanMessage``s, because the provider transcript has
no other turn a runtime note can ride on. Untagged, they are indistinguishable from the
user: turn detection had to recognise them by text prefix, and the model reads them as
new instructions from the person it is helping.

Claude Code's answer (free-code ``utils/messages.ts`` ``isMeta`` + ``<system-reminder>``,
explained in its system prompt): tag each injected message, and wrap it on the way out
so the model can see where it came from. Here the tag is an ``additional_kwargs`` key —
a ``BaseMessage`` field, so it survives the SDK boundary where persisted history comes
back as bare ``BaseMessage`` shells — and the wrapper is applied only to the outbound
copy, so the persisted text stays byte-identical to the correction constants.
"""

from __future__ import annotations

from typing import Any

from langchain_core.messages import HumanMessage

HARNESS_KEY = "cuga_harness"

_OPEN = "<system-reminder>"
_CLOSE = "</system-reminder>"


def harness_message(text: str, kind: str) -> HumanMessage:
    """A runtime note, tagged with what produced it (``kind``)."""
    return HumanMessage(content=text, additional_kwargs={HARNESS_KEY: kind})


def harness_kind(message: Any) -> str:
    """The tag of a runtime note, or ``""`` for anything else (dicts included)."""
    kwargs = (
        message.get("additional_kwargs")
        if isinstance(message, dict)
        else getattr(message, "additional_kwargs", None)
    )
    if isinstance(kwargs, dict):
        return str(kwargs.get(HARNESS_KEY) or "")
    return ""


def is_harness_message(message: Any) -> bool:
    return bool(harness_kind(message))


def wrap_reminder(text: str) -> str:
    text = text or ""
    if text.lstrip().startswith(_OPEN):
        return text
    return f"{_OPEN}\n{text}\n{_CLOSE}"


def for_model(message: HumanMessage) -> HumanMessage:
    """The outbound copy of a runtime note: wrapped in ``<system-reminder>`` tags."""
    content = message.content
    if not isinstance(content, str):
        return message
    return message.model_copy(update={"content": wrap_reminder(content)})
