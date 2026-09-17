"""Unit tests for _extract_text_input (Task 2.2) and CugaACPAgent (Task 2.3)."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import MagicMock

import pytest
from acp_sdk.models import Error, ErrorCode, Message, MessageAwaitRequest, MessageAwaitResume, MessagePart
from acp_sdk.models.errors import ACPError

from cuga.backend.server.acp.agent import (
    _INVALID_INPUT_MSG,
    _extract_text_input,
    CugaACPAgent,
)
from cuga.backend.server.agent_protocol.events import AgentStreamEvent
from cuga.backend.server.agent_protocol.simple_runner import _MAX_AUTO_RESUMES

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helpers — Task 2.2
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
# Happy-path tests — Task 2.2
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
# Rejection tests — Task 2.2
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
# Non-reflection of submitted secrets — Task 2.2
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


# ===========================================================================
# Task 2.3 — CugaACPAgent
# ===========================================================================


# ---------------------------------------------------------------------------
# Scripted runner and fake context helpers
# ---------------------------------------------------------------------------


def _scripted_runner(*events: AgentStreamEvent) -> Any:
    """Return a mock AgentRunner that yields the given events on run()."""

    class _Runner:
        def __init__(self) -> None:
            self.calls: list[tuple[str, str | None, dict | None]] = []

        async def run(
            self,
            message: str,
            context_id: str | None = None,
            approval: dict | None = None,
        ) -> AsyncIterator[AgentStreamEvent]:
            self.calls.append((message, context_id, approval))
            for ev in events:
                yield ev

    return _Runner()


def _multi_call_runner(*call_events: list[AgentStreamEvent]) -> Any:
    """Runner that returns different event sequences for successive calls."""

    class _MultiRunner:
        def __init__(self) -> None:
            self._sequences = list(call_events)
            self._idx = 0
            self.calls: list[tuple[str, str | None, dict | None]] = []

        async def run(
            self,
            message: str,
            context_id: str | None = None,
            approval: dict | None = None,
        ) -> AsyncIterator[AgentStreamEvent]:
            self.calls.append((message, context_id, approval))
            seq = self._sequences[min(self._idx, len(self._sequences) - 1)]
            self._idx += 1
            for ev in seq:
                yield ev

    return _MultiRunner()


def _fake_context(session_id: uuid.UUID | None = None) -> MagicMock:
    """Build a minimal fake ACP Context with a session carrying *session_id*."""
    ctx = MagicMock()
    ctx.session.id = session_id or uuid.uuid4()
    return ctx


def _input_messages(text: str) -> list[Message]:
    return [_user_msg(text)]


async def _collect(agent: CugaACPAgent, messages: list[Message], context: Any) -> list[Any]:
    """Drain the agent's run() generator to completion, returning all yielded values."""
    results = []
    gen = agent.run(messages, context)
    value: Any = None
    try:
        while True:
            item = await gen.asend(value)
            results.append(item)
            value = None  # default: no resume
    except StopAsyncIteration:
        pass
    return results


async def _collect_with_resumes(
    agent: CugaACPAgent,
    messages: list[Message],
    context: Any,
    resume_texts: list[str],
) -> list[Any]:
    """Drain run() generator, feeding resume_texts in order after each MessageAwaitRequest."""
    results = []
    resume_iter = iter(resume_texts)
    gen = agent.run(messages, context)
    value: Any = None
    try:
        while True:
            item = await gen.asend(value)
            results.append(item)
            if isinstance(item, MessageAwaitRequest):
                text = next(resume_iter, None)
                if text is not None:
                    value = MessageAwaitResume(
                        message=Message(
                            role="user",
                            parts=[MessagePart(content_type="text/plain", content=text)],
                        )
                    )
                else:
                    value = None
            else:
                value = None
    except StopAsyncIteration:
        pass
    return results


