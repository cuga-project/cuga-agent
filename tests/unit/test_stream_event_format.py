"""Regression tests for ``StreamEvent.format`` SSE formatting.

These cover the bug where calling ``.format(OutputFormat.DEFAULT, ...)`` on
non-``Answer`` events (such as ``SlashSkillInvoked``) short-circuited to
bare ``self.data`` and skipped the ``event: <name>\\n`` prefix and the SSE
terminator blank line, causing the client to see raw JSON glued to the
next event.
"""

import json

import pytest

from cuga.backend.cuga_graph.utils.agent_loop import OutputFormat, StreamEvent

pytestmark = pytest.mark.unit


def _assert_sse_block(formatted: str, name: str, data: str) -> None:
    """Assert ``formatted`` is a well-formed SSE event block.

    A conformant block must (1) start with ``event: <name>\\n``, (2) have one
    ``data:`` line per logical line of ``data``, and (3) terminate with a
    blank line so the EventSource parser dispatches it.
    """
    assert formatted.startswith(f"event: {name}\n"), f"missing event prefix; got: {formatted!r}"
    assert formatted.endswith("\n\n"), f"missing SSE terminator blank line; got: {formatted!r}"
    # Round-trip parse to confirm the data survives intact.
    round_tripped = StreamEvent.parse(formatted)
    assert round_tripped.name == name
    assert round_tripped.data == data


def test_default_format_wraps_slash_skill_invoked_event():
    """Regression: ``SlashSkillInvoked`` under ``DEFAULT`` used to leak raw JSON."""
    payload = json.dumps({"resolved_name": "echo", "raw_input": "/echo", "raw_args": ""})
    out = StreamEvent(name="SlashSkillInvoked", data=payload).format(OutputFormat.DEFAULT, thread_id="t-1")
    _assert_sse_block(out, "SlashSkillInvoked", payload)


def test_default_format_still_wraps_answer_event():
    """``Answer`` events must continue to receive the SSE wrapper."""
    out = StreamEvent(name="Answer", data="hello world").format(OutputFormat.DEFAULT, thread_id="t-3")
    _assert_sse_block(out, "Answer", "hello world")


def test_default_format_multiline_data_splits_per_sse_spec():
    """Multi-line bodies must emit one ``data:`` line per logical line so
    blank lines in markdown don't truncate the body at the first ``\\n\\n``.
    """
    body = "line one\n\nline three"
    out = StreamEvent(name="Answer", data=body).format(OutputFormat.DEFAULT, thread_id="t-4")
    # Exactly three data lines, one of them empty.
    assert out == "event: Answer\ndata: line one\ndata: \ndata: line three\n\n"
    # And round-trip preserves the blank line.
    assert StreamEvent.parse(out).data == body


def test_none_format_matches_default_for_non_answer_events():
    """``run_stream`` calls ``.format()`` with no arg; that path and the
    explicit-DEFAULT path used by slash code should produce identical output.
    Equivalence is what guarantees the slash events look like every other
    streamed event on the wire.
    """
    payload = json.dumps({"x": 1})
    a = StreamEvent(name="CodeAgent", data=payload).format()
    b = StreamEvent(name="CodeAgent", data=payload).format(OutputFormat.DEFAULT)
    assert a == b
    _assert_sse_block(a, "CodeAgent", payload)


def test_wxo_format_path_is_unchanged():
    """``OutputFormat.WXO`` still wraps events into the chat-completion shape."""
    out = StreamEvent(name="Answer", data="hi").format(OutputFormat.WXO, thread_id="t-5")
    assert out.startswith("data: ")
    assert out.endswith("\n\n")
    # The payload between ``data: `` and the trailing newlines is JSON.
    body = out[len("data: ") : -2]
    parsed = json.loads(body)
    assert parsed["thread_id"] == "t-5"
    assert parsed["choices"][0]["delta"]["role"] == "assistant"
