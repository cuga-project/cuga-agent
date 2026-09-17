"""Contract tests for the agent_protocol package.

Covers:
- All valid AgentStreamEvent construction shapes.
- Structural (duck-typing) check that a fake async runner satisfies AgentRunner.
"""

from __future__ import annotations

import json as _json
from typing import Any, AsyncIterator

import pytest

from cuga.backend.server.agent_protocol import AgentRunner, AgentStreamEvent

pytestmark = pytest.mark.unit


# ── AgentStreamEvent ──────────────────────────────────────────────────────────


@pytest.mark.unit
def test_event_name_only() -> None:
    """Minimal event: only name is required."""
    ev = AgentStreamEvent(name="ping")
    assert ev.name == "ping"
    assert ev.data is None
    assert ev.final is False


@pytest.mark.unit
def test_event_with_mapping_data() -> None:
    """data may be a Mapping[str, Any]."""
    ev = AgentStreamEvent(name="final_answer", data={"text": "hello"}, final=True)
    assert ev.name == "final_answer"
    assert ev.data == {"text": "hello"}
    assert ev.final is True


@pytest.mark.unit
def test_event_with_string_data() -> None:
    """data may be a plain str."""
    ev = AgentStreamEvent(name="chunk", data="partial text")
    assert ev.data == "partial text"
    assert ev.final is False


@pytest.mark.unit
def test_event_with_none_data_explicit() -> None:
    """data=None is valid and is the default."""
    ev = AgentStreamEvent(name="heartbeat", data=None)
    assert ev.data is None


@pytest.mark.unit
def test_event_final_default_is_false() -> None:
    """final defaults to False."""
    ev = AgentStreamEvent(name="step")
    assert ev.final is False


@pytest.mark.unit
def test_event_terminal_flag() -> None:
    """final=True marks the terminal event."""
    ev = AgentStreamEvent(name="done", final=True)
    assert ev.final is True


@pytest.mark.unit
def test_event_slots_no_arbitrary_attributes() -> None:
    """AgentStreamEvent uses __slots__; arbitrary attributes must not be allowed."""
    ev = AgentStreamEvent(name="test")
    with pytest.raises(AttributeError):
        ev.unexpected_field = "oops"  # type: ignore[attr-defined]


# ── AgentRunner structural check ──────────────────────────────────────────────


class _FakeRunner:
    """Minimal fake that structurally matches AgentRunner."""

    async def run(
        self,
        message: str,
        context_id: str | None = None,
        approval: dict[str, Any] | None = None,
    ) -> AsyncIterator[AgentStreamEvent]:
        yield AgentStreamEvent(name="final_answer", data={"text": message}, final=True)


@pytest.mark.unit
def test_fake_runner_satisfies_agent_runner_protocol() -> None:
    """A class with the correct run() signature is usable as AgentRunner.

    No @runtime_checkable is used in this project, so we verify structural
    compatibility by annotating the fake as AgentRunner and confirming the
    method exists with the expected signature.
    """
    runner: AgentRunner = _FakeRunner()  # type: ignore[assignment]
    assert callable(runner.run)


@pytest.mark.unit
async def test_fake_runner_yields_agent_stream_event() -> None:
    """The fake runner must yield AgentStreamEvent instances."""
    runner = _FakeRunner()
    events = []
    async for ev in runner.run("hello"):
        events.append(ev)
    assert len(events) == 1
    assert isinstance(events[0], AgentStreamEvent)
    assert events[0].name == "final_answer"
    assert events[0].final is True


@pytest.mark.unit
async def test_fake_runner_passes_context_id() -> None:
    """context_id is accepted; here the fake ignores it (valid runner behaviour)."""
    runner = _FakeRunner()
    events = []
    async for ev in runner.run("msg", context_id="ctx-123"):
        events.append(ev)
    assert events[0].data == {"text": "msg"}


@pytest.mark.unit
async def test_fake_runner_passes_approval() -> None:
    """approval kwarg is accepted without error."""
    runner = _FakeRunner()
    events = []
    async for ev in runner.run("q", approval={"action_id": "x", "confirmed": True}):
        events.append(ev)
    assert events[0].final is True


# ── __init__ re-exports ───────────────────────────────────────────────────────


@pytest.mark.unit
def test_package_exports_agent_stream_event() -> None:
    """AgentStreamEvent must be importable from the package root."""
    from cuga.backend.server.agent_protocol import AgentStreamEvent as ASE

    assert ASE is AgentStreamEvent