# ---------------------------------------------------------------------------
# Test: mapping table — terminal answer events
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "event_name",
    ["final_answer", "task_complete", "completed", "done"],
)
async def test_terminal_event_yields_agent_message(event_name: str) -> None:
    runner = _scripted_runner(AgentStreamEvent(event_name, {"text": "Hello!"}, final=True))
    agent = CugaACPAgent(runner=runner, name="test-agent", description="desc")
    ctx = _fake_context()

    results = await _collect(agent, _input_messages("hi"), ctx)

    assert len(results) == 1
    msg = results[0]
    assert isinstance(msg, Message)
    assert msg.role == "agent"
    assert msg.parts[0].content == "Hello!"
    assert msg.parts[0].content_type == "text/plain"


@pytest.mark.asyncio
async def test_final_true_event_yields_agent_message() -> None:
    """Any event with final=True that is not HITL or error yields a terminal Message."""
    runner = _scripted_runner(AgentStreamEvent("some_custom_name", {"text": "done text"}, final=True))
    agent = CugaACPAgent(runner=runner, name="test-agent", description="desc")
    ctx = _fake_context()

    results = await _collect(agent, _input_messages("hi"), ctx)

    assert len(results) == 1
    msg = results[0]
    assert isinstance(msg, Message)
    assert msg.parts[0].content == "done text"


# ---------------------------------------------------------------------------
# Test: mapping table — error events
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("event_name", ["error", "failed", "failure", "exception"])
async def test_error_event_yields_sanitized_error(event_name: str) -> None:
    runner = _scripted_runner(AgentStreamEvent(event_name, {"text": "secret internal error"}, final=True))
    agent = CugaACPAgent(runner=runner, name="test-agent", description="desc")
    ctx = _fake_context()

    results = await _collect(agent, _input_messages("hi"), ctx)

    assert len(results) == 1
    err = results[0]
    assert isinstance(err, Error)
    assert err.code == ErrorCode.SERVER_ERROR
    assert err.message == "CUGA agent execution failed"
    # Must not contain any internal error text
    assert "secret internal error" not in err.message


# ---------------------------------------------------------------------------
# Test: mapping table — non-terminal progress is ignored
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_progress_events_ignored() -> None:
    """Non-terminal progress events before a terminal should not appear in output."""
    runner = _scripted_runner(
        AgentStreamEvent("thinking", {"text": "reasoning..."}, final=False),
        AgentStreamEvent("step", {"text": "working..."}, final=False),
        AgentStreamEvent("final_answer", {"text": "The answer is 42."}, final=True),
    )
    agent = CugaACPAgent(runner=runner, name="test-agent", description="desc")
    ctx = _fake_context()

    results = await _collect(agent, _input_messages("hi"), ctx)

    assert len(results) == 1
    msg = results[0]
    assert isinstance(msg, Message)
    assert msg.parts[0].content == "The answer is 42."


# ---------------------------------------------------------------------------
# Test: mapping table — stream ends without terminal
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_empty_stream_yields_completion_message() -> None:
    """An empty stream (no events) yields the 'Agent completed processing' fallback."""
    runner = _scripted_runner()  # no events
    agent = CugaACPAgent(runner=runner, name="test-agent", description="desc")
    ctx = _fake_context()

    results = await _collect(agent, _input_messages("hi"), ctx)

    assert len(results) == 1
    msg = results[0]
    assert isinstance(msg, Message)
    assert msg.parts[0].content == "Agent completed processing"


@pytest.mark.asyncio
async def test_progress_only_stream_yields_completion_message() -> None:
    """A stream with only non-terminal events yields the 'Agent completed processing' fallback."""
    runner = _scripted_runner(
        AgentStreamEvent("thinking", {"text": "..."}, final=False),
        AgentStreamEvent("step", {"text": "..."}, final=False),
    )
    agent = CugaACPAgent(runner=runner, name="test-agent", description="desc")
    ctx = _fake_context()

    results = await _collect(agent, _input_messages("hi"), ctx)

    assert len(results) == 1
    msg = results[0]
    assert isinstance(msg, Message)
    assert msg.parts[0].content == "Agent completed processing"


