"""Terminal chat coverage for the DAM image launcher."""

from __future__ import annotations

from io import StringIO
from typing import Any, AsyncIterator

import pytest

from cuga.backend.server.acp.terminal import run_console
from cuga.backend.server.agent_protocol.events import AgentStreamEvent

pytestmark = pytest.mark.unit


class _Runner:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str | None, dict[str, Any] | None]] = []
        self.closed = False

    async def run(
        self, message: str, context_id: str | None = None, approval: dict[str, Any] | None = None
    ) -> AsyncIterator[AgentStreamEvent]:
        self.calls.append((message, context_id, approval))
        print("library diagnostic with a secret")
        yield AgentStreamEvent("final_answer", {"text": f"Reply to {message}"}, final=True)

    async def shutdown(self) -> None:
        self.closed = True


@pytest.mark.anyio
async def test_terminal_chat_keeps_a_conversation_until_new() -> None:
    runner = _Runner()
    output = StringIO()

    status = await run_console(runner, StringIO("first\nsecond\n:new\nthird\n:quit\n"), output)

    assert status == 0
    assert [call[0] for call in runner.calls] == ["first", "second", "third"]
    assert runner.calls[0][1] == runner.calls[1][1]
    assert runner.calls[1][1] != runner.calls[2][1]
    assert "CUGA> Reply to first\n" in output.getvalue()
    assert "CUGA> Reply to third\n" in output.getvalue()
    assert "library diagnostic with a secret" not in output.getvalue()
    assert runner.closed


class _PermissionRunner(_Runner):
    async def run(
        self, message: str, context_id: str | None = None, approval: dict[str, Any] | None = None
    ) -> AsyncIterator[AgentStreamEvent]:
        self.calls.append((message, context_id, approval))
        if approval is None:
            yield AgentStreamEvent(
                "input_required", {"text": "Send message?", "action_id": "action-1"}, final=True
            )
        else:
            yield AgentStreamEvent("final_answer", {"text": "Approved"}, final=True)


@pytest.mark.anyio
async def test_terminal_chat_confirms_permission_and_resumes() -> None:
    runner = _PermissionRunner()
    output = StringIO()

    status = await run_console(runner, StringIO("send\nyes\n:quit\n"), output)

    assert status == 0
    assert runner.calls[1][1] == runner.calls[0][1]
    assert runner.calls[1][2] == {"action_id": "action-1", "confirmed": True}
    assert "Allow once? [y/N]" in output.getvalue()
    assert "CUGA> Approved\n" in output.getvalue()


class _ErrorRunner(_Runner):
    async def run(
        self, message: str, context_id: str | None = None, approval: dict[str, Any] | None = None
    ) -> AsyncIterator[AgentStreamEvent]:
        del message, context_id, approval
        raise RuntimeError("sensitive error detail")
        yield  # pragma: no cover


@pytest.mark.anyio
async def test_terminal_chat_reports_error_without_exposing_details() -> None:
    runner = _ErrorRunner()
    output = StringIO()

    status = await run_console(runner, StringIO("hello\n:quit\n"), output)

    assert status == 0
    assert "CUGA> Request failed (RequestError)." in output.getvalue()
    assert "sensitive error detail" not in output.getvalue()
    assert runner.closed