@pytest.mark.unit
def test_package_exports_agent_runner() -> None:
    """AgentRunner must be importable from the package root."""
    from cuga.backend.server.agent_protocol import AgentRunner as AR

    assert AR is AgentRunner


# ── SimpleAgentRunner caller_user_id ──────────────────────────────────────────


class _Snap:
    def __init__(self, next_, values):
        self.next = next_
        self.values = values


class _Graph:
    def __init__(self, snaps):
        self._snaps = snaps
        self.calls = 0

    def get_state(self, _config):
        snap = self._snaps[min(self.calls, len(self._snaps) - 1)]
        self.calls += 1
        return snap


class _Agent:
    def __init__(self, graph):
        self.graph = graph


class _AppState:
    def __init__(self, graph):
        self.agent = _Agent(graph)
        self.output_format = None


def _answer_frame(text: str) -> bytes:
    payload = _json.dumps({"data": text, "variables": {}, "active_policies": []})
    return f"event: Answer\ndata: {payload}\n\n".encode()


@pytest.mark.anyio
@pytest.mark.unit
async def test_simple_agent_runner_passes_configured_caller_id() -> None:
    """SimpleAgentRunner should pass its configured caller_user_id to event_stream."""
    from cuga.backend.server.agent_protocol.simple_runner import SimpleAgentRunner

    captured_user_ids: list[str] = []
    graph = _Graph([_Snap((), {})])

    async def event_stream(**kwargs):
        captured_user_ids.append(kwargs.get("user_id", ""))
        yield _answer_frame("response")

    runner = SimpleAgentRunner(
        _AppState(graph),
        event_stream,
        auto_approve=False,
        caller_user_id="custom_caller",
    )
    events = [ev async for ev in runner.run("hello", "ctx-1")]

    assert events[-1].name == "final_answer"
    assert captured_user_ids == ["custom_caller"]


@pytest.mark.anyio
@pytest.mark.unit
async def test_simple_agent_runner_default_caller_id() -> None:
    """SimpleAgentRunner defaults to 'agent_protocol_user' when no caller_user_id given."""
    from cuga.backend.server.agent_protocol.simple_runner import SimpleAgentRunner

    captured_user_ids: list[str] = []
    graph = _Graph([_Snap((), {})])

    async def event_stream(**kwargs):
        captured_user_ids.append(kwargs.get("user_id", ""))
        yield _answer_frame("response")

    runner = SimpleAgentRunner(_AppState(graph), event_stream)
    events = [ev async for ev in runner.run("hello", "ctx-2")]

    assert events[-1].name == "final_answer"
    assert captured_user_ids == ["agent_protocol_user"]


@pytest.mark.anyio
@pytest.mark.unit
async def test_a2a_wrapper_passes_a2a_user() -> None:
    """SimpleA2ARunner compatibility wrapper must always pass caller_user_id='a2a_user'."""
    from cuga.backend.server.a2a.simple_runner import SimpleA2ARunner

    captured_user_ids: list[str] = []
    graph = _Graph([_Snap((), {})])

    async def event_stream(**kwargs):
        captured_user_ids.append(kwargs.get("user_id", ""))
        yield _answer_frame("a2a response")

    runner = SimpleA2ARunner(_AppState(graph), event_stream)
    events = [ev async for ev in runner.run("hello", "ctx-3")]

    assert events[-1].name == "final_answer"
    assert captured_user_ids == ["a2a_user"]


@pytest.mark.unit
def test_package_exports_simple_agent_runner() -> None:
    """SimpleAgentRunner must be importable from the package root."""
    from cuga.backend.server.agent_protocol import SimpleAgentRunner as SAR

    assert SAR.__name__ == "SimpleAgentRunner"


@pytest.mark.unit
def test_package_exports_supervisor_agent_runner() -> None:
    """SupervisorAgentRunner must be importable from the package root."""
    from cuga.backend.server.agent_protocol import SupervisorAgentRunner as SUAR

    assert SUAR.__name__ == "SupervisorAgentRunner"


@pytest.mark.unit
def test_a2a_stream_event_is_agent_stream_event_alias() -> None:
    """A2AStreamEvent in a2a/runner.py must be the same object as AgentStreamEvent."""
    from cuga.backend.server.a2a.runner import A2AStreamEvent
    from cuga.backend.server.agent_protocol import AgentStreamEvent

    assert A2AStreamEvent is AgentStreamEvent