# ---------------------------------------------------------------------------
# Test: stable session ID as context_id
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stable_session_id_used_as_context_id() -> None:
    """The same session UUID must be passed as context_id to every runner call."""
    session_id = uuid.uuid4()
    runner = _scripted_runner(AgentStreamEvent("final_answer", {"text": "ok"}, final=True))
    agent = CugaACPAgent(runner=runner, name="test-agent", description="desc")
    ctx = _fake_context(session_id)

    await _collect(agent, _input_messages("hello"), ctx)

    assert len(runner.calls) == 1
    _, used_context_id, _ = runner.calls[0]
    assert used_context_id == str(session_id)


# ---------------------------------------------------------------------------
# Test: HITL — approval resume
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "hitl_event_name",
    ["input_required", "approval_needed", "user_input", "interrupt", "hitl_check"],
)
async def test_hitl_event_yields_message_await_request(hitl_event_name: str) -> None:
    """HITL events cause a MessageAwaitRequest to be yielded."""
    hitl_data = {"text": "Approve?", "action_id": "abc-123"}
    runner = _scripted_runner(AgentStreamEvent(hitl_event_name, hitl_data, final=True))
    agent = CugaACPAgent(runner=runner, name="test-agent", description="desc")
    ctx = _fake_context()

    results = await _collect(agent, _input_messages("hi"), ctx)

    assert len(results) == 1
    assert isinstance(results[0], MessageAwaitRequest)
    # The prompt text should appear in the message
    assert "Approve?" in results[0].message.parts[0].content


@pytest.mark.asyncio
async def test_hitl_approval_resume_passes_confirmed_true() -> None:
    """Approving a HITL prompt calls runner again with confirmed=True approval."""
    action_id = "action-42"
    hitl_events = [AgentStreamEvent("input_required", {"text": "Allow?", "action_id": action_id}, final=True)]
    final_events = [AgentStreamEvent("final_answer", {"text": "Done!"}, final=True)]
    runner = _multi_call_runner(hitl_events, final_events)
    agent = CugaACPAgent(runner=runner, name="test-agent", description="desc")
    ctx = _fake_context()

    results = await _collect_with_resumes(agent, _input_messages("start"), ctx, resume_texts=["approve"])

    # Should eventually yield the final message
    final_msgs = [r for r in results if isinstance(r, Message)]
    assert len(final_msgs) == 1
    assert final_msgs[0].parts[0].content == "Done!"

    # Second runner call should carry approval
    assert len(runner.calls) == 2
    _, _, approval = runner.calls[1]
    assert approval is not None
    assert approval["action_id"] == action_id
    assert approval["confirmed"] is True


@pytest.mark.asyncio
async def test_hitl_denial_resume_passes_confirmed_false() -> None:
    """Denying a HITL prompt calls runner again with confirmed=False approval."""
    action_id = "action-99"
    hitl_events = [
        AgentStreamEvent("input_required", {"text": "Proceed?", "action_id": action_id}, final=True)
    ]
    final_events = [AgentStreamEvent("final_answer", {"text": "Cancelled."}, final=True)]
    runner = _multi_call_runner(hitl_events, final_events)
    agent = CugaACPAgent(runner=runner, name="test-agent", description="desc")
    ctx = _fake_context()

    results = await _collect_with_resumes(agent, _input_messages("start"), ctx, resume_texts=["deny"])

    final_msgs = [r for r in results if isinstance(r, Message)]
    assert len(final_msgs) == 1

    _, _, approval = runner.calls[1]
    assert approval is not None
    assert approval["action_id"] == action_id
    assert approval["confirmed"] is False


# ---------------------------------------------------------------------------
# Test: HITL — repeated ambiguous resume
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_hitl_ambiguous_resume_passes_none_approval() -> None:
    """An ambiguous (neither approve nor deny) resume text passes None approval."""
    action_id = "action-77"
    hitl_data = {"text": "Confirm?", "action_id": action_id}
    # First call: HITL; second call: final answer
    hitl_events = [AgentStreamEvent("input_required", hitl_data, final=True)]
    final_events = [AgentStreamEvent("final_answer", {"text": "ok"}, final=True)]
    runner = _multi_call_runner(hitl_events, final_events)
    agent = CugaACPAgent(runner=runner, name="test-agent", description="desc")
    ctx = _fake_context()

    # Resume with ambiguous text — no clear approve/deny
    await _collect_with_resumes(agent, _input_messages("go"), ctx, resume_texts=["maybe later"])

    assert len(runner.calls) == 2
    _, _, approval = runner.calls[1]
    # Ambiguous → approval payload should be None
    assert approval is None


