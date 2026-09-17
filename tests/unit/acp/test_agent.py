"""Unit tests for _extract_text_input (Task 2.2)."""

from __future__ import annotations

import pytest
from acp_sdk.models import Error, ErrorCode, Message, MessagePart
from acp_sdk.models.errors import ACPError

from cuga.backend.server.acp.agent import _INVALID_INPUT_MSG, _extract_text_input

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _user_msg(*texts: str) -> Message:
    """Build a user Message with one plain-text part per *texts* item."""
    return Message(
        role="user",
        parts=[MessagePart(content_type="text/plain", content=t, content_encoding="plain") for t in texts],
    )


def _agent_msg(text: str) -> Message:
    return Message(
        role="agent",
        parts=[MessagePart(content_type="text/plain", content=text, content_encoding="plain")],
    )


# ---------------------------------------------------------------------------
# Happy-path tests
# ---------------------------------------------------------------------------


def test_single_part():
    result = _extract_text_input([_user_msg("hello")])
    assert result == "hello"


def test_multiple_parts_joined_without_separator():
    """Parts within one message must be joined with '' (no separator)."""
    result = _extract_text_input([_user_msg("foo", "bar")])
    assert result == "foobar"


def test_multiple_messages_joined_with_newline():
    msgs = [_user_msg("first"), _user_msg("second")]
    result = _extract_text_input(msgs)
    assert result == "first\nsecond"


def test_order_preserved():
    msgs = [_user_msg("a", "b"), _user_msg("c")]
    result = _extract_text_input(msgs)
    assert result == "ab\nc"


def test_agent_messages_ignored():
    """Agent-role messages must be silently skipped."""
    msgs = [_agent_msg("ignore me"), _user_msg("keep")]
    result = _extract_text_input(msgs)
    assert result == "keep"


# ---------------------------------------------------------------------------
# Rejection tests
# ---------------------------------------------------------------------------


def _assert_rejects(messages: list[Message]) -> None:
    with pytest.raises(ACPError) as exc_info:
        _extract_text_input(messages)
    err: Error = exc_info.value.error
    assert err.code == ErrorCode.INVALID_INPUT
    assert err.message == _INVALID_INPUT_MSG


def test_wrong_role_only():
    """Only agent-role messages → empty result → rejected."""
    _assert_rejects([_agent_msg("hello")])


def test_non_text_mime_type():
    """Parts with a non text/plain MIME type must be ignored; if nothing remains → rejected."""
    msg = Message(
        role="user",
        parts=[
            MessagePart(content_type="application/json", content='{"key":"value"}', content_encoding="plain")
        ],
    )
    _assert_rejects([msg])


def test_base64_encoding_rejected():
    """Parts with base64 encoding must raise ACPError."""
    msg = Message(
        role="user",
        parts=[MessagePart(content_type="text/plain", content="aGVsbG8=", content_encoding="base64")],
    )
    _assert_rejects([msg])


def test_content_url_rejected():
    """Parts backed by a URL must raise ACPError."""
    msg = Message(
        role="user",
        parts=[MessagePart(content_type="text/plain", content_url="https://example.com/data.txt")],  # type: ignore[arg-type]
    )
    _assert_rejects([msg])


def test_missing_content_rejected():
    """Parts with no inline content and no URL must raise ACPError."""
    msg = Message(
        role="user",
        parts=[MessagePart(content_type="text/plain", content=None)],
    )
    _assert_rejects([msg])


def test_empty_input_rejected():
    """Empty message list → rejected."""
    _assert_rejects([])


def test_empty_string_parts_yield_rejection():
    """If all accepted parts are empty strings, final string is empty → rejected."""
    msg = Message(
        role="user",
        parts=[MessagePart(content_type="text/plain", content="", content_encoding="plain")],
    )
    _assert_rejects([msg])


# ---------------------------------------------------------------------------
# Non-reflection of submitted secrets
# ---------------------------------------------------------------------------


def test_error_does_not_echo_input():
    """The error message must never echo any submitted content (security)."""
    secret = "my-super-secret-api-key-12345"
    msg = Message(
        role="user",
        parts=[MessagePart(content_type="text/plain", content=secret, content_encoding="base64")],
    )
    with pytest.raises(ACPError) as exc_info:
        _extract_text_input([msg])
    assert secret not in exc_info.value.error.message
    assert secret not in str(exc_info.value)


def test_error_does_not_echo_url():
    """The error message must never echo a submitted URL."""
    url = "https://evil.example.com/secret-payload.txt"
    msg = Message(
        role="user",
        parts=[MessagePart(content_type="text/plain", content_url=url)],  # type: ignore[arg-type]
    )
    with pytest.raises(ACPError) as exc_info:
        _extract_text_input([msg])
    assert url not in exc_info.value.error.message
    assert url not in str(exc_info.value)
