"""ACP agent helpers.

Provides utility functions for processing ACP input messages before they are
forwarded to the underlying CUGA agent.
"""

from __future__ import annotations

from acp_sdk.models import Error, ErrorCode, Message
from acp_sdk.models.errors import ACPError

_INVALID_INPUT_MSG = "Input must contain at least one user message with plain-text content."


def _extract_text_input(messages: list[Message]) -> str:
    """Extract plain-text content from a list of ACP messages.

    Rules
    -----
    - Only ``role == "user"`` messages are considered.
    - Only parts with ``content_type == "text/plain"`` are accepted.
    - ``content_encoding`` must be ``"plain"`` or absent (``None``).
    - Parts must have inline ``content``; ``content_url`` is rejected.
    - Message and part order is preserved.
    - Parts within a single message are joined with ``""``
      (mirrors ``Message.__str__()``).
    - Separate messages are joined with ``"\\n"``.
    - An empty final string is rejected.

    Raises
    ------
    ACPError
        With ``ErrorCode.INVALID_INPUT`` when the above conditions are not
        satisfied.  The error message is a fixed, safe constant — it never
        echoes submitted content.
    """
    segments: list[str] = []

    for message in messages:
        if message.role != "user":
            continue

        parts_text: list[str] = []
        for part in message.parts:
            if part.content_type != "text/plain":
                continue
            if part.content_encoding not in ("plain", None):
                _reject()
            if part.content_url is not None:
                _reject()
            if part.content is None:
                _reject()
            parts_text.append(part.content)

        if parts_text:
            segments.append("".join(parts_text))

    result = "\n".join(segments)
    if not result:
        _reject()

    return result


def _reject() -> None:
    raise ACPError(Error(code=ErrorCode.INVALID_INPUT, message=_INVALID_INPUT_MSG))