# ---------------------------------------------------------------------------
# Test: HITL cycle limit
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_hitl_cycle_limit() -> None:
    """HITL resume cycles are capped at _MAX_AUTO_RESUMES."""
    action_id = "loop-action"
    hitl_event = AgentStreamEvent("input_required", {"text": "Again?", "action_id": action_id}, final=True)
    # Every call returns a HITL event — should be capped
    runner = _scripted_runner(hitl_event)
    agent = CugaACPAgent(runner=runner, name="test-agent", description="desc")
    ctx = _fake_context()

    resume_texts = ["approve"] * (_MAX_AUTO_RESUMES + 5)
    results = await _collect_with_resumes(agent, _input_messages("start"), ctx, resume_texts=resume_texts)

    # At most _MAX_AUTO_RESUMES + 1 total runner calls (initial + _MAX_AUTO_RESUMES resumes)
    assert len(runner.calls) <= _MAX_AUTO_RESUMES + 1
    # Final result must be the completion fallback or a MessageAwaitRequest (not an exception)
    assert len(results) >= 1
    last = results[-1]
    assert isinstance(last, (Message, MessageAwaitRequest))


# ---------------------------------------------------------------------------
# Test: exception sanitization
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_exception_sanitization() -> None:
    """Raw exceptions from the runner must not propagate; yield sanitized Error instead."""

    class _ExplodingRunner:
        async def run(
            self,
            message: str,
            context_id: str | None = None,
            approval: dict | None = None,
        ) -> AsyncIterator[AgentStreamEvent]:
            raise RuntimeError("internal secret: api_key=supersecret")
            yield  # make it an async generator

    agent = CugaACPAgent(runner=_ExplodingRunner(), name="test-agent", description="desc")
    ctx = _fake_context()

    results = await _collect(agent, _input_messages("hi"), ctx)

    assert len(results) == 1
    err = results[0]
    assert isinstance(err, Error)
    assert err.code == ErrorCode.SERVER_ERROR
    assert err.message == "CUGA agent execution failed"
    assert "supersecret" not in err.message
    assert "api_key" not in err.message


@pytest.mark.asyncio
async def test_exception_during_iteration_is_sanitized() -> None:
    """Exception raised mid-iteration is also caught and sanitized."""

    class _MidExplodingRunner:
        async def run(
            self,
            message: str,
            context_id: str | None = None,
            approval: dict | None = None,
        ) -> AsyncIterator[AgentStreamEvent]:
            yield AgentStreamEvent("thinking", {"text": "..."}, final=False)
            raise ValueError("sensitive data: password=123")

    agent = CugaACPAgent(runner=_MidExplodingRunner(), name="test-agent", description="desc")
    ctx = _fake_context()

    results = await _collect(agent, _input_messages("hi"), ctx)

    assert len(results) == 1
    err = results[0]
    assert isinstance(err, Error)
    assert err.code == ErrorCode.SERVER_ERROR
    assert "password" not in err.message


# ---------------------------------------------------------------------------
# Test: content_types properties
# ---------------------------------------------------------------------------


def test_input_output_content_types() -> None:
    runner = _scripted_runner()
    agent = CugaACPAgent(runner=runner, name="myagent", description="My agent")
    assert agent.input_content_types == ["text/plain"]
    assert agent.output_content_types == ["text/plain"]


def test_name_and_description() -> None:
    runner = _scripted_runner()
    agent = CugaACPAgent(runner=runner, name="myagent", description="My agent")
    assert agent.name == "myagent"
    assert agent.description == "My agent"
